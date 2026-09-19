# -*- coding: utf-8 -*-
"""SUEP 成绩自动查询与监控 —— Web 主程序。

**网页版 与 docker版 共用这一份**（两版差异仅在 config_manager.py 的 _SUBDIR 一行，
docker 版把数据放在 data/）。改动请两处同步。

与旧版相比修掉的问题：

1. ``config_manager`` 未导入却使用（旧网页版 app.py 第 279 行
   ``key in config_manager.DEFAULT_CONFIG``）：只要前端提交一个不在 config 里的键，
   保存设置就会 NameError → HTTP 500。现在显式导入 DEFAULT_CONFIG 做白名单。
2. ``debug=True`` + ``host=0.0.0.0``：Werkzeug 调试器等同于开放远程代码执行，
   而且会自动重载、把模块级代码跑两遍（旧版会重复登录）。现在 debug=False。
3. Web 界面完全没有鉴权，``/api/config`` 直接把学号、密码、SMTP 授权码、
   各推送 Token 明文返回给任何能访问端口的人。现在支持 web_password（HTTP Basic），
   并在未设置时于启动日志里明确告警。
4. ``__main__`` 里同步登录：学校站点不可达时整个服务起不来（旧网页版）。
   现在登录延迟到第一次查询，服务照常启动。
5. 手动"立即查询"与定时任务可以并发跑同一个 perform_query，
   共用 Session、同时写同一个成绩文件。现在用锁串行化。
6. 空结果与"会话过期"被混为一谈（未登录时教务系统返回 HTTP 200 的登录页），
   于是过期后永远"查询成功但 0 条成绩"。现在靠 GradeFetcher 抛 SessionExpired
   触发重新登录。
7. 环境变量会在启动时 save_config 写回 config.json，永久覆盖用户设置且丢失原值；
   现在只作用于内存。
8. SECRET_KEY 硬编码 'dev-secret-key'；现在每次启动随机生成。
"""
import atexit
import hmac
import os
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, render_template, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config_manager
from config_manager import DEFAULT_CONFIG, load_config, save_config
from grade_fetcher import GradeFetcher, semester_str_to_id
from ids import IdsAuth, SessionExpired, parse_cookie_string, dump_cookie_string
from notifier import Notifier, summarize

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(32)

LOG_FILE = os.path.join(config_manager.LOG_DIR, 'web.log')
LOG_MAX_SIZE = 1 * 1024 * 1024      # 单文件 1MB，超出后轮转为 .old

config = load_config()

# ---------------- 环境变量覆盖（只作用于内存，不写回 config.json） ----------------
_ENV_MAP = (
    ('PROXY_ENABLED', 'proxy_enabled', 'bool'),
    ('PROXY_URL', 'proxy_url', 'str'),
    ('QUERY_INTERVAL', 'query_interval', 'int'),
    ('RETRY_INTERVAL', 'retry_interval', 'int'),
    ('MAX_RETRIES', 'max_retries', 'int'),
    ('SEMESTER_STR', 'semester_str', 'str'),
    ('WEB_HOST', 'web_host', 'str'),
    ('WEB_PORT', 'web_port', 'int'),
    ('WEB_PASSWORD', 'web_password', 'str'),
    ('LOG_RETENTION_DAYS', 'log_retention_days', 'int'),
)


def apply_env_overrides(target):
    for env_name, key, kind in _ENV_MAP:
        raw = os.getenv(env_name)
        if raw is None or raw == '':
            continue
        if kind == 'bool':
            value = raw.strip().lower() in ('1', 'true', 'yes', 'on')
        elif kind == 'int':
            try:
                value = int(raw)
            except ValueError:
                continue
        else:
            value = raw
        target[key] = value


apply_env_overrides(config)

# ---------------- 全局状态 ----------------
scheduler = BackgroundScheduler(job_defaults={
    'coalesce': True,
    'max_instances': 1,
    'misfire_grace_time': 60,
})
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False) if scheduler.running else None)

