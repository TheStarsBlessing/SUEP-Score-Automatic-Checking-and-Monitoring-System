# -*- coding: utf-8 -*-
"""统一身份认证（CAS / 教务系统）会话封装。

本文件在 桌面版 / 网页版 / docker版 三个目录里是**同一份**，改动请三处同步。

与旧版相比修掉的问题（均为实测确认）：

1. Session / cookies / ok 改为**实例属性**。旧版把这些写在类体里
   （``s = requests.Session()``），所有实例共用一个 Session、共用一个 cookie jar，
   于是 `IdsAuth()` 重新构造并不能清空 cookie，"删掉 cookies.txt 再重新登录"
   实际上仍带着旧票据发请求；docker 版里 `IdsAuth(proxy=...)` 还会把代理永久写进那个共享 Session。

2. 所有请求都带超时。旧版完全没有超时，网络卡住会把调用线程永久挂起——
   网页版更是在 ``__main__`` 里同步登录，学校站点不可达时整个 Web 服务都起不来。

3. 登录表单只在"含 username 与 password 的那个 <form>"里收集字段，
   而旧版用 ``//form//input``（页面上多出一个表单就会串味）。

4. 未登录时教务系统会 302 到 CAS，requests 跟随跳转后**仍是 HTTP 200**，
   最终 URL 落在 authserver 上。旧版只看 status_code == 200 就当作成功，
   于是会话过期会表现为"查询成功但 0 条成绩"并且永远不重新登录。
   现在由 GradeFetcher 依据 ``resp.url`` 判定为 SessionExpired。
"""
import requests

try:  # 关闭 verify 时别刷屏
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass

from lxml import etree

IDS_LOGIN_URL = 'https://ids.shiep.edu.cn/authserver/login'
JW_HOME_URL = 'https://jw.shiep.edu.cn/eams/home.action'
JW_LOGIN_SERVICE = 'http://jw.shiep.edu.cn/eams/login.action'

DEFAULT_TIMEOUT = (8, 20)   # (连接超时, 读取超时) 秒

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


class AuthError(Exception):
    """认证类错误基类。"""


class LoginFailed(AuthError):
    """登录流程本身走不通（页面结构变化、账号密码错误等）。"""


class SessionExpired(AuthError):
    """Cookie 已失效，需要重新登录。"""


