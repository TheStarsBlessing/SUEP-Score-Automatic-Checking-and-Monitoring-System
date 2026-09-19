# -*- coding: utf-8 -*-
"""成绩抓取、解析、落盘与变动检测。

三个版本（桌面版 / 网页版 / docker版）共用同一份，改动请三处同步。

修掉的旧问题：

1. **静默假成功**：未登录时教务系统 302 到 CAS，requests 跟随跳转后仍是 HTTP 200。
   旧版只看 status_code，于是 ``_parse_grades`` 返回空列表，上层把它当作
   "查询成功但本学期无成绩"，既不重新登录也不报警——会话一旦过期就永远查不到成绩。
   现在按最终 URL 是否落在 authserver 判定为 SessionExpired 并抛异常，交给上层重登。

2. **XPath 太脆**：旧版 ``//table[@class="gridtable"]/tbody/tr`` 要求 class 精确相等
   且必须有 tbody（lxml 不会自动补 tbody）。现在用 ``contains(@class,"gridtable")``，
   并区分三种情况：被重定向到登录页 -> 抛 SessionExpired；
   没有表格 -> 按"本学期暂无成绩"返回空并记下页面提示；
   有数据行但列数对不上 -> 抛 ParseError（页面结构变了）。

3. **没有超时**：网络卡死会永久挂起调用线程。

4. **semesterId 硬编码公式不可靠**：实测从教务系统爬到的权威学期列表显示，
   ``2024-2025.2 = 364，每学期 +20`` 只对部分学期成立——
   2023-2024.1 真实是 284（公式给 304）、2023-2024.2 真实是 304（公式给 324）、
   2019-2020.2 真实是 163（公式给 164）、2022-2023.2 真实是 264（公式给 284）。
   照公式查 2023-2024 学年会查到不存在的学期，旧版会静默表现为"没有成绩"。
   现在优先用 fetch_semester_list() 爬到的权威 id，公式只作离线兜底，
   并且 ID 被修正时会写日志，最后还有 _verify_semester 用返回的学期标签兜底。

5. **文件名三版不一致**：桌面版写 ``grade_data_2025-2026_2.txt``、网页版写
   ``grade_data_2025-2026.2.txt``，同一学期两个版本互不共享历史。现在统一为
   ``grade_data_2025-2026.2.txt``，并且读取时会自动兼容旧命名。

6. **非原子落盘**：旧版直接 'w' 覆盖历史文件，写一半崩溃就丢历史。改为临时文件 + os.replace。
"""
import os
import re
import tempfile
import time

from lxml import etree

from ids import SessionExpired

import config_manager

# ---------------------------------------------------------------------------
# semesterId 离线兜底公式
# 实测：2025-2026.2 -> 404（与 config.json 里的 semester_id 一致）
# ---------------------------------------------------------------------------
_BASE_YEAR = 2024
_BASE_SEMESTER = 2
_BASE_ID = 364
_ID_STEP = 20

GRADE_URL = ('https://jw.shiep.edu.cn/eams/teach/grade/course/person!search.action'
             '?semesterId={sem_id}&projectType=')

# 学期列表的权威来源：成绩页内嵌的学年学期"日历"控件，
# 其数据由 POST /eams/dataQuery.action {tagId, dataType:"semesterCalendar"} 提供
# （tagId 必须是页面上真实存在的元素 id，服务端按会话登记，所以每次先去页面取）。
INNER_INDEX_URL = ('https://jw.shiep.edu.cn/eams/teach/grade/course/person'
                   '!innerIndex.action?projectId=1')
DATA_QUERY_URL = 'https://jw.shiep.edu.cn/eams/dataQuery.action'
SEM_TAG_RE = re.compile(r'jQuery\("#(\w*Semester\w*)"\)\.semesterCalendar')
# 响应是 JS 字面量（键名无引号），直接按条目正则抽取最稳
SEM_ENTRY_RE = re.compile(r'\{\s*id\s*:\s*(\d+)\s*,\s*schoolYear\s*:\s*"([^"]*)"'
                          r'\s*,\s*name\s*:\s*"([^"]*)"\s*\}')
SEMESTER_CACHE_SECONDS = 6 * 3600

HEADER_LINE = ('学年学期\t课程代码\t课程序号\t课程名称\t课程类别\t学分\t'
               '正考总评成绩\t最终\t绩点\n')
COLUMNS = ('semester', 'code', 'seq', 'name', 'category',
           'credit', 'score', 'final', 'gpa')


class FetchError(Exception):
    """抓取/解析类错误基类。"""


