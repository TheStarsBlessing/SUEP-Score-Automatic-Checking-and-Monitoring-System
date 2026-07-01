import os
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect

# ---------- 延迟导入调度器 ----------
SCHEDULER_AVAILABLE = False
BackgroundScheduler = None
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    SCHEDULER_AVAILABLE = True
except Exception as e:
    print(f"⚠️ APScheduler 导入失败，定时监控功能不可用: {e}")

from ids import IdsAuth
from grade_fetcher import GradeFetcher, semester_str_to_id
from config_manager import load_config, save_config
from notifier import Notifier

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'

# ---------- 全局变量 ----------
scheduler = None
job = None
is_running = False
fetcher = None
ids_session = None
last_grades = []          # 当前学期成绩，用于界面显示
last_query_time = None    # 最新一次成功查询的时间（字符串）
config = load_config()

_first_scheduled_run = True

# ---------- 环境变量覆盖配置 ----------
def apply_env_overrides():
    global config
    if os.getenv('PROXY_ENABLED', '').lower() == 'true':
        config['proxy_enabled'] = True
    elif os.getenv('PROXY_ENABLED', '').lower() == 'false':
        config['proxy_enabled'] = False
    if os.getenv('PROXY_URL'):
        config['proxy_url'] = os.getenv('PROXY_URL')
    if os.getenv('QUERY_INTERVAL'):
        try:
            config['query_interval'] = int(os.getenv('QUERY_INTERVAL'))
        except:
            pass
    if os.getenv('SEMESTER_STR'):
        config['semester_str'] = os.getenv('SEMESTER_STR')
    save_config(config)

apply_env_overrides()

# ---------- 日志记录 ----------
LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'web.log')

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f'[{timestamp}] {msg}'
    print(log_line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_line + '\n')

# ---------- 日志清理功能 ----------
def clean_old_logs():
    """删除超过配置天数的日志文件（仅处理 logs 目录下的 .log 文件）"""
    retention_days = config.get('log_retention_days', 7)
    cutoff_time = datetime.now() - timedelta(days=retention_days)
    try:
        for filename in os.listdir(LOG_DIR):
            if filename.endswith('.log'):
                filepath = os.path.join(LOG_DIR, filename)
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if mtime < cutoff_time:
                    os.remove(filepath)
                    log_message(f"已删除过期日志文件: {filename}")
    except Exception as e:
        log_message(f"日志清理失败: {e}")

# 启动时清理一次
clean_old_logs()

# 定时清理（每天凌晨 3 点执行，如果调度器可用）
def schedule_log_cleanup():
    global scheduler
    if SCHEDULER_AVAILABLE and scheduler is None:
        scheduler = BackgroundScheduler()
        scheduler.start()
    if SCHEDULER_AVAILABLE:
        scheduler.add_job(
            clean_old_logs,
            CronTrigger(hour=3, minute=0),
            id='log_cleanup',
            replace_existing=True
        )
        log_message("已注册日志清理任务（每天 03:00）")

# ---------- 辅助函数 ----------
def get_proxy_config():
    if config.get('proxy_enabled', False):
        return config.get('proxy_url', 'socks5://127.0.0.1:1080')
    return None

def get_fetcher():
    global ids_session, fetcher
    if fetcher is not None:
        return fetcher

    proxy = get_proxy_config()
    cookies_file = 'data/cookies.txt'
    os.makedirs('data', exist_ok=True)

    if os.path.exists(cookies_file):
        try:
            with open(cookies_file, 'r') as f:
                cookies = {i.split('=')[0]: i.split('=')[1] for i in f.read().split(';')}
            ids_session = IdsAuth(cookies, proxy=proxy)
        except Exception:
            pass

    if ids_session is None or not ids_session.ok:
        username = config.get('username', '')
        password = config.get('password', '')
        if username and password:
            ids_session = IdsAuth(proxy=proxy)
            ids_session.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
        else:
            raise Exception('未配置用户名或密码')

    if ids_session.ok:
        with open(cookies_file, 'w') as f:
            f.write(';'.join([f'{k}={v}' for k, v in ids_session.cookies.items()]))
        fetcher = GradeFetcher(ids_session)
        log_message('登录成功')
        return fetcher
    else:
        raise Exception('登录失败，请检查账号密码和网络')

