import json
import os

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "semester_str": "2025-2026.2",
    "query_interval": 300,
    "retry_interval": 60,
    "max_retries": 2,
    # 通知相关 —— 改为列表，支持多种方式
    "notification_methods": [],  # 例如 ["email", "serverchan"]
    "email_enabled": False,      # 保留兼容，但实际由 methods 控制
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "sender_email": "",
    "sender_password": "",
    "receiver_email": "",
    # 各通知方式专用配置
    "serverchan_token": "",
    "pushplus_token": "",
    "wework_webhook": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "bark_key": "",
}

CONFIG_FILE = "config.json"

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
        # 兼容旧版本：如果存在 notification_method 且不为 none，则迁移到列表
        if 'notification_method' in data and data['notification_method'] not in ('none', ''):
            if 'notification_methods' not in data or not data['notification_methods']:
                data['notification_methods'] = [data['notification_method']]
        return data
    except:
        return DEFAULT_CONFIG.copy()

def save_config(config):
    # 确保 notification_methods 是列表
    if 'notification_methods' not in config or not isinstance(config['notification_methods'], list):
        config['notification_methods'] = []
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)