class AuthExpiredError(FetchError, SessionExpired):
    """会话已失效（教务系统把我们重定向到了统一身份认证）。"""


class ParseError(FetchError):
    """页面结构与预期不符。"""


def semester_str_to_id(sem_str):
    """把 ``2025-2026.2`` 转成教务系统 semesterId（离线兜底公式）。

    :raises ValueError: 格式非法
    """
    match = re.match(r'\s*(\d{4})-(\d{4})\.([12])\s*$', str(sem_str or ''))
    if not match:
        raise ValueError('无效的学期格式: %s，应为 2025-2026.2' % sem_str)
    start_year = int(match.group(1))
    end_year = int(match.group(2))
    semester = int(match.group(3))
    if end_year != start_year + 1:
        raise ValueError('学年跨度必须为1年')
    semester_diff = ((start_year - _BASE_YEAR) * 2
                     + (semester - _BASE_SEMESTER))
    return _BASE_ID + _ID_STEP * semester_diff


def semester_str_from_text(text):
    """从下拉项文本里解析学期字符串，例如
    ``2025-2026学年 第2学期`` / ``2025-2026 2`` / ``2025-2026-2`` -> ``2025-2026.2``
    """
    if not text:
        return None
    text = str(text).replace('\u3000', ' ').strip()
    m = re.search(r'(\d{4})\s*[-–—~至]\s*(\d{4})', text)
    if not m:
        return None
    y1, y2 = m.group(1), m.group(2)
    rest = text[m.end():]
    n = None
    m2 = re.search(r'第\s*([12])\s*学期', rest) or re.search(r'([12])\s*学期', rest)
    if m2:
        n = m2.group(1)
    else:
        m2 = re.search(r'([12])\s*$', rest.strip())
        if m2:
            n = m2.group(1)
    if not n:
        return None
    return '%s-%s.%s' % (y1, y2, n)


