# -*- coding:utf-8 -*-
"""
命令模块关键测试。

侧重:
  - commands/_utils.py 的工具/定位器解析函数(最大共享模块)
  - output.py 的输出与格式化函数
  - 通过 Click CliRunner + mock get_browser 测 CLI 入口与 element 命令路径

全程不依赖真实浏览器 / 网络。
"""
import json

import pytest
from click.testing import CliRunner

from dp_cli.commands import _utils
from dp_cli import output


# ─────────────────────────────────────────────────────────────────────────────
# 假对象
# ─────────────────────────────────────────────────────────────────────────────

class FakeEle:
    def __init__(self, tag="button", attrs=None, raw_text="", clicked=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.raw_text = raw_text
        self._clicked = clicked if clicked is not None else []

        class _Click:
            def __init__(self, outer):
                self._outer = outer
            def __call__(self, *a, **k):
                self._outer._clicked.append("click")
            def js(self, *a, **k):
                self._outer._clicked.append("js")
        self.click = _Click(self)


class NoneEle:
    pass
NoneEle.__name__ = "NoneElement"


class FakePage:
    def __init__(self, ele=None, run_cdp_return=None, run_js_return=None):
        self._ele = ele
        self.latest_tab = None
        self.url = "https://example.com"
        self.title = "Demo"
        self._run_cdp_return = run_cdp_return
        self._run_js_return = run_js_return

        class _States:
            ready_state = "complete"
        self.states = _States()

    def ele(self, locator, index=1, timeout=10):
        return self._ele if self._ele is not None else NoneEle()

    def run_cdp(self, method, **kw):
        if isinstance(self._run_cdp_return, Exception):
            raise self._run_cdp_return
        return self._run_cdp_return

    def run_js(self, script, *a, **k):
        if isinstance(self._run_js_return, Exception):
            raise self._run_js_return
        return self._run_js_return


# ─────────────────────────────────────────────────────────────────────────────
# _utils.normalize_url
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_url_empty():
    assert _utils.normalize_url("") == ""


@pytest.mark.unit
def test_normalize_url_adds_https():
    assert _utils.normalize_url("example.com") == "https://example.com"


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://x.com", "https://x.com", "file:///tmp/a.html",
])
def test_normalize_url_keeps_scheme(url):
    assert _utils.normalize_url(url) == url


# ─────────────────────────────────────────────────────────────────────────────
# _utils.normalize_locator
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("loc", [
    "css:.a", "xpath://div", "text:登录", "@name=user", "ref:3", "pw:role=button",
])
def test_normalize_locator_known_prefix_unchanged(loc):
    assert _utils.normalize_locator(loc) == loc


@pytest.mark.unit
def test_normalize_locator_xpath_start():
    assert _utils.normalize_locator("//div[@id='a']") == "xpath://div[@id='a']"


@pytest.mark.unit
@pytest.mark.parametrize("loc", ["#id", ".cls"])
def test_normalize_locator_css_id_class(loc):
    assert _utils.normalize_locator(loc) == f"css:{loc}"


@pytest.mark.unit
@pytest.mark.parametrize("loc", ["div.cls", "a[href]", "h1#title"])
def test_normalize_locator_tag_selector(loc):
    assert _utils.normalize_locator(loc) == f"css:{loc}"


@pytest.mark.unit
def test_normalize_locator_combinator():
    assert _utils.normalize_locator("ul > li").startswith("css:")


@pytest.mark.unit
def test_normalize_locator_plain_text_unchanged():
    # 纯文本(无前缀/无 CSS 特征)→ 原样返回
    assert _utils.normalize_locator("登录按钮") == "登录按钮"


# ─────────────────────────────────────────────────────────────────────────────
# _utils.resolve_locator
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_resolve_locator_plain_normalizes():
    assert _utils.resolve_locator("#id", "default") == "css:#id"


