import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import os

from ids import IdsAuth
from grade_fetcher import GradeFetcher, semester_str_to_id
from config_manager import load_config, save_config
from notifier import Notifier
import envconfig

class SettingsWindow:
    def __init__(self, parent, config, callback):
        self.parent = parent
        self.config = config.copy()
        self.callback = callback
        self.window = tk.Toplevel(parent)
        self.window.title("设置")
        self.window.geometry("520x780")
        self.window.resizable(False, False)

        self.entries = {}
        row = 0

        # 基本配置
        basic_labels = [
            ("学号", "username"),
            ("密码", "password"),
            ("学年学期 (如 2025-2026.2)", "semester_str"),
            ("查询间隔(秒)", "query_interval"),
            ("重试间隔(秒)", "retry_interval"),
            ("最大重试次数", "max_retries"),
        ]
        for label_text, key in basic_labels:
            tk.Label(self.window, text=label_text).grid(row=row, column=0, sticky='e', padx=5, pady=3)
            entry = tk.Entry(self.window, width=30)
            entry.insert(0, str(self.config.get(key, '')))
            entry.grid(row=row, column=1, padx=5, pady=3)
            self.entries[key] = entry
            row += 1

        # 通知方式多选（使用 Listbox 多选）
        tk.Label(self.window, text="通知方式 (按住 Ctrl 多选)").grid(row=row, column=0, sticky='ne', padx=5, pady=3)
        self.methods_listbox = tk.Listbox(self.window, selectmode=tk.MULTIPLE, height=5, width=25)
        methods = ['email', 'serverchan', 'pushplus', 'wework', 'desktop', 'telegram', 'bark']
        for m in methods:
            self.methods_listbox.insert(tk.END, m)
        self.methods_listbox.grid(row=row, column=1, padx=5, pady=3)
        # 预选已配置的方法
        current_methods = self.config.get('notification_methods', [])
        for i, m in enumerate(methods):
            if m in current_methods:
                self.methods_listbox.selection_set(i)
        row += 1

        # 测试通知按钮
        test_btn = tk.Button(self.window, text="📨 发送测试通知", command=self.test_notification)
        test_btn.grid(row=row, column=0, columnspan=2, pady=5)
        row += 1

        # 邮件配置
        mail_labels = [
            ("SMTP服务器", "smtp_server"),
            ("SMTP端口", "smtp_port"),
            ("发件邮箱", "sender_email"),
            ("发件密码(授权码)", "sender_password"),
            ("收件邮箱", "receiver_email"),
        ]
        for label_text, key in mail_labels:
            tk.Label(self.window, text=label_text).grid(row=row, column=0, sticky='e', padx=5, pady=3)
            entry = tk.Entry(self.window, width=30)
            entry.insert(0, str(self.config.get(key, '')))
            entry.grid(row=row, column=1, padx=5, pady=3)
            self.entries[key] = entry
            row += 1

        # 各通知方式专用配置
        extra_labels = [
            ("Server酱 SendKey", "serverchan_token"),
            ("Pushplus Token", "pushplus_token"),
            ("企业微信 Webhook", "wework_webhook"),
            ("Telegram Bot Token", "telegram_bot_token"),
            ("Telegram Chat ID", "telegram_chat_id"),
            ("Bark Key", "bark_key"),
        ]
        for label_text, key in extra_labels:
            tk.Label(self.window, text=label_text).grid(row=row, column=0, sticky='e', padx=5, pady=3)
            entry = tk.Entry(self.window, width=30)
            entry.insert(0, str(self.config.get(key, '')))
            entry.grid(row=row, column=1, padx=5, pady=3)
            self.entries[key] = entry
            row += 1

        # 保存按钮
        btn_frame = tk.Frame(self.window)
        btn_frame.grid(row=row+1, column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="保存", command=self.save).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="取消", command=self.window.destroy).pack(side=tk.LEFT, padx=10)

    def test_notification(self):
        """测试通知功能"""
        # 临时读取当前配置
        temp_config = self.config.copy()
        for key, entry in self.entries.items():
            val = entry.get().strip()
            if key in ['query_interval', 'retry_interval', 'max_retries', 'smtp_port']:
                try:
                    temp_config[key] = int(val)
                except:
                    messagebox.showerror("错误", f"{key} 必须为数字")
                    return
            else:
                temp_config[key] = val
        # 获取选中的通知方式
        selected = self.methods_listbox.curselection()
        methods = [self.methods_listbox.get(i) for i in selected]
        temp_config['notification_methods'] = methods

        notifier = Notifier(temp_config)
        success = notifier.send_test()
        if success:
            messagebox.showinfo("测试通知", "测试通知已发送，请检查是否收到。")
        else:
            messagebox.showerror("测试通知", "测试通知发送失败，请检查配置和网络。")

    def save(self):
        for key, entry in self.entries.items():
            val = entry.get().strip()
            if key in ['query_interval', 'retry_interval', 'max_retries', 'smtp_port']:
                try:
                    self.config[key] = int(val)
                except:
                    messagebox.showerror("错误", f"{key} 必须为数字")
                    return
            else:
                self.config[key] = val
        # 获取选中的通知方式
        selected = self.methods_listbox.curselection()
        methods = [self.methods_listbox.get(i) for i in selected]
        self.config['notification_methods'] = methods
        # 向后兼容
        self.config['email_enabled'] = 'email' in methods
        save_config(self.config)
        messagebox.showinfo("提示", "设置已保存，请重启程序使全部设置生效")
        self.callback(self.config)
        self.window.destroy()


class GradeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('SUEP 成绩自动查询')
        self.root.geometry('950x650')

        self.config = load_config()
        for key, value in self.config.items():
            if hasattr(envconfig, key):
                setattr(envconfig, key, value)

        self.running = False
        self.ids = None
        self.fetcher = None
        self.timer = None
        self.current_grades = []
        self.last_grades = []

        self.create_widgets()
        self.login()

    def create_widgets(self):
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=5, fill=tk.X)

        tk.Label(control_frame, text='学年学期:').pack(side=tk.LEFT, padx=5)
        self.sem_entry = tk.Entry(control_frame, width=15)
        self.sem_entry.pack(side=tk.LEFT, padx=5)
        self.sem_entry.insert(0, self.config.get('semester_str', '2025-2026.2'))

        self.start_btn = tk.Button(control_frame, text='开始监控', command=self.start_monitor)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk.Button(control_frame, text='停止', command=self.stop_monitor, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.query_now_btn = tk.Button(control_frame, text='立即查询', command=self.manual_query)
        self.query_now_btn.pack(side=tk.LEFT, padx=5)

        self.settings_btn = tk.Button(control_frame, text='设置', command=self.open_settings)
        self.settings_btn.pack(side=tk.LEFT, padx=10)

        self.status_label = tk.Label(control_frame, text='状态: 未登录', fg='gray')
        self.status_label.pack(side=tk.LEFT, padx=20)

        # 成绩表格
        self.tree = ttk.Treeview(self.root, columns=('semester','code','seq','name','category','credit','score','final','gpa'), show='headings')
        col_config = [
            ('semester', '学年学期', 100),
            ('code', '课程代码', 80),
            ('seq', '课程序号', 80),
            ('name', '课程名称', 150),
            ('category', '课程类别', 120),
            ('credit', '学分', 50),
            ('score', '正考总评', 70),
            ('final', '最终', 70),
            ('gpa', '绩点', 60)
        ]
        for col_id, text, width in col_config:
            self.tree.heading(col_id, text=text)
            self.tree.column(col_id, width=width, anchor='center')
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(self.root, height=8, state='disabled')
        self.log_text.pack(fill=tk.X, padx=10, pady=5)

    def open_settings(self):
        def update_config(new_config):
            self.config = new_config
            self.sem_entry.delete(0, tk.END)
            self.sem_entry.insert(0, new_config.get('semester_str', '2025-2026.2'))
            for key, value in new_config.items():
                if hasattr(envconfig, key):
                    setattr(envconfig, key, value)
            messagebox.showinfo("提示", "部分设置（如学号密码）需重新登录生效，请重启程序。")
        SettingsWindow(self.root, self.config, update_config)

    def login(self):
        self.log('正在初始化认证...')
        self.ids = IdsAuth()
        if os.path.exists('cookies.txt'):
            try:
                with open('cookies.txt', 'r') as f:
                    cookies = {i.split('=')[0]: i.split('=')[1] for i in f.read().split(';')}
                self.ids = IdsAuth(cookies)
            except:
                pass
        if not self.ids.ok:
            username = self.config.get('username', '')
            password = self.config.get('password', '')
            if not username or not password:
                self.log('请先在设置中填写学号和密码')
                self.status_label.config(text='状态: 未设置账号', fg='orange')
                return
            self.log('需要账号密码登录...')
            self.ids.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
        if self.ids.ok:
            with open('cookies.txt', 'w') as f:
                f.write(';'.join([f'{k}={v}' for k, v in self.ids.cookies.items()]))
            self.log('登录成功！')
            self.status_label.config(text='状态: 已登录', fg='green')
            self.fetcher = GradeFetcher(self.ids)
            self.manual_query()
        else:
            self.log('登录失败，请检查用户名密码')
            self.status_label.config(text='状态: 登录失败', fg='red')

    def manual_query(self):
        if not self.fetcher:
            self.log('未登录，无法查询')
            return
        try:
            sem_str = self.sem_entry.get().strip()
            if not sem_str:
                sem_str = self.config.get('semester_str', '2025-2026.2')
            sem_id = semester_str_to_id(sem_str)
            grades = self.fetcher.fetch_grades(sem_id)
            self.current_grades = grades
            self.update_tree(grades)
            self.log(f'查询成功，共 {len(grades)} 条成绩')
        except Exception as e:
            self.log(f'查询失败: {e}')

    def start_monitor(self):
        if self.running:
            return
        if not self.fetcher:
            messagebox.showerror('错误', '请先登录')
            return
        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.query_now_btn.config(state=tk.DISABLED)
        self.settings_btn.config(state=tk.DISABLED)
        self.status_label.config(text='状态: 监控中...', fg='blue')
        self.last_grades = GradeFetcher.load_from_file()
        self.log(f'已加载历史成绩 {len(self.last_grades)} 条')
        self._schedule_query()

    def stop_monitor(self):
        self.running = False
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.query_now_btn.config(state=tk.NORMAL)
        self.settings_btn.config(state=tk.NORMAL)
        self.status_label.config(text='状态: 已停止', fg='orange')
        self.log('监控已停止')

    def _schedule_query(self):
        if not self.running:
            return
        self._perform_query_with_retry()
        interval = self.config.get('query_interval', 300)
        self.timer = self.root.after(interval * 1000, self._schedule_query)

    def _perform_query_with_retry(self):
        max_retries = self.config.get('max_retries', 2)
        retry_interval = self.config.get('retry_interval', 60)
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # 每次尝试前检查是否需要重新登录（删除cookie）
                if attempt > 0:
                    self.log(f'第 {attempt} 次重试，清理cookie并重新登录...')
                    self._renew_session()  # 重新登录

                sem_str = self.sem_entry.get().strip()
                if not sem_str:
                    sem_str = self.config.get('semester_str', '2025-2026.2')
                sem_id = semester_str_to_id(sem_str)
                new_grades = self.fetcher.fetch_grades(sem_id)
                self.current_grades = new_grades
                self.update_tree(new_grades)
                GradeFetcher.save_to_file(new_grades)
                changes = self.fetcher.detect_changes(self.last_grades, new_grades)
                if changes['added'] or changes['modified']:
                    self.log(f'检测到变动: 新增 {len(changes["added"])}, 修改 {len(changes["modified"])}')
                    # 发送通知
                    if self.config.get('notification_methods'):
                        notifier = Notifier(self.config)
                        title = "SUEP成绩变动提醒"
                        content = self._build_notification_content(changes)
                        if notifier.send(title, content):
                            self.log('通知发送成功')
                        else:
                            self.log('通知发送失败，请检查配置')
                self.last_grades = new_grades
                return  # 成功，退出重试循环

            except Exception as e:
                last_error = str(e)
                self.log(f'查询失败 (尝试 {attempt+1}/{max_retries+1}): {e}')
                if attempt < max_retries:
                    time.sleep(retry_interval)
                else:
                    # 所有重试失败，终止监控并发送错误通知
                    self.log('查询失败，已达到最大重试次数，程序将终止监控并发送错误通知。')
                    self._handle_fatal_error(last_error)
                    return

    def _renew_session(self):
        """删除cookie并重新登录"""
        if os.path.exists('cookies.txt'):
            os.remove('cookies.txt')
            self.log('已删除 cookies.txt')
        # 重新初始化认证
        self.ids = IdsAuth()
        username = self.config.get('username', '')
        password = self.config.get('password', '')
        if username and password:
            self.ids.login(username, password, 'http://jw.shiep.edu.cn/eams/login.action')
            if self.ids.ok:
                with open('cookies.txt', 'w') as f:
                    f.write(';'.join([f'{k}={v}' for k, v in self.ids.cookies.items()]))
                self.fetcher = GradeFetcher(self.ids)
                self.log('重新登录成功')
            else:
                raise Exception('重新登录失败')
        else:
            raise Exception('未配置用户名密码，无法重新登录')

    def _handle_fatal_error(self, error_msg):
        """处理致命错误：停止监控，发送错误通知，并弹出警告"""
        self.running = False
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.query_now_btn.config(state=tk.NORMAL)
        self.settings_btn.config(state=tk.NORMAL)
        self.status_label.config(text='状态: 错误终止', fg='red')
        self.log('程序因连续错误已终止监控。')

        # 发送错误通知（使用所有已配置的通知方式）
        if self.config.get('notification_methods'):
            notifier = Notifier(self.config)
            full_error = f"监控因连续错误已终止。\n最后一次错误：{error_msg}"
            if notifier.send_error(full_error):
                self.log('错误通知发送成功')
            else:
                self.log('错误通知发送失败，请检查通知配置')

        # 弹出错误提示
        messagebox.showerror("严重错误", f"连续查询失败，监控已终止。\n\n错误信息：\n{error_msg}")

    def update_tree(self, grades):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for g in grades:
            self.tree.insert('', tk.END, values=(
                g['semester'], g['code'], g['seq'], g['name'],
                g['category'], g['credit'], g['score'], g['final'], g['gpa']
            ))

    def _build_notification_content(self, changes):
        lines = []
        if changes['added']:
            lines.append("📌 新增课程：")
            for g in changes['added']:
                lines.append(self._format_course_line(g))
            lines.append("")
        if changes['modified']:
            lines.append("📝 成绩更新：")
            for m in changes['modified']:
                old = m['old']
                new = m['new']
                line = f"{new['name']} ({new['code']})"
                line += f"  成绩: {old['score']} → {new['score']}"
                if new.get('gpa'):
                    line += f"  绩点: {old.get('gpa', '')} → {new['gpa']}"
                if self._is_failing(new['score']) or self._is_failing(new['final']):
                    line += "  ⚠️ 补考要加油啊！"
                lines.append(line)
            lines.append("")
        return "\n".join(lines).strip()

    def _format_course_line(self, course):
        line = f"{course['name']} ({course['code']})"
        line += f"  成绩: {course['score']}"
        if course.get('gpa'):
            line += f"  绩点: {course['gpa']}"
        if self._is_failing(course['score']) or self._is_failing(course['final']):
            line += "  ⚠️ 补考要加油啊！"
        return line

    def _is_failing(self, score_str):
        if not score_str or score_str.strip() == '':
            return False
        try:
            score = float(score_str.strip())
            return score < 60
        except (ValueError, TypeError):
            return True

    def log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f'[{time.strftime("%H:%M:%S")}] {msg}\n')
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')


if __name__ == '__main__':
    root = tk.Tk()
    app = GradeGUI(root)
    root.mainloop()