# -*- coding: utf-8 -*-
"""多渠道通知发送。

三个版本（桌面版 / 网页版 / docker版）共用同一份，改动请三处同步。

修掉的旧问题：
1. 旧版全部用 print 输出失败原因，Web 界面 / docker 日志里看不到
   "通知发送失败"，只会看到"查询成功"却收不到消息。现在统一走传入的 logger。
2. Telegram 旧版写死 ``parse_mode="Markdown"`` 却不转义内容，
   课程名里只要出现 ``_`` / ``*`` / ``[`` 就 400，通知静默丢失。现在不用 Markdown 解析。
3. Bark 旧版把标题和正文塞进 URL 查询串，长内容会被截断/超长报错；改为 POST JSON。
4. smtplib 没有超时，SMTP 服务器不响应时会永久挂起；现在带 timeout。
"""
import json
import smtplib
import urllib.parse
from email.header import Header
from email.mime.text import MIMEText

import requests

SUPPORTED_METHODS = ('email', 'serverchan', 'pushplus', 'wework',
                     'desktop', 'telegram', 'bark')


class Notifier:
    def __init__(self, config, logger=None):
        self.config = config
        self.log = logger if callable(logger) else (lambda msg: None)
        self.results = {}

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def send(self, title, content, methods=None):
        """依次发送到所有启用的渠道；任一失败不影响其它渠道。

        :return: 全部成功才返回 True
        """
        if methods is None:
            methods = self.config.get('notification_methods') or []
        if not methods:
            self.log('未配置任何通知方式，跳过发送')
            return False

        self.results = {}
        senders = {
            'email': self._send_email,
            'serverchan': self._send_serverchan,
            'pushplus': self._send_pushplus,
            'wework': lambda t, c: self._send_wework(c),
            'desktop': self._send_desktop,
            'telegram': self._send_telegram,
            'bark': self._send_bark,
        }

        all_ok = True
        for method in methods:
            sender = senders.get(method)
            if sender is None:
                self.log('未知通知方式，已跳过: %s（可用: %s）'
                         % (method, ', '.join(SUPPORTED_METHODS)))
                self.results[method] = False
                all_ok = False
                continue
            try:
                ok = bool(sender(title, content))
            except Exception as exc:                      # 兜底，绝不让通知把主流程带崩
                self.log('%s 通知异常: %s' % (method, exc))
                ok = False
            self.results[method] = ok
            if not ok:
                all_ok = False
            self.log('%s 通知%s' % (method, '发送成功' if ok else '发送失败'))
        return all_ok

    def send_test(self):
        return self.send('🎯 通知测试',
                         '这是一条来自 SUEP 成绩查询工具的测试消息。\n配置正确，可以正常接收通知！')

    def send_error(self, error_msg):
        return self.send('❌ 程序错误提醒',
                         '程序运行出现错误，已自动停止监控。\n错误信息：\n%s' % error_msg)

    # ------------------------------------------------------------------
    # 各渠道实现
    # ------------------------------------------------------------------
    def _send_email(self, title, content):
        sender = self.config.get('sender_email')
        password = self.config.get('sender_password')
        receiver = self.config.get('receiver_email')
        server = self.config.get('smtp_server', 'smtp.qq.com')
        try:
            port = int(self.config.get('smtp_port', 465) or 465)
        except (TypeError, ValueError):
            port = 465

        if not sender or not receiver:
            self.log('邮件配置不完整（缺少发件邮箱或收件邮箱）')
            return False

        msg = MIMEText(content, 'plain', 'utf-8')
        msg['Subject'] = Header(title, 'utf-8')
        msg['From'] = sender
        msg['To'] = receiver
        try:
            if port == 465:
                with smtplib.SMTP_SSL(server, port, timeout=20) as smtp:
                    if password:
                        smtp.login(sender, password)
                    smtp.sendmail(sender, [receiver], msg.as_string())
            elif port == 587:
                with smtplib.SMTP(server, port, timeout=20) as smtp:
                    smtp.starttls()
                    if password:
                        smtp.login(sender, password)
                    smtp.sendmail(sender, [receiver], msg.as_string())
            else:
                # 25 端口：只在配置了密码时才尝试认证（很多内网中继不需要）
                with smtplib.SMTP(server, port, timeout=20) as smtp:
                    if password:
                        smtp.login(sender, password)
                    smtp.sendmail(sender, [receiver], msg.as_string())
            return True
        except Exception as exc:
            self.log('邮件发送失败: %s' % exc)
            return False

    def _post_json(self, url, payload, ok_field=None, ok_value=None):
        resp = requests.post(url, json=payload, timeout=15)
        try:
            result = resp.json()
        except ValueError:
            self.log('接口返回的不是 JSON（HTTP %s）：%s'
                     % (resp.status_code, (resp.text or '')[:120]))
            return False
        if ok_field:
            return result.get(ok_field) == ok_value
        return result

    def _send_serverchan(self, title, content):
        token = self.config.get('serverchan_token', '')
        if not token:
            self.log('Server酱 Token 未配置')
            return False
        try:
            url = 'https://sctapi.ftqq.com/%s.send' % token
            result = self._post_json(url, {'title': title, 'desp': content})
            if isinstance(result, dict) and result.get('code') == 0:
                return True
            self.log('Server酱推送失败: %s' % (result,))
            return False
        except Exception as exc:
            self.log('Server酱异常: %s' % exc)
            return False

    def _send_pushplus(self, title, content):
        token = self.config.get('pushplus_token', '')
        if not token:
            self.log('Pushplus Token 未配置')
            return False
        try:
            result = self._post_json('https://www.pushplus.plus/send/',
                                     {'token': token, 'title': title,
                                      'content': content, 'template': 'markdown'})
            if isinstance(result, dict) and result.get('code') == 200:
                return True
            self.log('Pushplus推送失败: %s' % (result,))
            return False
        except Exception as exc:
            self.log('Pushplus异常: %s' % exc)
            return False

    def _send_wework(self, content):
        webhook = self.config.get('wework_webhook', '')
        if not webhook:
            self.log('企业微信 Webhook 未配置')
            return False
        try:
            result = self._post_json(webhook, {'msgtype': 'markdown',
                                               'markdown': {'content': content}})
            if isinstance(result, dict) and result.get('errcode') == 0:
                return True
            self.log('企业微信发送失败: %s' % (result,))
            return False
        except Exception as exc:
            self.log('企业微信异常: %s' % exc)
            return False

    def _send_telegram(self, title, content):
        bot_token = self.config.get('telegram_bot_token', '')
        chat_id = self.config.get('telegram_chat_id', '')
        if not bot_token or not chat_id:
            self.log('Telegram 配置不完整')
            return False
        try:
            # 不使用 parse_mode：课程名里的 _ * [ 会破坏 Markdown 解析并导致 400
            result = self._post_json(
                'https://api.telegram.org/bot%s/sendMessage' % bot_token,
                {'chat_id': chat_id, 'text': '%s\n%s' % (title, content),
                 'disable_web_page_preview': True})
            if isinstance(result, dict) and result.get('ok'):
                return True
            self.log('Telegram 返回: %s' % (result,))
            return False
        except Exception as exc:
            self.log('Telegram发送失败: %s' % exc)
            return False

    def _send_bark(self, title, content):
        key = self.config.get('bark_key', '')
        if not key:
            self.log('Bark Key 未配置')
            return False
        server = (self.config.get('bark_server') or 'https://api.day.app').rstrip('/')
        try:
            # 用 POST，避免长正文被塞进 URL
            result = self._post_json('%s/%s' % (server, urllib.parse.quote(key)),
                                     {'title': title, 'body': content})
            if isinstance(result, dict) and result.get('code') == 200:
                return True
            self.log('Bark 返回: %s' % (result,))
            return False
        except Exception as exc:
            self.log('Bark发送失败: %s' % exc)
            return False

    def _send_desktop(self, title, content):
        try:
            from win10toast import ToastNotifier
        except ImportError:
            try:
                import ctypes
                # 注意：MessageBoxW 是模态的，在无人的服务器上会永久阻塞当前线程
                ctypes.windll.user32.MessageBoxW(0, content, title, 0)
                return True
            except Exception as exc:
                self.log('桌面通知失败: %s' % exc)
                return False
        try:
            ToastNotifier().show_toast(title, content, duration=10, threaded=True)
            return True
        except Exception as exc:
            self.log('桌面通知失败: %s' % exc)
            return False


def summarize(results):
    """把 results 字典整理成一行日志。"""
    if not results:
        return '未发送'
    return ', '.join('%s=%s' % (k, '成功' if v else '失败') for k, v in results.items())


__all__ = ['Notifier', 'SUPPORTED_METHODS', 'summarize', 'json']
