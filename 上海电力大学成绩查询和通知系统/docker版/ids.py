import requests
from lxml import etree

class IdsAuth:
    cookies = {}
    ok = False

    headers = {
        'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/106.0.0.0 Safari/537.36',
    }
    s = requests.Session()
    rVerify = False  # or 'mitm.pem'

    def __init__(self, cookies=None, proxy=None):
        """
        :param cookies: 初始 cookie 字典
        :param proxy: 代理 URL，例如 'socks5://127.0.0.1:1080'
        """
        if cookies:
            self.s.cookies = requests.utils.cookiejar_from_dict(cookies)
            self.cookies = self.s.cookies.get_dict()
        if proxy:
            self.s.proxies = {
                'http': proxy,
                'https': proxy,
            }
            self.s.trust_env = False   # 忽略系统代理环境变量
        self.check()

    def login(self, username: str, password: str, service: str):
        url = 'https://ids.shiep.edu.cn/authserver/login'

        resp = self.s.get(url,
                          params={'service': service},
                          headers=self.headers,
                          verify=self.rVerify)
        e = etree.HTML(resp.text)
        form = {
            i.get('name'): i.get('value')
            for i in e.xpath('//form//input')
        }
        form['username'] = username
        form['password'] = password

        resp = self.s.post(url,
                           params={'service': service},
                           data=form,
                           headers=self.headers,
                           verify=self.rVerify)
        self.cookies = self.s.cookies.get_dict()
        self.check()

    def check(self):
        url = 'https://jw.shiep.edu.cn/eams/home.action'
        resp = self.s.get(url,
                          headers=self.headers,
                          verify=self.rVerify,
                          allow_redirects=False)
        self.ok = (resp.status_code == 200)

    def get(self, url: str, **kwargs):
        return self.s.get(url, verify=self.rVerify, **kwargs)

    def post(self, url: str, **kwargs):
        return self.s.post(url, verify=self.rVerify, **kwargs)

    def head(self, url: str, **kwargs):
        return self.s.head(url, verify=self.rVerify, **kwargs)