def renew_session():
    global ids_session, fetcher
    cookies_file = 'data/cookies.txt'
    if os.path.exists(cookies_file):
        os.remove(cookies_file)
    proxy = get_proxy_config()
    ids_session = IdsAuth(proxy=proxy)
    username = config.get('username', '')
    password = config.get('password', '')
    if not username or not password:
        raise Exception('未配置用户名密码')
    ids_session.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
    if ids_session.ok:
        with open(cookies_file, 'w') as f:
            f.write(';'.join([f'{k}={v}' for k, v in ids_session.cookies.items()]))
        fetcher = GradeFetcher(ids_session)
        log_message('重新登录成功')
    else:
        raise Exception('重新登录失败')

def is_failing(score_str):
    try:
        return float(score_str.strip()) < 60
    except:
        return False

def build_notification_content(changes):
    lines = []
    if changes['added']:
        lines.append("📌 新增课程：")
        for g in changes['added']:
            line = f"{g['name']} ({g['code']})  成绩: {g['score']}"
            if g.get('gpa'): line += f"  绩点: {g['gpa']}"
            if is_failing(g['score']): line += "  ⚠️ 补考要加油啊！"
            lines.append(line)
        lines.append("")
    if changes['modified']:
        lines.append("📝 成绩更新：")
        for m in changes['modified']:
            old, new = m['old'], m['new']
            line = f"{new['name']} ({new['code']})  成绩: {old['score']} → {new['score']}"
            if new.get('gpa'): line += f"  绩点: {old.get('gpa', '')} → {new['gpa']}"
            old_fail = is_failing(old['score'])
            new_fail = is_failing(new['score'])
            if old_fail and not new_fail:
                line += "  🎉恭喜过关！"
            elif old_fail and new_fail:
                line += "  QAQ"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).strip()

# ---------- 核心查询函数 ----------
def perform_query():
    global last_grades, fetcher, ids_session, last_query_time
    max_retries = config.get('max_retries', 2)
    retry_interval = config.get('retry_interval', 60)
    sem_str = config.get('semester_str', '2025-2026.2')

    # 加载当前学期的旧成绩
    old_grades = GradeFetcher.load_from_file(sem_str)

    for attempt in range(max_retries + 1):
        try:
            if fetcher is None:
                get_fetcher()
            if attempt > 0:
                log_message(f'重试 {attempt}，刷新登录...')
                renew_session()
            sem_id = semester_str_to_id(sem_str)
            new_grades = fetcher.fetch_grades(sem_id)

            # 只有非空结果才保存并检测变化
            if new_grades:
                GradeFetcher.save_to_file(new_grades, sem_str)
                changes = fetcher.detect_changes(old_grades, new_grades)
                last_grades = new_grades  # 更新全局显示用
                if changes['added'] or changes['modified']:
                    log_message(f'检测到变动: 新增{len(changes["added"])}, 修改{len(changes["modified"])}')
                    if config.get('notification_methods'):
                        notifier = Notifier(config)
                        title = "SUEP成绩变动提醒"
                        content = build_notification_content(changes)
                        if content:  # 有内容才发送
                            notifier.send(title, content)
                else:
                    log_message('查询完成，无变化')
                # 更新查询时间
                last_query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                return True, changes, None
            else:
                # 查询成功但结果为空，不保存，视为无变化
                log_message('查询结果为空，未更新成绩文件')
                last_query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                return True, {'added': [], 'modified': []}, None

        except Exception as e:
            error_msg = str(e)
            log_message(f'查询失败 (尝试 {attempt+1}/{max_retries+1}): {error_msg}')
            if attempt < max_retries:
                time.sleep(retry_interval)
            else:
                log_message('达到最大重试次数，终止监控并发送错误通知')
                if config.get('notification_methods'):
                    notifier = Notifier(config)
                    notifier.send_error(f"连续查询失败，监控已终止。最后一次错误：{error_msg}")
                return False, None, error_msg
    return False, None, '未知错误'