@pytest.mark.unit
def test_resolve_locator_ref_no_refs_exits(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs", lambda s: {})
    with pytest.raises(SystemExit):
        _utils.resolve_locator("ref:1", "default")


@pytest.mark.unit
def test_resolve_locator_ref_not_found_exits(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs", lambda s: {"1": {"locator": "#a"}})
    with pytest.raises(SystemExit):
        _utils.resolve_locator("ref:99", "default")


@pytest.mark.unit
def test_resolve_locator_ref_falls_back_to_locator(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs",
                        lambda s: {"1": {"locator": "#real", "name": "x"}})
    # 无 backendNodeId → 回退到保存的 locator
    assert _utils.resolve_locator("ref:1", "default", page=None) == "#real"


@pytest.mark.unit
def test_resolve_locator_ref_falls_back_to_name_text(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs",
                        lambda s: {"1": {"locator": "t:button", "name": "登录"}})
    # locator 以 t: 开头被忽略 → 用 name 做 text 定位
    assert _utils.resolve_locator("ref:1", "default", page=None) == "text:登录"


@pytest.mark.unit
def test_resolve_locator_ref_unresolvable_exits(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs",
                        lambda s: {"1": {"role": "button"}})
    with pytest.raises(SystemExit):
        _utils.resolve_locator("ref:1", "default", page=None)


@pytest.mark.unit
def test_resolve_locator_ref_backend_id_marks(monkeypatch):
    monkeypatch.setattr(_utils, "load_refs",
                        lambda s: {"1": {"backendNodeId": 42, "name": "x"}})
    page = FakePage(run_cdp_return={"object": {"objectId": "obj-1"}})
    out = _utils.resolve_locator("ref:1", "default", page=page)
    assert out.startswith("@data-dp-ref=")


# ─────────────────────────────────────────────────────────────────────────────
# _utils._mark_element_by_backend_id
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_mark_element_success():
    page = FakePage(run_cdp_return={"object": {"objectId": "obj-1"}})
    marker = _utils._mark_element_by_backend_id(page, 10)
    assert marker and marker.startswith("dp")


@pytest.mark.unit
def test_mark_element_no_object_returns_none():
    page = FakePage(run_cdp_return={"object": {}})
    assert _utils._mark_element_by_backend_id(page, 10) is None


@pytest.mark.unit
def test_mark_element_cdp_error_returns_none():
    page = FakePage(run_cdp_return=RuntimeError("cdp down"))
    assert _utils._mark_element_by_backend_id(page, 10) is None


# ─────────────────────────────────────────────────────────────────────────────
# _utils._resolve_pw
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_resolve_pw_syntax_error_exits(monkeypatch):
    from dp_cli import locators

    def bad_parse(expr):
        raise locators.PwParseError("bad")
    monkeypatch.setattr(_utils, "parse_pw", bad_parse, raising=False)
    # parse_pw 是在函数内 from dp_cli.locators import parse_pw,需 patch 源
    monkeypatch.setattr(locators, "parse_pw", bad_parse)
    with pytest.raises(SystemExit):
        _utils._resolve_pw("role=button", "default", FakePage())


@pytest.mark.unit
def test_resolve_pw_not_found_exits(monkeypatch):
    from dp_cli import locators
    monkeypatch.setattr(locators, "parse_pw", lambda e: [("role", "button")])
    monkeypatch.setattr(locators, "build_pw_js", lambda m: "return null;")
    page = FakePage(run_js_return=None)  # 未匹配
    with pytest.raises(SystemExit):
        _utils._resolve_pw("role=button", "default", page)


@pytest.mark.unit
def test_resolve_pw_success(monkeypatch):
    from dp_cli import locators
    monkeypatch.setattr(locators, "parse_pw", lambda e: [("role", "button")])
    monkeypatch.setattr(locators, "build_pw_js", lambda m: "return 'mk';")
    page = FakePage(run_js_return="mk123")
    out = _utils._resolve_pw("role=button", "default", page)
    assert out == "@data-dp-ref=mk123"


# ─────────────────────────────────────────────────────────────────────────────
# _utils.records_to_csv
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_records_to_csv_empty():
    assert _utils.records_to_csv([]) == ""


@pytest.mark.unit
def test_records_to_csv_basic():
    csv_str = _utils.records_to_csv([{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
    assert "a,b" in csv_str
    assert "1,x" in csv_str


@pytest.mark.unit
def test_records_to_csv_list_joined():
    csv_str = _utils.records_to_csv([{"tags": ["p", "q"]}])
    assert "p|q" in csv_str


# ─────────────────────────────────────────────────────────────────────────────
# output.ok / output.error
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_output_ok(capsys):
    output.ok({"x": 1}, msg="done")
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["data"] == {"x": 1}
    assert out["message"] == "done"


@pytest.mark.unit
def test_output_error_exits(capsys):
    with pytest.raises(SystemExit):
        output.error("boom", code="E1", detail="d")
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert out["code"] == "E1"
    assert out["detail"] == "d"


# ─────────────────────────────────────────────────────────────────────────────
# output.format_element / format_page_info
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_format_element_basic():
    ele = FakeEle(tag="a", attrs={"id": "lnk"}, raw_text="点我")
    info = output.format_element(ele)
    assert info["tag"] == "a"
    assert info["text"] == "点我"
    assert info["loc"] == "#lnk"


@pytest.mark.unit
def test_format_page_info():
    info = output.format_page_info(FakePage())
    assert info["url"] == "https://example.com"
    assert info["title"] == "Demo"
    assert info["ready_state"] == "complete"


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口 (CliRunner)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.unit
def test_cli_help(runner):
    from dp_cli.main import cli
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "dp-cli" in result.output


@pytest.mark.unit
def test_cli_version(runner):
    from dp_cli.main import cli
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_cli_no_subcommand_shows_help(runner):
    from dp_cli.main import cli
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage" in result.output or "快速开始" in result.output


@pytest.mark.unit
def test_click_command_success(runner, monkeypatch):
    """click 命令 happy path:mock get_browser + 返回可点击元素。"""
    from dp_cli.main import cli
    ele = FakeEle()
    monkeypatch.setattr(_utils, "get_browser", lambda s: FakePage(ele=ele))
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "text:登录"])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["status"] == "ok"
    assert "click" in ele._clicked


@pytest.mark.unit
def test_click_command_element_not_found(runner, monkeypatch):
    """click 命令:元素未找到 → error 退出码 1。"""
    from dp_cli.main import cli
    monkeypatch.setattr(_utils, "get_browser", lambda s: FakePage(ele=None))
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "text:不存在"])
    assert result.exit_code == 1
    out = json.loads(result.output)
    assert out["status"] == "error"
    assert out["code"] == "ELEMENT_NOT_FOUND"


@pytest.mark.unit
def test_click_command_no_session_errors(runner, monkeypatch):
    """click 命令:get_browser 抛错 → SESSION_NOT_FOUND。"""
    from dp_cli.main import cli

    def boom(s):
        raise ConnectionError("no browser")
    monkeypatch.setattr(_utils, "get_browser", boom)
    result = runner.invoke(cli, ["click", "text:x"])
    assert result.exit_code == 1
    out = json.loads(result.output)
    assert out["status"] == "error"
    assert out["code"] == "SESSION_NOT_FOUND"
