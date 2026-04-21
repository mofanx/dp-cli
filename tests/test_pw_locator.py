# -*- coding:utf-8 -*-
"""pw: Playwright 风格定位器解析器测试（不依赖浏览器）。"""
import json
import pytest

from dp_cli.locators import parse_pw, build_pw_js, PwParseError
from dp_cli.locators.playwright import _split_chunks, _parse_value


# ─────────────────────────────────────────────────────────────────────────────
# 值规格 _parse_value
# ─────────────────────────────────────────────────────────────────────────────

def test_value_bare_is_substr():
    assert _parse_value('Login') == {'kind': 'substr', 'value': 'Login'}
    assert _parse_value('  Login  ') == {'kind': 'substr', 'value': 'Login'}


def test_value_double_quoted_is_exact():
    assert _parse_value('"Login"') == {'kind': 'exact', 'value': 'Login'}


def test_value_single_quoted_is_exact():
    assert _parse_value("'Login'") == {'kind': 'exact', 'value': 'Login'}


def test_value_quote_escape():
    # \" 被转为 "
    assert _parse_value(r'"say \"hi\""') == {'kind': 'exact', 'value': 'say "hi"'}


def test_value_regex_no_flags():
    r = _parse_value('/^Sign/')
    assert r == {'kind': 'regex', 'value': '^Sign', 'flags': ''}


def test_value_regex_with_flags():
    r = _parse_value('/^sign/i')
    assert r == {'kind': 'regex', 'value': '^sign', 'flags': 'i'}


def test_value_regex_illegal_flag():
    with pytest.raises(PwParseError):
        _parse_value('/foo/xz')


def test_value_empty_raises():
    with pytest.raises(PwParseError):
        _parse_value('')
    with pytest.raises(PwParseError):
        _parse_value('   ')


# ─────────────────────────────────────────────────────────────────────────────
# _split_chunks：尊重引号 + 正则
# ─────────────────────────────────────────────────────────────────────────────

def test_split_simple():
    assert _split_chunks('role=button') == ['role=button']
    assert _split_chunks('css=.a >> role=button') == ['css=.a', 'role=button']


def test_split_triple_chain():
    assert _split_chunks('css=.sidebar >> role=listitem >> nth=2') == \
        ['css=.sidebar', 'role=listitem', 'nth=2']


def test_split_inside_quotes_not_affected():
    # 引号内的 >> 不切
    assert _split_chunks('text="a >> b" >> role=link') == \
        ['text="a >> b"', 'role=link']


def test_split_inside_regex_not_affected():
    assert _split_chunks('text=/foo>>bar/ >> role=link') == \
        ['text=/foo>>bar/', 'role=link']


def test_split_unclosed_quote_raises():
    with pytest.raises(PwParseError):
        _split_chunks('text="unclosed >> role=link')


def test_split_unclosed_regex_raises():
    with pytest.raises(PwParseError):
        _split_chunks('text=/open >> role=link')


# ─────────────────────────────────────────────────────────────────────────────
# parse_pw 完整解析
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_role_no_name():
    assert parse_pw('role=button') == [{
        'type': 'role', 'role': 'button', 'name': None
    }]


def test_parse_role_with_exact_name():
    assert parse_pw('role=button[name="Submit"]') == [{
        'type': 'role', 'role': 'button',
        'name': {'kind': 'exact', 'value': 'Submit'}
    }]


def test_parse_role_with_regex_name():
    assert parse_pw('role=button[name=/^Sign/i]') == [{
        'type': 'role', 'role': 'button',
        'name': {'kind': 'regex', 'value': '^Sign', 'flags': 'i'}
    }]


def test_parse_role_with_substr_name():
    assert parse_pw('role=link[name=More]') == [{
        'type': 'role', 'role': 'link',
        'name': {'kind': 'substr', 'value': 'More'}
    }]


def test_parse_role_bad_filter_raises():
    with pytest.raises(PwParseError):
        parse_pw('role=button[unknown=foo]')


def test_parse_text_variants():
    assert parse_pw('text=Login') == [{
        'type': 'text', 'value': {'kind': 'substr', 'value': 'Login'}
    }]
    assert parse_pw('text="Login"') == [{
        'type': 'text', 'value': {'kind': 'exact', 'value': 'Login'}
    }]
    assert parse_pw('text=/login/i') == [{
        'type': 'text', 'value': {'kind': 'regex', 'value': 'login', 'flags': 'i'}
    }]


def test_parse_label_placeholder_alt_title_testid():
    assert parse_pw('label="Email"')[0]['type'] == 'label'
    assert parse_pw('placeholder=search')[0]['type'] == 'placeholder'
    assert parse_pw('alt="Logo"')[0]['type'] == 'alt'
    assert parse_pw('title="Close"')[0]['type'] == 'title'
    assert parse_pw('testid=submit')[0] == {
        'type': 'testid', 'value': {'kind': 'substr', 'value': 'submit'}
    }


def test_parse_nth_positive_and_negative():
    assert parse_pw('nth=0') == [{'type': 'nth', 'index': 0}]
    assert parse_pw('nth=3') == [{'type': 'nth', 'index': 3}]
    assert parse_pw('nth=-1') == [{'type': 'nth', 'index': -1}]


def test_parse_has_text():
    assert parse_pw('has-text="Price"') == [{
        'type': 'has-text', 'value': {'kind': 'exact', 'value': 'Price'}
    }]


