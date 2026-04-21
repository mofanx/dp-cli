# -*- coding:utf-8 -*-
"""Playwright 风格定位器解析器（纯 Python，不依赖浏览器）。

输入：去掉 'pw:' 前缀的表达式字符串，例如
  css=.sidebar >> role=listitem[name="Chat"] >> nth=2
  role=button[name=/^Sign/i]
  text="Login" >> has-text="今天"

输出：matcher 列表（list[dict]），交给 JS 逐段求值。

matcher dict 结构示例：
  {'type': 'role', 'role': 'button',
   'name': {'kind': 'exact', 'value': 'Submit'}}     # 或 None
  {'type': 'text',  'value': {'kind': 'substr', 'value': 'Login'}}
  {'type': 'label', 'value': {'kind': 'exact', 'value': 'Email'}}
  {'type': 'placeholder', 'value': {'kind': 'substr', 'value': 'search'}}
  {'type': 'alt' | 'title' | 'testid', 'value': {...}}
  {'type': 'css',   'value': '.btn'}
  {'type': 'xpath', 'value': '//div[@id="foo"]'}
  {'type': 'nth',   'index': 2}
  {'type': 'has-text', 'value': {'kind': 'substr', 'value': 'Price'}}
  {'type': 'visible', 'value': True}

值规格（value spec）：
  {'kind': 'exact',  'value': 'Submit'}                 # "Submit" / 'Submit'
  {'kind': 'substr', 'value': 'Sub'}                    # Submit（裸值）
  {'kind': 'regex',  'value': '^Sign', 'flags': 'i'}    # /^Sign/i
"""
import re


class PwParseError(ValueError):
    """pw: 表达式语法错误。"""


# 允许的顶层 chunk 类型（不含 >> 分段符）
_VALUE_TYPES = (
    'text', 'label', 'placeholder', 'alt', 'title', 'testid', 'has-text'
)
_RAW_TYPES = ('css', 'xpath')  # 保留原值，不再解析
_ALL_TYPES = _VALUE_TYPES + _RAW_TYPES + ('role', 'nth', 'visible')


def parse_pw(expr: str) -> list:
    """把 pw: 表达式解析为 matcher 列表。

    :raises PwParseError: 语法非法
    """
    if not expr or not expr.strip():
        raise PwParseError('空的 pw 表达式')
    chunks = _split_chunks(expr)
    if not chunks:
        raise PwParseError(f'未找到有效的 chunk: {expr!r}')
    return [_parse_chunk(c) for c in chunks]


# ─────────────────────────────────────────────────────────────────────────────
# chunk 切分：按 ' >> ' 分段，尊重引号和正则字面量
# ─────────────────────────────────────────────────────────────────────────────

def _split_chunks(expr: str) -> list:
    """把表达式按 >> 切成若干 chunk。

    规则：
      - 引号（' / "）内的 >> 不切
      - 正则字面量 /.../[flags] 内的 >> 不切
      - >> 前后可以有空格，也可以没有（但建议有）
    """
    parts = []
    buf = []
    i = 0
    n = len(expr)
    in_quote = None  # None | '"' | "'"
    in_regex = False
    while i < n:
        c = expr[i]
        if in_quote:
            buf.append(c)
            # 处理反斜杠转义
            if c == '\\' and i + 1 < n:
                buf.append(expr[i + 1])
                i += 2
                continue
            if c == in_quote:
                in_quote = None
            i += 1
            continue
        if in_regex:
            buf.append(c)
            if c == '\\' and i + 1 < n:
                buf.append(expr[i + 1])
                i += 2
                continue
            if c == '/':
                # 结束正则，继续吃 flags
                j = i + 1
                while j < n and expr[j].isalpha():
                    buf.append(expr[j])
                    j += 1
                in_regex = False
                i = j
                continue
            i += 1
            continue
        # 非引号、非正则态
        if c in ('"', "'"):
            in_quote = c
            buf.append(c)
            i += 1
            continue
        # 识别正则起始：=/.../ 这种，简化判断为前一个非空字符是 =
        if c == '/':
            # 往前看非空格字符
            k = len(buf) - 1
            while k >= 0 and buf[k] == ' ':
                k -= 1
            if k >= 0 and buf[k] == '=':
                in_regex = True
                buf.append(c)
                i += 1
                continue
        if c == '>' and i + 1 < n and expr[i + 1] == '>':
            parts.append(''.join(buf).strip())
            buf = []
            i += 2
            continue
        buf.append(c)
        i += 1

    if in_quote:
        raise PwParseError(f'引号未闭合: {expr!r}')
    if in_regex:
        raise PwParseError(f'正则字面量未闭合: {expr!r}')

    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


