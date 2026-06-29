import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import json
import time

class Notifier:
    def __init__(self, config):
        self.config = config

    def send(self, title, content, methods=None):
        """
        发送通知，若 methods 为 None 则使用配置中的所有方式
        """
        if methods is None:
            methods = self.config.get('notification_methods', [])
        if not methods:
            return False

        success = True
        for method in methods:
            if method == 'email':
                if not self._send_email(title, content):
                    success = False
            elif method == 'serverchan':
                if not self._send_serverchan(title, content):
                    success = False
            elif method == 'pushplus':
                if not self._send_pushplus(title, content):
                    success = False
            elif method == 'wework':
                if not self._send_wework(content):
                    success = False
            elif method == 'desktop':
                if not self._send_desktop(title, content):
                    success = False
            elif method == 'telegram':
                if not self._send_telegram(title, content):
                    success = False
            elif method == 'bark':
                if not self._send_bark(title, content):
                    success = False
            else:
                print(f"未知通知方式: {method}")
        return success

    def send_test(self):
        """发送测试消息"""
        title = "🎯 通知测试"
        content = "这是一条来自 SUEP 成绩查询工具的测试消息。\n配置正确，可以正常接收通知！"
        return self.send(title, content)

    def send_error(self, error_msg):
        """发送错误通知"""
        title = "❌ 程序错误提醒"
        content = f"程序运行出现错误，已自动终止监控。\n错误信息：\n{error_msg}"
        return self.send(title, content)

    # 以下各 _send_xxx 方法不变，但已在上面的 send 中调用
    def _send_email(self, title, content):
        try:
            sender = self.config.get('sender_email')
            password = self.config.get('sender_password')
            receiver = self.config.get('receiver_email')
            server = self.config.get('smtp_server', 'smtp.qq.com')
            port = self.config.get('smtp_port', 465)

            if not sender or not password or not receiver:
                print("邮件配置不完整")
                return False

            msg = MIMEText(content, 'plain', 'utf-8')
            msg['Subject'] = Header(title, 'utf-8')
            msg['From'] = sender
            msg['To'] = receiver

            if port == 465:
                with smtplib.SMTP_SSL(server, port) as smtp:
                    smtp.login(sender, password)
                    smtp.sendmail(sender, [receiver], msg.as_string())
            elif port == 587:
                with smtplib.SMTP(server, port) as smtp:
                    smtp.starttls()
                    smtp.login(sender, password)
                    smtp.sendmail(sender, [receiver], msg.as_string())
            else:
                with smtplib.SMTP(server, port) as smtp:
                    smtp.sendmail(sender, [receiver], msg.as_string())
            return True
        except Exception as e:
            print(f"邮件发送失败: {e}")
            return False

    def _send_serverchan(self, title, content):
        token = self.config.get('serverchan_token', '')
        if not token:
            print("Server酱 Token 未配置")
            return False
        try:
            url = f"https://sctapi.ftqq.com/{token}.send"
            data = {"title": title, "desp": content}
            resp = requests.post(url, data=data, timeout=10)
            result = resp.json()
            if result.get('code') == 0:
                return True
            else:
                print(f"Server酱推送失败: {result.get('message')}")
                return False
        except Exception as e:
            print(f"Server酱异常: {e}")
            return False

    def _send_pushplus(self, title, content):
        token = self.config.get('pushplus_token', '')
        if not token:
            print("Pushplus Token 未配置")
            return False
        try:
            url = "https://www.pushplus.plus/send/"
            payload = {
                "token": token,
                "title": title,
                "content": content,
                "template": "markdown"
            }
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get('code') == 200:
                return True
            else:
                print(f"Pushplus推送失败: {result.get('msg')}")
                return False
        except Exception as e:
            print(f"Pushplus异常: {e}")
            return False

    def _send_wework(self, content):
        webhook = self.config.get('wework_webhook', '')
        if not webhook:
            print("企业微信 Webhook 未配置")
            return False
        try:
            data = {
                "msgtype": "markdown",
                "markdown": {"content": content}
            }
            resp = requests.post(webhook, json=data, timeout=10)
            result = resp.json()
            if result.get('errcode') == 0:
                return True
            else:
                print(f"企业微信发送失败: {result}")
                return False
        except Exception as e:
            print(f"企业微信异常: {e}")
            return False

    def _send_telegram(self, title, content):
        bot_token = self.config.get('telegram_bot_token', '')
        chat_id = self.config.get('telegram_chat_id', '')
        if not bot_token or not chat_id:
            print("Telegram 配置不完整")
            return False
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": f"*{title}*\n{content}",
                "parse_mode": "Markdown"
            }
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            return result.get('ok', False)
        except Exception as e:
            print(f"Telegram发送失败: {e}")
            return False

    def _send_bark(self, title, content):
        key = self.config.get('bark_key', '')
        if not key:
            print("Bark Key 未配置")
            return False
        try:
            # 对内容进行 URL 编码
            import urllib.parse
            url = f"https://api.day.app/{key}/{urllib.parse.quote(title)}/{urllib.parse.quote(content)}"
            resp = requests.get(url, timeout=10)
            result = resp.json()
            return result.get('code') == 200
        except Exception as e:
            print(f"Bark发送失败: {e}")
            return False

    def _send_desktop(self, title, content):
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(title, content, duration=10, threaded=True)
            return True
        except ImportError:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, content, title, 0)
                return True
            except Exception as e:
                print(f"桌面通知失败: {e}")
                return False
        except Exception as e:
            print(f"桌面通知失败: {e}")
            return False