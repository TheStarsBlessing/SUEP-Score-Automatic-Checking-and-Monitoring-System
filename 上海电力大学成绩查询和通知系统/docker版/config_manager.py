import json
import os

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "semester_str": "2025-2026.2",
    "query_interval": 300,
    "retry_interval": 60,
    "max_retries": 2,
    "notification_methods": [],
    "email_enabled": False,
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "sender_email": "",
    "sender_password": "",
    "receiver_email": "",
    "serverchan_token": "",
    "pushplus_token": "",
    "wework_webhook": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "bark_key": "",
    "proxy_enabled": True,
    "proxy_url": "socks5://easyconnect:1080",   # 容器内默认使用服务名
    "log_retention_days": 7                     # 新增：日志保留天数
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for key, value in DEFAULT_CONFIG.items():
            if key not in data:
                data[key] = value
        if 'notification_method' in data and data['notification_method'] not in ('none', ''):
            if 'notification_methods' not in data or not data['notification_methods']:
                data['notification_methods'] = [data['notification_method']]
        return data
    except:
        return DEFAULT_CONFIG.copy()

def save_config(config):
    if 'notification_methods' not in config or not isinstance(config['notification_methods'], list):
        config['notification_methods'] = []
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)