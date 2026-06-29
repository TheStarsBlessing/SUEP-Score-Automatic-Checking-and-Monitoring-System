# envconfig.py
from config_manager import load_config

_config = load_config()

username = _config.get('username', '')
password = _config.get('password', '')
semester_str = _config.get('semester_str', '2025-2026.2')   # 使用字符串
query_interval = _config.get('query_interval', 300)
retry_interval = _config.get('retry_interval', 60)
max_retries = _config.get('max_retries', 2)
email_enabled = _config.get('email_enabled', False)
smtp_server = _config.get('smtp_server', 'smtp.qq.com')
smtp_port = _config.get('smtp_port', 465)
sender_email = _config.get('sender_email', '')
sender_password = _config.get('sender_password', '')
receiver_email = _config.get('receiver_email', '')