def parse_cookie_string(text):
    """解析 ``k=v;k2=v2`` 形式的 cookie 文本。

    旧版用 ``i.split('=')[1]``，只要 cookie 值里含 ``=``（base64 填充、JWT 都会）
    就 IndexError，而调用处是裸 ``except: pass``，于是静默退化成"没有 cookie"。
    这里用 ``split('=', 1)`` 并且不抛异常。
    """
    cookies = {}
    if not text:
        return cookies
    for part in text.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, value = part.split('=', 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def dump_cookie_string(cookies):
    """把 cookie 字典序列化成 ``k=v;k2=v2``。"""
    return ';'.join('%s=%s' % (k, v) for k, v in cookies.items())


class IdsAuth:
    """一次认证会话。每个实例持有自己的 requests.Session。"""

    def __init__(self, cookies=None, proxy=None, ssl_verify=False,
                 ca_bundle=None, timeout=DEFAULT_TIMEOUT, check=True):
        """
        :param cookies: 初始 cookie 字典（例如从 cookies.txt 恢复）
        :param proxy: 代理 URL，如 'socks5://127.0.0.1:1080'（docker 版用）
        :param ssl_verify: 是否校验 TLS 证书。学校证书链通常不在系统信任库里，
                           所以默认 False（与旧版 rVerify=False 行为一致）。
        :param ca_bundle: 指定 CA 文件/目录时自动开启证书校验，优先于 ssl_verify。
        :param timeout: (连接超时, 读取超时)
        :param check: 传入 cookies 时是否立即校验会话是否有效
        """
        self.s = requests.Session()
        self.headers = {'User-Agent': USER_AGENT}
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.verify = ca_bundle if ca_bundle else bool(ssl_verify)
        self.cookies = {}
        self.ok = False
        self.last_error = ''

        if proxy:
            self.s.proxies = {'http': proxy, 'https': proxy}
            # 显式指定代理时忽略系统代理环境变量，避免双重代理
            self.s.trust_env = False

        if cookies:
            self.s.cookies = requests.utils.cookiejar_from_dict(dict(cookies))
            self.cookies = self.s.cookies.get_dict()
            if check:
                self.check()

    # ---------- 请求封装：统一带超时与证书策略 ----------
    def get(self, url, **kwargs):
        kwargs.setdefault('verify', self.verify)
        kwargs.setdefault('timeout', self.timeout)
        return self.s.get(url, **kwargs)

    def post(self, url, **kwargs):
        kwargs.setdefault('verify', self.verify)
        kwargs.setdefault('timeout', self.timeout)
        return self.s.post(url, **kwargs)

    def head(self, url, **kwargs):
        kwargs.setdefault('verify', self.verify)
        kwargs.setdefault('timeout', self.timeout)
        return self.s.head(url, **kwargs)

    # ---------- 认证 ----------
    @staticmethod
    def _find_login_form(doc):
        """找到含 username + password 的那个表单；找不到再退而求其次。"""
        fallback = None
        for form in doc.xpath('//form'):
            names = {(i.get('name') or '') for i in form.xpath('.//input')}
            if 'username' in names and 'password' in names:
                return form
            if fallback is None and names:
                fallback = form
        by_id = doc.xpath('//form[@id="casLoginForm"]')
        if by_id:
            return by_id[0]
        return fallback

    def login(self, username, password, service=JW_LOGIN_SERVICE):
        """走 CAS 表单登录。返回 self.ok。

        只有"流程本身走不通"才抛 LoginFailed；账号密码错误表现为返回 False
        （调用方照旧检查 ``ids.ok``）。
        """
        resp = self.get(IDS_LOGIN_URL, params={'service': service},
                        headers=self.headers)
        if resp.status_code != 200:
            raise LoginFailed('认证页面返回状态码 %s' % resp.status_code)

        page = resp.text
        if 'pwdDefaultEncryptSalt' in page:
            # 当前（2026-07 实测）该页面没有这个字段，说明密码是明文提交的。
            # 学校一旦启用前端 AES 加密，这里会明确报错，而不是静默"登录失败"。
            raise LoginFailed(
                '认证页面已启用密码前端加密（pwdDefaultEncryptSalt），'
                '需要同步升级登录逻辑后再使用')

        doc = etree.HTML(page)
        form = self._find_login_form(doc)
        if form is None:
            raise LoginFailed('未在认证页面找到登录表单（页面结构可能已变化）')

        data = {}
        for inp in form.xpath('.//input'):
            name = inp.get('name')
            if not name:
                continue          # 未命名 input（如提交按钮）不要塞进表单
            # 注意：这里必须保留 None，而**不要**写成 `or ''`。
            # 页面上 rememberMe 是 <input type="checkbox"> 且没有 value 属性，
            # 取到的是 None；requests 会自动丢弃值为 None 的字段，于是复选框
            # 未勾选时该参数不会被提交。实测（2026-07）一旦把空字符串
            # "rememberMe=" 发出去，CAS 会直接拒绝登录（只回 JSESSIONID_ids2、
            # 不发 CASTGC），所以这里必须与原实现保持逐字节一致。
            data[name] = inp.get('value')

        if 'username' not in data or 'password' not in data:
            raise LoginFailed('认证表单缺少 username/password 字段（页面结构可能已变化）')

        data['username'] = username
        data['password'] = password
        data.setdefault('_eventId', 'submit')

        resp = self.post(IDS_LOGIN_URL, params={'service': service},
                         data=data, headers=self.headers)
        self.cookies = self.s.cookies.get_dict()
        self.check()
        if not self.ok:
            self.last_error = self._describe_login_failure(resp)
        return self.ok

    @staticmethod
    def _describe_login_failure(resp):
        text = resp.text or ''
        for marker in ('密码错误', '用户名或密码', '无效的凭据', 'authentication failed',
                       '账号或密码', '账户已被锁定', 'Account locked'):
            if marker in text:
                return '认证失败：%s' % marker
        return '认证失败：登录后仍无法访问教务系统（账号密码错误或权限不足）'

    def check(self):
        """当前 cookie 是否还能访问教务系统。

        未登录时教务系统会 302 到 CAS（实测），因此 200 才算有效。
        """
        resp = self.get(JW_HOME_URL, headers=self.headers, allow_redirects=False)
        self.ok = (resp.status_code == 200)
        return self.ok

    def logout(self):
        """尽力而为的登出，失败不影响主流程。"""
        try:
            self.get('https://ids.shiep.edu.cn/authserver/logout',
                      headers=self.headers, allow_redirects=False)
        except Exception:
            pass
        finally:
            self.cookies = {}
            self.ok = False
