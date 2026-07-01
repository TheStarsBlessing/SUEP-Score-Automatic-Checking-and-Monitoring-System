import os
import re
from lxml import etree
from ids import IdsAuth

def semester_str_to_id(sem_str):
    """
    将形如 "2025-2026.2" 的字符串转换为数字ID
    基准: 2024-2025.2 -> 364，每个学期递增20
    """
    match = re.match(r'(\d{4})-(\d{4})\.(\d)', sem_str)
    if not match:
        raise ValueError(f"无效的学期格式: {sem_str}，应为 2025-2026.2")
    start_year = int(match.group(1))
    end_year = int(match.group(2))
    semester = int(match.group(3))
    if end_year != start_year + 1:
        raise ValueError("学年跨度必须为1年")
    if semester not in (1, 2):
        raise ValueError("学期必须为1或2")
    # 基准: 2024-2025.2 -> 364
    base_year = 2024
    base_semester = 2
    base_id = 364
    semester_diff = (start_year - base_year) * 2 + (semester - base_semester)
    return base_id + 20 * semester_diff

class GradeFetcher:
    def __init__(self, ids: IdsAuth):
        self.ids = ids
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }

    def fetch_grades(self, sem_id: int):
        """传入数字ID"""
        url = f'https://jw.shiep.edu.cn/eams/teach/grade/course/person!search.action?semesterId={sem_id}&projectType='
        resp = self.ids.get(url, headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f'获取成绩失败，状态码 {resp.status_code}')
        return self._parse_grades(resp.text)

    def _parse_grades(self, html: str) -> list:
        tree = etree.HTML(html)
        rows = tree.xpath('//table[@class="gridtable"]/tbody/tr')
        grades = []
        for row in rows:
            tds = row.xpath('./td')
            if len(tds) < 9:
                continue
            semester = tds[0].xpath('string()').strip()
            code = tds[1].xpath('string()').strip()
            seq = tds[2].xpath('string()').strip()
            name = tds[3].xpath('string()').strip()
            category = tds[4].xpath('string()').strip()
            credit = tds[5].xpath('string()').strip()
            score = tds[6].xpath('string()').strip()
            final = tds[7].xpath('string()').strip()
            gpa = tds[8].xpath('string()').strip()
            grades.append({
                'semester': semester,
                'code': code,
                'seq': seq,
                'name': name,
                'category': category,
                'credit': credit,
                'score': score,
                'final': final,
                'gpa': gpa
            })
        return grades

    @staticmethod
    def _get_filename(semester_str=None):
        """根据学期字符串生成文件名，若未提供则使用默认"""
        if semester_str:
            # 替换不安全的文件名字符
            safe = semester_str.replace(' ', '_').replace('.', '_')
            return f'grade_data_{safe}.txt'
        return 'grade_data.txt'

    @staticmethod
    def save_to_file(grades: list, semester_str=None, filename=None):
        """保存成绩到文件，可指定学期或直接指定文件名"""
        if filename is None:
            filename = GradeFetcher._get_filename(semester_str)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('学年学期\t课程代码\t课程序号\t课程名称\t课程类别\t学分\t正考总评成绩\t最终\t绩点\n')
            for g in grades:
                line = f"{g['semester']}\t{g['code']}\t{g['seq']}\t{g['name']}\t{g['category']}\t{g['credit']}\t{g['score']}\t{g['final']}\t{g['gpa']}\n"
                f.write(line)

    @staticmethod
    def load_from_file(semester_str=None, filename=None):
        """从文件加载成绩，可指定学期或直接指定文件名"""
        if filename is None:
            filename = GradeFetcher._get_filename(semester_str)
        if not os.path.exists(filename):
            return []
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()[1:]
        grades = []
        for line in lines:
            parts = line.strip().split('\t')
            if len(parts) == 9:
                grades.append({
                    'semester': parts[0],
                    'code': parts[1],
                    'seq': parts[2],
                    'name': parts[3],
                    'category': parts[4],
                    'credit': parts[5],
                    'score': parts[6],
                    'final': parts[7],
                    'gpa': parts[8]
                })
        return grades

    def detect_changes(self, old_grades: list, new_grades: list) -> dict:
        old_map = {f"{g['code']}_{g['semester']}": g for g in old_grades}
        new_map = {f"{g['code']}_{g['semester']}": g for g in new_grades}
        added = []
        modified = []
        for key, ng in new_map.items():
            if key not in old_map:
                added.append(ng)
            else:
                og = old_map[key]
                if og['score'] != ng['score'] or og['final'] != ng['final']:
                    modified.append({'old': og, 'new': ng})
        return {'added': added, 'modified': modified}