# ---------- 定时任务 ----------
def scheduled_query():
    global is_running, _first_scheduled_run
    if not is_running:
        return

    if _first_scheduled_run:
        log_message('定时任务触发')
        _first_scheduled_run = False

    success, changes, error = perform_query()
    if not success:
        stop_monitor()

def start_monitor():
    global is_running, job, scheduler, _first_scheduled_run, last_grades
    if is_running:
        return
    # 加载当前学期的成绩用于显示
    sem_str = config.get('semester_str', '2025-2026.2')
    last_grades = GradeFetcher.load_from_file(sem_str)

    is_running = True
    interval = config.get('query_interval', 300)

    if not SCHEDULER_AVAILABLE:
        log_message('⚠️ APScheduler 不可用，无法启动定时监控')
        is_running = False
        return

    if scheduler is None:
        scheduler = BackgroundScheduler()
        scheduler.start()
        # 启动日志清理调度（仅一次）
        schedule_log_cleanup()

    if job:
        job.remove()
    job = scheduler.add_job(scheduled_query, IntervalTrigger(seconds=interval), id='grade_query')
    _first_scheduled_run = True
    log_message(f'监控已启动，间隔 {interval} 秒')

def stop_monitor():
    global is_running, job
    is_running = False
    if job:
        job.remove()
        job = None
    log_message('监控已停止')

# ---------- Flask 路由 ----------
@app.route('/')
def index():
    return render_template('index.html', config=config)

@app.route('/api/status', methods=['GET'])
def get_status():
    global last_grades, is_running, last_query_time
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            logs = lines[-20:] if len(lines) > 20 else lines
    grade_count = len(last_grades)
    return jsonify({
        'running': is_running,
        'grade_count': grade_count,
        'logs': logs,
        'config': config,
        'last_query_time': last_query_time  # 新增返回
    })

@app.route('/api/grades', methods=['GET'])
def get_grades():
    global last_grades
    return jsonify(last_grades)

@app.route('/api/query', methods=['POST'])
def query_now():
    log_message('手动查询开始')
    try:
        success, changes, error = perform_query()
        if success:
            log_message('手动查询完成')
        else:
            log_message(f'手动查询完成（失败: {error}）')
        return jsonify({
            'success': success,
            'changes': changes,
            'error': error
        })
    except Exception as e:
        log_message(f'手动查询异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/start', methods=['POST'])
def start():
    start_monitor()
    return jsonify({'status': 'started' if is_running else 'failed'})

@app.route('/api/stop', methods=['POST'])
def stop():
    stop_monitor()
    return jsonify({'status': 'stopped'})

@app.route('/api/config', methods=['GET', 'POST'])
def config_manage():
    global config
    if request.method == 'POST':
        new_config = request.get_json()
        for key in new_config:
            if key in config or key in ['notification_methods']:
                config[key] = new_config[key]
        if 'notification_methods' in new_config:
            config['notification_methods'] = new_config['notification_methods']
        save_config(config)
        # 如果监控正在运行，重启以应用新间隔等
        if is_running:
            stop_monitor()
            start_monitor()
        return jsonify({'status': 'ok'})
    else:
        return jsonify(config)

@app.route('/api/test_notification', methods=['POST'])
def test_notification():
    notifier = Notifier(config)
    success = notifier.send_test()
    return jsonify({'success': success})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return '暂无日志'

@app.route('/api/clean_logs', methods=['POST'])
def clean_logs_api():
    """手动触发日志清理"""
    clean_old_logs()
    return jsonify({'status': 'cleaned'})

if __name__ == '__main__':
    # 启动时调度日志清理（若尚未调度）
    if SCHEDULER_AVAILABLE:
        schedule_log_cleanup()
    app.run(host='0.0.0.0', port=15029, debug=True)