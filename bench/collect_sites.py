#!/usr/bin/env python3
"""采集器——页面→(state,goal,chosen_ref) 录制 schema 同构样本。

无外网依赖：优先 tests/fixtures/*.html 静态解析交互元素（可交互标签
+a/role 属性近似 a11y）；有 Chrome 时可扩展走 dpx snapshot 真 a11y。
产出 → ~/.dp_cli/decide_train.jsonl 兼容格式，train_reranker 直接吃。
"""
import json, re, sys, time
from html.parser import HTMLParser
from pathlib import Path

FIX = Path(__file__).parent.parent / 'tests' / 'fixtures'
OUT = Path(__file__).parent / 'collected_train.jsonl'

_GOAL = {'button': '点击{name}', 'a': '点击{name}', 'link': '点击{name}',
         'input': '在{name}输入', 'textbox': '在{name}输入',
         'select': '在{name}中选择', 'combobox': '在{name}中选择',
         'checkbox': '勾选{name}', 'radio': '选择{name}'}
_ROLE = {'button': 'button', 'a': 'link', 'input': 'textbox',
         'select': 'combobox', 'textarea': 'textbox',
         'input_checkbox': 'checkbox', 'input_radio': 'radio'}


class _P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elems = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        role = a.get('role') or _ROLE.get(
            f'input_{a.get("type")}' if tag == 'input' and a.get('type')
            else tag)
        if not role:
            return
        name = (a.get('aria-label') or a.get('placeholder')
                or a.get('value') or a.get('title') or '')
        self._cur = {'role': role, 'name': name,
                     'hidden': 'hidden' in a or a.get('aria-hidden') == 'true'}

    def handle_data(self, d):
        if self._cur is not None and not self._cur['name'] and d.strip():
            self._cur['name'] = d.strip()[:40]

    def handle_endtag(self, tag):
        if self._cur is not None:
            if self._cur['name'] and not self._cur['hidden']:
                self.elems.append(self._cur)
            self._cur = None


def collect(path: Path) -> list:
    """一个页面 → 每条可交互元素一条样本（goal=模板，chosen=该元素）。"""
    p = _P()
    p.feed(path.read_text(encoding='utf-8', errors='replace'))
    if len(p.elems) < 2:
        return []
    refs = [{'ref': i + 1, 'role': e['role'], 'name': e['name']}
            for i, e in enumerate(p.elems)]
    rows = []
    for e in p.elems:
        tmpl = _GOAL.get(e['role']) or _GOAL.get(e['role'] == 'link' and 'a')
        if not tmpl:
            continue
        rows.append({'ts': int(time.time()), 'session': 'collect',
                     'src': 'collector', 'page': path.name,
                     'goal': tmpl.format(name=e['name']),
                     'state': {'refs': refs},
                     'chosen_ref': refs[p.elems.index(e)]['ref']})
    return rows


def main():
    paths = sorted(FIX.glob('*.html')) + [Path(a) for a in sys.argv[1:]]
    all_rows = []
    for p in paths:
        rs = collect(p)
        all_rows += rs
        print(f'{p.name}: {len(rs)} 样本')
    OUT.write_text('\n'.join(json.dumps(r, ensure_ascii=False)
                             for r in all_rows) + '\n', encoding='utf-8')
    print(f'→ {OUT} 共 {len(all_rows)} 条（schema=decide_train 同构）')


if __name__ == '__main__':
    main()