# ─────────────────────────────────────────────────────────────────────────────
# 单个 chunk 解析
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_RE = re.compile(r'^role=([a-zA-Z][\w-]*)(.*)$')
_ROLE_NAME_RE = re.compile(r'^\[name=(.+)\]$')
_NTH_RE = re.compile(r'^nth=(-?\d+)$')


def _parse_chunk(chunk: str) -> dict:
    s = chunk.strip()
    if not s:
        raise PwParseError('空 chunk')

    # visible / visible=true / visible=false
    if s == 'visible' or s == 'visible=true':
        return {'type': 'visible', 'value': True}
    if s == 'visible=false':
        return {'type': 'visible', 'value': False}

    # nth=N
    m = _NTH_RE.match(s)
    if m:
        return {'type': 'nth', 'index': int(m.group(1))}

    # role=X 或 role=X[name=...]
    m = _ROLE_RE.match(s)
    if m:
        role = m.group(1)
        rest = m.group(2).strip()
        if not rest:
            return {'type': 'role', 'role': role, 'name': None}
        nm = _ROLE_NAME_RE.match(rest)
        if not nm:
            raise PwParseError(
                f'role= 后只支持 [name=...] 过滤: {chunk!r}')
        name_spec = _parse_value(nm.group(1))
        return {'type': 'role', 'role': role, 'name': name_spec}

    # 文本类过滤：text= / label= / placeholder= / alt= / title= / testid= / has-text=
    for t in _VALUE_TYPES:
        prefix = t + '='
        if s.startswith(prefix):
            spec = _parse_value(s[len(prefix):])
            return {'type': t, 'value': spec}

    # css= / xpath=：原样保留
    for t in _RAW_TYPES:
        prefix = t + '='
        if s.startswith(prefix):
            raw = s[len(prefix):].strip()
            if not raw:
                raise PwParseError(f'{t}= 后面不能为空: {chunk!r}')
            return {'type': t, 'value': raw}

    raise PwParseError(
        f'无法识别的 pw chunk: {chunk!r}；'
        f'合法类型: {", ".join(_ALL_TYPES)}')


# ─────────────────────────────────────────────────────────────────────────────
# 值规格解析（value spec）
# ─────────────────────────────────────────────────────────────────────────────

_REGEX_RE = re.compile(r'^/(.+)/([a-z]*)$', re.DOTALL)


def _parse_value(raw: str) -> dict:
    """解析值字符串。

    规则（按优先级）：
      "..." 或 '...'     → exact 精确匹配，支持 \\ 转义引号
      /pattern/[flags]   → regex 正则（Playwright 风格）
      其它（裸值）        → substr 子串匹配
    """
    s = raw.strip()
    if not s:
        raise PwParseError('值不能为空')
    # 引号包裹 → exact
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        inner = s[1:-1]
        # 去反斜杠转义
        inner = inner.replace('\\' + s[0], s[0]).replace('\\\\', '\\')
        return {'kind': 'exact', 'value': inner}
    # /re/flags → regex
    m = _REGEX_RE.match(s)
    if m:
        pattern = m.group(1)
        flags = m.group(2) or ''
        # 校验 flags（JS 允许的）
        for f in flags:
            if f not in 'gimsuy':
                raise PwParseError(f'非法的正则 flag: {f!r} in {raw!r}')
        return {'kind': 'regex', 'value': pattern, 'flags': flags}
    # 裸值 → substr
    return {'kind': 'substr', 'value': s}
