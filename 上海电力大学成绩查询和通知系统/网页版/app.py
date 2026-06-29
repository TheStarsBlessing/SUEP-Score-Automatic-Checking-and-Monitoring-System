import os
import json
import time
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ids import IdsAuth
from grade_fetcher import GradeFetcher, semester_str_to_id
from config_manager import load_config, save_config
from notifier import Notifier

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'

# 全局变量
scheduler = BackgroundScheduler()
scheduler.start()
job = None          # 当前调度任务
is_running = False
fetcher = None
ids_session = None
last_grades = []
config = load_config()

# 日志记录函数（写入文件和控制台）
def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_line = f'[{timestamp}] {msg}'
    print(log_line)
    # 写入 logs/web.log
    os.makedirs('logs', exist_ok=True)
    with open('logs/web.log', 'a', encoding='utf-8') as f:
        f.write(log_line + '\n')

# ---------- 辅助函数 ----------
def get_fetcher():
    global ids_session, fetcher
    if fetcher is None:
        ids_session = IdsAuth()
        # 尝试从 cookies 恢复
        if os.path.exists('cookies.txt'):
            try:
                with open('cookies.txt', 'r') as f:
                    cookies = {i.split('=')[0]: i.split('=')[1] for i in f.read().split(';')}
                ids_session = IdsAuth(cookies)
            except:
                pass
        if not ids_session.ok:
            username = config.get('username', '')
            password = config.get('password', '')
            if username and password:
                ids_session.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
        if ids_session.ok:
            with open('cookies.txt', 'w') as f:
                f.write(';'.join([f'{k}={v}' for k, v in ids_session.cookies.items()]))
            fetcher = GradeFetcher(ids_session)
            log_message('登录成功')
        else:
            log_message('登录失败，请检查账号密码')
    return fetcher

def perform_query():
    """执行一次成绩查询，返回 (成功与否, 变动信息, 错误信息)"""
    global last_grades, fetcher, ids_session
    max_retries = config.get('max_retries', 2)
    retry_interval = config.get('retry_interval', 60)
    sem_str = config.get('semester_str', '2025-2026.2')

    for attempt in range(max_retries + 1):
        try:
            # 重试时刷新认证
            if attempt > 0:
                log_message(f'重试 {attempt}，刷新登录...')
                renew_session()
            sem_id = semester_str_to_id(sem_str)
            new_grades = fetcher.fetch_grades(sem_id)
            # 保存最新数据
            GradeFetcher.save_to_file(new_grades)
            # 检测变动
            changes = fetcher.detect_changes(last_grades, new_grades)
            last_grades = new_grades
            if changes['added'] or changes['modified']:
                log_message(f'检测到变动: 新增{len(changes["added"])}, 修改{len(changes["modified"])}')
                # 发送通知
                if config.get('notification_methods'):
                    notifier = Notifier(config)
                    title = "SUEP成绩变动提醒"
                    content = build_notification_content(changes)
                    notifier.send(title, content)
            return True, changes, None
        except Exception as e:
            error_msg = str(e)
            log_message(f'查询失败 (尝试 {attempt+1}/{max_retries+1}): {error_msg}')
            if attempt < max_retries:
                time.sleep(retry_interval)
            else:
                # 所有重试失败
                log_message('达到最大重试次数，终止监控并发送错误通知')
                if config.get('notification_methods'):
                    notifier = Notifier(config)
                    notifier.send_error(f"连续查询失败，监控已终止。最后一次错误：{error_msg}")
                return False, None, error_msg
    return False, None, '未知错误'

def renew_session():
    """删除 cookies 并重新登录"""
    global ids_session, fetcher
    if os.path.exists('cookies.txt'):
        os.remove('cookies.txt')
    ids_session = IdsAuth()
    username = config.get('username', '')
    password = config.get('password', '')
    if username and password:
        ids_session.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
        if ids_session.ok:
            with open('cookies.txt', 'w') as f:
                f.write(';'.join([f'{k}={v}' for k, v in ids_session.cookies.items()]))
            fetcher = GradeFetcher(ids_session)
            log_message('重新登录成功')
        else:
            raise Exception('重新登录失败')
    else:
        raise Exception('未配置用户名密码')

def build_notification_content(changes):
    """构建通知内容（与桌面版一致）"""
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
            if is_failing(new['score']): line += "  ⚠️ 补考要加油啊！"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).strip()

def is_failing(score_str):
    try:
        return float(score_str.strip()) < 60
    except:
        return False

# ---------- 定时任务 ----------
def scheduled_query():
    if not is_running:
        return
    log_message('定时任务触发')
    success, changes, error = perform_query()
    if not success:
        # 发生致命错误，停止调度
        stop_monitor()
        # 界面通过轮询状态更新

# 调度器管理
def start_monitor():
    global is_running, job, last_grades
    if is_running:
        return
    # 加载历史成绩作为基准
    last_grades = GradeFetcher.load_from_file()
    is_running = True
    interval = config.get('query_interval', 300)
    if job:
        job.remove()
    job = scheduler.add_job(scheduled_query, IntervalTrigger(seconds=interval), id='grade_query')
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
    """返回当前状态：是否运行、上次查询结果、日志最后20行等"""
    global last_grades, is_running
    # 读取日志最后20行
    log_path = 'logs/web.log'
    logs = []
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            logs = lines[-20:] if len(lines) > 20 else lines
    # 获取成绩总数
    grade_count = len(last_grades)
    return jsonify({
        'running': is_running,
        'grade_count': grade_count,
        'logs': logs,
        'config': config
    })

@app.route('/api/grades', methods=['GET'])
def get_grades():
    """返回当前已加载的最新成绩列表（完整数据）"""
    global last_grades
    return jsonify(last_grades)

@app.route('/api/query', methods=['POST'])
def query_now():
    """立即执行一次查询（手动触发）"""
    success, changes, error = perform_query()
    return jsonify({
        'success': success,
        'changes': changes,
        'error': error
    })

@app.route('/api/start', methods=['POST'])
def start():
    start_monitor()
    return jsonify({'status': 'started'})

@app.route('/api/stop', methods=['POST'])
def stop():
    stop_monitor()
    return jsonify({'status': 'stopped'})

@app.route('/api/config', methods=['GET', 'POST'])
def config_manage():
    global config
    if request.method == 'POST':
        new_config = request.get_json()
        # 合并更新
        for key in new_config:
            if key in config or key in config_manager.DEFAULT_CONFIG:
                config[key] = new_config[key]
        # 特殊处理 notification_methods (列表)
        if 'notification_methods' in new_config:
            config['notification_methods'] = new_config['notification_methods']
        save_config(config)
        # 如果监控正在运行，需要重启以应用新间隔
        if is_running:
            stop_monitor()
            start_monitor()
        return jsonify({'status': 'ok'})
    else:
        return jsonify(config)

@app.route('/api/test_notification', methods=['POST'])
def test_notification():
    """发送测试通知"""
    notifier = Notifier(config)
    success = notifier.send_test()
    return jsonify({'success': success})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """获取完整日志（可滚动）"""
    log_path = 'logs/web.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            return f.read()
    return '暂无日志'

if __name__ == '__main__':
    # 启动时尝试登录
    get_fetcher()
    app.run(host='0.0.0.0', port=15029, debug=True)