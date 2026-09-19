# -*- coding: utf-8 -*-
"""配置读写、路径解析、原子落盘。

三个版本共用同一份实现，**只有下面 _SUBDIR 一行不同**：
docker 版是 ``"data"``（与 docker-compose 挂载的 ./data:/app/data 对应），
桌面版 / 网页版是 ``""``（数据就放在程序目录）。

顺带修掉的问题：
* 旧版把配置、cookie、日志、成绩文件全都用相对路径（相对当前工作目录），
  换个目录启动就会读写到别处；现在一律相对于脚本所在目录。
* 旧版 save_config 直接覆盖写入，进程被杀会留下半截 JSON；
  旧版 load_config 遇到坏 JSON 静默返回默认值，用户会以为配置"丢了"。现在原子写 + 明确报错。
"""
import json
import os
import tempfile

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_SUBDIR = "data"                    # ← docker 版是 "data"，桌面版/网页版是 ""

DATA_DIR = os.path.join(BASE_DIR, _SUBDIR) if _SUBDIR else BASE_DIR
LOG_DIR = os.path.join(BASE_DIR, 'logs')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
COOKIE_FILE = os.path.join(DATA_DIR, 'cookies.txt')
LEGACY_COOKIE_FILE = os.path.join(BASE_DIR, 'cookies.txt')


def ensure_dirs():
    for path in (DATA_DIR, LOG_DIR):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    # ---- 账号 ----
    "username": "",
    "password": "",
    # ---- 查询 ----
    "semester_str": "2025-2026.2",
    "query_interval": 300,
    "retry_interval": 60,
    "max_retries": 2,
    # ---- 网络 ----
    "connect_timeout": 8,          # 秒，TCP 连接超时
    "read_timeout": 20,            # 秒，读取超时
    "ssl_verify": False,           # 学校证书链不在系统信任库，默认关闭校验
    "ca_bundle": "",               # 填了 CA 文件/目录路径就自动开启校验
    "proxy_enabled": False,        # docker 版常用（EasyConnect SOCKS5）
    "proxy_url": "",
    # ---- Web 界面（网页版 / docker版）----
    "web_host": "0.0.0.0",
    "web_port": 15029,
    "web_password": "",            # 留空 = 不鉴权（强烈建议设置）
    # ---- 日志 ----
    "log_retention_days": 7,
    # ---- 通知（notification_methods 为启用列表）----
    "notification_methods": [],
    "email_enabled": False,        # 旧字段，保留兼容
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
}

# 这些字段属于"机密"，Web 界面回显与日志里要小心处理
SECRET_KEYS = ('password', 'sender_password', 'serverchan_token', 'pushplus_token',
               'wework_webhook', 'telegram_bot_token', 'bark_key', 'web_password')


def _warn(msg):
    print('[config_manager] %s' % msg)


def load_config(path=None):
    """读取配置；文件不存在时用默认配置创建它。"""
    path = path or CONFIG_FILE
    ensure_dirs()
    if not os.path.exists(path):
        config = dict(DEFAULT_CONFIG)
        try:
            save_config(config, path)
        except OSError as exc:
            _warn('无法写入默认配置 %s: %s' % (path, exc))
        return config

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError('配置根节点不是对象')
    except Exception as exc:
        _warn('配置文件 %s 无法解析（%s），本次使用默认配置；原文件未被覆盖。'
              % (path, exc))
        return dict(DEFAULT_CONFIG)

    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
    # 旧版本迁移：notification_method -> notification_methods
    legacy = data.get('notification_method')
    if legacy and legacy not in ('none', '') and not data.get('notification_methods'):
        data['notification_methods'] = [legacy]
        changed = True
    if changed:
        try:
            save_config(data, path)
        except OSError as exc:
            _warn('补全默认项后写回失败: %s' % exc)
    return data


def save_config(config, path=None):
    """原子写入配置文件。"""
    path = path or CONFIG_FILE
    ensure_dirs()
    if 'notification_methods' not in config or not isinstance(config['notification_methods'], list):
        config['notification_methods'] = []

    directory = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.config_', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def mask_secret(value):
    """把机密值打码，用于日志。"""
    if not value:
        return ''
    value = str(value)
    if len(value) <= 4:
        return '*' * len(value)
    return value[:2] + '*' * (len(value) - 4) + value[-2:]
