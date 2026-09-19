# -*- coding: utf-8 -*-
"""SUEP 成绩自动查询 —— 桌面 Tkinter 版。

与旧版相比修掉的问题：

1. **界面卡死**：旧版用 ``root.after`` 直接在 Tk 主线程里跑
   ``fetch_grades()``，失败时还 ``time.sleep(retry_interval)``；
   默认重试 2 次、间隔 60 秒，最坏情况窗口"无响应"两分钟
   （旧文件 import 了 threading 却从未使用）。现在所有网络/等待都在工作线程，
   UI 只通过队列在主线程更新。
2. ``_is_failing`` 对非数字成绩返回 True，于是"合格/通过/良/优秀"这类
   体育、通识、实践课会被通知成"⚠️ 补考要加油啊！"
   （线上 2025-2026.2 就有"认识实习 良"这种真实数据）。现在按其它两版一致返回 False。
3. 成绩文件名与网页版/docker 版不一致（桌面版把 '.' 换成 '_'），
   同一学期两个版本互不共享历史。现已统一（读取时兼容旧命名）。
4. 设置窗口里的"发送测试通知"同样会冻结界面，现在也走线程。
5. 密码输入框改为掩码显示。
6. 删除了从未被使用的 envconfig.py 依赖。

界面上活：
* 设置窗口内容放进 Canvas + 滚动条，窗口可自由缩放（原来固定 520x780，
  小屏/高 DPI 下底部按钮会跑到屏幕外）。
* 学年学期改为**可编辑下拉框**，选项从教务系统爬取（成绩页内嵌"学年学期"控件的数据源），
  带"刷新学期"按钮；拿不到时仍可手工输入。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk

import config_manager
from config_manager import load_config, save_config
from grade_fetcher import GradeFetcher
from ids import IdsAuth, SessionExpired, dump_cookie_string, parse_cookie_string
from notifier import Notifier, summarize

METHODS = ['email', 'serverchan', 'pushplus', 'wework', 'desktop', 'telegram', 'bark']
INT_KEYS = ('query_interval', 'retry_interval', 'max_retries', 'smtp_port',
            'connect_timeout', 'read_timeout', 'log_retention_days')
BOOL_KEYS = ('ssl_verify', 'proxy_enabled')


def setup_dpi():
    """开启高 DPI 感知，返回缩放比（1.0 = 96 DPI）。必须在创建 Tk 根窗口**之前**调用。

    注意：只调用它是**不够**的。开启感知后 Tk 的字体按真实 DPI 放大了，
    但 ttk.Treeview 的 ``rowheight`` 默认仍是未缩放的固定像素 —— 结果行高比字矮，
    文字被裁掉（表现为"成绩显示区域行高太低、文字显示不正常"）。
    所以还要按字体实际行高显式设置 rowheight（见 create_widgets）。
    """
    scale = 1.0
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)       # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            windll.user32.SetProcessDPIAware()
        try:
            dpi = windll.user32.GetDpiForSystem()
        except Exception:
            hdc = windll.user32.GetDC(0)
            dpi = windll.gdi32.GetDeviceCaps(hdc, 88)     # LOGPIXELSX
            windll.user32.ReleaseDC(0, hdc)
        if dpi:
            scale = max(1.0, round(dpi / 96.0, 2))
    except Exception:
        scale = 1.0
    return scale


def detect_ui_scale(root, fallback=1.0):
    """按当前默认字体的实际行高推算界面缩放比。

    比直接信 DPI 数值更可靠：布局要跟的是"字有多大"，而字已经按 DPI 缩放过了
    （96 DPI 下 TkDefaultFont 行高约 16px）。
    """
    try:
        line = tkfont.nametofont('TkDefaultFont').metrics('linespace')
        if line:
            return max(1.0, round(line / 16.0, 2))
    except Exception:
        pass
    return fallback


def build_tls_kwargs(cfg):
    """把配置里的超时/证书/代理整理成 IdsAuth 的参数。"""
    try:
        timeout = (int(cfg.get('connect_timeout', 8) or 8),
                   int(cfg.get('read_timeout', 20) or 20))
    except (TypeError, ValueError):
        timeout = (8, 20)
    return {
        'timeout': timeout,
        'ssl_verify': bool(cfg.get('ssl_verify', False)),
        'ca_bundle': (cfg.get('ca_bundle') or '').strip() or None,
        'proxy': (cfg.get('proxy_url') or '').strip() if cfg.get('proxy_enabled') else None,
    }


def _safe_print(text):
    """打包成 --windowed 的 exe 后没有 stdout，输出要容错。"""
    try:
        if sys.stdout is not None:
            print(text)
    except Exception:
        pass


class ScrollableFrame:
    """带竖直滚动条的可滚动容器（Tkinter 标准做法）。

    把子控件放进 ``.body``；内容变高或窗口缩放时自动更新滚动区域。
    """

    def __init__(self, master):
        self.outer = tk.Frame(master)
        self.canvas = tk.Canvas(self.outer, highlightthickness=0)
        self.vsb = tk.Scrollbar(self.outer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.body = tk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', self._on_body)
        self.canvas.bind('<Configure>', self._on_canvas)
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.body)

    def _on_body(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas(self, event):
        # 让内容宽度跟随窗口，避免横向留白
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, widget):
        widget.bind('<MouseWheel>', self._on_wheel, add='+')
        widget.bind('<Button-4>', lambda e: self.canvas.yview_scroll(-1, 'units'), add='+')
        widget.bind('<Button-5>', lambda e: self.canvas.yview_scroll(1, 'units'), add='+')

    def _on_wheel(self, event):
        # Windows: delta 是 120 的倍数
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def pack(self, **kwargs):
        self.outer.pack(**kwargs)
        return self


class SettingsWindow:
    def __init__(self, parent, config, callback, post=None, semesters=None, ui_scale=1.0):
        self.parent = parent
        self.config = dict(config)
        self.callback = callback
        self.post = post if callable(post) else (lambda *a: None)
        self.ui_scale = max(1.0, float(ui_scale or 1.0))

        self.window = tk.Toplevel(parent)
        self.window.title('设置')
        size = (int(620 * self.ui_scale), int(660 * self.ui_scale))
        try:                       # 内容本身可滚动，窗口别超出屏幕
            size = (min(size[0], max(520, self.window.winfo_screenwidth() - 60)),
                    min(size[1], max(400, self.window.winfo_screenheight() - 120)))
        except Exception:
            pass
        self.window.geometry('%dx%d' % size)
        self.window.minsize(int(520 * self.ui_scale), int(400 * self.ui_scale))
        # 可自由缩放：高 DPI / 小屏下也不会把"保存"按钮挤出屏幕
        self.window.resizable(True, True)

        # 底部按钮条固定，先 pack 以便占据底部空间
        btns = tk.Frame(self.window)
        btns.pack(side=tk.BOTTOM, fill=tk.X, pady=8)
        tk.Button(btns, text='保存', width=10, command=self.save).pack(side=tk.RIGHT, padx=12)
        tk.Button(btns, text='取消', width=10,
                  command=self.window.destroy).pack(side=tk.RIGHT, padx=4)
        tk.Label(btns, text='提示：学号/密码/通知方式改动需重启程序生效',
                 fg='gray').pack(side=tk.LEFT, padx=10)

        self.scroll = ScrollableFrame(self.window)
        self.scroll.pack(fill=tk.BOTH, expand=True)
        body = self.scroll.body
        body.columnconfigure(1, weight=1)

        self.entries = {}
        self.bools = {}
        row = 0

        def add_entry(label_text, key, secret=False, values=None, hint=None):
            nonlocal row
            tk.Label(body, text=label_text).grid(row=row, column=0, sticky='e', padx=6, pady=3)
            if values is not None:
                widget = ttk.Combobox(body, width=30, values=values)
                widget.set(str(self.config.get(key, '')))
            else:
                widget = tk.Entry(body, width=33, show='*' if secret else '')
                widget.insert(0, str(self.config.get(key, '')))
            widget.grid(row=row, column=1, sticky='we', padx=6, pady=3)
            if hint:
                tk.Label(body, text=hint, fg='gray', font=('', 8)).grid(
                    row=row, column=2, sticky='w', padx=4)
            self.entries[key] = widget
            row += 1
            return widget

        add_entry('学号', 'username')
        add_entry('密码', 'password', secret=True)
        self.sem_combo = add_entry(
            '学年学期', 'semester_str',
            values=[item['semester'] for item in (semesters or [])],
            hint='可直接输入，或点下方按钮从教务系统获取列表')
        self.sem_hint = tk.Label(body, text='', fg='#0a7', font=('', 8))
        self.sem_hint.grid(row=row, column=0, columnspan=2, sticky='w', padx=6)
        row += 1
        self.sem_refresh_btn = tk.Button(body, text='🔄 从教务系统获取学期列表',
                                         command=self.refresh_semesters)
        self.sem_refresh_btn.grid(row=row, column=1, sticky='w', padx=6, pady=2)
        row += 1

        add_entry('查询间隔(秒)', 'query_interval')
        add_entry('重试间隔(秒)', 'retry_interval')
        add_entry('最大重试次数', 'max_retries')
        add_entry('连接超时(秒)', 'connect_timeout')
        add_entry('读取超时(秒)', 'read_timeout')
        add_entry('日志保留(天)', 'log_retention_days')

        tk.Label(body, text='通知方式 (按住 Ctrl 多选)').grid(
            row=row, column=0, sticky='ne', padx=6, pady=3)
        self.methods_listbox = tk.Listbox(body, selectmode=tk.MULTIPLE, height=7, width=27,
                                          exportselection=False)
        for method in METHODS:
            self.methods_listbox.insert(tk.END, method)
        for i, method in enumerate(METHODS):
            if method in (self.config.get('notification_methods') or []):
                self.methods_listbox.selection_set(i)
        self.methods_listbox.grid(row=row, column=1, sticky='w', padx=6, pady=3)
        row += 1

        tk.Button(body, text='📨 发送测试通知', command=self.test_notification).grid(
            row=row, column=0, columnspan=2, pady=6)
        row += 1

        for label_text, key in (('SMTP服务器', 'smtp_server'), ('SMTP端口', 'smtp_port'),
                                ('发件邮箱', 'sender_email'),
                                ('发件密码(授权码)', 'sender_password'),
                                ('收件邮箱', 'receiver_email')):
            add_entry(label_text, key, secret=(key == 'sender_password'))

        for label_text, key in (('Server酱 SendKey', 'serverchan_token'),
                                ('Pushplus Token', 'pushplus_token'),
                                ('企业微信 Webhook', 'wework_webhook'),
                                ('Telegram Bot Token', 'telegram_bot_token'),
                                ('Telegram Chat ID', 'telegram_chat_id'),
                                ('Bark Key', 'bark_key'),
                                ('代理地址 (校外/docker)', 'proxy_url'),
                                ('CA 证书路径 (可选)', 'ca_bundle')):
            add_entry(label_text, key)

        for label_text, key in (('启用代理', 'proxy_enabled'),
                                ('强制校验 TLS 证书', 'ssl_verify')):
            var = tk.BooleanVar(value=bool(self.config.get(key)))
            tk.Checkbutton(body, text=label_text, variable=var).grid(
                row=row, column=1, sticky='w', padx=6)
            self.bools[key] = var
            row += 1

        self.window.bind('<Escape>', lambda e: self.window.destroy())

    # ------------------------------------------------------------------
    def set_semesters(self, items):
        """学期列表爬回来后刷新下拉框与提示。"""
        values = [item['semester'] for item in (items or [])]
        self.sem_combo['values'] = values
        if values:
            current = self.sem_combo.get().strip()
            if current not in values:
                values = [current] + values
                self.sem_combo['values'] = values
            self.sem_hint.config(text='已从教务系统获取 %d 个学期（最新 %s）'
                                      % (len(items), items[-1]['semester']))
        else:
            self.sem_hint.config(text='未取到学期列表，可手工填写，如 2025-2026.2')

    def refresh_semesters(self):
        self.sem_refresh_btn.config(state=tk.DISABLED, text='⏳ 读取中...')
        # 交给主线程去派发工作线程（post 的消息在主线程处理）
        self.post('refresh_semesters')

    def semesters_loaded(self, ok, message=''):
        self.sem_refresh_btn.config(state=tk.NORMAL, text='🔄 从教务系统获取学期列表')
        if not ok:
            self.sem_hint.config(text=message or '获取学期列表失败')

    # ------------------------------------------------------------------
    def collect(self):
        """把界面上的值收进一份配置副本；数字非法时返回 (None, 错误信息)。"""
        cfg = dict(self.config)
        for key, widget in self.entries.items():
            val = widget.get().strip()
            if key in INT_KEYS:
                try:
                    cfg[key] = int(val) if val else 0
                except ValueError:
                    return None, '%s 必须为数字' % key
            else:
                cfg[key] = val
        for key, var in self.bools.items():
            cfg[key] = bool(var.get())
        cfg['notification_methods'] = [self.methods_listbox.get(i)
                                       for i in self.methods_listbox.curselection()]
        cfg['email_enabled'] = 'email' in cfg['notification_methods']
        return cfg, None

    def test_notification(self):
        cfg, err = self.collect()
        if err:
            messagebox.showerror('错误', err, parent=self.window)
            return

        def work():
            notifier = Notifier(cfg, logger=lambda m: self.post('log', m))
            ok = notifier.send_test()
            detail = summarize(notifier.results)
            if ok:
                self.post('info', '测试通知', '测试通知已发送，请检查是否收到。\n\n%s' % detail)
            else:
                self.post('error', '测试通知', '测试通知发送失败，请检查配置和网络。\n\n%s' % detail)

        # 不要在主线程里发网络请求，否则设置窗口同样会卡住
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        cfg, err = self.collect()
        if err:
            messagebox.showerror('错误', err, parent=self.window)
            return
        try:
            save_config(cfg)
        except OSError as exc:
            messagebox.showerror('错误', '配置写入失败: %s' % exc, parent=self.window)
            return
        self.callback(cfg)
        messagebox.showinfo('提示', '设置已保存。学号 / 密码 / 通知方式的改动需要重启程序生效。',
                            parent=self.window)
        self.window.destroy()


class GradeGUI:
    def __init__(self, root, ui_scale=None):
        self.root = root
        self.root.title('SUEP 成绩自动查询')
        # 界面缩放：高 DPI 下字体被放大，窗口/列宽/行高都要跟着放大，否则会挤在一起
        self.ui_scale = max(1.0, float(ui_scale)) if ui_scale else detect_ui_scale(root)
        width, height = int(1020 * self.ui_scale), int(740 * self.ui_scale)
        try:                       # 别让窗口比屏幕还大
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            width = min(width, max(700, screen_w - 60))
            height = min(height, max(520, screen_h - 120))
            min_w = min(int(760 * self.ui_scale), max(600, screen_w - 80))
            min_h = min(int(560 * self.ui_scale), max(420, screen_h - 160))
        except Exception:
            min_w, min_h = 760, 560
        self.root.geometry('%dx%d' % (width, height))
        self.root.minsize(min_w, min_h)

        self.config = load_config()

        self.running = False
        self.ids = None
        self.fetcher = None
        self.timer = None
        self.current_grades = []
        self.last_grades = []
        self.last_query_time = None
        self.semesters = []
        self._sem_value = self.config.get('semester_str', '2025-2026.2')  # 学期值的纯 Python 镜像
        self._queue = queue.Queue()
        self._worker = None
        self._worker_label = ''
        self._closing = False
        self._settings = None

        self.create_widgets()

        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.root.after(120, self._drain_queue)
        # 登录放到后台线程，启动时界面先出来；
        # 学期值必须在主线程读出后再传进去（工作线程不能访问 Tk 控件）
        boot_sem = self._sem_str()
        self._dispatch(lambda: self._boot_task(boot_sem), label='启动')

    # ------------------------------------------------------------------
    # 线程 -> 主线程 的消息通道
    # ------------------------------------------------------------------
    def post(self, kind, *args):
        """工作线程调用：把消息投递给主线程处理。"""
        self._queue.put((kind, args))

    def _drain_queue(self):
        try:
            while True:
                kind, args = self._queue.get_nowait()
                try:
                    handler = getattr(self, '_ui_' + kind, None)
                    if handler:
                        handler(*args)
                except Exception as exc:      # UI 回调里的异常不该杀死轮询
                    print('UI 处理 %s 失败: %s' % (kind, exc))
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(120, self._drain_queue)

    def _ui_log(self, msg):
        self.log(msg)

    def _ui_status(self, text, color='gray'):
        self.status_label.config(text='状态: %s' % text, fg=color)

    def _ui_time(self, text):
        self.time_label.config(text='上次查询: %s' % text)

    def _ui_grades(self, grades):
        self.current_grades = grades
        self.update_tree(grades)

    def _ui_info(self, title, text):
        messagebox.showinfo(title, text)

    def _ui_error(self, title, text):
        messagebox.showerror(title, text)

    def _ui_fatal(self, text):
        self._apply_stopped_buttons()
        self.status_label.config(text='状态: 错误终止', fg='red')
        messagebox.showerror('严重错误', text)

    def _ui_buttons(self, running):
        state = tk.DISABLED if running else tk.NORMAL
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.query_now_btn.config(state=state)
        self.settings_btn.config(state=state)

    def _ui_config(self, cfg):
        self.config = cfg
        self.sem_entry.set(cfg.get('semester_str', '2025-2026.2'))

    def _ui_semesters(self, items):
        """学期列表爬回来了：刷新主窗口与设置窗口的下拉框。"""
        self.semesters = items or []
        values = [item['semester'] for item in self.semesters]
        if values:
            current = self.sem_entry.get().strip()
            if current and current not in values:
                values = [current] + values
            self.sem_entry['values'] = values
            self.log('已获取 %d 个学期（最新：%s）'
                     % (len(self.semesters), self.semesters[-1]['semester']))
        self._settings_reload_semesters()

    def _ui_refresh_semesters(self):
        self.refresh_semesters()

    def _settings_reload_semesters(self):
        if self._settings is None:
            return
        try:
            if not self._settings.window.winfo_exists():
                self._settings = None
                return
            self._settings.set_semesters(self.semesters)
            self._settings.semesters_loaded(bool(self.semesters))
        except tk.TclError:
            self._settings = None

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def create_widgets(self):
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=5, fill=tk.X)

        tk.Label(control_frame, text='学年学期:').pack(side=tk.LEFT, padx=5)
        # 可编辑下拉框：既能选也能手工输入
        self.sem_entry = ttk.Combobox(control_frame, width=18, values=[])
        self.sem_entry.set(self.config.get('semester_str', '2025-2026.2'))
        self.sem_entry.pack(side=tk.LEFT, padx=5)
        self.sem_refresh = tk.Button(control_frame, text='🔄', width=3,
                                     command=self.refresh_semesters)
        self.sem_refresh.pack(side=tk.LEFT)

        self.start_btn = tk.Button(control_frame, text='开始监控', command=self.start_monitor)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk.Button(control_frame, text='停止', command=self.stop_monitor,
                                  state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.query_now_btn = tk.Button(control_frame, text='立即查询', command=self.manual_query)
        self.query_now_btn.pack(side=tk.LEFT, padx=5)
        self.settings_btn = tk.Button(control_frame, text='设置', command=self.open_settings)
        self.settings_btn.pack(side=tk.LEFT, padx=10)

        self.status_label = tk.Label(control_frame, text='状态: 未登录', fg='gray')
        self.status_label.pack(side=tk.LEFT, padx=10)
        self.time_label = tk.Label(control_frame, text='上次查询: 无', fg='gray')
        self.time_label.pack(side=tk.LEFT, padx=10)

        # 行高必须跟字体走：开启 DPI 感知后字体被放大，而 Treeview 的默认
        # rowheight 仍是未缩放的固定像素，不显式设置就会"行高太低、文字被裁"
        try:
            line = tkfont.nametofont('TkDefaultFont').metrics('linespace')
        except Exception:
            line = 16
        row_h = max(24, int(round((line or 16) * 1.5)))
        style = ttk.Style(self.root)
        try:
            style.configure('Treeview', rowheight=row_h)
            style.configure('Treeview.Heading', padding=(6, max(3, int(row_h * 0.14))))
        except Exception:
            pass

        self.tree = ttk.Treeview(
            self.root,
            columns=('semester', 'code', 'seq', 'name', 'category',
                     'credit', 'score', 'final', 'gpa'),
            show='headings')
        col_config = [
            ('semester', '学年学期', 100), ('code', '课程代码', 80), ('seq', '课程序号', 80),
            ('name', '课程名称', 160), ('category', '课程类别', 130), ('credit', '学分', 50),
            ('score', '正考总评', 70), ('final', '最终', 70), ('gpa', '绩点', 60),
        ]
        for col_id, text, width in col_config:
            self.tree.heading(col_id, text=text)
            self.tree.column(col_id, width=int(width * self.ui_scale),
                             minwidth=int(width * 0.6 * self.ui_scale), anchor='center')
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        log_frame = tk.Frame(self.root)
        log_frame.pack(fill=tk.X, padx=10, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state='disabled')
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Button(log_frame, text='清空日志', command=self.clear_log).pack(side=tk.RIGHT, padx=5)

    def clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

    def open_settings(self):
        self._settings = SettingsWindow(self.root, self.config, self._on_config_saved,
                                        post=self.post, semesters=self.semesters,
                                        ui_scale=self.ui_scale)

    def _on_config_saved(self, new_config):
        self.config = new_config
        self.sem_entry.set(new_config.get('semester_str', '2025-2026.2'))

    def log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, '[%s] %s\n' % (time.strftime('%H:%M:%S'), msg))
        lines = self.log_text.get(1.0, tk.END).splitlines()
        if len(lines) > 1000:                 # 只保留最近 500 行
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, '\n'.join(lines[-500:]) + '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def update_tree(self, grades):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for g in grades:
            self.tree.insert('', tk.END, values=(
                g.get('semester', ''), g.get('code', ''), g.get('seq', ''),
                g.get('name', ''), g.get('category', ''), g.get('credit', ''),
                g.get('score', ''), g.get('final', ''), g.get('gpa', '')))

    # ------------------------------------------------------------------
    # 工作线程调度
    # ------------------------------------------------------------------
    def _dispatch(self, fn, label=''):
        """在后台线程里跑 fn；同一时刻只允许一个后台任务。"""
        if self._worker is not None and self._worker.is_alive():
            self.log('上一次操作（%s）还在进行，请稍候' % self._worker_label)
            return False
        self._worker_label = label
        self._worker = threading.Thread(target=self._run_guarded, args=(fn,), daemon=True)
        self._worker.start()
        return True

    def _run_guarded(self, fn):
        try:
            fn()
        except Exception as exc:              # 后台线程绝不能让异常静默消失
            self.post('log', '后台任务异常: %s' % exc)

    # ------------------------------------------------------------------
    # 登录 / 重新登录（工作线程）
    # ------------------------------------------------------------------
    def _tls_kwargs(self):
        return build_tls_kwargs(self.config)

    def _boot_task(self, sem_str):
        """启动：登录 -> 拉学期列表 -> 查一次成绩（都在工作线程里）。

        sem_str 由主线程读出后传入（工作线程不能碰 Tk 控件）。
        """
        if not self._login_task():
            return
        self._load_semesters_task()
        self._perform_query_task(sem_str)

    def _login_task(self):
        self.post('log', '正在初始化认证...')
        self.post('status', '登录中', 'blue')
        cookies = {}
        if os.path.exists(config_manager.COOKIE_FILE):
            try:
                with open(config_manager.COOKIE_FILE, 'r', encoding='utf-8') as f:
                    cookies = parse_cookie_string(f.read())
            except OSError as exc:
                self.post('log', '读取 cookies 失败: %s' % exc)

        ids = IdsAuth(cookies, **self._tls_kwargs()) if cookies else IdsAuth(**self._tls_kwargs())
        if cookies and ids.ok:
            self.post('log', '使用缓存的 Cookie 登录成功')
        else:
            username = (self.config.get('username') or '').strip()
            password = self.config.get('password') or ''
            if not username or not password:
                self.post('log', '请先在设置中填写学号和密码')
                self.post('status', '未设置账号', 'orange')
                return False
            self.post('log', '需要账号密码登录...')
            try:
                ids.login(username, password)
            except Exception as exc:
                self.post('log', '登录请求异常: %s' % exc)
                self.post('status', '登录失败', 'red')
                return False
            if not ids.ok:
                self.post('log', ids.last_error or '登录失败，请检查用户名密码')
                self.post('status', '登录失败', 'red')
                return False

        self.ids = ids
        try:
            config_manager.ensure_dirs()
            with open(config_manager.COOKIE_FILE, 'w', encoding='utf-8', newline='\n') as f:
                f.write(dump_cookie_string(ids.cookies))
        except OSError as exc:
            self.post('log', '写入 cookies 失败: %s' % exc)

        self.fetcher = GradeFetcher(ids, logger=lambda m: self.post('log', m))
        self.post('log', '登录成功！')
        self.post('status', '已登录', 'green')
        return True

    def _load_semesters_task(self):
        """爬取学期列表填进下拉框（失败不影响查询）。"""
        if not self.fetcher:
            return
        try:
            items = self.fetcher.fetch_semester_list(force=True)
            self.post('semesters', items)
        except Exception as exc:
            self.post('log', '获取学期列表失败: %s' % exc)
            self.post('semesters', [])

    def refresh_semesters(self):
        if not self.fetcher:
            self.log('尚未登录成功，无法获取学期列表')
            if self._settings is not None:
                try:
                    self._settings.semesters_loaded(False, '尚未登录成功')
                except tk.TclError:
                    self._settings = None
            return
        self._dispatch(self._load_semesters_task, label='获取学期列表')

    def _renew_session(self):
        """在工作线程里重新登录（调用方在查询线程内）。"""
        if os.path.exists(config_manager.COOKIE_FILE):
            try:
                os.remove(config_manager.COOKIE_FILE)
            except OSError as exc:
                self.post('log', '删除 cookies 失败: %s' % exc)
            self.post('log', '已删除 cookies.txt')
        username = (self.config.get('username') or '').strip()
        password = self.config.get('password') or ''
        if not username or not password:
            raise Exception('未配置用户名密码，无法重新登录')
        ids = IdsAuth(**self._tls_kwargs())
        ids.login(username, password)
        if not ids.ok:
            raise Exception(ids.last_error or '重新登录失败')
        self.ids = ids
        try:
            with open(config_manager.COOKIE_FILE, 'w', encoding='utf-8', newline='\n') as f:
                f.write(dump_cookie_string(ids.cookies))
        except OSError as exc:
            self.post('log', '写入 cookies 失败: %s' % exc)
        self.fetcher = GradeFetcher(ids, logger=lambda m: self.post('log', m))
        self.post('log', '重新登录成功')

    # ------------------------------------------------------------------
    # 查询（工作线程）
    # ------------------------------------------------------------------
    def _sem_str(self):
        """读取"当前学年学期"。

        **只能在主线程调用**——它会访问 Tk 控件。工作线程请用 _dispatch_query()
        （在主线程把值读出来再传进去），否则 Tk 会抛
        "main thread is not in main loop"（本项目的测试就是这么抓到这个 bug 的）。
        另外维护一份纯 Python 镜像，非主线程误调时退回镜像值而不是崩掉。
        """
        try:
            value = (self.sem_entry.get() or '').strip()
        except RuntimeError:
            value = self._sem_value
        if value:
            self._sem_value = value
        return value or (self.config.get('semester_str') or '2025-2026.2').strip()

    def _dispatch_query(self, label):
        """在主线程读出学期值，再交给工作线程去查（避免跨线程访问 Tk）。"""
        sem_str = self._sem_str()
        self._dispatch(lambda: self._perform_query_task(sem_str), label=label)

    def _perform_query_task(self, sem_str):
        if not self.fetcher:
            self.post('log', '未登录，无法查询')
            return

        cfg = self.config
        try:
            retries = max(0, int(cfg.get('max_retries', 2) or 0))
            retry_interval = max(0, int(cfg.get('retry_interval', 60) or 0))
        except (TypeError, ValueError):
            retries, retry_interval = 2, 60

        sem_str = (sem_str or '').strip() or (
            self.config.get('semester_str') or '2025-2026.2').strip()
        old_grades = GradeFetcher.load_from_file(sem_str)
        budget = retries + 1
        auth_extra = 1
        tried = 0
        last_error = None

        while tried < budget:
            tried += 1
            try:
                current = self.fetcher
                if tried > 1:
                    self.post('log', '第 %d 次尝试，清理 Cookie 并重新登录...' % tried)
                    self._renew_session()
                    current = self.fetcher

                new_grades = current.fetch_by_semester_str(sem_str)
                self.post('grades', new_grades)

                if new_grades:
                    path = GradeFetcher.save_to_file(new_grades, sem_str)
                    self.post('log', '成绩已保存: %s（%d 条）'
                              % (os.path.basename(path), len(new_grades)))
                else:
                    self.post('log', '本学期暂无成绩（未写入文件）')

                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.last_query_time = now
                self.post('time', now)

                if not old_grades and new_grades:
                    self.post('log', '首次查询到 %d 条成绩' % len(new_grades))
                    self._notify(cfg, 'SUEP成绩提醒', '首次查询到 %d 条成绩。' % len(new_grades))
                elif old_grades and new_grades:
                    changes = current.detect_changes(old_grades, new_grades)
                    if changes['added'] or changes['modified']:
                        self.post('log', '检测到变动: 新增 %d, 修改 %d'
                                  % (len(changes['added']), len(changes['modified'])))
                        self._notify(cfg, 'SUEP成绩变动提醒',
                                     self._build_notification_content(changes))
                    else:
                        self.post('log', '查询完成，无变化')
                else:
                    self.post('log', '查询完成，无变化')
                return

            except Exception as exc:
                last_error = exc
                self.post('log', '查询失败 (尝试 %d/%d): %s' % (tried, budget, exc))
                if isinstance(exc, SessionExpired) and auth_extra > 0:
                    auth_extra -= 1
                    budget += 1
                if tried >= budget:
                    break
                if retry_interval:
                    time.sleep(retry_interval)

        self.post('log', '查询失败，已达最大尝试次数，停止监控并发送错误通知。')
        self.post('status', '错误终止', 'red')
        self._notify(cfg, '❌ 程序错误提醒',
                     '监控因连续错误已停止。\n最后一次错误：\n%s' % last_error)
        self.post('fatal', '连续查询失败，监控已停止。\n\n错误信息：\n%s' % last_error)

    def _notify(self, cfg, title, content):
        methods = cfg.get('notification_methods') or []
        if not methods or not content:
            return
        notifier = Notifier(cfg, logger=lambda m: self.post('log', m))
        ok = notifier.send(title, content)
        self.post('log', '通知%s（%s）' % ('发送成功' if ok else '发送失败',
                                          summarize(notifier.results)))

    # ------------------------------------------------------------------
    # 监控开关
    # ------------------------------------------------------------------
    def start_monitor(self):
        if self.running:
            return
        if not self.fetcher:
            messagebox.showerror('错误', '尚未登录成功，无法开始监控')
            return

        self.running = True
        self._ui_buttons(True)
        self._ui_status('监控中', 'blue')

        sem_str = self._sem_str()
        self.last_grades = GradeFetcher.load_from_file(sem_str)
        self.log('已加载历史成绩 %d 条 (学期: %s)' % (len(self.last_grades), sem_str))
        self._schedule_query()

    def stop_monitor(self):
        if not self.running and self.timer is None:
            return
        self.running = False
        if self.timer is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
            self.timer = None
        self._apply_stopped_buttons()
        self._ui_status('已停止', 'orange')
        self.log('监控已停止')

    def _apply_stopped_buttons(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.query_now_btn.config(state=tk.NORMAL)
        self.settings_btn.config(state=tk.NORMAL)

    def _schedule_query(self):
        if not self.running:
            return
        self._dispatch_query('定时查询')
        try:
            interval = max(30, int(self.config.get('query_interval', 300) or 300))
        except (TypeError, ValueError):
            interval = 300
        self.timer = self.root.after(interval * 1000, self._schedule_query)

    def manual_query(self):
        if not self.fetcher:
            self.log('未登录，无法查询')
            return
        self._dispatch_query('手动查询')

    # ------------------------------------------------------------------
    # 通知内容
    # ------------------------------------------------------------------
    def _is_failing(self, score_str):
        """只有真正的数字且 <60 才算挂科。

        旧版在 float() 失败时 return True，会把"合格/通过/良/优秀"这类
        非数字成绩（体育、通识、集中实践课）误报成"补考要加油"。
        """
        if score_str is None or str(score_str).strip() == '':
            return False
        try:
            return float(str(score_str).strip()) < 60
        except (TypeError, ValueError):
            return False

    def _format_course_line(self, course):
        line = '%s (%s)  成绩: %s' % (course.get('name'), course.get('code'),
                                      course.get('score'))
        if course.get('gpa'):
            line += '  绩点: %s' % course['gpa']
        if self._is_failing(course.get('score')) or self._is_failing(course.get('final')):
            line += '  ⚠️ 补考要加油啊！'
        return line

    def _build_notification_content(self, changes):
        lines = []
        if changes.get('added'):
            lines.append('📌 新增课程：')
            for g in changes['added']:
                lines.append(self._format_course_line(g))
            lines.append('')
        if changes.get('modified'):
            lines.append('📝 成绩更新：')
            for item in changes['modified']:
                old, new = item['old'], item['new']
                line = '%s (%s)  成绩: %s → %s' % (new.get('name'), new.get('code'),
                                                  old.get('score'), new.get('score'))
                if new.get('gpa'):
                    line += '  绩点: %s → %s' % (old.get('gpa', ''), new['gpa'])
                old_fail = self._is_failing(old.get('score'))
                new_fail = self._is_failing(new.get('score'))
                if old_fail and not new_fail:
                    line += '  🎉恭喜过关！'
                elif old_fail and new_fail:
                    line += '  QAQ'
                elif new_fail:
                    line += '  ⚠️ 补考要加油啊！'
                lines.append(line)
            lines.append('')
        return '\n'.join(lines).strip()

    # ------------------------------------------------------------------
    def on_close(self):
        self._closing = True
        self.running = False
        if self.timer is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
        self.root.destroy()


def run_selftest(out_path=None):
    """无界面自检：真实登录 + 取学期列表 + 抓一次成绩 + 落盘，结果写文件。

    窗口程序（--windowed）没有 stdout，所以结果必须写成文件，才能脚本化验证打包产物。
    用法： ``SUEP成绩监控.exe --selftest [--out 结果文件]``
    返回 0 表示全部正常。
    """
    import traceback
    lines = []
    ok = True

    def add(text):
        lines.append(text)

    add('=== SUEP 桌面版自检 %s ===' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    add('python      : %s' % sys.version.split()[0])
    add('frozen      : %s' % bool(getattr(sys, 'frozen', False)))
    add('exe         : %s' % (sys.executable if getattr(sys, 'frozen', False) else '(源码运行)'))
    add('app_dir     : %s' % config_manager.BASE_DIR)
    add('config_file : %s' % config_manager.CONFIG_FILE)
    add('config 存在 : %s' % os.path.exists(config_manager.CONFIG_FILE))
    add('MEIPASS     : %s' % getattr(sys, '_MEIPASS', '(无)'))

    try:
        cfg = load_config()
        add('username    : %s' % (cfg.get('username') or '(空)'))
        add('semester    : %s' % (cfg.get('semester_str') or '(空)'))
        add('timeout     : 连接 %s / 读取 %s' % (cfg.get('connect_timeout'),
                                                cfg.get('read_timeout')))
        add('proxy       : %s' % (cfg.get('proxy_url') if cfg.get('proxy_enabled') else '未启用'))

        if not cfg.get('username') or not cfg.get('password'):
            add('! 未配置学号/密码，跳过联网检查')
            ok = False
        else:
            kwargs = build_tls_kwargs(cfg)
            t0 = time.time()
            auth = IdsAuth(**kwargs)
            logged = auth.login(cfg['username'], cfg['password'])
            add('login       : %s  (%.1fs)  %s'
                % (logged, time.time() - t0, auth.last_error or ''))
            if not logged:
                ok = False
            else:
                # 把 cookie 一并落盘并校验位置：config / cookies / 成绩 / logs
                # 这四样都必须落在程序目录，落到 %TEMP% 解包目录就会退出即丢
                try:
                    config_manager.ensure_dirs()
                    cookie_path = config_manager.COOKIE_FILE
                    with open(cookie_path, 'w', encoding='utf-8', newline='\n') as f:
                        f.write(dump_cookie_string(auth.cookies))
                    add('cookies     : %s（%d 个）' % (cookie_path, len(auth.cookies)))
                    add('cookies 存在: %s' % os.path.exists(cookie_path))
                    if not os.path.exists(cookie_path):
                        ok = False
                except OSError as exc:
                    add('cookies 写入失败: %s' % exc)
                    ok = False

                fetcher = GradeFetcher(auth, logger=lambda m: add('  [fetcher] %s' % m))
                semesters = fetcher.fetch_semester_list(force=True)
                add('semesters   : %d 个' % len(semesters))
                if semesters:
                    add('  最新      : %s (id=%s)'
                        % (semesters[-1]['semester'], semesters[-1]['id']))
                grades = fetcher.fetch_by_semester_str(cfg.get('semester_str'))
                add('grades      : %d 条' % len(grades))
                for row in grades[:3]:
                    add('   %s  %s  %s  成绩 %s'
                        % (row['semester'], row['code'], row['name'], row['score']))
                if not grades:
                    ok = False
                else:
                    saved = GradeFetcher.save_to_file(grades, cfg.get('semester_str'))
                    add('saved       : %s' % saved)
                    add('saved 存在  : %s' % os.path.exists(saved))
                    # 关键：文件必须落在**程序目录**，不能落在 %TEMP% 解包目录
                    inside_temp = bool(getattr(sys, '_MEIPASS', None)) and \
                        os.path.abspath(saved).startswith(
                            os.path.abspath(getattr(sys, '_MEIPASS')))
                    add('落在程序目录: %s' % (not inside_temp))
                    if not os.path.exists(saved) or inside_temp:
                        ok = False
    except Exception:
        add('EXCEPTION:\n%s' % traceback.format_exc())
        ok = False

    add('RESULT      : %s' % ('OK' if ok else 'FAIL'))
    text = '\n'.join(lines)

    target = out_path or os.path.join(config_manager.BASE_DIR, 'selftest_result.txt')
    try:
        with open(target, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text + '\n')
    except OSError:
        pass
    _safe_print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    # 无界面自检：给打包产物做脚本化验证用
    if '--selftest' in sys.argv:
        out_file = None
        if '--out' in sys.argv:
            idx = sys.argv.index('--out')
            if idx + 1 < len(sys.argv):
                out_file = sys.argv[idx + 1]
        sys.exit(run_selftest(out_file))

    # 高 DPI 适配：必须在创建根窗口之前开启感知，并把缩放比传进去
    # （只开启感知不够，行高/列宽/窗口尺寸都要跟着字体放大，见 setup_dpi 的说明）
    ui_scale = setup_dpi()

    root = tk.Tk()
    gui = GradeGUI(root, ui_scale=ui_scale)
    root.mainloop()
