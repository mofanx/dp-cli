# -*- coding:utf-8 -*-
"""Python Playwright API 表达式 → TypeScript 等价转换。

record-export / expect-gen 共享：定位器语义由 _pw_locator /
pw_candidates 产出（snake_case Python API），本模块只做 API 层
机械映射，不涉及定位器策略：

- get_by_role → getByRole；name="x" kwarg → { name: "x" } options 对象
- to_have_url → toHaveURL；not_to_be_checked → .not.toBeChecked()
- 断言前置 await（TS Playwright 无同步断言）
- 字符串字面量统一 json.dumps（双引号，TS/Python 通用）
"""
import ast
import json
import re

# 显式映射表：非机械驼峰可推断的（to_have_url→toHaveURL 中 URL
# 全大写、assertion 专有命名）先查表，其余走 snake→camel 推断
_EXPLICIT = {
    'get_by_test_id': 'getByTestId',
    'get_by_role': 'getByRole',
    'get_by_label': 'getByLabel',
    'get_by_placeholder': 'getByPlaceholder',
    'get_by_alt_text': 'getByAltText',
    'get_by_title': 'getByTitle',
    'get_by_text': 'getByText',
    'to_have_url': 'toHaveURL',
    'to_have_title': 'toHaveTitle',
    'to_have_text': 'toHaveText',
    'to_contain_text': 'toContainText',
    'to_have_value': 'toHaveValue',
    'to_have_count': 'toHaveCount',
    'to_have_class': 'toHaveClass',
    'to_have_id': 'toHaveId',
    'to_be_visible': 'toBeVisible',
    'to_be_hidden': 'toBeHidden',
    'to_be_checked': 'toBeChecked',
    'to_be_disabled': 'toBeDisabled',
    'to_be_enabled': 'toBeEnabled',
    'to_be_editable': 'toBeEditable',
    'to_have_accessible_name': 'toHaveAccessibleName',
    'to_have_accessible_description': 'toHaveAccessibleDescription',
    'to_have_attribute': 'toHaveAttribute',
    'to_have_css': 'toHaveCSS',
    'to_have_js_property': 'toHaveJSProperty',
    'select_option': 'selectOption',
    'set_input_files': 'setInputFiles',
}


def _camel(name: str) -> str:
    if name in _EXPLICIT:
        return _EXPLICIT[name]
    return re.sub(r'_([a-z])', lambda m: m.group(1).upper(), name)


def ts_str(s) -> str:
    """TS 字符串字面量（json.dumps 双引号，兼容任意 Unicode/转义）。"""
    return json.dumps(str(s), ensure_ascii=False)


def _ts_val(v) -> str:
    # json.dumps 对 str/bool/None/int 即 TS 字面量（true/false/null）
    return json.dumps(v, ensure_ascii=False)


def py_loc_to_ts(expr: str) -> str:
    """'page.get_by_role("button", name="x")' → 'page.getByRole("button", { name: "x" })'

    非 page. 前缀或解析失败原样返回（兜底不丢信息）。
    """
    if not expr or not expr.startswith('page.'):
        return expr or ''
    try:
        node = ast.parse(expr, mode='eval').body
        method = _camel(node.func.attr)
        args = [_ts_val(ast.literal_eval(a)) for a in node.args]
        if node.keywords:
            pairs = ', '.join(
                f'{k.arg}: {_ts_val(ast.literal_eval(k.value))}'
                for k in node.keywords)
            args.append('{ ' + pairs + ' }')
        return f'page.{method}({", ".join(args)})'
    except (ValueError, SyntaxError, AttributeError):
        return expr


_EXPECT_RE = re.compile(
    r'^expect\((.+)\)\.(not_)?([a-z_]+)\((.*)\)\s*$')


def py_expect_to_ts(code: str) -> str:
    """'expect(L).to_be_visible()' → 'await expect(L).toBeVisible()'

    not_to_be_checked() → await expect(L).not.toBeChecked()。
    断言参数为 _ps（json.dumps）字符串或 int，天然 TS 字面量，
    原样透传。不匹配的输入原样返回。
    """
    m = _EXPECT_RE.match(code.strip())
    if not m:
        return code
    inner, not_, method, args = m.groups()
    inner_ts = 'page' if inner.strip() == 'page' else py_loc_to_ts(inner)
    neg = '.not' if not_ else ''
    return f'await expect({inner_ts}){neg}.{_camel(method)}({args})'