def test_parse_visible():
    assert parse_pw('visible') == [{'type': 'visible', 'value': True}]
    assert parse_pw('visible=true') == [{'type': 'visible', 'value': True}]
    assert parse_pw('visible=false') == [{'type': 'visible', 'value': False}]


def test_parse_css_xpath_raw():
    assert parse_pw('css=.btn.primary')[0] == {
        'type': 'css', 'value': '.btn.primary'
    }
    assert parse_pw('xpath=//a[@id="x"]')[0] == {
        'type': 'xpath', 'value': '//a[@id="x"]'
    }


def test_parse_chain_real_world():
    result = parse_pw('css=.sidebar >> role=listitem[name="Chat"] >> nth=2')
    assert len(result) == 3
    assert result[0] == {'type': 'css', 'value': '.sidebar'}
    assert result[1] == {
        'type': 'role', 'role': 'listitem',
        'name': {'kind': 'exact', 'value': 'Chat'}
    }
    assert result[2] == {'type': 'nth', 'index': 2}


def test_parse_chain_has_text():
    result = parse_pw('css=li >> has-text="Python"')
    assert result == [
        {'type': 'css', 'value': 'li'},
        {'type': 'has-text', 'value': {'kind': 'exact', 'value': 'Python'}},
    ]


def test_parse_empty_raises():
    with pytest.raises(PwParseError):
        parse_pw('')
    with pytest.raises(PwParseError):
        parse_pw('   ')


def test_parse_unknown_chunk_raises():
    with pytest.raises(PwParseError):
        parse_pw('wat=foo')


def test_parse_empty_css_raises():
    with pytest.raises(PwParseError):
        parse_pw('css=')


# ─────────────────────────────────────────────────────────────────────────────
# build_pw_js：嵌入正确性
# ─────────────────────────────────────────────────────────────────────────────

def test_build_pw_js_embeds_matchers():
    matchers = [{'type': 'css', 'value': '.btn'}]
    js = build_pw_js(matchers)
    # 模板占位符必须被替换
    assert '__MATCHERS_JSON__' not in js
    # 必须含 JSON.parse 以及 matchers 内容
    assert 'JSON.parse' in js
    assert '.btn' in js


def test_build_pw_js_chinese_safe():
    matchers = [{'type': 'text', 'value': {'kind': 'exact', 'value': '新建对话'}}]
    js = build_pw_js(matchers)
    assert '新建对话' in js  # 不应被 ASCII 转义
    # 可以直接 eval 出 matchers
    # 提取 JSON.parse 第一个参数（字符串字面量）
    import re
    m = re.search(r'JSON\.parse\((".*?(?<!\\)")\)', js, re.DOTALL)
    assert m, 'JSON.parse literal not found'
    inner = json.loads(m.group(1))  # JS 字符串字面量等同 JSON 字符串
    parsed = json.loads(inner)
    assert parsed == matchers


def test_build_pw_js_script_injection_safe():
    # 值里包含 </script> 等危险字符：不应产生能被 HTML 解析器关闭 script 的情况
    matchers = [{'type': 'text', 'value': {'kind': 'exact', 'value': '</script><script>'}}]
    js = build_pw_js(matchers)
    # JSON 字符串里的 / 会被保留，但不会破坏 JS 语法；核心是能 round-trip 正确
    import re
    m = re.search(r'JSON\.parse\((".*?(?<!\\)")\)', js, re.DOTALL)
    inner = json.loads(m.group(1))
    assert json.loads(inner) == matchers


def test_build_pw_js_complex_chain_roundtrip():
    matchers = parse_pw('css=.sidebar >> role=button[name=/^New/i] >> nth=0')
    js = build_pw_js(matchers)
    import re
    m = re.search(r'JSON\.parse\((".*?(?<!\\)")\)', js, re.DOTALL)
    inner = json.loads(m.group(1))
    assert json.loads(inner) == matchers


# ─────────────────────────────────────────────────────────────────────────────
# resolve_locator 集成（mock page）
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_locator_pw_success():
    from unittest.mock import MagicMock, patch
    from dp_cli.commands import _utils

    page = MagicMock()
    page.run_js.return_value = 'dpabc123def456'

    result = _utils.resolve_locator(
        'pw:role=button[name="Submit"]', session='x', page=page)
    assert result == '@data-dp-ref=dpabc123def456'
    # 必须传入 JS（不是 matchers list）
    called = page.run_js.call_args
    assert 'JSON.parse' in called.args[0]


def test_resolve_locator_pw_not_found_exits():
    from unittest.mock import MagicMock, patch
    from dp_cli.commands import _utils

    page = MagicMock()
    page.run_js.return_value = None

    with patch.object(_utils, 'error') as mock_err:
        mock_err.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            _utils.resolve_locator('pw:role=button', session='x', page=page)
        assert 'PW_NOT_FOUND' in str(mock_err.call_args)


def test_resolve_locator_pw_syntax_error_exits():
    from unittest.mock import MagicMock, patch
    from dp_cli.commands import _utils

    with patch.object(_utils, 'error') as mock_err:
        mock_err.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            _utils.resolve_locator('pw:', session='x', page=MagicMock())
        assert 'PW_SYNTAX' in str(mock_err.call_args)


def test_resolve_locator_non_pw_not_affected():
    # 没有 pw: 前缀的定位器不应调用 page.run_js
    from unittest.mock import MagicMock
    from dp_cli.commands import _utils

    page = MagicMock()
    result = _utils.resolve_locator('css:.btn', session='x', page=page)
    assert result == 'css:.btn'
    page.run_js.assert_not_called()