job = None
is_running = False
fetcher = None
ids_session = None
last_grades = []
last_query_time = None
_query_lock = threading.Lock()          # 串行化 perform_query（手动 + 定时）
_state_lock = threading.Lock()


# ---------------- 日志 ----------------
def log_message(msg):
    """写日志：控制台 + 文件，文件超过 1MB 轮转为 .old。"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = '[%s] %s' % (timestamp, msg)
    print(log_line)
    try:
        config_manager.ensure_dirs()
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_SIZE:
            old_file = LOG_FILE + '.old'
            try:
                if os.path.exists(old_file):
                    os.remove(old_file)
                os.replace(LOG_FILE, old_file)
            except OSError as exc:
                print('日志轮转失败: %s' % exc)
        with open(LOG_FILE, 'a', encoding='utf-8', newline='\n') as f:
            f.write(log_line + '\n')
    except OSError as exc:
        print('写日志失败: %s' % exc)


def clean_old_logs():
    """按 log_retention_days 删除 logs 目录下的过期 .log 文件。"""
    try:
        retention = int(config.get('log_retention_days', 7) or 7)
    except (TypeError, ValueError):
        retention = 7
    cutoff = datetime.now() - timedelta(days=retention)
    removed = 0
    try:
        for filename in os.listdir(config_manager.LOG_DIR):
            if not filename.endswith('.log'):
                continue
            filepath = os.path.join(config_manager.LOG_DIR, filename)
            try:
                if datetime.fromtimestamp(os.path.getmtime(filepath)) < cutoff:
                    os.remove(filepath)
                    removed += 1
            except OSError:
                continue
    except OSError as exc:
        log_message('日志清理失败: %s' % exc)
        return 0
    if removed:
        log_message('已清理 %d 个过期日志文件' % removed)
    return removed


# ---------------- 访问控制 ----------------
@app.before_request
def require_auth():
    """设置了 web_password 时启用 HTTP Basic 鉴权（留空则不做任何校验）。"""
    password = str(config.get('web_password') or '')
    if not password:
        return None
    auth = request.authorization
    if auth is not None and hmac.compare_digest(
            (auth.password or '').encode('utf-8'), password.encode('utf-8')):
        return None
    return Response('需要授权', 401,
                    {'WWW-Authenticate': 'Basic realm="SUEP Grade Monitor"'})


# ---------------- 辅助函数 ----------------
def get_tls_kwargs():
    try:
        connect = int(config.get('connect_timeout', 8) or 8)
        read = int(config.get('read_timeout', 20) or 20)
    except (TypeError, ValueError):
        connect, read = 8, 20
    return {
        'timeout': (connect, read),
        'ssl_verify': bool(config.get('ssl_verify', False)),
        'ca_bundle': (config.get('ca_bundle') or '').strip() or None,
    }


def get_proxy_config():
    if config.get('proxy_enabled'):
        return (config.get('proxy_url') or '').strip() or None
    return None


def build_ids_session(cookies=None):
    return IdsAuth(cookies, proxy=get_proxy_config(), **get_tls_kwargs())


def save_cookies(ids):
    try:
        config_manager.ensure_dirs()
        with open(config_manager.COOKIE_FILE, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump_cookie_string(ids.cookies))
    except OSError as exc:
        log_message('写入 cookies 失败: %s' % exc)


def get_fetcher():
    """取得（必要时建立）一个可用的 GradeFetcher。失败抛异常。"""
    global ids_session, fetcher
    if fetcher is not None:
        return fetcher

    cookies = {}
    if os.path.exists(config_manager.COOKIE_FILE):
        try:
            with open(config_manager.COOKIE_FILE, 'r', encoding='utf-8') as f:
                cookies = parse_cookie_string(f.read())
        except OSError as exc:
            log_message('读取 cookies 失败: %s' % exc)

    if cookies:
        ids_session = build_ids_session(cookies)
        if ids_session.ok:
            log_message('使用缓存的 Cookie 登录成功')
        else:
            log_message('缓存的 Cookie 已失效，改为账号密码登录')
    else:
        ids_session = build_ids_session()

    if not ids_session.ok:
        username = (config.get('username') or '').strip()
        password = config.get('password') or ''
        if not username or not password:
            raise Exception('未配置学号或密码，请在设置里填写')
        ids_session.login(username, password)
        if not ids_session.ok:
            raise Exception(ids_session.last_error or '登录失败，请检查账号密码和网络')

    save_cookies(ids_session)
    fetcher = GradeFetcher(ids_session, logger=log_message)
    log_message('登录成功（学号 %s）' % (config.get('username') or '?'))
    return fetcher


def renew_session():
    """删掉本地 Cookie 并强制重新登录。"""
    global ids_session, fetcher
    fetcher = None
    if os.path.exists(config_manager.COOKIE_FILE):
        try:
            os.remove(config_manager.COOKIE_FILE)
        except OSError as exc:
            log_message('删除 cookies 失败: %s' % exc)

    username = (config.get('username') or '').strip()
    password = config.get('password') or ''
    if not username or not password:
        raise Exception('未配置学号或密码，无法重新登录')

    ids_session = build_ids_session()
    ids_session.login(username, password)
    if not ids_session.ok:
        raise Exception(ids_session.last_error or '重新登录失败')
    save_cookies(ids_session)
    fetcher = GradeFetcher(ids_session, logger=log_message)
    log_message('重新登录成功')


def is_failing(score_str):
    try:
        return float(str(score_str).strip()) < 60
    except (TypeError, ValueError):
        return False


def build_notification_content(changes):
    lines = []
    if changes.get('added'):
        lines.append('📌 新增课程：')
        for g in changes['added']:
            line = '%s (%s)  成绩: %s' % (g.get('name'), g.get('code'), g.get('score'))
            if g.get('gpa'):
                line += '  绩点: %s' % g['gpa']
            if is_failing(g.get('score')):
                line += '  ⚠️ 补考要加油啊！'
            lines.append(line)
        lines.append('')
    if changes.get('modified'):
        lines.append('📝 成绩更新：')
        for item in changes['modified']:
            old, new = item['old'], item['new']
            line = '%s (%s)  成绩: %s → %s' % (new.get('name'), new.get('code'),
                                              old.get('score'), new.get('score'))
            if new.get('gpa'):
                line += '  绩点: %s → %s' % (old.get('gpa', ''), new['gpa'])
            old_fail, new_fail = is_failing(old.get('score')), is_failing(new.get('score'))
            if old_fail and not new_fail:
                line += '  🎉恭喜过关！'
            elif old_fail and new_fail:
                line += '  QAQ'
            elif new_fail:
                line += '  ⚠️ 补考要加油啊！'
            lines.append(line)
        lines.append('')
    return '\n'.join(lines).strip()


def notify(title, content):
    if not content:
        return False
    methods = config.get('notification_methods') or []
    if not methods:
        return False
    notifier = Notifier(config, logger=log_message)
    ok = notifier.send(title, content)
    log_message('通知结果: %s' % summarize(notifier.results))
    return ok


# ---------------- 核心查询 ----------------
def perform_query():
    """执行一次成绩查询，返回 (成功?, changes, 错误信息)。并发安全。"""
    with _query_lock:
        return _perform_query_locked()


def _perform_query_locked():
    global last_grades, last_query_time
    sem_str = (config.get('semester_str') or '2025-2026.2').strip()
    try:
        retries = max(0, int(config.get('max_retries', 2) or 0))
        retry_interval = max(0, int(config.get('retry_interval', 60) or 0))
    except (TypeError, ValueError):
        retries, retry_interval = 2, 60

    old_grades = GradeFetcher.load_from_file(sem_str)
    budget = retries + 1
    auth_extra = 1          # 会话过期额外给一次机会，不占用用户配置的重试次数
    tried = 0
    last_error = None

    while tried < budget:
        tried += 1
        try:
            current = get_fetcher()
            if tried > 1:
                try:
                    renew_session()
                    current = fetcher
                except Exception as exc:
                    log_message('重新登录失败: %s' % exc)
                    last_error = exc
            new_grades = current.fetch_by_semester_str(sem_str)

            if not new_grades:
                # 页面正常但没有成绩行 —— 确实是没有成绩，不写文件、不改基准
                log_message('查询成功，本学期的成绩列表为空')
                last_query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                return True, {'added': [], 'modified': [], 'removed': []}, None

            GradeFetcher.save_to_file(new_grades, sem_str)
            changes = current.detect_changes(old_grades, new_grades)
            last_grades = new_grades
            last_query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_message('查询成功，获取 %d 条成绩' % len(new_grades))

            if changes.get('added') or changes.get('modified'):
                log_message('检测到变动: 新增 %d, 修改 %d'
                            % (len(changes['added']), len(changes['modified'])))
                notify('SUEP成绩变动提醒', build_notification_content(changes))
            else:
                log_message('查询完成，无变化')
            return True, changes, None

        except Exception as exc:
            last_error = exc
            log_message('查询失败 (尝试 %d/%d): %s' % (tried, budget, exc))
            if isinstance(exc, SessionExpired) and auth_extra > 0:
                auth_extra -= 1
                budget += 1
            if tried >= budget:
                break
            if retry_interval:
                time.sleep(retry_interval)

    log_message('达到最大尝试次数，终止监控')
    notify('SUEP成绩监控已终止',
           '连续查询失败，监控已自动停止。\n最后一次错误：\n%s' % last_error)
    return False, None, str(last_error)


# ---------------- 定时任务 ----------------
def scheduled_query():
    if not is_running:
        return
    success, _changes, _error = perform_query()
    if not success:
        stop_monitor()


def start_monitor():
    global is_running, job, last_grades
    with _state_lock:
        if is_running:
            return True

        sem_str = (config.get('semester_str') or '2025-2026.2').strip()
        last_grades = GradeFetcher.load_from_file(sem_str)

        try:
            interval = max(30, int(config.get('query_interval', 300) or 300))
        except (TypeError, ValueError):
            interval = 300

        if job:
            try:
                job.remove()
            except Exception:
                pass
        job = scheduler.add_job(scheduled_query, IntervalTrigger(seconds=interval),
                                id='grade_query', replace_existing=True)
        is_running = True
        log_message('监控已启动，间隔 %s 秒（已加载历史成绩 %d 条）'
                    % (interval, len(last_grades)))
        return True


def stop_monitor():
    global is_running, job
    with _state_lock:
        if not is_running and job is None:
            return
        is_running = False
        if job:
            try:
                job.remove()
            except Exception:
                pass
            job = None
        log_message('监控已停止')


# ---------------- Flask 路由 ----------------
@app.route('/')
def index():
    return render_template('index.html', config=config)


@app.route('/api/status', methods=['GET'])
def get_status():
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            logs = lines[-20:]
        except OSError:
            pass
    return jsonify({
        'running': is_running,
        'grade_count': len(last_grades),
        'last_query_time': last_query_time,
        'logs': logs,
    })


@app.route('/api/grades', methods=['GET'])
def get_grades():
    return jsonify(last_grades)


@app.route('/api/query', methods=['POST'])
def query_now():
    log_message('手动查询开始')
    try:
        success, changes, error = perform_query()
        log_message('手动查询结束（%s）' % ('成功' if success else '失败: %s' % error))
        return jsonify({'success': success, 'changes': changes, 'error': error})
    except Exception as exc:
        log_message('手动查询异常: %s' % exc)
        return jsonify({'success': False, 'changes': None, 'error': str(exc)}), 500


@app.route('/api/start', methods=['POST'])
def start():
    return jsonify({'status': 'started' if start_monitor() else 'failed'})


@app.route('/api/stop', methods=['POST'])
def stop():
    stop_monitor()
    return jsonify({'status': 'stopped'})


@app.route('/api/config', methods=['GET', 'POST'])
def config_manage():
    global config
    if request.method == 'GET':
        return jsonify(config)

    new_config = request.get_json(silent=True)
    if not isinstance(new_config, dict):
        return jsonify({'status': 'error', 'message': '请求体不是合法的 JSON 对象'}), 400

    unknown = [k for k in new_config if k not in DEFAULT_CONFIG]
    for key, value in new_config.items():
        if key in DEFAULT_CONFIG:
            config[key] = value
    if 'notification_methods' in new_config and not isinstance(
            config['notification_methods'], list):
        config['notification_methods'] = []
    if unknown:
        log_message('设置里有未识别的字段，已忽略: %s' % ', '.join(unknown))

    save_config(config)
    if is_running:
        stop_monitor()
        start_monitor()
    log_message('设置已保存')
    return jsonify({'status': 'ok', 'ignored': unknown})


@app.route('/api/semesters', methods=['GET'])
def get_semesters():
    """从教务系统爬取可选学期列表（供界面下拉框使用）。

    走的是成绩页内嵌"学年学期"控件的数据源：
    GET person!innerIndex.action 取控件 id -> POST dataQuery.action
    {tagId, dataType:"semesterCalendar"}，返回带真实 semesterId 的学期列表。
    拿不到时返回 success=false，界面保留手工输入的学期。
    """
    try:
        current = get_fetcher()
        items = current.fetch_semester_list(force=request.args.get('refresh') == '1')
        return jsonify({
            'success': bool(items),
            'semesters': items,
            'current': config.get('semester_str') or '',
            'error': '' if items else '教务系统未返回可用的学期列表',
        })
    except Exception as exc:
        log_message('获取学期列表失败: %s' % exc)
        return jsonify({'success': False, 'semesters': [],
                        'current': config.get('semester_str') or '',
                        'error': str(exc)})


@app.route('/api/test_notification', methods=['POST'])
def test_notification():
    notifier = Notifier(config, logger=log_message)
    success = notifier.send_test()
    log_message('测试通知结果: %s' % summarize(notifier.results))
    return jsonify({'success': success, 'results': notifier.results})


@app.route('/api/logs', methods=['GET'])
def get_logs():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return f.read()
        except OSError as exc:
            return '读取日志失败: %s' % exc
    return '暂无日志'


@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'w', encoding='utf-8'):
                pass
            log_message('日志已手动清空')
            return jsonify({'status': 'cleared'})
        except OSError as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 500
    return jsonify({'status': 'no_log'})


@app.route('/api/clean_logs', methods=['POST'])
def clean_logs_api():
    return jsonify({'status': 'cleaned', 'removed': clean_old_logs()})


def _bootstrap():
    """启动时做的最小初始化（不联网、不阻塞）。"""
    config_manager.ensure_dirs()
    global last_grades
    sem_str = (config.get('semester_str') or '2025-2026.2').strip()
    try:
        last_grades = GradeFetcher.load_from_file(sem_str)
    except Exception as exc:
        log_message('加载历史成绩失败: %s' % exc)
    try:
        scheduler.add_job(clean_old_logs, CronTrigger(hour=3, minute=0),
                          id='log_cleanup', replace_existing=True)
    except Exception as exc:
        log_message('注册日志清理任务失败: %s' % exc)


_bootstrap()


if __name__ == '__main__':
    host = (config.get('web_host') or '0.0.0.0').strip() or '0.0.0.0'
    try:
        port = int(config.get('web_port', 15029) or 15029)
    except (TypeError, ValueError):
        port = 15029
    log_message('服务启动: http://%s:%s' % (host, port))
    if not (config.get('web_password') or '').strip():
        log_message('⚠️ 未设置 web_password，Web 界面没有任何鉴权，'
                    '任何能访问该端口的人都能读取学号/密码/通知 Token。'
                    '建议在设置里配置"Web 访问口令"。')
    # debug 必须保持 False：Werkzeug 调试器在 0.0.0.0 上等于开放远程代码执行
    app.run(host=host, port=port, debug=False, threaded=True)