class GradeFetcher:
    def __init__(self, ids, logger=None):
        """
        :param ids: IdsAuth 实例
        :param logger: 可选的可调用对象，接受一个字符串（用于落日志）
        """
        self.ids = ids
        self.log = logger if callable(logger) else (lambda msg: None)
        self.headers = {'User-Agent': getattr(ids, 'headers', {}).get('User-Agent', '')}
        self._semesters = []          # 从教务系统爬到的学期列表缓存
        self._semesters_at = 0.0

    # ------------------------------------------------------------------
    # 抓取
    # ------------------------------------------------------------------
    def _get_grade_html(self, sem_id):
        resp = self.ids.get(GRADE_URL.format(sem_id=sem_id), headers=self.headers)
        # 未登录时最终 URL 会落在 authserver 上（实测），此时状态码仍是 200
        if 'authserver' in (resp.url or ''):
            raise AuthExpiredError('会话已过期（被重定向到统一身份认证）')
        if resp.status_code != 200:
            raise FetchError('获取成绩失败，状态码 %s' % resp.status_code)
        return resp.text

    def _verify_semester(self, grades, sem_str, sem_id):
        """用返回行自带的"学年学期"列反查是否真的查对了学期。

        学校若重排 semesterId，硬编码公式会**静默**查到另一个学期。
        这里用数据自身的学期标签兜底告警
        （实测标签格式 '2025-2026 2'，规范化后为 '2025-2026.2'）。
        """
        if not grades:
            return True
        expected = semester_str_from_text(sem_str) or str(sem_str).strip()
        counts = {}
        for g in grades:
            raw = g.get('semester')
            label = semester_str_from_text(raw) or str(raw or '').strip()
            counts[label] = counts.get(label, 0) + 1
        if expected in counts and len(counts) == 1:
            return True
        self.log('⚠️ 学期核对不一致：请求的学期是 %s（semesterId=%s），'
                 '但返回成绩的学期是 %s。可能是学校的 semesterId 规则变了，'
                 '请核对 config.json 里的 semester_str。'
                 % (expected, sem_id,
                    '、'.join('%s×%d' % kv for kv in sorted(counts.items()))))
        return False

    # ------------------------------------------------------------------
    # 学期列表（从教务系统爬取，权威）
    # ------------------------------------------------------------------
    def fetch_semester_list(self, force=False):
        """爬取学校全部学期，返回 [{'id':404,'semester':'2025-2026.2','label':...}, ...]

        结果缓存 6 小时。任何失败都只记日志并返回已有缓存/空列表——
        学期列表拿不到不能影响正常查询（resolve_semester_id 会退回公式）。
        """
        now = time.time()
        if not force and self._semesters and now - self._semesters_at < SEMESTER_CACHE_SECONDS:
            return self._semesters
        try:
            page = self.ids.get(INNER_INDEX_URL, headers=self.headers).text or ''
            tags = SEM_TAG_RE.findall(page)
            if not tags:
                self.log('未在页面里找到学期控件 id，学期列表不可用（回退到离线公式）')
                return self._semesters
            resp = self.ids.post(DATA_QUERY_URL,
                                 data={'tagId': tags[0], 'dataType': 'semesterCalendar'},
                                 headers=self.headers)
            body = resp.text or ''
        except Exception as exc:
            self.log('获取学期列表失败（回退到离线公式）: %s' % exc)
            return self._semesters

        items, seen = [], set()
        for sid, school_year, term_name in SEM_ENTRY_RE.findall(body):
            m = re.search(r'[12]', term_name)
            if not m or not school_year.strip():
                continue
            term = m.group(0)
            sem = '%s.%s' % (school_year.strip().replace(' ', ''), term)
            if sem in seen:
                continue
            seen.add(sem)
            items.append({'id': int(sid), 'semester': sem,
                          'label': '%s学年 第%s学期' % (school_year.strip(), term)})
        items.sort(key=lambda item: item['id'])
        if items:
            self._semesters = items
            self._semesters_at = now
            self.log('已从教务系统获取 %d 个学期（%s ~ %s）'
                     % (len(items), items[0]['semester'], items[-1]['semester']))
        else:
            self.log('学期列表解析为空（页面格式可能已变化，回退到离线公式）')
        return self._semesters

    def resolve_semester_id(self, sem_str):
        """学期字符串 -> 数字 semesterId。

        优先用教务系统学期列表里的**权威 id**；列表拿不到时才退回离线公式。
        实测（2026-07）离线公式只对部分学期成立：
        2023-2024.1 真实是 284 而公式给 304、2023-2024.2 真实是 304 而公式给 324，
        照公式查会查到不存在的学期（Old 版本会静默表现为"没有成绩"）。
        """
        sem_str = str(sem_str or '').strip()
        for item in self.fetch_semester_list():
            if item['semester'] == sem_str:
                return item['id']
        return semester_str_to_id(sem_str)

    def semester_label(self, sem_str):
        """给界面用的中文标签，例如 2025-2026学年 第2学期。"""
        sem_str = str(sem_str or '').strip()
        for item in self.fetch_semester_list():
            if item['semester'] == sem_str:
                return item['label']
        return sem_str

    def fetch_grades(self, sem_id):
        """按数字 semesterId 抓取并解析成绩（返回 list[dict]）。"""
        return self._parse_grades(self._get_grade_html(sem_id))

    def fetch_by_semester_str(self, sem_str):
        """按 ``2025-2026.2`` 这样的学期字符串抓取成绩，并核对返回的学期标签。"""
        sem_str = str(sem_str or '').strip()
        sem_id = self.resolve_semester_id(sem_str)
        try:
            formula_id = semester_str_to_id(sem_str)
            if formula_id != sem_id:
                self.log('semesterId 按教务系统学期列表修正：%s 离线公式=%s，实际=%s'
                         % (sem_str, formula_id, sem_id))
        except ValueError:
            pass
        grades = self._parse_grades(self._get_grade_html(sem_id))
        self._verify_semester(grades, sem_str, sem_id)
        return grades

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _visible_text(doc):
        """取页面可见文本（剔除 script/style 内容，否则会把 jQuery 代码当成提示语）。"""
        for bad in doc.xpath('//script|//style'):
            parent = bad.getparent()
            if parent is not None:
                parent.remove(bad)
        return ' '.join((doc.xpath('string(//body)') or '').split())

    def _parse_grades(self, html):
        """解析成绩表格。

        实测（2026-07）教务系统对**所有**"查不到成绩"的情形（合法但本学期无成绩、
        或 semesterId 根本不存在）返回的是同一张通用提示页（正文以"请不要过快点击"开头，
        没有 gridtable）。所以"没有表格"必须按"本学期暂无成绩"处理并返回空列表——
        这里若抛异常，当前学期还没出成绩时监控会被反复打断。
        真正需要报错的是：被重定向到登录页（会话过期），
        以及有数据行但列数对不上（页面结构变了）。
        """
        doc = etree.HTML(html)
        tables = doc.xpath('//table[contains(@class, "gridtable")]')
        if not tables:
            if ('casLoginForm' in html or 'name="execution"' in html
                    or '统一身份认证' in html):
                raise AuthExpiredError('会话已过期（返回的是登录页）')
            self.log('未发现成绩表格，按"本学期暂无成绩"处理；页面提示：%r'
                     % (self._visible_text(doc)[:80] or ''))
            return []

        grades = []
        skipped = 0
        for row in tables[0].xpath('.//tr'):
            tds = row.xpath('./td')
            if not tds:
                continue                       # 表头用的是 <th>
            if len(tds) < len(COLUMNS):
                skipped += 1
                continue
            values = [td.xpath('string()').strip() for td in tds[:len(COLUMNS)]]
            grades.append(dict(zip(COLUMNS, values)))

        if not grades and skipped:
            # 有数据行但一列都对不上 —— 说明列数变了，必须报出来而不是当成"没成绩"
            raise ParseError('成绩表格有 %d 行数据但列数不足 %d，页面结构可能已变化'
                             % (skipped, len(COLUMNS)))
        if skipped:
            self.log('注意：%d 行成绩因列数不足被跳过' % skipped)
        return grades

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_semester(sem_str):
        return re.sub(r'[/\\]', '_', str(sem_str or '').strip())

    @staticmethod
    def grade_file_path(sem_str):
        """当前学期的成绩文件绝对路径。"""
        return os.path.join(config_manager.DATA_DIR,
                            'grade_data_%s.txt' % GradeFetcher._safe_semester(sem_str))

    @staticmethod
    def _candidate_paths(sem_str):
        """读取时的候选路径：新命名优先，再兼容历史命名。"""
        safe = GradeFetcher._safe_semester(sem_str)
        names = [
            'grade_data_%s.txt' % safe,                      # 统一后的命名
            'grade_data_%s.txt' % safe.replace('.', '_'),    # 桌面版旧命名
            'grade_data_%s.txt' % safe.replace('.', '-'),    # 其它可能的历史命名
            'grade_data.txt',                                # 最早的单一文件
        ]
        out = []
        for name in names:
            path = os.path.join(config_manager.DATA_DIR, name)
            if path not in out:
                out.append(path)
        return out

    @staticmethod
    def save_to_file(grades, sem_str):
        """原子写入当前学期的成绩文件。"""
        path = GradeFetcher.grade_file_path(sem_str)
        config_manager.ensure_dirs()
        fd, tmp = tempfile.mkstemp(prefix='.grade_data_', suffix='.tmp',
                                   dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(HEADER_LINE)
                for g in grades:
                    f.write('\t'.join(str(g.get(c, '')) for c in COLUMNS) + '\n')
            os.replace(tmp, path)          # 同目录 rename 是原子的
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return path

    @staticmethod
    def load_from_file(sem_str):
        """读取当前学期的历史成绩；找不到或解析不了就返回 []。"""
        for path in GradeFetcher._candidate_paths(sem_str):
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except OSError:
                continue
            grades = []
            for line in lines:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < len(COLUMNS):
                    continue
                if parts[0].strip() == '学年学期':       # 表头
                    continue
                grades.append(dict(zip(COLUMNS, [p.strip() for p in parts[:len(COLUMNS)]])))
            if grades:
                return grades
        return []

    # ------------------------------------------------------------------
    # 变动检测
    # ------------------------------------------------------------------
    @staticmethod
    def _key(g):
        """变动检测的主键：课程代码 + 课程序号 + 学期。

        旧版只用 ``课程代码_学期``，同一学期同一课程代码出现两条
        （重修 / 多个课程序号）时字典会互相覆盖，其中一条的变化永远检测不到。
        """
        seq = (g.get('seq') or '').strip()
        if not seq:
            seq = (g.get('name') or '').strip()
        return '%s|%s|%s' % ((g.get('code') or '').strip(), seq,
                             (g.get('semester') or '').strip())

    def detect_changes(self, old_grades, new_grades):
        """对比新旧成绩，返回 {'added': [...], 'modified': [...], 'removed': [...]}。"""
        old_map = {self._key(g): g for g in (old_grades or [])}
        new_map = {self._key(g): g for g in (new_grades or [])}

        added, modified = [], []
        for key, ng in new_map.items():
            og = old_map.get(key)
            if og is None:
                added.append(ng)
            elif og.get('score') != ng.get('score') or og.get('final') != ng.get('final'):
                modified.append({'old': og, 'new': ng})

        removed = [og for key, og in old_map.items() if key not in new_map]
        return {'added': added, 'modified': modified, 'removed': removed}
