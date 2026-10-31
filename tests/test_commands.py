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
            def at(self, *a, **k):
                self._outer._clicked.append("at")
        self.click = _Click(self)

    def run_js(self, script, *a, **k):
        self._clicked.append(f"js:{script}")


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
    # 输出应为纯版本号（不含 Usage/帮助文本）
    assert result.output.strip() and "Usage" not in result.output


@pytest.mark.unit
def test_cli_version_short_flag(runner):
    """-V 与 --version 同义（pytest/git -V 大写惯例），-v 仍是 verbose。"""
    from dp_cli.main import cli
    r_long = runner.invoke(cli, ["--version"])
    r_short = runner.invoke(cli, ["-V"])
    assert r_short.exit_code == 0
    assert r_short.output == r_long.output
    # -v 不回归：仍走 verbose 分支（无子命令时输出 help，而非版本号）
    r_verbose = runner.invoke(cli, ["-v"])
    assert r_verbose.exit_code == 0
    assert "Usage" in r_verbose.output
    assert r_verbose.output.strip() != r_short.output.strip()


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
def test_click_command_by_js(runner, monkeypatch):
    """click --by-js：走 ele.run_js('this.click()') 而非已删的 clicker.js。"""
    from dp_cli.main import cli
    ele = FakeEle()
    monkeypatch.setattr(_utils, "get_browser", lambda s: FakePage(ele=ele))
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "css:#b", "--by-js"])
    assert result.exit_code == 0
    assert "js:this.click()" in ele._clicked


@pytest.mark.unit
def test_click_command_by_mouse(runner, monkeypatch):
    """click --by-mouse：走 click.at() 坐标级鼠标（含 down/up）。"""
    from dp_cli.main import cli
    ele = FakeEle()
    monkeypatch.setattr(_utils, "get_browser", lambda s: FakePage(ele=ele))
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "css:#d", "--by-mouse"])
    assert result.exit_code == 0
    assert "at" in ele._clicked


@pytest.mark.unit
def test_click_command_by_js_by_mouse_mutex(runner, monkeypatch):
    """--by-js 与 --by-mouse 互斥。"""
    from dp_cli.main import cli
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "css:#b", "--by-js", "--by-mouse"])
    assert result.exit_code == 2
    assert json.loads(result.output)["code"] == "INVALID_ARGS"


@pytest.mark.unit
def test_click_command_element_not_found(runner, monkeypatch):
    """click 命令:元素未找到 → error 退出码 4（not_found 分级）。"""
    from dp_cli.main import cli
    monkeypatch.setattr(_utils, "get_browser", lambda s: FakePage(ele=None))
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "text:不存在"])
    assert result.exit_code == 4
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
    monkeypatch.setattr(_utils, "load_session", lambda s: {})
    result = runner.invoke(cli, ["click", "text:x"])
    assert result.exit_code == 3
    out = json.loads(result.output)
    assert out["status"] == "error"
    assert out["code"] == "SESSION_NOT_FOUND"


@pytest.mark.unit
def test_session_disconnected_when_session_exists(runner, monkeypatch):
    """会话文件存在但 get_browser 抛错 → SESSION_DISCONNECTED（与
    会话不存在分流）。"""
    from dp_cli.main import cli

    monkeypatch.setattr(_utils, "load_session",
                        lambda s: {'port': 9222, 'user_connected': True})
    monkeypatch.setattr(_utils, "get_browser",
                        lambda s: (_ for _ in ()).throw(
                            ConnectionError("user browser dead")))
    result = runner.invoke(cli, ["click", "text:x"])
    assert result.exit_code == 3
    out = json.loads(result.output)
    assert out["code"] == "SESSION_DISCONNECTED"


@pytest.mark.unit
def test_session_disconnected_exit_code(runner):
    """SESSION_DISCONNECTED 注册为 EXIT_CONNECT=3（连接类）。"""
    assert output._exit_code_for('SESSION_DISCONNECTED') == 3


# ─────────────────────────────────────────────────────────────────────────────
# t3 增强回归：storage / network / keyboard / record
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_cookie_list_domain_filter(runner, monkeypatch):
    """cookie-list --domain 按域名过滤（旧版忽略该参数）。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            assert all_domains and all_info
            return [
                {'name': 'a', 'value': '1', 'domain': '.example.com', 'path': '/'},
                {'name': 'b', 'value': '2', 'domain': '.other.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--domain', 'example.com'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'
    assert [c['name'] for c in out['data']['cookies']] == ['a']
    assert out['data']['count'] == 1


@pytest.mark.unit
def test_cookie_list_url_filter(runner, monkeypatch):
    """cookie-list --url 按 URL host 匹配 cookie 域名（含子域）。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1', 'domain': '.example.com', 'path': '/'},
                {'name': 'b', 'value': '2', 'domain': '.other.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--url', 'https://sub.example.com/p'])
    out = json.loads(result.output)
    assert [c['name'] for c in out['data']['cookies']] == ['a']


@pytest.mark.unit
def test_sessionstorage_delete(runner, monkeypatch):
    """sessionstorage-delete 命令存在且调用 removeItem。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    calls = []

    class P:
        def run_js(self, script, *a, **k):
            calls.append(script)
            return None

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['sessionstorage-delete', 'mykey'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'
    assert 'sessionStorage.removeItem("mykey")' in calls[0]


@pytest.mark.unit
def test_listen_stop_always_stops(runner, monkeypatch):
    """listen-stop：wait 抛异常时也必须执行 listen.stop()（旧版会残留监听）。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    class _Listen:
        listening = True

        def __init__(self):
            self.stopped = False

        def wait(self, count, timeout, fit_count):
            raise TimeoutError('no packet')

        def stop(self):
            self.stopped = True

    class P:
        def __init__(self):
            self.listen = _Listen()

    page = P()
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['listen-stop'])
    assert result.exit_code == 1
    assert json.loads(result.output)['code'] == 'LISTEN_FAILED'
    assert page.listen.stopped


@pytest.mark.unit
def test_http_get_output_saves_json_response(runner, monkeypatch, tmp_path):
    """http-get --output：JSON 响应也应写入文件（旧版只在非 JSON 时保存）。"""
    import DrissionPage
    from dp_cli.main import cli

    class Resp:
        status_code = 200
        content = b'{"x":1}'
        text = '{"x":1}'

        def json(self):
            return {'x': 1}

    class SP:
        def __init__(self, session_or_options=None):
            self.url = ''
            self.response = None

        def get(self, url, timeout=None):
            self.url = url
            self.response = Resp()

    monkeypatch.setattr(DrissionPage, 'SessionPage', SP)
    out_file = tmp_path / 'resp.json'
    result = runner.invoke(
        cli, ['http-get', 'https://api.example.com', '--output', str(out_file)])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'
    assert out['data']['saved_to'] == str(out_file)
    assert out_file.read_bytes() == b'{"x":1}'


@pytest.mark.unit
def test_http_post_output_option(runner, monkeypatch, tmp_path):
    """http-post --output：与 http-get 对称，支持保存响应体。"""
    import DrissionPage
    from dp_cli.main import cli

    class Resp:
        status_code = 200
        content = b'ok-body'
        text = 'ok-body'

        def json(self):
            raise ValueError('not json')

    class SP:
        def __init__(self, session_or_options=None):
            self.url = ''
            self.response = None

        def post(self, url, **kw):
            self.url = url
            self.response = Resp()

    monkeypatch.setattr(DrissionPage, 'SessionPage', SP)
    out_file = tmp_path / 'resp.txt'
    result = runner.invoke(
        cli, ['http-post', 'https://api.example.com',
              '--data', '{"a":1}', '--output', str(out_file)])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['data']['saved_to'] == str(out_file)
    assert out_file.read_bytes() == b'ok-body'


@pytest.mark.unit
def test_press_function_key(runner, monkeypatch):
    """press F5 映射到 Keys.F5，而不是字面输入 'F5'。"""
    import dp_cli.commands.keyboard as kb_mod
    from DrissionPage._functions.keys import Keys
    from dp_cli.main import cli

    class _Actions:
        def __init__(self):
            self.calls = []

        def key_down(self, k):
            self.calls.append(('down', k))
            return self

        def key_up(self, k):
            self.calls.append(('up', k))
            return self

        def type(self, t):
            self.calls.append(('type', t))
            return self

    class P:
        def __init__(self):
            self.actions = _Actions()

    page = P()
    monkeypatch.setattr(kb_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['press', 'F5'])
    assert result.exit_code == 0
    assert page.actions.calls == [('type', Keys.F5)]


@pytest.mark.unit
def test_press_multi_modifier(runner, monkeypatch):
    """press Control+Shift+Delete：多修饰键依次 key_down、反向 key_up。"""
    import dp_cli.commands.keyboard as kb_mod
    from DrissionPage._functions.keys import Keys
    from dp_cli.main import cli

    class _Actions:
        def __init__(self):
            self.calls = []

        def key_down(self, k):
            self.calls.append(('down', k))
            return self

        def key_up(self, k):
            self.calls.append(('up', k))
            return self

        def type(self, t):
            self.calls.append(('type', t))
            return self

    class P:
        def __init__(self):
            self.actions = _Actions()

    page = P()
    monkeypatch.setattr(kb_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['press', 'Control+Shift+Delete'])
    assert result.exit_code == 0
    assert page.actions.calls == [
        ('down', Keys.CTRL), ('down', Keys.SHIFT),
        ('type', '\ue017'),
        ('up', Keys.SHIFT), ('up', Keys.CTRL),
    ]


@pytest.mark.unit
def test_press_single_modifier_unchanged(runner, monkeypatch):
    """press Control+A 行为保持兼容。"""
    import dp_cli.commands.keyboard as kb_mod
    from DrissionPage._functions.keys import Keys
    from dp_cli.main import cli

    class _Actions:
        def __init__(self):
            self.calls = []

        def key_down(self, k):
            self.calls.append(('down', k))
            return self

        def key_up(self, k):
            self.calls.append(('up', k))
            return self

        def type(self, t):
            self.calls.append(('type', t))
            return self

    class P:
        def __init__(self):
            self.actions = _Actions()

    page = P()
    monkeypatch.setattr(kb_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['press', 'Control+A'])
    assert result.exit_code == 0
    assert page.actions.calls == [
        ('down', Keys.CTRL), ('type', 'A'), ('up', Keys.CTRL),
    ]


@pytest.mark.unit
def test_record_active_tab_direct_get_tab(monkeypatch):
    """_get_active_page_for_record 直接 get_tab(targetId)，
    不再对每个 target 遍历全部 tab（旧版 O(targets×tabs) 次调用）。"""
    import dp_cli.session as sess_mod
    import dp_cli.commands.record as rec

    get_tab_calls = []

    class _Tab:
        def __init__(self, tid):
            self.tab_id = tid

    class B:
        latest_tab = None

        def run_cdp(self, m, **kw):
            assert m == 'Target.getTargets'
            return {'targetInfos': [
                {'attached': True, 'type': 'page', 'targetId': 'T1'},
                {'attached': False, 'type': 'page', 'targetId': 'T0'},
                {'attached': True, 'type': 'page', 'targetId': 'T2'},
            ]}

        def get_tab(self, tid):
            get_tab_calls.append(tid)
            return _Tab(tid)

    monkeypatch.setattr(sess_mod, 'get_browser', lambda s: B())
    tab = rec._get_active_page_for_record('default')
    assert tab.tab_id == 'T1'
    assert get_tab_calls == ['T1']


@pytest.mark.unit
def test_record_start_none_url_no_crash(runner, monkeypatch):
    """record-start：tab.url 为 None 时不再 TypeError（旧版 [:40] 崩溃）。"""
    import dp_cli.session as sess_mod
    import dp_cli.commands.record as rec
    from dp_cli.main import cli

    class _Tab:
        tab_id = 'T1'
        url = None
        title = None

    class B:
        latest_tab = None

        def run_cdp(self, m, **kw):
            return {'targetInfos': [
                {'attached': True, 'type': 'page', 'targetId': 'T1'}]}

        def get_tab(self, tid):
            return _Tab()

    monkeypatch.setattr(sess_mod, 'get_browser', lambda s: B())
    monkeypatch.setattr(rec, 'load_session', lambda s: {})
    monkeypatch.setattr(rec, 'save_session', lambda s, d: None)
    monkeypatch.setattr(rec, 'clear_recorded_actions', lambda p: None)
    monkeypatch.setattr(rec, 'inject_recorder', lambda p: {'injected': True})
    result = runner.invoke(cli, ['record-start'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['status'] == 'ok'


# ─────────────────────────────────────────────────────────────────────────────
# t4 复核缺陷回归：PageUp/PageDown 键名映射、_emit_response None 响应、
# cookie-list 域级匹配边界
# ─────────────────────────────────────────────────────────────────────────────

class _RecActions:
    """记录 actions 链式调用的假对象。"""

    def __init__(self):
        self.calls = []

    def key_down(self, k):
        self.calls.append(('down', k))
        return self

    def key_up(self, k):
        self.calls.append(('up', k))
        return self

    def type(self, t):
        self.calls.append(('type', t))
        return self


class _ActionsPage:
    def __init__(self):
        self.actions = _RecActions()


@pytest.mark.unit
@pytest.mark.parametrize('key_name,attr', [
    ('PageUp', 'PAGE_UP'), ('PageDown', 'PAGE_DOWN'),
    ('pgup', 'PAGE_UP'), ('pgdn', 'PAGE_DOWN'),
    ('page-up', 'PAGE_UP'), ('page-down', 'PAGE_DOWN'),
    ('Home', 'HOME'), ('End', 'END'), ('Insert', 'INSERT'),
])
def test_press_special_key_names(runner, monkeypatch, key_name, attr):
    """press PageUp/PageDown/Home/End 等映射到 Keys 枚举，
    不再回退为字面文本输入。"""
    import dp_cli.commands.keyboard as kb_mod
    from DrissionPage._functions.keys import Keys
    from dp_cli.main import cli

    page = _ActionsPage()
    monkeypatch.setattr(kb_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['press', key_name])
    assert result.exit_code == 0
    assert page.actions.calls == [('type', getattr(Keys, attr))]


@pytest.mark.unit
def test_press_control_pageup_no_literal(runner, monkeypatch):
    """Control+PageUp：主键必须是 Keys.PAGE_UP 而非字面 'PageUp'——
    旧版映射失败会带 Ctrl 输入文本，误触 Ctrl+P 等浏览器快捷键。"""
    import dp_cli.commands.keyboard as kb_mod
    from DrissionPage._functions.keys import Keys
    from dp_cli.main import cli

    page = _ActionsPage()
    monkeypatch.setattr(kb_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['press', 'Control+PageUp'])
    assert result.exit_code == 0
    assert page.actions.calls == [
        ('down', Keys.CTRL), ('type', Keys.PAGE_UP), ('up', Keys.CTRL),
    ]


@pytest.mark.unit
def test_emit_response_none_response_with_output(tmp_path):
    """_emit_response：response=None + --output 不崩溃，写空文件。"""
    import dp_cli.commands.network as net_mod

    out_file = tmp_path / 'empty.bin'
    result = net_mod._emit_response('https://x', None, str(out_file))
    assert result['status_code'] is None
    assert result['saved_to'] == str(out_file)
    assert out_file.read_bytes() == b''


@pytest.mark.unit
def test_emit_response_none_response_inline():
    """_emit_response：response=None 且无 --output → body=None，不崩。"""
    import dp_cli.commands.network as net_mod

    result = net_mod._emit_response('https://x', None, None)
    assert result['status_code'] is None
    assert result['body'] is None


@pytest.mark.unit
def test_cookie_list_domain_boundary_match(runner, monkeypatch):
    """cookie-list --domain：域级边界匹配，notexample.com 不被
    example.com 误中（旧版子串匹配缺陷）。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1', 'domain': '.example.com', 'path': '/'},
                {'name': 'b', 'value': '2', 'domain': 'sub.example.com', 'path': '/'},
                {'name': 'c', 'value': '3', 'domain': '.notexample.com', 'path': '/'},
                {'name': 'd', 'value': '4', 'domain': '.ample.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--domain', 'example.com'])
    out = json.loads(result.output)
    names = [c['name'] for c in out['data']['cookies']]
    assert names == ['a', 'b']


@pytest.mark.unit
def test_cookie_list_domain_subfilter(runner, monkeypatch):
    """cookie-list --domain sub.example.com：命中父域 cookie
    （.example.com 对该子域生效），仍不命中无关域。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1', 'domain': '.example.com', 'path': '/'},
                {'name': 'b', 'value': '2', 'domain': 'sub.example.com', 'path': '/'},
                {'name': 'c', 'value': '3', 'domain': '.other.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--domain', 'sub.example.com'])
    out = json.loads(result.output)
    names = [c['name'] for c in out['data']['cookies']]
    assert names == ['a', 'b']


@pytest.mark.unit
def test_cookie_list_url_notexample_rejected(runner, monkeypatch):
    """cookie-list --url https://notexample.com：不命中 .example.com cookie。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1', 'domain': '.example.com', 'path': '/'},
                {'name': 'c', 'value': '3', 'domain': '.notexample.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--url', 'https://notexample.com/'])
    out = json.loads(result.output)
    names = [c['name'] for c in out['data']['cookies']]
    assert names == ['c']


# ─────────────────────────────────────────────────────────────────────────────
# p0b 增强：console/error 捕获、dialog 应答、cookie-list 残余边界
# ─────────────────────────────────────────────────────────────────────────────


class _FakeDriver:
    """模拟 DrissionPage driver 的 CDP 回调注册接口。"""

    def __init__(self):
        self.callbacks = {}

    def set_callback(self, event, cb):
        self.callbacks[event] = cb


class _ConsolePage:
    def __init__(self, msgs=None):
        self.driver = _FakeDriver()
        self.cdp_calls = []
        if msgs is not None:
            self._dp_console_msgs = msgs

    def run_cdp(self, method, **kwargs):
        self.cdp_calls.append(method)
        return {}


@pytest.mark.unit
def test_console_listen_registers_cdp_callbacks(runner, monkeypatch):
    """console-listen：开启 Runtime/Log 域并注册三类回调。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['console-listen'])
    assert result.exit_code == 0, result.output
    assert 'Runtime.enable' in page.cdp_calls
    assert 'Log.enable' in page.cdp_calls
    assert hasattr(page, '_dp_console_msgs')
    cbs = page.driver.callbacks
    assert callable(cbs['Runtime.consoleAPICalled'])
    assert callable(cbs['Runtime.exceptionThrown'])
    assert callable(cbs['Log.entryAdded'])


@pytest.mark.unit
def test_console_callbacks_capture_messages(runner, monkeypatch):
    """console-listen 注册的回调应把 console/exception/log 事件写入队列。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    runner.invoke(cli, ['console-listen'])
    cbs = page.driver.callbacks
    cbs['Runtime.consoleAPICalled'](
        type='error', args=[{'value': 'boom'}], timestamp=111)
    cbs['Runtime.exceptionThrown'](
        exceptionDetails={'exception': {'description': 'uncaught'}},
        timestamp=222)
    cbs['Log.entryAdded'](
        entry={'level': 'warning', 'text': '404 not found',
               'source': 'network'},
        timestamp=333)
    msgs = list(page._dp_console_msgs)
    assert msgs[0]['level'] == 'error'
    assert msgs[0]['text'] == 'boom'
    assert msgs[0]['source'] == 'console'
    assert msgs[1]['level'] == 'error'
    assert msgs[1]['source'] == 'exception'
    assert 'uncaught' in msgs[1]['text']
    assert msgs[2]['level'] == 'warning'
    assert msgs[2]['source'] == 'network'


@pytest.mark.unit
def test_console_dump_returns_and_cleans(runner, monkeypatch):
    """console-dump：返回捕获事件、清空队列并移除回调。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage(
        msgs=[{'level': 'error', 'text': 'boom',
               'source': 'console', 'timestamp': 1}])
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['console-dump'])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out['status'] == 'ok'
    assert out['data']['count'] == 1
    assert out['data']['messages'][0]['text'] == 'boom'
    assert page._dp_console_msgs == []
    assert page.driver.callbacks['Runtime.consoleAPICalled'] is None
    assert page.driver.callbacks['Runtime.exceptionThrown'] is None
    assert page.driver.callbacks['Log.entryAdded'] is None


@pytest.mark.unit
def test_console_dump_keep(runner, monkeypatch):
    """console-dump --keep：保留事件与回调。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    runner.invoke(cli, ['console-listen'])
    page._dp_console_msgs.append(
        {'level': 'info', 'text': 'keep-me',
         'source': 'console', 'timestamp': 1})
    result = runner.invoke(cli, ['console-dump', '--keep'])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out['data']['count'] == 1
    assert len(page._dp_console_msgs) == 1
    assert page.driver.callbacks['Runtime.consoleAPICalled'] is not None


@pytest.mark.unit
def test_console_dump_empty(runner, monkeypatch):
    """console-dump 在未 listen 的页面上返回空列表而非报错。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['console-dump'])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out['data']['count'] == 0


@pytest.mark.unit
def test_dialog_accept_calls_handle_alert(runner, monkeypatch):
    """dialog-accept 传入文本时以 accept=True + send 调用 handle_alert。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    class P:
        def __init__(self):
            self.calls = []

        def handle_alert(self, accept=True, send=None,
                         timeout=None, next_one=False):
            self.calls.append((accept, send, timeout, next_one))
            return 'alert text'

    page = P()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['dialog-accept', 'hello'])
    assert result.exit_code == 0, result.output
    assert page.calls == [(True, 'hello', None, False)]


@pytest.mark.unit
def test_dialog_dismiss_calls_handle_alert(runner, monkeypatch):
    """dialog-dismiss 以 accept=False 调用 handle_alert。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    class P:
        def __init__(self):
            self.calls = []

        def handle_alert(self, accept=True, send=None,
                         timeout=None, next_one=False):
            self.calls.append((accept, send, timeout, next_one))
            return 'dismissed'

    page = P()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['dialog-dismiss'])
    assert result.exit_code == 0, result.output
    assert page.calls == [(False, None, None, False)]


@pytest.mark.unit
def test_cookie_list_url_strips_userinfo(runner, monkeypatch):
    """cookie-list --url 含 userinfo 时应剥离凭据再匹配 host。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1',
                 'domain': '.example.com', 'path': '/'},
                {'name': 'b', 'value': '2',
                 'domain': '.evil.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(
        cli, ['cookie-list', '--url',
              'https://user:pass@sub.example.com/path'])
    out = json.loads(result.output)
    names = [c['name'] for c in out['data']['cookies']]
    assert names == ['a']


@pytest.mark.unit
def test_cookie_list_domain_bare_suffix_rejected(runner, monkeypatch):
    """cookie-list --domain 裸顶级后缀（如 com）应被拒绝，避免命中全站。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):  # pragma: no cover
            return []

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--domain', 'com'])
    assert result.exit_code == 2
    out = json.loads(result.output)
    assert out['status'] == 'error'
    assert out['code'] == 'INVALID_ARGS'


# ─────────────────────────────────────────────────────────────────────────────
# p0c 工程合规回归：退出码分级 / debug 日志通道 / cookie 扩展字段
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_exit_code_tiers():
    """错误码 → 退出码分级：用法=2/连接=3/未找到=4/超时=5，
    未注册码与通用运行时失败保持 1（向后兼容）。"""
    from dp_cli.output import _exit_code_for
    assert _exit_code_for('INVALID_ARGS') == 2
    assert _exit_code_for('PW_SYNTAX') == 2
    assert _exit_code_for('SESSION_NOT_FOUND') == 3
    assert _exit_code_for('HTTP_FAILED') == 3
    assert _exit_code_for('ELEMENT_NOT_FOUND') == 4
    assert _exit_code_for('NO_REFS') == 4
    assert _exit_code_for('WAIT_TIMEOUT') == 5
    assert _exit_code_for('LISTEN_FAILED') == 1
    assert _exit_code_for('SOME_UNREGISTERED_CODE') == 1


@pytest.mark.unit
def test_error_json_contract_unchanged(capsys):
    """error() 的 JSON 契约不变：status/code/message/detail 字段照旧。"""
    import pytest as _pytest
    from dp_cli.output import error
    with _pytest.raises(SystemExit) as exc_info:
        error('msg', code='ELEMENT_NOT_FOUND', detail='d')
    assert exc_info.value.code == 4
    out = json.loads(capsys.readouterr().out)
    assert out == {'status': 'error', 'code': 'ELEMENT_NOT_FOUND',
                   'message': 'msg', 'detail': 'd'}


@pytest.mark.unit
def test_debug_channel_gated(capsys):
    """debug() 默认静默；启用后输出到 stderr，不污染 stdout。"""
    from dp_cli import logs
    logs.set_debug(False)
    logs.debug('hidden', ValueError('x'))
    captured = capsys.readouterr()
    assert captured.err == '' and captured.out == ''

    try:
        logs.set_debug(True)
        logs.debug('visible msg', ValueError('boom'))
        captured = capsys.readouterr()
        assert '[dp:debug] visible msg: ValueError: boom' in captured.err
        assert captured.out == ''
    finally:
        logs.set_debug(False)


@pytest.mark.unit
def test_verbose_flag_enables_debug(runner):
    """CLI 全局 -v/--verbose 开关启用 debug 通道。"""
    from dp_cli import logs
    from dp_cli.main import cli
    logs.set_debug(False)
    try:
        result = runner.invoke(cli, ['-v'])
        assert result.exit_code == 0
        assert logs.is_debug()
    finally:
        logs.set_debug(False)


@pytest.mark.unit
def test_cookie_list_extended_fields(runner, monkeypatch):
    """cookie-list 输出应包含 secure/httpOnly/sameSite/expires 字段。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [{'name': 'sid', 'value': '1', 'domain': '.example.com',
                     'path': '/', 'secure': True, 'httpOnly': True,
                     'sameSite': 'Lax', 'expires': 1893456000.0}]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list'])
    assert result.exit_code == 0
    c = json.loads(result.output)['data']['cookies'][0]
    assert c['secure'] is True
    assert c['httpOnly'] is True
    assert c['sameSite'] == 'Lax'
    assert c['expires'] == 1893456000.0


# ─────────────────────────────────────────────────────────────────────────────
# p1b 自动化能力回归：emulate / download / intercept
# ─────────────────────────────────────────────────────────────────────────────

class _CdpPage:
    """记录 run_cdp 调用的假页面。"""
    def __init__(self):
        self.calls = []
        self.url = 'https://x'

    def run_cdp(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return {}

    def run_js(self, js, **kw):
        return 'UA-CURRENT'


@pytest.mark.unit
def test_emulate_all_overrides(runner, monkeypatch):
    """emulate 全参数：5 类 CDP override 全部下发，配置写入会话。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    saved = {}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))

    result = runner.invoke(cli, [
        'emulate', '--viewport', '375x812', '--ua', 'MyUA',
        '--locale', 'zh-CN', '--timezone', 'Asia/Shanghai',
        '--geolocation', '39.9,116.4,50'])
    assert result.exit_code == 0, result.output
    cmds = [c[0] for c in page.calls]
    assert 'Emulation.setDeviceMetricsOverride' in cmds
    assert 'Emulation.setTimezoneOverride' in cmds
    assert 'Emulation.setLocaleOverride' in cmds
    assert 'Emulation.setUserAgentOverride' in cmds
    assert 'Emulation.setGeolocationOverride' in cmds
    metrics = dict(page.calls)['Emulation.setDeviceMetricsOverride']
    assert metrics['width'] == 375 and metrics['height'] == 812
    geo = dict(page.calls)['Emulation.setGeolocationOverride']
    assert geo['latitude'] == 39.9 and geo['longitude'] == 116.4 and geo['accuracy'] == 50.0
    ua = dict(page.calls)['Emulation.setUserAgentOverride']
    assert ua['userAgent'] == 'MyUA' and ua['acceptLanguage'] == 'zh-CN'
    assert saved['emulate']['viewport'] == (375, 812)


@pytest.mark.unit
def test_emulate_locale_only_fetches_current_ua(runner, monkeypatch):
    """仅 --locale：setUserAgentOverride 需带 userAgent，用 navigator.userAgent 回填。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: None)
    result = runner.invoke(cli, ['emulate', '--locale', 'ja-JP'])
    assert result.exit_code == 0
    ua = dict(page.calls)['Emulation.setUserAgentOverride']
    assert ua['userAgent'] == 'UA-CURRENT' and ua['acceptLanguage'] == 'ja-JP'


@pytest.mark.unit
def test_emulate_invalid_args(runner, monkeypatch):
    """非法 viewport/geolocation → INVALID_ARGS（退出码 2）。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    monkeypatch.setattr(em, '_get_page', lambda s: _CdpPage())
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    r1 = runner.invoke(cli, ['emulate', '--viewport', 'abc'])
    assert r1.exit_code == 2
    assert json.loads(r1.output)['code'] == 'INVALID_ARGS'
    r2 = runner.invoke(cli, ['emulate', '--geolocation', 'x,y'])
    assert r2.exit_code == 2


@pytest.mark.unit
def test_emulate_reset(runner, monkeypatch):
    """--reset：清除全部 override 并移除会话配置。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    saved = {}
    sess = {'emulate': {'viewport': (1, 1)}}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['emulate', '--reset'])
    assert result.exit_code == 0
    cmds = [c[0] for c in page.calls]
    assert 'Emulation.clearDeviceMetricsOverride' in cmds
    assert 'Emulation.clearGeolocationOverride' in cmds
    assert 'emulate' not in saved


@pytest.mark.unit
def test_intercept_block_and_header(runner, monkeypatch):
    """intercept --block/--header：Network domain 三次调用 + 会话持久化。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    saved = {}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, [
        'intercept', '--block', '*.png', '--block', '*ads*',
        '--header', 'X-Token=abc'])
    assert result.exit_code == 0
    kw = dict(page.calls)
    assert kw['Network.setBlockedURLs']['urls'] == ['*.png', '*ads*']
    assert kw['Network.setExtraHTTPHeaders']['headers'] == {'X-Token': 'abc'}
    assert saved['intercept']['block'] == ['*.png', '*ads*']


@pytest.mark.unit
def test_intercept_off_clears(runner, monkeypatch):
    """intercept --off：下发空配置并从会话移除。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    saved = {}
    sess = {'intercept': {'block': ['*.png'], 'headers': {'A': 'b'}}}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['intercept', '--off'])
    assert result.exit_code == 0
    kw = dict(page.calls)
    assert kw['Network.setBlockedURLs']['urls'] == []
    assert kw['Network.setExtraHTTPHeaders']['headers'] == {}
    assert 'intercept' not in saved


@pytest.mark.unit
def test_intercept_header_bad_format(runner, monkeypatch):
    """--header 缺 = → INVALID_ARGS。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    monkeypatch.setattr(em, '_get_page', lambda s: _CdpPage())
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['intercept', '--header', 'novalue'])
    assert r.exit_code == 2


@pytest.mark.unit
def test_download_sets_path_and_reports_empty(runner, monkeypatch, tmp_path):
    """download --dir：调用 set.download_path；无任务时给 note 提示。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli
    from types import SimpleNamespace

    calls = []
    mgr = SimpleNamespace(missions={})
    page = SimpleNamespace(
        browser=SimpleNamespace(_dl_mgr=mgr),
        download_path=str(tmp_path),
        set=SimpleNamespace(
            download_path=lambda p: calls.append(p),
            when_download_file_exists=lambda m: calls.append(('exists', m)),
        ),
    )
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['download', '--dir', str(tmp_path),
                               '--timeout', '0', '--exists', 'overwrite'])
    assert result.exit_code == 0
    out = json.loads(result.output)['data']
    assert calls == [str(tmp_path), ('exists', 'overwrite')]
    assert out['missions'] == []
    assert '未检测到下载任务' in out['note']


@pytest.mark.unit
def test_download_completed_mission(runner, monkeypatch, tmp_path):
    """download：已完成的 mission 正常汇总输出。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli
    from types import SimpleNamespace

    m = SimpleNamespace(name='f.zip', url='https://x/f.zip', state='completed',
                        is_done=True, final_path='/tmp/f.zip', folder=None)
    mgr = SimpleNamespace(missions={'g1': m})
    page = SimpleNamespace(
        browser=SimpleNamespace(_dl_mgr=mgr),
        download_path='.',
        set=SimpleNamespace(download_path=lambda p: None,
                            when_download_file_exists=lambda x: None),
    )
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['download', '--timeout', '0'])
    assert result.exit_code == 0
    out = json.loads(result.output)['data']
    assert out['missions'][0]['name'] == 'f.zip'
    assert out['missions'][0]['state'] == 'completed'


@pytest.mark.unit
def test_get_page_replays_session_overrides(monkeypatch):
    """_get_page 对会话中的 emulate/intercept 配置自动重放到目标页。"""
    from dp_cli.commands import _utils
    from dp_cli.main import cli  # noqa

    page = _CdpPage()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: {
        'emulate': {'viewport': [100, 200], 'timezone': 'UTC'},
        'intercept': {'block': ['*.png'], 'headers': {'A': 'b'}},
    })
    target = _utils._get_page('s')
    cmds = [c[0] for c in target.calls]
    assert 'Emulation.setDeviceMetricsOverride' in cmds
    assert 'Emulation.setTimezoneOverride' in cmds
    assert 'Network.setBlockedURLs' in cmds
    assert 'Network.setExtraHTTPHeaders' in cmds


# ─────────────────────────────────────────────────────────────────────────────
# p2b 观测能力回归：perf / trace / screencast
# ─────────────────────────────────────────────────────────────────────────────

class _TracePage(_CdpPage):
    """带 driver.set_callback 的假页面，Tracing.end 时触发 tracingComplete。"""
    def __init__(self):
        super().__init__()
        self.cbs = {}
        self.driver = self

    def set_callback(self, name, fn):
        if fn is None:
            self.cbs.pop(name, None)
        else:
            self.cbs[name] = fn

    def run_cdp(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd == 'Performance.getMetrics':
            return {'metrics': [
                {'name': 'JSHeapUsedSize', 'value': 1024},
                {'name': 'Nodes', 'value': 500},
                {'name': 'SomeOtherMetric', 'value': 1},
            ]}
        if cmd == 'Tracing.start':
            cb = self.cbs.get('Tracing.dataCollected')
            if cb:
                cb(value=[{'name': 'e1'}, {'name': 'e2'}])
        if cmd == 'Tracing.end':
            cb = self.cbs.get('Tracing.tracingComplete')
            if cb:
                cb()
        return {}


@pytest.mark.unit
def test_perf_aggregates_metrics_and_timing(runner, monkeypatch):
    """perf：默认只输出关键 metrics 子集 + Navigation Timing 聚合。"""
    import dp_cli.commands.observe as ob
    from dp_cli.main import cli

    page = _TracePage()
    nav = {'url': 'https://x', 'ttfb_ms': 12.5, 'duration_ms': 345.6}
    monkeypatch.setattr(ob, '_get_page', lambda s: page)
    page.run_js = lambda js, **kw: json.dumps(nav)
    result = runner.invoke(cli, ['perf'])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)['data']
    assert out['metrics'] == {'JSHeapUsedSize': 1024, 'Nodes': 500}
    assert out['navigation_timing']['ttfb_ms'] == 12.5


@pytest.mark.unit
def test_perf_raw_keeps_all(runner, monkeypatch):
    """perf --raw：保留全部 metrics。"""
    import dp_cli.commands.observe as ob
    from dp_cli.main import cli

    page = _TracePage()
    monkeypatch.setattr(ob, '_get_page', lambda s: page)
    page.run_js = lambda js, **kw: '{}'
    result = runner.invoke(cli, ['perf', '--raw'])
    out = json.loads(result.output)['data']
    assert 'SomeOtherMetric' in out['metrics']


@pytest.mark.unit
def test_perf_failure(runner, monkeypatch):
    """getMetrics 抛错 → PERF_FAILED（exit 1）。"""
    import dp_cli.commands.observe as ob
    from dp_cli.main import cli

    page = _TracePage()
    def boom(cmd, **kw):
        if cmd == 'Performance.getMetrics':
            raise RuntimeError('cdp down')
        return {}
    page.run_cdp = boom
    monkeypatch.setattr(ob, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['perf'])
    assert result.exit_code == 1
    assert json.loads(result.output)['code'] == 'PERF_FAILED'


@pytest.mark.unit
def test_trace_collects_and_writes(runner, monkeypatch, tmp_path):
    """trace：Tracing.start→end，dataCollected 事件写入 output JSON，
    且回调在 finally 中被清理。"""
    import dp_cli.commands.observe as ob
    from dp_cli.main import cli

    page = _TracePage()
    monkeypatch.setattr(ob, '_get_page', lambda s: page)
    out = tmp_path / 't.json'
    result = runner.invoke(cli, ['trace', '--duration', '0',
                               '--output', str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data['traceEvents'] == [{'name': 'e1'}, {'name': 'e2'}]
    # 回调已清理
    assert 'Tracing.dataCollected' not in page.cbs


@pytest.mark.unit
def test_screencast_frugal_imgs(runner, monkeypatch, tmp_path):
    """screencast：走 frugal_imgs 模式 start/stop，返回帧目录。"""
    import dp_cli.commands.observe as ob
    from dp_cli.main import cli
    from types import SimpleNamespace

    calls = []
    sc = SimpleNamespace(
        set_mode=lambda: SimpleNamespace(
            frugal_imgs_mode=lambda: calls.append('mode')),
        start=lambda p: calls.append(('start', p)),
        stop=lambda: calls.append('stop') or str(tmp_path),
    )
    page = SimpleNamespace(screencast=sc)
    monkeypatch.setattr(ob, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['screencast', '--dir', str(tmp_path),
                               '--duration', '0'])
    assert result.exit_code == 0, result.output
    assert calls[0] == 'mode' and calls[1] == ('start', str(tmp_path))
    out = json.loads(result.output)['data']
    assert out['frames'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# p2c 遗留修复回归：emulate --no-mobile / console falsy / IPv6 host / 无 _dl_mgr
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_emulate_no_mobile(runner, monkeypatch):
    """emulate --viewport --no-mobile：setDeviceMetricsOverride mobile=False。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    saved = {}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))

    result = runner.invoke(
        cli, ['emulate', '--viewport', '1920x1080', '--no-mobile'])
    assert result.exit_code == 0, result.output
    metrics = dict(page.calls)['Emulation.setDeviceMetricsOverride']
    assert metrics['mobile'] is False
    assert saved['emulate']['mobile'] is False


@pytest.mark.unit
def test_emulate_mobile_default_true(runner, monkeypatch):
    """emulate --viewport 未指定 --mobile：默认 mobile=True（保持既有行为）。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: None)

    result = runner.invoke(cli, ['emulate', '--viewport', '375x812'])
    assert result.exit_code == 0, result.output
    metrics = dict(page.calls)['Emulation.setDeviceMetricsOverride']
    assert metrics['mobile'] is True


@pytest.mark.unit
def test_console_callback_falsy_values(runner, monkeypatch):
    """console 回调：value 为 0/False/'' 时按原值记录而非落为 description。"""
    import dp_cli.commands.page as page_mod
    from dp_cli.main import cli

    page = _ConsolePage()
    monkeypatch.setattr(page_mod, '_get_page', lambda s: page)
    runner.invoke(cli, ['console-listen'])
    cb = page.driver.callbacks['Runtime.consoleAPICalled']
    cb(type='log', args=[{'value': 0}, {'value': False}, {'value': ''}],
       timestamp=1)
    msgs = list(page._dp_console_msgs)
    assert msgs[0]['text'] == '0 False ' or msgs[0]['text'].startswith('0 False')




@pytest.mark.unit
def test_cookie_list_url_ipv6(runner, monkeypatch):
    """cookie-list --url 支持 IPv6 host（[::1] 形式），不再截出 '['。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'a', 'value': '1', 'domain': '::1', 'path': '/'},
                {'name': 'b', 'value': '2', 'domain': '.example.com', 'path': '/'},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    result = runner.invoke(cli, ['cookie-list', '--url', 'http://[::1]:9222/p'])
    out = json.loads(result.output)
    names = [c['name'] for c in out['data']['cookies']]
    assert names == ['a']


@pytest.mark.unit
def test_download_no_dl_mgr_graceful(runner, monkeypatch):
    """download：DrissionPage 无 _dl_mgr 私有属性时给 note 而非 AttributeError。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli
    from types import SimpleNamespace

    page = SimpleNamespace(
        browser=SimpleNamespace(),  # 无 _dl_mgr
        download_path='.',
        set=SimpleNamespace(download_path=lambda p: None,
                            when_download_file_exists=lambda x: None),
    )
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['download', '--timeout', '0'])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)['data']
    assert out['missions'] == []
    assert '_dl_mgr' in out['note']


# ─────────────────────────────────────────────────────────────────────────────
# p2a 网络与作用域增强：har-dump / intercept mock / --frame
# ─────────────────────────────────────────────────────────────────────────────

class _EvtDriver:
    """set_callback 注册时立即回放预置事件的假 driver。

    event_handlers 与 callbacks 同一 dict——对齐 DrissionPage
    Driver 真实语义（set_callback 写 event_handlers、falsy 即摘除），
    har-dump 的快照/恢复路径走它。"""

    def __init__(self, events=None):
        self.callbacks = {}
        self.event_handlers = self.callbacks
        self._events = events or {}

    def set_callback(self, event, cb):
        if cb:
            self.callbacks[event] = cb
            if event in self._events:
                for kw in self._events[event]:
                    cb(**kw)
        else:
            self.callbacks.pop(event, None)


class _HarPage:
    """har-dump 用假页面：记录 run_cdp，driver 回放预置 Network 事件。"""

    def __init__(self, events=None):
        self.driver = _EvtDriver(events)
        self.calls = []
        self.url = 'https://x.local/'

    def run_cdp(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd == 'Network.getResponseBody':
            return {'body': 'RESP-OK', 'base64Encoded': False}
        return {}


_HAR_EVENTS = {
    'Network.requestWillBeSent': [{
        'requestId': 'r1',
        'wallTime': 1700000000.0,
        'timestamp': 100.0,
        'request': {'url': 'https://x.local/api/user', 'method': 'POST',
                    'headers': {'Content-Type': 'application/json'},
                    'postData': '{"a":1}'},
    }],
    'Network.responseReceived': [{
        'requestId': 'r1',
        'timestamp': 100.1,
        'response': {'status': 200, 'statusText': 'OK',
                     'protocol': 'h2', 'mimeType': 'application/json',
                     'headers': {'Content-Type': 'application/json'},
                     'timing': {'sendStart': 1.0, 'sendEnd': 2.0,
                                'receiveHeadersEnd': 10.0}},
    }],
    'Network.loadingFinished': [{
        'requestId': 'r1', 'encodedDataLength': 1234, 'timestamp': 100.3,
    }],
}


@pytest.mark.unit
def test_har_dump_aggregates_entries(runner, monkeypatch):
    """har-dump：Network 事件聚合为 HAR 1.2 entries + timings。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    page = _HarPage(_HAR_EVENTS)
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['har-dump', '--seconds', '0.05'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)['data']
    har = data['har']
    assert har['log']['version'] == '1.2'
    assert data['entries'] == 1
    e = har['log']['entries'][0]
    assert e['request']['method'] == 'POST'
    assert e['request']['url'] == 'https://x.local/api/user'
    assert e['request']['postData']['text'] == '{"a":1}'
    assert e['response']['status'] == 200
    assert e['response']['content']['mimeType'] == 'application/json'
    assert e['response']['content']['size'] == 1234
    assert e['timings']['send'] == pytest.approx(1.0)
    assert e['timings']['wait'] == pytest.approx(8.0)
    assert e['timings']['receive'] == pytest.approx(200.0)
    assert e['startedDateTime'].endswith('Z')
    # 内部临时字段不泄漏到 HAR
    assert '_timing' not in e and '_resp_ts' not in e


@pytest.mark.unit
def test_har_dump_body_and_output(runner, monkeypatch, tmp_path):
    """har-dump --body 补抓响应体；--output 写文件。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    page = _HarPage(_HAR_EVENTS)
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    out_file = tmp_path / 'trace.har'
    result = runner.invoke(
        cli, ['har-dump', '--seconds', '0.05', '--body',
              '--output', str(out_file)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)['data']
    assert data['saved_to'] == str(out_file)
    har = json.loads(out_file.read_text(encoding='utf-8'))
    e = har['log']['entries'][0]
    assert e['response']['content']['text'] == 'RESP-OK'
    assert e['response']['content'].get('encoding') != 'base64'


@pytest.mark.unit
def test_har_dump_url_filter(runner, monkeypatch):
    """har-dump --filter：不匹配的 URL 不进入 entries。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    page = _HarPage(_HAR_EVENTS)
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['har-dump', '--seconds', '0.05', '--filter', 'other'])
    assert result.exit_code == 0
    assert json.loads(result.output)['data']['entries'] == 0


@pytest.mark.unit
def test_har_dump_restores_netlog_callbacks(runner, monkeypatch):
    """har-dump set_callback 是单槽覆盖：finally 恢复 netlog 已挂的
    旧回调而非置 None（旧版会让 netlog 被动日志永久停写）。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    page = _HarPage(_HAR_EVENTS)
    # 模拟 serve/daemon 托管时 netlog 已挂载的 3 个事件回调
    netlog_cbs = {ev: (lambda **kw: None)
                  for ev in ('Network.requestWillBeSent',
                             'Network.responseReceived',
                             'Network.loadingFailed')}
    page.driver.event_handlers.update(netlog_cbs)
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['har-dump', '--seconds', '0.05'])
    assert result.exit_code == 0, result.output
    h = page.driver.event_handlers
    # netlog 的 3 个旧回调原样回填
    for ev, cb in netlog_cbs.items():
        assert h[ev] is cb, ev
    # har 自用 loadingFinished（netlog 不占用）恢复原状=摘除
    assert 'Network.loadingFinished' not in h


@pytest.mark.unit
def test_intercept_mock_rule_and_fetch_enable(runner, monkeypatch):
    """intercept --mock：规则追加到 mocks、Fetch.enable 下发、回调注册。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    page.driver = _EvtDriver()
    saved = {}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, [
        'intercept', '--mock', '*api/user*', '--mock-status', '201',
        '--mock-body', '{"id":1}', '--mock-header', 'X-Mock=yes'])
    assert result.exit_code == 0, result.output
    kw = dict(page.calls)
    patterns = kw['Fetch.enable']['patterns']
    assert patterns == [{'urlPattern': '*api/user*',
                         'requestStage': 'Request'}]
    rule = saved['intercept']['mocks'][0]
    assert rule['pattern'] == '*api/user*'
    assert rule['status'] == 201
    assert rule['body'] == '{"id":1}'
    assert rule['headers'] == {'X-Mock': 'yes'}
    assert callable(page.driver.callbacks['Fetch.requestPaused'])


@pytest.mark.unit
def test_intercept_mock_extras_without_mock(runner, monkeypatch):
    """--mock-body/--mock-status/--mock-header 无 --mock → INVALID_ARGS。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    monkeypatch.setattr(em, '_get_page', lambda s: _CdpPage())
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    for args in (['--mock-body', 'x'], ['--mock-status', '404'],
                 ['--mock-header', 'A=b']):
        r = runner.invoke(cli, ['intercept'] + args)
        assert r.exit_code == 2, args
        assert json.loads(r.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_intercept_mock_clear(runner, monkeypatch):
    """--mock-clear 清空 mocks 并 Fetch.disable。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    page.driver = _EvtDriver()
    saved = {}
    sess = {'intercept': {'block': [], 'headers': {},
                          'mocks': [{'pattern': '*a*', 'status': 200,
                                     'body': 'x', 'headers': {}}]}}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['intercept', '--mock-clear'])
    assert result.exit_code == 0
    assert saved['intercept']['mocks'] == []
    cmds = [c[0] for c in page.calls]
    assert 'Fetch.disable' in cmds


@pytest.mark.unit
def test_mock_handler_fulfill_and_continue(runner):
    """_make_mock_handler：URL 匹配 → fulfillRequest（b64 body+status+headers）；
    不匹配 → continueRequest。"""
    import base64 as b64
    import dp_cli.commands.emulate as em

    page = _CdpPage()
    mocks = [{'pattern': '*api/user*', 'status': 201,
              'body': '{"ok":true}', 'headers': {'X-Mock': 'yes'}}]
    handler = em._make_mock_handler(page, mocks)

    handler(requestId='r1',
            request={'url': 'https://x/api/user/1', 'method': 'GET'})
    handler(requestId='r2',
            request={'url': 'https://x/static/a.png', 'method': 'GET'})

    cmds = {kw['requestId']: (cmd, kw) for cmd, kw in page.calls}
    f_cmd, f_kw = cmds['r1']
    assert f_cmd == 'Fetch.fulfillRequest'
    assert f_kw['responseCode'] == 201
    assert b64.b64decode(f_kw['body']).decode() == '{"ok":true}'
    assert f_kw['responseHeaders'] == [{'name': 'X-Mock', 'value': 'yes'}]
    c_cmd, _ = cmds['r2']
    assert c_cmd == 'Fetch.continueRequest'


# ── --frame 作用域 ──

class _FakeFrame:
    """ChromiumFrame 假对象：ele/eles 记录定位器并返回预置元素。"""

    _type = 'ChromiumFrame'

    def __init__(self, ele=None, eles=None):
        self._ele = ele
        self._eles = eles or []
        self.ele_calls = []

    def ele(self, locator, **kw):
        self.ele_calls.append(locator)
        return self._ele if self._ele is not None else FakeNoneEle()

    def eles(self, locator, **kw):
        self.ele_calls.append(locator)
        return self._eles


class _FramePage(FakePage):
    """带 get_frame 的假页面。"""

    def __init__(self, frame=None, **kw):
        super().__init__(**kw)
        self._frame = frame
        self.frame_calls = []

    def get_frame(self, loc, timeout=None):
        self.frame_calls.append(loc)
        return self._frame


@pytest.mark.unit
def test_click_frame_uses_frame_context(runner, monkeypatch):
    """click --frame：元素查找发生在 frame 上下文而非主页面。"""
    import dp_cli.commands.element as em
    from dp_cli.main import cli

    ele = FakeEle()
    frame = _FakeFrame(ele=ele)
    page = _FramePage(frame=frame)
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['click', 'css:.btn', '--frame', 'css:iframe'])
    assert result.exit_code == 0, result.output
    assert page.frame_calls == ['css:iframe']
    assert frame.ele_calls == ['css:.btn']
    assert 'click' in ele._clicked


@pytest.mark.unit
def test_frame_not_found_errors(runner, monkeypatch):
    """--frame 定位不到 iframe → ELEMENT_NOT_FOUND（exit 4）。"""
    import dp_cli.commands.element as em
    from dp_cli.main import cli

    page = _FramePage(frame=None)
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['click', 'css:.btn', '--frame', 'css:iframe'])
    assert result.exit_code == 4
    assert json.loads(result.output)['code'] == 'ELEMENT_NOT_FOUND'


@pytest.mark.unit
def test_assert_count_in_frame(runner, monkeypatch):
    """assert-count --frame：在 frame 上下文内计数。"""
    import dp_cli.commands.assert_cmd as am
    from dp_cli.main import cli

    frame = _FakeFrame(eles=[FakeEle(), FakeEle()])
    page = _FramePage(frame=frame)
    monkeypatch.setattr(am, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['assert-count', 'css:.item', '2', '--frame', 'css:iframe'])
    assert result.exit_code == 0, result.output
    assert frame.ele_calls == ['css:.item']


@pytest.mark.unit
def test_frame_ctx_none_passthrough(runner, monkeypatch):
    """frame_ctx 无 frame 时透传 page（不调用 get_frame）。"""
    from dp_cli.commands._utils import frame_ctx
    page = _FramePage()
    assert frame_ctx(page, None) is page
    assert page.frame_calls == []


# ─────────────────────────────────────────────────────────────────────────────
# fix2：frame_ctx NoneElement 判定 / cookie domain 白名单
# ─────────────────────────────────────────────────────────────────────────────

class _RealNoneElement:
    """模拟真实 NoneElement：类名判定为 NoneElement，但任意属性访问
    抛 ElementNotFoundError（非 AttributeError，getattr 默认值挡不住）。"""

    def __getattr__(self, item):
        from DrissionPage.errors import ElementNotFoundError
        raise ElementNotFoundError()


_RealNoneElement.__name__ = 'NoneElement'


@pytest.mark.unit
def test_frame_ctx_nonelement_exits_4(runner, monkeypatch):
    """get_frame 返回真实 NoneElement（属性访问即抛）→ ELEMENT_NOT_FOUND(4)，
    而非被 getattr('_type') 触发 ElementNotFoundError 冒泡为 exit1。"""
    import dp_cli.commands.element as em
    from dp_cli.main import cli

    page = _FramePage(frame=_RealNoneElement())
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['click', 'css:.btn', '--frame', 'css:iframe'])
    assert result.exit_code == 4
    assert json.loads(result.output)['code'] == 'ELEMENT_NOT_FOUND'


@pytest.mark.unit
def test_cookie_list_domain_localhost_and_ip(runner, monkeypatch):
    """--domain localhost / IPv4 / IPv6 字面量放行（无点号但合法）。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [{'name': 'a', 'value': '1',
                     'domain': 'localhost', 'path': '/'},
                    {'name': 'b', 'value': '2',
                     'domain': '127.0.0.1', 'path': '/'}]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    for dom, expect_names in (
            ('localhost', ['a']), ('127.0.0.1', ['b']), ('::1', [])):
        r = runner.invoke(cli, ['cookie-list', '--domain', dom])
        assert r.exit_code == 0, (dom, r.output)
        names = [c['name'] for c in
                 json.loads(r.output)['data']['cookies']]
        assert names == expect_names, dom
    # 裸顶级后缀仍拒绝
    r = runner.invoke(cli, ['cookie-list', '--domain', 'com'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


# ─────────────────────────────────────────────────────────────────────────────
# t2 逆向闭环三件套：cookie-export / --impersonate / listen-stop --output
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_cookie_export_netscape(runner, monkeypatch, tmp_path):
    """cookie-export：Netscape 格式字段序/标志位/会话 cookie expires=0。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [
                {'name': 'sid', 'value': 'abc', 'domain': '.example.com',
                 'path': '/', 'secure': True, 'expires': 1893456000.0},
                {'name': 'sess', 'value': 'x', 'domain': 'example.com',
                 'path': '/', 'secure': False, 'expires': -1},
                {'name': 'other', 'value': 'y', 'domain': '.other.com',
                 'path': '/', 'secure': False, 'expires': 0},
            ]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    out_file = tmp_path / 'cookies.txt'
    r = runner.invoke(cli, ['cookie-export', '--domain', 'example.com',
                            '--output', str(out_file)])
    assert r.exit_code == 0, r.output
    lines = out_file.read_text(encoding='utf-8').splitlines()
    assert lines[0].startswith('# Netscape HTTP Cookie File')
    rows = [l.split('\t') for l in lines if not l.startswith('#')]
    assert len(rows) == 2  # --domain 过滤掉 .other.com
    assert rows[0] == ['.example.com', 'TRUE', '/', 'TRUE',
                       '1893456000', 'sid', 'abc']
    # 会话 cookie expires<=0 → 0；裸域 includeSubdomains=FALSE
    assert rows[1] == ['example.com', 'FALSE', '/', 'FALSE',
                       '0', 'sess', 'x']


@pytest.mark.unit
def test_cookie_export_json_inline(runner, monkeypatch):
    """cookie-export --format json：结构化数组内联到 data.content。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class P:
        def cookies(self, all_domains=False, all_info=False):
            return [{'name': 'a', 'value': '1', 'domain': 'example.com'}]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['cookie-export', '--format', 'json'])
    assert r.exit_code == 0
    out = json.loads(r.output)
    assert out['data']['count'] == 1
    assert json.loads(out['data']['content'])[0]['name'] == 'a'


@pytest.mark.unit
def test_http_get_impersonate_falls_back_without_curl_cffi(runner, monkeypatch):
    """--impersonate 但 curl_cffi 缺失：降级 requests 后端并附 warnings。"""
    import sys
    import DrissionPage
    from dp_cli.main import cli

    # 确保 curl_cffi 不可导入（即便环境碰巧装了也强制缺失路径）
    monkeypatch.setitem(sys.modules, 'curl_cffi', None)

    class Resp:
        status_code = 200
        content = b'{}'
        text = '{}'

        def json(self):
            return {}

    class SP:
        def __init__(self, session_or_options=None):
            self.url = ''
            self.response = None

        def get(self, url, timeout=None):
            self.url = url
            self.response = Resp()

    monkeypatch.setattr(DrissionPage, 'SessionPage', SP)
    r = runner.invoke(cli, ['http-get', 'https://api.example.com',
                            '--impersonate', 'chrome131'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['status'] == 'ok'
    assert out['data']['status_code'] == 200
    assert any('curl_cffi' in w for w in out['data'].get('warnings', []))


@pytest.mark.unit
def test_http_get_impersonate_uses_curl_cffi(runner, monkeypatch):
    """--impersonate 走 curl_cffi 后端，impersonate 参数透传。"""
    import sys
    import types
    from dp_cli.main import cli

    captured = {}

    class Resp:
        status_code = 200
        url = 'https://api.example.com/'
        content = b'{"ok":true}'
        text = '{"ok":true}'

        def json(self):
            return {'ok': True}

    fake_req = types.SimpleNamespace(
        get=lambda url, **kw: (captured.update(url=url, kw=kw), Resp())[1],
        post=lambda url, **kw: (captured.update(url=url, kw=kw), Resp())[1],
    )
    fake_mod = types.ModuleType('curl_cffi')
    fake_mod.requests = fake_req
    monkeypatch.setitem(sys.modules, 'curl_cffi', fake_mod)
    monkeypatch.setitem(sys.modules, 'curl_cffi.requests', fake_req)

    r = runner.invoke(cli, ['http-get', 'https://api.example.com',
                            '--impersonate', 'chrome131',
                            '--headers', '{"X-A":"1"}', '--proxy', 'http://p:1'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['status_code'] == 200
    assert 'warnings' not in out['data']
    assert captured['kw']['impersonate'] == 'chrome131'
    assert captured['kw']['headers'] == {'X-A': '1'}
    assert captured['kw']['proxies'] == {'http': 'http://p:1',
                                         'https': 'http://p:1'}


@pytest.mark.unit
def test_listen_stop_output_writes_json(runner, monkeypatch, tmp_path):
    """listen-stop --output：数据包结构化 JSON 写文件（含请求头/postData）。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    class _Req:
        headers = {'Content-Type': 'application/json'}
        postData = '{"token":"sig"}'

    class _Resp:
        status = 200
        headers = {'Content-Type': 'application/json'}

        @property
        def body(self):
            return '{"code":0}'

    class _Pkt:
        url = 'https://api.example.com/login'
        method = 'POST'
        resourceType = 'XHR'
        request = _Req()
        response = _Resp()

    class _Listen:
        listening = True

        def wait(self, count, timeout, fit_count):
            return [_Pkt()]

        def stop(self):
            pass

    class P:
        listen = _Listen()

    monkeypatch.setattr(net_mod, '_get_page', lambda s: P())
    out_file = tmp_path / 'packets.json'
    r = runner.invoke(cli, ['listen-stop', '--output', str(out_file)])
    assert r.exit_code == 0, r.output
    saved = json.loads(out_file.read_text())
    pkt = saved['packets'][0]
    assert pkt['url'] == 'https://api.example.com/login'
    assert pkt['postData'] == '{"token":"sig"}'
    assert pkt['request_headers']['Content-Type'] == 'application/json'
    assert pkt['response_body'] == '{"code":0}'


@pytest.mark.unit
def test_emulate_device_switch_clears_touch(runner, monkeypatch):
    """iphone-15 → desktop-chrome 切换：touch 残留必须显式清除。

    修复前 desktop-chrome 预设无 touch 键，merge 后 cfg['touch']=True
    残留 → 桌面 UA + maxTouchPoints=5 的矛盾指纹。
    """
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    sess = {'emulate': dict(em.DEVICE_PRESETS['iphone-15'])}
    saved = {}
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(em, 'save_session', lambda s, d: saved.update(d))

    result = runner.invoke(cli, ['emulate', '--device', 'desktop-chrome'])
    assert result.exit_code == 0, result.output
    # touch=False 必须显式下发 disable 且写入会话
    touch_calls = [c for c in page.calls
                   if c[0] == 'Emulation.setTouchEmulationEnabled']
    assert touch_calls and touch_calls[-1][1]['enabled'] is False
    assert saved['emulate']['touch'] is False
    assert saved['emulate']['mobile'] is False


@pytest.mark.unit
def test_emulate_reset_clears_touch_override(runner, monkeypatch):
    """--reset：除已有 5 类清除外，还需关闭 touch 模拟。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli

    page = _CdpPage()
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr(em, 'load_session',
                        lambda s: {'emulate': dict(em.DEVICE_PRESETS['iphone-15'])})
    monkeypatch.setattr(em, 'save_session', lambda s, d: None)
    result = runner.invoke(cli, ['emulate', '--reset'])
    assert result.exit_code == 0
    touch_calls = [c for c in page.calls
                   if c[0] == 'Emulation.setTouchEmulationEnabled']
    assert touch_calls and touch_calls[-1][1]['enabled'] is False


# ─────────────────────────────────────────────────────────────────────────────
# t3：dp read（正文提取）+ dp cf-wait（CF 挑战等待）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_read_missing_deps(runner, monkeypatch):
    """read：trafilatura/readability 均缺失 → DEPENDENCY_MISSING + 安装提示。"""
    import sys
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    # 强制双缺路径（即便环境碰巧装了也覆盖 ImportError 分支）
    monkeypatch.setitem(sys.modules, 'trafilatura', None)
    monkeypatch.setitem(sys.modules, 'readability', None)

    class P:
        html = '<html><body>text</body></html>'
        url = 'https://x'
        title = 't'

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['read'])
    assert r.exit_code == 1
    out = json.loads(r.output)
    assert out['code'] == 'DEPENDENCY_MISSING'
    assert 'dp-cli[extract]' in out['message']


@pytest.mark.unit
def test_read_trafilatura_markdown(runner, monkeypatch, tmp_path):
    """read：trafilatura 引擎输出 markdown，--output 落盘。"""
    import sys
    import types
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    captured = {}

    def fake_extract(html, output_format='txt', url=None, include_links=False):
        captured.update(fmt=output_format, links=include_links)
        return '# 标题\n\n正文内容'

    fake = types.ModuleType('trafilatura')
    fake.extract = fake_extract
    monkeypatch.setitem(sys.modules, 'trafilatura', fake)

    class P:
        html = '<html><body>article</body></html>'
        url = 'https://x/a'
        title = 'demo'

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    out_file = tmp_path / 'a.md'
    r = runner.invoke(cli, ['read', '--output', str(out_file)])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['engine'] == 'trafilatura'
    assert out['data']['format'] == 'markdown'
    assert captured['fmt'] == 'markdown' and captured['links'] is True
    assert out_file.read_text(encoding='utf-8') == '# 标题\n\n正文内容'


@pytest.mark.unit
def test_read_readability_fallback(runner, monkeypatch):
    """read：trafilatura 缺失时 readability 兜底，html→text 转文本。"""
    import sys
    import types
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    monkeypatch.setitem(sys.modules, 'trafilatura', None)

    class Doc:
        def __init__(self, html):
            pass

        def summary(self):
            return '<div><p>Hello <b>World</b></p><script>x()</script></div>'

    fake = types.ModuleType('readability')
    fake.Document = Doc
    monkeypatch.setitem(sys.modules, 'readability', fake)

    class P:
        html = '<html></html>'
        url = 'https://x'
        title = ''

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['read', '--format', 'text'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['engine'] == 'readability'
    assert out['data']['content'] == 'Hello World'


@pytest.mark.unit
def test_read_selector_scope(runner, monkeypatch):
    """read --selector：用 ele().html 限定区域而非整页。"""
    import sys
    import types
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    fake = types.ModuleType('trafilatura')
    fake.extract = lambda html, **kw: 'REGION'
    monkeypatch.setitem(sys.modules, 'trafilatura', fake)

    class Ele:
        html = '<div>scoped</div>'

    class P:
        html = '<html>full</html>'
        url = 'x'
        title = ''

        def ele(self, locator):
            assert locator == 'css:article'
            return Ele()

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['read', '--selector', 'css:article'])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)['data']['content'] == 'REGION'


@pytest.mark.unit
def test_read_html_format_uses_trafilatura_html(runner, monkeypatch):
    """r3 修复：read --format html 应以 html 输出格式提取正文（而非原样回显整页）。"""
    import sys
    import types
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    captured = {}

    def fake_extract(html, output_format='txt', url=None, include_links=False):
        captured['fmt'] = output_format
        return '<article>正文HTML</article>'

    fake = types.ModuleType('trafilatura')
    fake.extract = fake_extract
    monkeypatch.setitem(sys.modules, 'trafilatura', fake)

    class P:
        html = '<html><body>nav junk + article</body></html>'
        url = 'x'
        title = ''

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['read', '--format', 'html'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert captured['fmt'] == 'html'
    # 返回的是提取结果而非原始整页 HTML
    assert out['data']['content'] == '<article>正文HTML</article>'


@pytest.mark.unit
def test_read_trafilatura_exception_falls_back(runner, monkeypatch):
    """r3 修复：trafilatura 抛非 ImportError 时降级 readability。"""
    import sys
    import types
    import dp_cli.commands.extract as ex_mod
    from dp_cli.main import cli

    fake_t = types.ModuleType('trafilatura')

    def boom(html, **kw):
        raise ValueError('malformed input')

    fake_t.extract = boom
    monkeypatch.setitem(sys.modules, 'trafilatura', fake_t)

    class Doc:
        def __init__(self, html):
            pass

        def summary(self):
            return '<p>fallback text</p>'

    fake_r = types.ModuleType('readability')
    fake_r.Document = Doc
    monkeypatch.setitem(sys.modules, 'readability', fake_r)

    class P:
        html = '<html></html>'
        url = 'x'
        title = ''

    monkeypatch.setattr(ex_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['read', '--format', 'text'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['engine'] == 'readability'
    assert out['data']['content'] == 'fallback text'


@pytest.mark.unit
def test_cf_wait_js_transient_error_keeps_waiting(runner, monkeypatch):
    """r3 修复：run_js 瞬断（导航中上下文销毁）不误判通过，继续轮询。"""
    import dp_cli.commands.browser as bmod
    from dp_cli.main import cli

    seq = iter([RuntimeError('context destroyed'),       # 第 1 次瞬断
                {'f': True, 't': 'Just a moment...'},     # 挑战仍在
                {'f': False, 't': 'My Site'}])            # 挑战通过

    class P:
        def run_js(self, script, *a, **k):
            r = next(seq)
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(bmod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['cf-wait', '--timeout', '10', '--poll', '0.01'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['passed'] is True
    assert out['data']['was_challenged'] is True
    assert out['data']['title'] == 'My Site'


@pytest.mark.unit
def test_cf_wait_not_challenged_and_clears(runner, monkeypatch):
    """cf-wait：无挑战秒过；挑战页 title 消失后返回 was_challenged=True。"""
    import dp_cli.commands.browser as bmod
    from dp_cli.main import cli

    class P:
        def run_js(self, script, *a, **k):
            return {'f': False, 't': 'My Site'}

    monkeypatch.setattr(bmod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['cf-wait'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['passed'] is True
    assert out['data']['was_challenged'] is False

    seq = iter([{'f': True, 't': 'Just a moment...'},
                {'f': False, 't': 'My Site'}])

    class P2:
        def run_js(self, script, *a, **k):
            return next(seq)

    monkeypatch.setattr(bmod, '_get_page', lambda s: P2())
    r = runner.invoke(cli, ['cf-wait', '--poll', '0.01'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['was_challenged'] is True
    assert out['data']['title'] == 'My Site'


@pytest.mark.unit
def test_cf_wait_timeout(runner, monkeypatch):
    """cf-wait：挑战持续存在 → WAIT_TIMEOUT 退出码 5。"""
    import dp_cli.commands.browser as bmod
    from dp_cli.main import cli

    class P:
        def run_js(self, script, *a, **k):
            return {'f': True, 't': 'Just a moment...'}

    monkeypatch.setattr(bmod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['cf-wait', '--timeout', '0.05', '--poll', '0.01'])
    assert r.exit_code == 5, r.output
    assert json.loads(r.output)['code'] == 'WAIT_TIMEOUT'


# ─────────────────────────────────────────────────────────────────────────────
# t2：dp dom-snapshot（DOMSnapshot.captureSnapshot / DOM.getSnapshot 子树）
# ─────────────────────────────────────────────────────────────────────────────

class _DomSnapPage:
    """记录 run_cdp 调用并按方法返回预设快照的假页面。"""

    def __init__(self, responses=None, ele=None):
        self.cdp_calls = []
        self._responses = responses or {}
        self._ele = ele

    def run_cdp(self, method, **kw):
        self.cdp_calls.append((method, kw))
        return self._responses.get(method, {})

    def ele(self, locator, **kw):
        return self._ele


@pytest.mark.unit
def test_dom_snapshot_whole_doc(runner, monkeypatch, tmp_path):
    """dom-snapshot 整文档：captureSnapshot 调用 + stats 摘要 + --output。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    fake_snap = {'documents': [
        {'nodes': {'nodeName': [0, 1, 2, 3]}, 'layout': {}},
        {'nodes': {'nodeName': [4]}, 'layout': {}},
    ], 'strings': ['a', 'b']}
    page = _DomSnapPage(
        responses={'DOMSnapshot.captureSnapshot': fake_snap})
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    out_file = tmp_path / 'snap.json'
    r = runner.invoke(cli, ['dom-snapshot', '--output', str(out_file)])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['scope'] == 'document'
    assert out['data']['stats'] == {'documents': 2, 'nodes': 5,
                                    'strings': 2}
    assert json.loads(out_file.read_text()) == fake_snap
    method, kw = page.cdp_calls[0]
    assert method == 'DOMSnapshot.captureSnapshot'
    assert 'computedStyles' in kw and kw['includeDOMRects'] is True


@pytest.mark.unit
def test_dom_snapshot_subtree(runner, monkeypatch):
    """dom-snapshot --selector：用元素 _backend_id 走 DOM.getSnapshot 子树。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    class Ele:
        _backend_id = 777
        _node_id = 55

    fake_snap = {'domNodes': [{'x': 1}, {'x': 2}],
                 'layoutTreeNodes': [{'y': 1}],
                 'computedStyles': [{'z': 1}]}
    page = _DomSnapPage(responses={'DOM.getSnapshot': fake_snap},
                        ele=Ele())
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['dom-snapshot', '--selector', 'css:main',
                            '--styles', 'display,color'])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['scope'] == 'subtree'
    assert out['data']['stats'] == {'dom_nodes': 2, 'layout_nodes': 1,
                                    'computed_styles': 1}
    method, kw = page.cdp_calls[0]
    assert method == 'DOM.getSnapshot'
    assert kw['backendNodeId'] == 777
    assert kw['computedStyleWhitelist'] == ['display', 'color']


@pytest.mark.unit
def test_dom_snapshot_styles_stripped(runner, monkeypatch):
    """--styles 逗号分隔带空格 → strip 后传入白名单（防 CDP 匹配静默丢失）。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    class Ele:
        _backend_id = 1
        _node_id = 1

    page = _DomSnapPage(
        responses={'DOM.getSnapshot': {'domNodes': [], 'layoutTreeNodes': [],
                                       'computedStyles': []}},
        ele=Ele())
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['dom-snapshot', '--selector', 'css:main',
                            '--styles', 'display, color , font-size'])
    assert r.exit_code == 0, r.output
    _, kw = page.cdp_calls[0]
    assert kw['computedStyleWhitelist'] == ['display', 'color', 'font-size']


@pytest.mark.unit
def test_dom_snapshot_selector_not_found(runner, monkeypatch):
    """dom-snapshot --selector 未命中 → ELEMENT_NOT_FOUND exit4。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    class _NE:
        pass
    _NE.__name__ = 'NoneElement'

    page = _DomSnapPage(ele=_NE())
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['dom-snapshot', '--selector', 'css:missing'])
    assert r.exit_code == 4
    assert json.loads(r.output)['code'] == 'ELEMENT_NOT_FOUND'


# ─────────────────────────────────────────────────────────────────────────────
# t2 性能：snapshot --depth/--max-nodes + inspect 单次 evaluate 合并
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_snapshot_depth_and_max_nodes_options(runner, monkeypatch):
    """--depth/--max-nodes 透传 take_a11y_snapshot，默认 None 不改旧行为。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    captured = {}

    def fake_snap(page, **kw):
        captured.update(kw)
        return {'page': {'url': 'u', 'title': 't'},
                'stats': {'total': 1, 'interactive': 0},
                'tree': {}, 'method': 'cdp'}

    page = _DomSnapPage()
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    monkeypatch.setattr(smod, 'take_a11y_snapshot', fake_snap)
    r = runner.invoke(cli, ['snapshot', '--depth', '3',
                            '--max-nodes', '50', '--no-clickables'])
    assert r.exit_code == 0, r.output
    assert captured['max_depth'] == 3
    assert captured['max_nodes'] == 50

    captured.clear()
    r = runner.invoke(cli, ['snapshot', '--no-clickables'])
    assert captured['max_depth'] is None
    assert captured['max_nodes'] is None


@pytest.mark.unit
def test_inspect_single_evaluate(runner, monkeypatch):
    """inspect 合并为一次 run_js：输出字段与旧逐项调用版完全一致。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    calls = []

    class Ele:
        def run_js(self, js, *a, **k):
            calls.append(js)
            return {
                'tag': 'input', 'text': 'Hello\xa0World',
                'attrs': {'id': 'inp', 'type': 'checkbox'},
                'displayed': True, 'enabled': True, 'checked': True,
                'clickable': True,
                'rect': {'location': [10, 20], 'size': [100, 30],
                         'midpoint': [60, 35]},
                'styles': {'color': 'rgb(0,0,0)', 'display': 'block'},
            }

    class P(_DomSnapPage):
        pass

    page = P(ele=Ele())
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['inspect', 'css:#inp',
                            '--include-rect', '--include-style'])
    assert r.exit_code == 0, r.output
    assert len(calls) == 1, f'应只调用一次 run_js，实际 {len(calls)}'
    out = json.loads(r.output)
    d = out['data']
    assert d['tag'] == 'input'
    assert d['text'] == 'Hello World'          # nbsp→空格 + strip
    assert d['attrs'] == {'id': 'inp', 'type': 'checkbox'}
    assert d['states'] == {'is_displayed': True, 'is_enabled': True,
                           'is_checked': True, 'is_clickable': True}
    assert d['rect']['size'] == [100, 30]
    assert d['styles']['display'] == 'block'


@pytest.mark.unit
def test_inspect_checked_gated_by_tag(runner, monkeypatch):
    """非 input/option 元素 is_checked=None（与旧逻辑一致）。"""
    import dp_cli.commands.snapshot_cmd as smod
    from dp_cli.main import cli

    class Ele:
        def run_js(self, js, *a, **k):
            return {'tag': 'div', 'text': '', 'attrs': {},
                    'displayed': True, 'enabled': True, 'checked': True,
                    'clickable': False,
                    'rect': {}, 'styles': {}}

    page = _DomSnapPage(ele=Ele())
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    r = runner.invoke(cli, ['inspect', 'css:div'])
    assert r.exit_code == 0
    d = json.loads(r.output)['data']
    assert d['states']['is_checked'] is None
    # 未指定 flag 时不含 rect/styles 键
    assert 'rect' not in d and 'styles' not in d


# ─────────────────────────────────────────────────────────────────────────────
# state-save --format playwright：Playwright storageState 结构导出
# ─────────────────────────────────────────────────────────────────────────────

class _StatePage:
    def __init__(self, cookies=None, ls=None, url='https://app.example.com/home'):
        self._cookies = cookies or []
        self._ls = ls
        self.url = url

    def cookies(self, all_domains=False, all_info=False):
        assert all_domains and all_info
        return self._cookies

    def run_js(self, js, **kw):
        import json as _j
        # 新契约：{o: location.origin, items: localStorage 条目}
        return _j.dumps({'o': self.url.rsplit('/', 1)[0] or self.url,
                         'items': self._ls or []})

    def eles(self, *a, **kw):
        return []


def _save_state(runner, monkeypatch, tmp_path, page, *extra):
    import dp_cli.commands.misc as misc_mod
    from dp_cli.main import cli
    monkeypatch.setattr(misc_mod, '_get_page', lambda s: page)
    f = tmp_path / 'state.json'
    r = runner.invoke(cli, ['state-save', str(f), *extra])
    assert r.exit_code == 0, r.output
    return r, json.loads(f.read_text())


@pytest.mark.unit
def test_state_save_playwright_schema(runner, monkeypatch, tmp_path):
    """--format playwright：cookies 全字段映射 + storageState 顶层结构。"""
    page = _StatePage(cookies=[
        {'name': 'sid', 'value': 'abc', 'domain': '.example.com',
         'path': '/', 'expires': 1893456000.0,
         'httpOnly': True, 'secure': True, 'sameSite': 'Strict'},
    ], ls=[{'name': 'tok', 'value': 'v1'}])
    _, st = _save_state(runner, monkeypatch, tmp_path, page,
                        '--format', 'playwright')
    # Playwright storageState 顶层结构
    assert set(st) == {'cookies', 'origins'}
    c = st['cookies'][0]
    assert c == {'name': 'sid', 'value': 'abc', 'domain': '.example.com',
                 'path': '/', 'expires': 1893456000.0,
                 'httpOnly': True, 'secure': True, 'sameSite': 'Strict'}
    assert st['origins'] == [{
        'origin': 'https://app.example.com',
        'localStorage': [{'name': 'tok', 'value': 'v1'}]}]


@pytest.mark.unit
def test_state_save_playwright_session_cookie(runner, monkeypatch, tmp_path):
    """会话 cookie（session 标志/expires 缺失/<=0）→ expires=-1。"""
    page = _StatePage(cookies=[
        {'name': 's1', 'value': 'a', 'domain': 'e.com', 'path': '/',
         'session': True, 'expires': 9999999999.0},
        {'name': 's2', 'value': 'b', 'domain': 'e.com', 'path': '/',
         'expires': 0},
        {'name': 's3', 'value': 'c', 'domain': 'e.com', 'path': '/'},
    ])
    _, st = _save_state(runner, monkeypatch, tmp_path, page,
                        '--format', 'playwright')
    assert [c['expires'] for c in st['cookies']] == [-1, -1, -1]


@pytest.mark.unit
def test_state_save_playwright_samesite_map(runner, monkeypatch, tmp_path):
    """sameSite 枚举映射：Lax/Strict/None/no_restriction/缺失→Lax。"""
    page = _StatePage(cookies=[
        {'name': 'a', 'sameSite': 'strict'},
        {'name': 'b', 'sameSite': 'None'},
        {'name': 'c', 'sameSite': 'no_restriction'},
        {'name': 'd'},
    ])
    _, st = _save_state(runner, monkeypatch, tmp_path, page,
                        '--format', 'playwright')
    sites = [c['sameSite'] for c in st['cookies']]
    assert sites == ['Strict', 'None', 'None', 'Lax']
    assert all(s in ('Strict', 'Lax', 'None') for s in sites)


@pytest.mark.unit
def test_state_save_playwright_non_http_origin_skipped(runner, monkeypatch,
                                                       tmp_path):
    """非 http(s) 页（file://）不产出 origins 条目。"""
    page = _StatePage(url='file:///tmp/x.html',
                      ls=[{'name': 'k', 'value': 'v'}])
    _, st = _save_state(runner, monkeypatch, tmp_path, page,
                        '--format', 'playwright')
    assert st['origins'] == []


@pytest.mark.unit
def test_state_save_default_format_unchanged(runner, monkeypatch, tmp_path):
    """默认 dp 格式不变：{name:value} cookies + flat localStorage + url。"""
    class P:
        url = 'https://a.com/'

        def cookies(self, all_domains=False):
            class _CL(list):
                def as_dict(self):
                    return {'sid': 'abc'}
            return _CL()

        def run_js(self, js, **kw):
            return '{"k": "v"}'

    r, st = _save_state(runner, monkeypatch, tmp_path, P())
    assert st == {'cookies': {'sid': 'abc'},
                  'url': 'https://a.com/', 'localStorage': {'k': 'v'}}
    assert json.loads(r.output)['data']['format'] == 'dp'


# ─────────────────────────────────────────────────────────────────────────────
# as_expr=True + 顶层 return 回归守卫（Runtime.evaluate 下非法语法）
# ─────────────────────────────────────────────────────────────────────────────

class _StrictExprPage:
    """run_js 模拟 Runtime.evaluate：as_expr 且脚本以 return 开头即抛错。"""

    def __init__(self, cookies=None, ls_items=None, url='https://app.example.com/'):
        self._cookies = cookies or []
        self._ls_items = ls_items
        self.url = url

    def cookies(self, all_domains=False, all_info=False):
        class _CL(list):
            def as_dict(self):
                return {c['name']: c['value'] for c in self}
        return _CL(self._cookies)

    def run_js(self, js, *a, **kw):
        if kw.get('as_expr') and js.lstrip().startswith('return'):
            raise RuntimeError('Illegal return statement')
        # localstorage-list：Object.fromEntries → {k:v}
        if 'Object.fromEntries' in js:
            items = self._ls_items or []
            return json.dumps({i['name']: i['value'] for i in items})
        # storageState 新契约：{o: origin, items: [{name,value}]}
        items = self._ls_items or []
        if 'location.origin' in js:
            return json.dumps({'o': 'https://app.example.com',
                               'items': items})
        return json.dumps(items)

    def eles(self, *a, **kw):
        return []


@pytest.mark.unit
def test_state_save_playwright_no_toplevel_return(runner, monkeypatch, tmp_path):
    """as_expr 脚本以 return 开头会被 Runtime.evaluate 拒绝——
    origins 不应因此静默为空。"""
    import dp_cli.commands.misc as misc_mod
    from dp_cli.main import cli
    monkeypatch.setattr(misc_mod, '_get_page', lambda s: _StrictExprPage(
        ls_items=[{'name': 'tok', 'value': 'v9'}]))
    f = tmp_path / 'st.json'
    r = runner.invoke(cli, ['state-save', str(f), '--format', 'playwright'])
    assert r.exit_code == 0, r.output
    st = json.loads(f.read_text())
    assert st['origins'] == [{
        'origin': 'https://app.example.com',
        'localStorage': [{'name': 'tok', 'value': 'v9'}]}]


@pytest.mark.unit
def test_state_save_dp_localstorage_no_toplevel_return(runner, monkeypatch,
                                                       tmp_path):
    """默认 dp 格式同样不得用顶层 return（此前 localStorage 静默为空）。"""
    import dp_cli.commands.misc as misc_mod
    from dp_cli.main import cli
    monkeypatch.setattr(misc_mod, '_get_page', lambda s: _StrictExprPage(
        cookies=[{'name': 'sid', 'value': 'x'}],
        ls_items=[{'name': 'k', 'value': 'v'}]))
    f = tmp_path / 'st.json'
    r = runner.invoke(cli, ['state-save', str(f)])
    assert r.exit_code == 0, r.output
    assert json.loads(f.read_text())['localStorage'] == {'k': 'v'}


@pytest.mark.unit
def test_localstorage_list_no_toplevel_return(runner, monkeypatch):
    """localstorage-list 同样修复（此前真机必报 STORAGE_FAILED）。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli
    monkeypatch.setattr(storage_mod, '_get_page', lambda s: _StrictExprPage(
        ls_items=[{'name': 'a', 'value': '1'}]))
    r = runner.invoke(cli, ['localstorage-list'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['storage'] == {'a': '1'} and d['count'] == 1


@pytest.mark.unit
def test_version_flag_runs_drift_warning(runner, monkeypatch, tmp_path):
    """-V：漂移时 stderr 告警 + stdout 纯版本号。"""
    from dp_cli.main import cli
    calls = []
    monkeypatch.setattr('dp_cli.skill_dist.warn_drift',
                        lambda: calls.append(1))
    r = runner.invoke(cli, ['-V'])
    assert r.exit_code == 0 and calls == [1]
    import re
    assert re.fullmatch(r'\d+\.\d+\.\d+\s*', r.output.strip())


@pytest.mark.unit
def test_version_flag_clean_stdout_no_drift(runner, monkeypatch):
    """-V：不漂移时 stdout 只有版本号。"""
    from dp_cli.main import cli
    monkeypatch.setattr('dp_cli.skill_dist.warn_drift', lambda: None)
    r = runner.invoke(cli, ['-V'])
    assert r.exit_code == 0
    import re
    assert re.fullmatch(r'\d+\.\d+\.\d+\s*', r.output.strip())


# ─────────────────────────────────────────────────────────────────────────────
# actionability / click 参数面 / dialog-policy / state-load pw 兼容
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_click_nowait_flag(runner, monkeypatch):
    """click --no-wait：跳过 actionability 直接点。"""
    from dp_cli.main import cli
    ele = FakeEle()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage(ele=ele))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['click', '#btn', '--no-wait'])
    assert r.exit_code == 0, r.output
    assert ele._clicked == ['click']


@pytest.mark.unit
def test_click_bad_modifiers(runner, monkeypatch):
    """click --modifiers 非法键 → INVALID_ARGS。"""
    from dp_cli.main import cli
    from dp_cli.commands.element import register as _reg
    ele = FakeEle()
    ele.rect = type('R', (), {'location': (0, 0), 'size': (50, 20)})()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage(ele=ele))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['click', '#btn', '--modifiers', 'Bogus',
                            '--no-wait', '--force'])
    assert '未知修饰键' in r.output or 'INVALID_ARGS' in r.output


@pytest.mark.unit
def test_click_cdp_path_dispatches(runner, monkeypatch):
    """click --button right：走 CDP dispatchMouseEvent（pressed+released）。"""
    from dp_cli.main import cli
    calls = []

    class P(FakePage):
        def run_cdp(self, method, **kw):
            calls.append((method, kw))

    ele = FakeEle()
    ele.rect = type('R', (), {'location': (10, 20), 'size': (40, 20)})()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: P(ele=ele))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['click', '#btn', '--button', 'right',
                            '--no-wait', '--force'])
    assert r.exit_code == 0, r.output
    types = [kw['type'] for m, kw in calls if m == 'Input.dispatchMouseEvent']
    assert types == ['mouseMoved', 'mousePressed', 'mouseReleased']
    assert any(kw.get('button') == 'right' for m, kw in calls)


@pytest.mark.unit
def test_dialog_policy_writes_session(runner, monkeypatch, tmp_path):
    """dialog-policy --accept 写 session['dialog_policy']。"""
    from dp_cli.main import cli
    import dp_cli.commands.page as page_mod
    saved = {}
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {'a': 1})
    monkeypatch.setattr('dp_cli.session.save_session',
                        lambda s, d: saved.update(d))
    r = runner.invoke(cli, ['dialog-policy', '--accept'])
    assert r.exit_code == 0, r.output
    assert saved.get('dialog_policy', {}).get('action') == 'accept'


@pytest.mark.unit
def test_dialog_policy_requires_flag(runner, monkeypatch):
    from dp_cli.main import cli
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {'a': 1})
    r = runner.invoke(cli, ['dialog-policy'])
    assert 'INVALID_ARGS' in r.output or '请指定' in r.output


@pytest.mark.unit
def test_state_load_playwright_format(runner, monkeypatch, tmp_path):
    """state-load 识别 pw storageState（cookies=list + origins.localStorage）。"""
    from dp_cli.main import cli
    import dp_cli.commands.misc as misc_mod
    import json as _json
    state = {
        'cookies': [{'name': 'sid', 'value': 'v1', 'domain': '.ex.com',
                     'path': '/', 'secure': True, 'httpOnly': True,
                     'sameSite': 'Lax', 'expires': -1}],
        'origins': [{'origin': 'https://ex.com',
                     'localStorage': [{'name': 'tok', 'value': 'abc'}]}],
    }
    f = tmp_path / 'st.json'
    f.write_text(_json.dumps(state))
    set_ck = []
    js_calls = []

    class _Set:
        def cookies(self, ck):
            set_ck.append(ck)

    class P(FakePage):
        set = _Set()

        def run_js(self, s, *a, **k):
            js_calls.append(s)

    monkeypatch.setattr(misc_mod, '_get_page', lambda s: P())
    r = runner.invoke(cli, ['state-load', str(f)])
    assert r.exit_code == 0, r.output
    assert set_ck and set_ck[0]['name'] == 'sid'
    assert set_ck[0]['secure'] is True and set_ck[0]['httpOnly'] is True
    assert any('localStorage.setItem' in c and 'tok' in c for c in js_calls)


@pytest.mark.unit
def test_wait_actionable_occluded_reports(runner):
    """wait_actionable：elementFromPoint 遮挡 → occluded + 遮挡者信息。"""
    from dp_cli.commands._utils import wait_actionable

    class E(FakeEle):
        def run_js(self, s, *a, **k):
            return {'ok': False, 'reason': 'occluded',
                    'occluder': {'tag': 'div', 'id': 'overlay', 'cls': 'mask'},
                    'rect': [0, 0, 10, 10], 'center': [5, 5]}

    class Ctx:
        def ele(self, loc, index=1, timeout=0.3):
            return E()

    ele, fail = wait_actionable(Ctx(), '#x', timeout=0.5)
    assert fail and fail['reason'] == 'occluded'
    assert fail['occluder']['id'] == 'overlay'


@pytest.mark.unit
def test_wait_actionable_stable_returns(runner):
    """wait_actionable：ok+rect 稳定两拍 → 返回元素。"""
    from dp_cli.commands._utils import wait_actionable

    class E(FakeEle):
        def run_js(self, s, *a, **k):
            return {'ok': True, 'reason': 'ok',
                    'rect': [0, 0, 10, 10], 'center': [5, 5]}

    class Ctx:
        def ele(self, loc, index=1, timeout=0.3):
            return E()

    ele, fail = wait_actionable(Ctx(), '#x', timeout=2.0, settle=0.1)
    assert fail is None and ele is not None


# ─────────────────────────────────────────────────────────────────────────────
# A 批：allowed-domains / boundary / screenshot --if-changed
# B 批：cookies-import 嗅探 / read direct / netlog ring buffer
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_host_allowed_suffix():
    from dp_cli.commands.emulate import _host_allowed
    assert _host_allowed('https://ex.com/a', ['ex.com'])
    assert _host_allowed('https://api.ex.com/', ['ex.com'])
    assert not _host_allowed('https://evil.com/', ['ex.com'])
    assert _host_allowed('data:text/plain,x', ['ex.com'])  # 非 http 放行


@pytest.mark.unit
def test_sniff_cookies_json_list():
    from dp_cli.commands.storage import _sniff_cookies
    r = _sniff_cookies('[{"name":"a","value":"1","domain":".x.com"}]')
    assert r == [{'name': 'a', 'value': '1', 'domain': '.x.com'}]


@pytest.mark.unit
def test_sniff_cookies_json_dict():
    from dp_cli.commands.storage import _sniff_cookies
    r = _sniff_cookies('{"a":"1","b":"2"}')
    assert {c['name'] for c in r} == {'a', 'b'}


@pytest.mark.unit
def test_sniff_cookies_netscape():
    from dp_cli.commands.storage import _sniff_cookies
    raw = '# Netscape\n.x.com\tTRUE\t/\tTRUE\t1700000000\tsid\tv1\n'
    r = _sniff_cookies(raw)
    assert r[0]['name'] == 'sid' and r[0]['secure'] is True


@pytest.mark.unit
def test_sniff_cookies_header():
    from dp_cli.commands.storage import _sniff_cookies
    r = _sniff_cookies('Cookie: a=1; b=hello world')
    assert r == [{'name': 'a', 'value': '1'},
                 {'name': 'b', 'value': 'hello world'}]


@pytest.mark.unit
def test_sniff_cookies_garbage():
    from dp_cli.commands.storage import _sniff_cookies
    assert _sniff_cookies('not cookies at all') is None


@pytest.mark.unit
def test_sniff_cookies_httponly_prefix():
    """curl 私有扩展 #HttpOnly_ 前缀行：剥前缀正常解析+置 httpOnly
    （旧版按注释行整体静默丢弃）。"""
    from dp_cli.commands.storage import _sniff_cookies
    raw = ('# Netscape\n'
           '#HttpOnly_.x.com\tTRUE\t/\tFALSE\t1700000000\tsid\tv1\n'
           '.x.com\tTRUE\t/\tTRUE\t1700000000\tb\tv2\n')
    r = _sniff_cookies(raw)
    assert len(r) == 2
    assert r[0]['name'] == 'sid' and r[0]['value'] == 'v1'
    assert r[0]['httpOnly'] is True
    assert r[0]['domain'] == '.x.com'
    assert r[1]['name'] == 'b' and 'httpOnly' not in r[1]


@pytest.mark.unit
def test_sniff_cookies_malformed_line_reported():
    """Netscape 分支字段不足的行不再静默 continue——记入 skipped。"""
    from dp_cli.commands.storage import _sniff_cookies
    skipped = []
    r = _sniff_cookies('.x.com\tTRUE\t/\tTRUE\t1\ta\t1\nbad\tshort\n',
                       skipped)
    assert r[0]['name'] == 'a' and r[0]['secure'] is True
    assert skipped == ['bad\tshort']


@pytest.mark.unit
def test_cookies_import_httponly_and_skipped(runner, monkeypatch,
                                             tmp_path):
    """cookies-import：#HttpOnly_ 行成功导入（httpOnly=True）+
    解析失败行进 data.skipped_lines 而非静默丢。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    got = []

    class _Set:
        def cookies(self, c):
            got.append(c)

    class P:
        set = _Set()

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    f = tmp_path / 'c.txt'
    f.write_text('#HttpOnly_.x.com\tTRUE\t/\tFALSE\t0\tsid\tv1\n'
                 'broken\tline\n', encoding='utf-8')
    r = runner.invoke(cli, ['cookies-import', str(f)])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['imported'] == 1
    assert got[0]['name'] == 'sid' and got[0]['httpOnly'] is True
    assert d['skipped_lines'] == ['broken\tline']


@pytest.mark.unit
def test_wrap_boundary_marks():
    from dp_cli.commands._utils import wrap_boundary
    w, n = wrap_boundary('evil content')
    assert f'nonce="{n}"' in w and 'UNTRUSTED-CONTENT' in w
    assert 'evil content' in w


@pytest.mark.unit
def test_goto_forbidden_domain(runner, monkeypatch):
    """goto 命中白名单外域名 → FORBIDDEN_DOMAIN。"""
    from dp_cli.main import cli
    import dp_cli.commands.browser as browser_mod
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(browser_mod, 'load_session',
                        lambda s: {'intercept': {'allowed_domains': ['ok.com']}})
    r = runner.invoke(cli, ['goto', 'https://evil.com/x'])
    assert 'FORBIDDEN_DOMAIN' in r.output


@pytest.mark.unit
def test_goto_allowed_domain_passes(runner, monkeypatch):
    from dp_cli.main import cli
    import dp_cli.commands.browser as browser_mod
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(browser_mod, 'load_session',
                        lambda s: {'intercept': {'allowed_domains': ['ok.com']}})
    r = runner.invoke(cli, ['goto', 'https://sub.ok.com/x'])
    assert 'FORBIDDEN_DOMAIN' not in r.output


@pytest.mark.unit
def test_requests_no_buffer_degrade(runner, monkeypatch, tmp_path):
    """requests 无缓冲文件 → NO_NETLOG 降级提示。"""
    from dp_cli.main import cli
    import dp_cli.netlog as nl
    monkeypatch.setattr(nl.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr('dp_cli.netlog._path',
                        lambda s: tmp_path / f'netlog-{s}.jsonl')
    r = runner.invoke(cli, ['requests'])
    assert 'NO_NETLOG' in r.output


@pytest.mark.unit
def test_requests_lists_ring(runner, monkeypatch, tmp_path):
    """requests 聚合 req+resp → 行（含 status 合并）。"""
    from dp_cli.main import cli
    import dp_cli.netlog as nl
    log = tmp_path / 'netlog-default.jsonl'
    log.write_text('\n'.join([
        json.dumps({'kind': 'req', 'id': '1', 'url': 'https://x/a',
                    'method': 'GET', 'type': 'XHR'}),
        json.dumps({'kind': 'resp', 'id': '1', 'url': 'https://x/a',
                    'status': 200}),
        json.dumps({'kind': 'req', 'id': '2', 'url': 'https://x/b',
                    'method': 'POST', 'type': 'Document'}),
        json.dumps({'kind': 'fail', 'id': '2', 'errorText': 'net::ERR'})]))
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'netlog-{s}.jsonl')
    r = runner.invoke(cli, ['requests'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output.strip())
    reqs = d['data']['requests']
    assert len(reqs) == 2
    a = next(x for x in reqs if x['id'] == '1')
    assert a['status'] == 200
    b = next(x for x in reqs if x['id'] == '2')
    assert 'failed' in b


@pytest.mark.unit
def test_netlog_ring_trims(tmp_path):
    """netlog 写超水位截到 ~500 行。"""
    import dp_cli.netlog as nl
    log = tmp_path / 'netlog-x.jsonl'
    monkeylog = []
    # 直接写 650 行再触发 trim
    log.write_text('\n'.join(json.dumps({'kind': 'req', 'id': str(i)})
                             for i in range(650)) + '\n')
    nl._maybe_trim(log)
    lines = log.read_text().splitlines()
    assert len(lines) <= 500


def _write_netlog(tmp_path, monkeypatch, entries, session='default'):
    """写一份 netlog-{session}.jsonl 并把 netlog._path 指过去。"""
    import dp_cli.netlog as nl
    log = tmp_path / f'netlog-{session}.jsonl'
    log.write_text('\n'.join(json.dumps(e) for e in entries) + '\n',
                   encoding='utf-8')
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'netlog-{s}.jsonl')
    return log


_NETLOG_SAMPLE = [
    {'kind': 'req', 'id': '1', 'url': 'https://x.com/api/u?p=1&k=2',
     'method': 'GET', 'type': 'XHR',
     'headers': {'Accept': 'application/json', 'X-Token': 'abc'}},
    {'kind': 'resp', 'id': '1', 'url': 'https://x.com/api/u?p=1&k=2',
     'status': 200, 'mimeType': 'application/json',
     'headers': {'Content-Type': 'application/json'}},
    {'kind': 'req', 'id': '2', 'url': 'https://x.com/post',
     'method': 'POST', 'type': 'Fetch',
     'headers': {'Content-Type': 'application/json'},
     'postData': '{"a":1}'},
    {'kind': 'resp', 'id': '2', 'url': 'https://x.com/post',
     'status': 201, 'headers': {'content-type': 'application/json'}},
    {'kind': 'req', 'id': '3', 'url': 'https://x.com/doc',
     'method': 'GET', 'type': 'Document'},
    {'kind': 'req', 'id': '4', 'url': 'https://x.com/app.js',
     'method': 'GET', 'type': 'Script'},
]


@pytest.mark.unit
def test_requests_export_curl(runner, monkeypatch, tmp_path):
    """requests --export curl：聚合行导出 curl 命令（method/URL/头/postData）。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['requests', '--export', 'curl'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output.strip())
    assert d['data']['format'] == 'curl'
    assert d['data']['count'] == 4
    ex = {e['idx']: e for e in d['data']['exports']}
    get1 = ex[0]['text']
    assert get1.startswith('curl -X GET')
    assert 'https://x.com/api/u?p=1&k=2' in get1
    assert "-H 'Accept: application/json'" in get1
    post2 = ex[1]['text']
    assert post2.startswith('curl -X POST')
    assert "--data-raw '{\"a\":1}'" in post2
    assert ex[1]['method'] == 'POST'


@pytest.mark.unit
def test_requests_export_curl_filters_pseudo_headers(runner, monkeypatch,
                                                     tmp_path):
    """HTTP/2 伪头（:authority）与 content-length 不进 curl 导出。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, [
        {'kind': 'req', 'id': '1', 'url': 'https://x.com/a',
         'method': 'POST', 'type': 'XHR',
         'headers': {':authority': 'x.com', 'Content-Length': '3',
                     'X-A': '1'},
         'postData': 'abc'}])
    r = runner.invoke(cli, ['requests', '--export', 'curl'])
    d = json.loads(r.output.strip())
    text = d['data']['exports'][0]['text']
    assert ':authority' not in text and 'content-length' not in text.lower()
    assert "-H 'X-A: 1'" in text


@pytest.mark.unit
def test_requests_export_python(runner, monkeypatch, tmp_path):
    """requests --export python：requests 库片段。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['requests', '--export', 'python', '--index', '1'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output.strip())
    assert d['data']['format'] == 'python'
    snip = d['data']['exports'][0]['text']
    assert 'import requests' in snip
    assert "requests.request('POST', 'https://x.com/post'" in snip
    assert 'data=' in snip


@pytest.mark.unit
def test_requests_export_index_and_bounds(runner, monkeypatch, tmp_path):
    """--index 走完整日志序号（同 api-list idx）；越界 REQUEST_NOT_FOUND。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['requests', '--export', 'curl', '--index', '2'])
    d = json.loads(r.output.strip())
    ex = d['data']['exports'][0]
    assert ex['idx'] == 2 and ex['url'] == 'https://x.com/doc'
    r = runner.invoke(cli, ['requests', '--export', 'curl', '--index', '99'])
    assert 'REQUEST_NOT_FOUND' in r.output
    # --index 与 --all 互斥
    r = runner.invoke(cli, ['requests', '--export', 'curl',
                            '--index', '0', '--all'])
    assert 'CONFLICTING_OPTIONS' in r.output


@pytest.mark.unit
def test_requests_export_empty_list(runner, monkeypatch, tmp_path):
    """过滤后无匹配 → EMPTY_LIST。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['requests', '--export', 'curl',
                            '--filter', 'nonexistent/'])
    assert r.exit_code != 0
    assert 'EMPTY_LIST' in r.output


@pytest.mark.unit
def test_requests_export_no_netlog(runner, monkeypatch, tmp_path):
    """无缓冲文件 → NO_NETLOG。"""
    from dp_cli.main import cli
    import dp_cli.netlog as nl
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'netlog-{s}.jsonl')
    r = runner.invoke(cli, ['requests', '--export', 'curl'])
    assert 'NO_NETLOG' in r.output


@pytest.mark.unit
def test_api_list_filters_xhr_fetch(runner, monkeypatch, tmp_path):
    """api-list：只列 XHR/Fetch，Document/Script 排除；idx 同 requests 序号。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['api-list'])
    assert r.exit_code == 0, r.output
    eps = json.loads(r.output.strip())['data']['endpoints']
    assert len(eps) == 2
    urls = {e['url'] for e in eps}
    assert 'https://x.com/doc' not in urls           # Document 排除
    assert 'https://x.com/app.js' not in urls        # Script 排除
    assert {e['idx'] for e in eps} == {0, 1}         # 完整日志序号
    a = next(e for e in eps if e['id'] == '1')
    assert a['method'] == 'GET'
    assert a['params_keys'] == ['p', 'k']
    assert a['url_template'] == 'https://x.com/api/u?p={p}&k={k}'
    assert a['content_type'] == 'application/json'
    assert a['status'] == 200


@pytest.mark.unit
def test_api_list_template_and_filters(runner, monkeypatch, tmp_path):
    """api-list：无 query 端点模板=URL 本身；--status/--method 过滤生效。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    r = runner.invoke(cli, ['api-list', '--method', 'POST'])
    eps = json.loads(r.output.strip())['data']['endpoints']
    assert len(eps) == 1 and eps[0]['url'] == 'https://x.com/post'
    assert eps[0]['url_template'] == 'https://x.com/post'
    assert eps[0]['params_keys'] == []
    assert eps[0]['status'] == 201
    r = runner.invoke(cli, ['api-list', '--status', '404'])
    assert 'EMPTY_LIST' in r.output


@pytest.mark.unit
def test_api_list_no_netlog_and_empty(runner, monkeypatch, tmp_path):
    """api-list：无缓冲 NO_NETLOG；仅 Document/静态资源 EMPTY_LIST。"""
    from dp_cli.main import cli
    import dp_cli.netlog as nl
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'netlog-{s}.jsonl')
    r = runner.invoke(cli, ['api-list'])
    assert 'NO_NETLOG' in r.output
    _write_netlog(tmp_path, monkeypatch, [
        {'kind': 'req', 'id': '1', 'url': 'https://x.com/doc',
         'method': 'GET', 'type': 'Document'}])
    r = runner.invoke(cli, ['api-list'])
    assert r.exit_code == 4
    assert 'EMPTY_LIST' in r.output


@pytest.mark.unit
def test_api_list_idx_replays_via_requests_export(runner, monkeypatch,
                                                  tmp_path):
    """api-list idx → requests --export curl --index 直接重放同一端点。"""
    from dp_cli.main import cli
    _write_netlog(tmp_path, monkeypatch, _NETLOG_SAMPLE)
    eps = json.loads(runner.invoke(cli, ['api-list']).output.strip()
                   )['data']['endpoints']
    idx = next(e['idx'] for e in eps if e['id'] == '2')
    r = runner.invoke(cli, ['requests', '--export', 'curl',
                            '--index', str(idx)])
    ex = json.loads(r.output.strip())['data']['exports'][0]
    assert ex['url'] == 'https://x.com/post'
    assert ex['method'] == 'POST'


class _ArmPage:
    """arm_netlog 用假 tab：tab_id + 记录 set_callback 的 driver。"""

    def __init__(self, tab_id):
        self.tab_id = tab_id
        self.cbs = {}
        self.calls = []

        class _Drv:
            def __init__(self, outer):
                self._o = outer

            def set_callback(self, ev, cb):
                self._o.calls.append(ev)
                self._o.cbs[ev] = cb

        self.driver = _Drv(self)

    def run_cdp(self, method, **kw):
        return {}


@pytest.mark.unit
def test_arm_netlog_rearm_on_tab_change(tmp_path, monkeypatch):
    """_armed 记 session→tab_id：latest_tab 换 tab（旧 driver WS 死）
    后重挂，同 tab 幂等不重挂。"""
    import dp_cli.netlog as nl
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'{s}.jsonl')
    nl._armed.clear()
    try:
        p1, p2 = _ArmPage('t1'), _ArmPage('t2')
        assert nl.arm_netlog(p1, 's') is True
        assert len(p1.calls) == 3 and nl._armed['s'] == 't1'
        # 同 tab 幂等：不重复注册
        assert nl.arm_netlog(p1, 's') is True
        assert len(p1.calls) == 3
        # tab 变了 → 在新 driver 上重挂，_armed 跟到新 tab
        assert nl.arm_netlog(p2, 's') is True
        assert len(p2.calls) == 3 and nl._armed['s'] == 't2'
    finally:
        nl._armed.clear()


@pytest.mark.unit
def test_arm_netlog_disarm_on_write_failure(tmp_path, monkeypatch):
    """回调写入路径异常 → 摘除 _armed，下次 _maybe_arm_netlog 可重挂。"""
    import dp_cli.netlog as nl
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'{s}.jsonl')
    nl._armed.clear()
    try:
        p = _ArmPage('t1')
        assert nl.arm_netlog(p, 's') is True
        assert nl._armed['s'] == 't1'

        def boom(*a, **k):
            raise RuntimeError('write failed')

        monkeypatch.setattr(nl, '_append', boom)
        p.cbs['Network.requestWillBeSent'](requestId='r', request={})
        assert 's' not in nl._armed
        # 摘除后同 tab 也能重挂
        monkeypatch.setattr(nl, '_append', lambda *a, **k: None)
        assert nl.arm_netlog(p, 's') is True
        assert nl._armed['s'] == 't1'
    finally:
        nl._armed.clear()


@pytest.mark.unit
def test_request_detail_missing(runner, monkeypatch, tmp_path):
    from dp_cli.main import cli
    import dp_cli.netlog as nl
    (tmp_path / 'netlog-default.jsonl').write_text(
        json.dumps({'kind': 'req', 'id': '1', 'url': 'https://x'}) + '\n')
    monkeypatch.setattr(nl, '_path',
                        lambda s: tmp_path / f'netlog-{s}.jsonl')
    r = runner.invoke(cli, ['request', '999'])
    assert 'REQUEST_NOT_FOUND' in r.output


@pytest.mark.unit
def test_host_allowed_malformed_denied():
    """畸形 URL（解析失败/无 host 的 http 形态）→ fail-closed 拒绝。"""
    from dp_cli.commands.emulate import _host_allowed
    assert not _host_allowed('https://', ['ex.com'])          # 无 host
    assert not _host_allowed('http://:bad/x', ['ex.com'])     # 空 host
    assert not _host_allowed('//nohost', ['ex.com'])          # 协议相对无 host


@pytest.mark.unit
def test_host_allowed_internal_schemes_pass():
    """data:/blob:/about:/chrome-extension: 等内部 scheme 放行（页面必需）。"""
    from dp_cli.commands.emulate import _host_allowed
    for u in ('data:text/html,<h1>x</h1>', 'about:blank',
              'blob:https://x.com/uuid', 'javascript:void(0)',
              'chrome-extension://abc/popup.html'):
        assert _host_allowed(u, ['ex.com']), u


@pytest.mark.unit
def test_host_allowed_file_denied_default():
    """file:// 默认拒绝，allow_file=True 放行。"""
    from dp_cli.commands.emulate import _host_allowed
    assert not _host_allowed('file:///etc/passwd', ['ex.com'])
    assert _host_allowed('file:///tmp/x.html', ['ex.com'], allow_file=True)


@pytest.mark.unit
def test_host_allowed_unknown_scheme_denied():
    """未知 scheme（ftp/custom-app）→ 拒绝；ws/wss 走 host 判定。"""
    from dp_cli.commands.emulate import _host_allowed
    assert not _host_allowed('ftp://ex.com/file', ['ex.com'])
    assert not _host_allowed('myapp://ex.com/open', ['ex.com'])
    assert _host_allowed('wss://api.ex.com/ws', ['ex.com'])
    assert not _host_allowed('wss://evil.com/ws', ['ex.com'])


@pytest.mark.unit
def test_wait_popup_diff_detects_new_tab(runner, monkeypatch):
    """wait --popup：tab_ids 出现新 id → 返回 tab_id。"""
    from dp_cli.main import cli

    class P(FakePage):
        _calls = 0

        @property
        def tab_ids(self):
            self._calls += 1
            return ['t1'] if self._calls < 3 else ['t1', 't2']

    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['wait', '--popup', '--timeout', '5'])
    assert r.exit_code == 0, r.output
    assert '"tab_id": "t2"' in r.output


@pytest.mark.unit
def test_wait_popup_timeout(runner, monkeypatch):
    from dp_cli.main import cli

    class P(FakePage):
        tab_ids = ['t1']

    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['wait', '--popup', '--timeout', '1'])
    assert 'WAIT_TIMEOUT' in r.output


@pytest.mark.unit
def test_sleep_negative_rejected(runner):
    from dp_cli.main import cli
    r = runner.invoke(cli, ['sleep', '--', '-1'])
    assert 'INVALID_ARGS' in r.output or r.exit_code != 0


@pytest.mark.unit
def test_state_save_all_origins(runner, monkeypatch):
    """state-save pw 格式：iframe 各 origin localStorage 都进 origins[]。"""
    import dp_cli.commands.misc as misc_mod

    class Frame:
        def run_js(self, s, *a, **k):
            return json.dumps({'o': 'https://cdn.x.com',
                               'items': [{'name': 'k2', 'v': '2'}]})
        def eles(self, *a, **k):
            return []

    class Fe:
        frame = Frame()

    class P(FakePage):
        url = 'https://main.com'
        def run_js(self, s, *a, **k):
            return json.dumps({'o': 'https://main.com',
                               'items': [{'name': 'k1', 'v': '1'}]})
        def eles(self, *a, **k):
            return [Fe()]
        def cookies(self, **kw):
            return []

    st = misc_mod._playwright_storage_state(P())
    origins = {o['origin'] for o in st['origins']}
    assert origins == {'https://main.com', 'https://cdn.x.com'}


@pytest.mark.unit
def test_wait_download_no_dir_errors(runner, monkeypatch):
    """wait --download 在 sess['download_dir'] 未设置时给出可行动错误。

    判定用 session 标记而非 page.download_path（DP 默认 cwd 恒 truthy）。
    """
    from dp_cli.main import cli

    class P(FakePage):
        download_path = '/default/cwd'  # 恒 truthy 也不得放行

    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: {})
    r = runner.invoke(cli, ['wait', '--download', '--timeout', '1'])
    assert 'DOWNLOAD_FAILED' in r.output
    assert 'dpx download --dir' in r.output


@pytest.mark.unit
def test_wait_popup_uses_browser_attr(runner, monkeypatch):
    """wait --popup：MixTab 无 tab_ids 时经 .browser 枚举（v1 回归）。"""
    from dp_cli.main import cli

    class _Browser:
        _calls = 0

        @property
        def tab_ids(self):
            self._calls += 1
            return ['t1'] if self._calls < 3 else ['t1', 't9']

    class MixTabLike:
        """模拟 _get_page 返回的 tab 对象：自身无 tab_ids，browser 有。"""
        browser = _Browser()

    monkeypatch.setattr(_utils, 'get_browser', lambda s: MixTabLike())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['wait', '--popup', '--timeout', '5'])
    assert r.exit_code == 0, r.output
    assert '"tab_id": "t9"' in r.output


@pytest.mark.unit
def test_wait_popup_no_tab_enum_errors(runner, monkeypatch):
    """tab_ids 完全不可得 → POPUP_FAILED 而非静默超时。"""
    from dp_cli.main import cli

    class Bare:
        pass

    monkeypatch.setattr(_utils, 'get_browser', lambda s: Bare())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['wait', '--popup', '--timeout', '1'])
    assert 'POPUP_FAILED' in r.output


@pytest.mark.unit
def test_wait_download_rearms_and_completes(runner, monkeypatch):
    """sess 有 download_dir → 重武装 _dl_mgr 并轮询 missions 完成。"""
    from dp_cli.main import cli

    class _M:
        name = 'f.bin'
        final_path = '/tmp/dl/f.bin'
        state = 'completed'
        is_done = True

    class _Mgr:
        missions = {'g1': _M()}

    class _Set:
        def download_path(self, p):
            self.path = p

    class _Br:
        _dl_mgr = _Mgr()

    class P(FakePage):
        set = _Set()
        browser = _Br()

    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session',
                        lambda s: {'download_dir': '/tmp/dl'})
    monkeypatch.setattr('dp_cli.session.load_session',
                        lambda s: {'download_dir': '/tmp/dl'})
    r = runner.invoke(cli, ['wait', '--download', '--timeout', '2'])
    assert r.exit_code == 0, r.output
    assert 'f.bin' in r.output


@pytest.mark.unit
def test_download_dir_persists_to_session(runner, monkeypatch, tmp_path):
    """download --dir 写 sess['download_dir']（跨进程判定依据）。"""
    from dp_cli.main import cli
    import dp_cli.commands.emulate as em
    saved = {}

    class P(FakePage):
        class _Set:
            def download_path(self, p):
                pass

            def when_download_file_exists(self, w):
                pass

        set = _Set()
        browser = type('B', (), {'_dl_mgr': None})()

    monkeypatch.setattr(em, '_get_page', lambda s: P())
    monkeypatch.setattr(em, 'load_session', lambda s: {})
    monkeypatch.setattr(em, 'save_session',
                        lambda s, d: saved.update(d))
    r = runner.invoke(cli, ['download', '--dir', str(tmp_path),
                            '--timeout', '1'])
    assert r.exit_code == 0, r.output
    assert saved.get('download_dir') == str(tmp_path.resolve())


@pytest.mark.unit
def test_wait_download_dir_fallback(runner, monkeypatch, tmp_path):
    """missions 空时回扫 download_dir：返回已存在文件（排除 crdownload）。"""
    from dp_cli.main import cli
    (tmp_path / 'done.bin').write_bytes(b'x' * 10)
    (tmp_path / 'wip.crdownload').write_bytes(b'x')

    class _Mgr:
        missions = {}

    class _Set:
        def download_path(self, p):
            pass

    class _Br:
        _dl_mgr = _Mgr()

    class P(FakePage):
        set = _Set()
        browser = _Br()

    sess = {'download_dir': str(tmp_path)}
    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: sess)
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: sess)
    r = runner.invoke(cli, ['wait', '--download', '--timeout', '1'])
    assert r.exit_code == 0, r.output
    assert 'done.bin' in r.output
    assert 'wip.crdownload' not in r.output
    assert '目录回扫' in r.output


@pytest.mark.unit
def test_wait_download_fallback_empty_dir(runner, monkeypatch, tmp_path):
    """missions 空且目录也空 → WAIT_TIMEOUT + 明确 detail。"""
    from dp_cli.main import cli

    class _Mgr:
        missions = {}

    class _Set:
        def download_path(self, p):
            pass

    class _Br:
        _dl_mgr = _Mgr()

    class P(FakePage):
        set = _Set()
        browser = _Br()

    sess = {'download_dir': str(tmp_path)}
    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: sess)
    monkeypatch.setattr('dp_cli.session.load_session', lambda s: sess)
    r = runner.invoke(cli, ['wait', '--download', '--timeout', '1'])
    assert 'WAIT_TIMEOUT' in r.output
    assert '不可追溯' in r.output


@pytest.mark.unit
def test_daemon_session_parse():
    from dp_cli.daemon import _session_of, _cmd_timeout
    assert _session_of(['eval', 'x', '--session', 's1']) == 's1'
    assert _session_of(['eval', 'x', '-S', 's2']) == 's2'
    assert _session_of(['eval', 'x', '--session=s3']) == 's3'
    assert _session_of(['eval', 'x']) == 'default'
    assert _cmd_timeout(['wait', '--timeout', '60']) == 75.0
    assert _cmd_timeout(['wait', '--timeout=45']) == 60.0


@pytest.mark.unit
def test_daemon_routes_only_when_enabled(runner, monkeypatch, tmp_path):
    """无 socket 且无 DPX_DAEMON → try_daemon 返回 None 走本地。"""
    from dp_cli.daemon import try_daemon
    monkeypatch.delenv('DPX_DAEMON', raising=False)
    monkeypatch.setattr('dp_cli.daemon._sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    assert try_daemon(['eval', '1']) is None


@pytest.mark.unit
def test_daemon_socket_roundtrip(tmp_path, monkeypatch, sock_dir):
    """真实 unix socket 往返：起 run_daemon 线程 → _send_recv → 关。"""
    import socket as sk
    import threading
    from dp_cli import daemon as dm
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    monkeypatch.setattr(dm, '_sock_path', lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(dm, '_meta_path', lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_IDLE_TTL', 99999)

    t = threading.Thread(target=dm.run_daemon, args=('test',), daemon=True)
    t.start()
    import time as _t
    # 轮询 meta 而非 sock——meta 在 srv.listen() 后才落盘，
    # sock 文件 bind 即出现、listen 前 connect 会 ECONNREFUSED（macOS 竞态）
    for _ in range(40):
        if (tmp_path / 'test.session').exists():
            break
        _t.sleep(0.1)
    r = dm._send_recv('test', {'argv': ['-V']}, 10)
    assert isinstance(r, dict) and r.get('exit') == 0


@pytest.mark.unit
def test_daemon_skip_cmds_not_routed(monkeypatch):
    """serve/sh/batch/daemon-* 不走 daemon 路由。"""
    from dp_cli.daemon import try_daemon
    import os
    os.environ['DPX_DAEMON'] = '1'
    try:
        for cmd in (['serve'], ['sh'], ['batch', 'f.txt'],
                    ['daemon-status'], ['daemon-stop'], ['daemon-start'],
                    ['catalog'], ['install', '--skills']):
            assert try_daemon(cmd) is None, cmd
    finally:
        del os.environ['DPX_DAEMON']


@pytest.mark.unit
def test_daemon_spawn_concurrent_once(tmp_path, monkeypatch):
    """8 线程并发 spawn：flock 下恰好起 1 次（bridge_registry 同款）。"""
    import threading
    from dp_cli import daemon as dm
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    monkeypatch.setattr(dm, '_sock_path', lambda s: tmp_path / f'{s}.sock')
    # 就绪判定按 POSIX 语义走 sock 文件——Windows 的 meta+TCP 探活
    # 由 test_windows_ready_* 专项覆盖，此处聚焦锁的互斥语义
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', True)
    spawns = []

    def fake_popen(*a, **k):
        spawns.append(1)
        (tmp_path / 'x.sock').write_text('')   # 模拟 socket 就绪

    monkeypatch.setattr(dm.subprocess, 'Popen', fake_popen)
    threads = [threading.Thread(target=dm._spawn_daemon, args=('x',))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(spawns) == 1


@pytest.mark.unit
def test_daemon_stop_reaches_handler(tmp_path, monkeypatch, sock_dir):
    """daemon-stop 不被 _SKIP_CMDS 拦——handler 判定顺序（死代码修复）。"""
    import socket as sk
    import threading
    from dp_cli import daemon as dm
    monkeypatch.setattr(dm, '_sock_path', lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(dm, '_meta_path', lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    t = threading.Thread(target=dm.run_daemon, args=('z',), daemon=True)
    t.start()
    import time as _t
    # 同上：meta 在 listen 后落盘——sock exists 轮询会撞 ECONNREFUSED
    for _ in range(40):
        if (tmp_path / 'z.session').exists():
            break
        _t.sleep(0.1)
    r = dm._send_recv('z', {'argv': ['daemon-stop']}, 10)
    assert isinstance(r, dict) and r.get('exit') == 0
    assert '退出中' in json.dumps(r, ensure_ascii=False)


@pytest.mark.unit
def test_daemon_flag_value_not_cmd(monkeypatch, tmp_path):
    """['--session','s','eval','1'] 的 's' 不得被当命令名。"""
    from dp_cli.daemon import try_daemon
    monkeypatch.setattr('dp_cli.daemon._sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    monkeypatch.delenv('DPX_DAEMON', raising=False)
    # sock 不存在 + 无 env → 应返回 None（'eval' 不在 skip，但未启用不路由）
    assert try_daemon(['--session', 's', 'eval', '1']) is None
    # daemon-status 在 skip——验证命令名解析对了（session 值 's' 不算命令）
    r = try_daemon(['--session', 'default', 'daemon-status'])
    assert r is None  # skip 名单命中（证明解析出 daemon-status 而非 'default'）


@pytest.mark.unit
def test_fill_form_multi_ok(runner, monkeypatch):
    """fill-form 多字段全成功。"""
    from dp_cli.main import cli
    filled = []

    class E(FakeEle):
        def input(self, v, clear=True, by_js=False):
            filled.append(v)

    monkeypatch.setattr(_utils, 'get_browser',
                        lambda s: FakePage(ele=E()))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['fill-form', '#u=admin', '#p=pw'])
    assert r.exit_code == 0, r.output
    assert filled == ['admin', 'pw']
    assert '"filled": 2' in r.output


@pytest.mark.unit
def test_fill_form_partial_fail_detail(runner, monkeypatch):
    """单字段失败：fields 明细 + 非零退出。"""
    from dp_cli.main import cli

    class E(FakeEle):
        def input(self, v, clear=True, by_js=False):
            pass

    class P(FakePage):
        def ele(self, locator, index=1, timeout=10):
            return E() if 'good' in locator else NoneEle()

    monkeypatch.setattr(_utils, 'get_browser', lambda s: P())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['fill-form', '#good=1', '#bad=2', '--no-wait'])
    assert r.exit_code != 0
    assert '"failed": 1' in r.output and '"ok": false' in r.output


@pytest.mark.unit
def test_fill_form_bad_json(runner, monkeypatch):
    from dp_cli.main import cli
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    r = runner.invoke(cli, ['fill-form', '--data', '{broken'])
    assert 'INVALID_JSON' in r.output


@pytest.mark.unit
def test_mcp_tool_table_and_schema():
    """白名单工具表：数量/schema/unsafe-eval 门控/argv 映射。"""
    from dp_cli import mcp as m
    assert len(m._TOOLS) >= 30
    assert 'eval' in m._UNSAFE
    s = m._schema('click', m._TOOLS['click'])
    assert s['required'] == ['locator']
    assert s['properties']['force']['type'] == 'boolean'
    argv = m._to_argv('fill', m._TOOLS['fill'],
                      {'locator': '#u', 'value': 'admin'})
    assert argv == ['fill', '#u', 'admin']
    argv2 = m._to_argv('screenshot', m._TOOLS['screenshot'],
                       {'path': 'a.png', 'annotate': True})
    assert argv2 == ['screenshot', '--filename', 'a.png', '--annotate']


@pytest.mark.unit
def test_mcp_without_sdk_actionable(runner, monkeypatch):
    """mcp 包缺失 → 可行动提示（MCP_FAILED）。"""
    from dp_cli.main import cli
    import dp_cli.mcp as mm
    monkeypatch.setattr(mm, 'run_mcp', lambda *a, **k: 1)
    r = runner.invoke(cli, ['mcp'])
    assert 'MCP_FAILED' in r.output


@pytest.mark.unit
def test_netlog_armed_before_invoke(tmp_path, monkeypatch, sock_dir):
    """daemon 路径：_maybe_arm_netlog 在 _invoke 前（首导航命令流量可捕获）。"""
    import threading
    import dp_cli.daemon as dm
    order = []
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    monkeypatch.setattr(dm, '_sock_path', lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(dm, '_meta_path', lambda s: tmp_path / f'{s}.session')

    import dp_cli.commands.serve as sv
    import dp_cli.commands.batch as bt
    monkeypatch.setattr(sv, '_maybe_arm_netlog',
                        lambda: order.append('arm'))
    monkeypatch.setattr(bt, '_invoke',
                        lambda argv: (order.append('invoke'), (0, {}))[1])

    t = threading.Thread(target=dm.run_daemon, args=('ord',), daemon=True)
    t.start()
    import time as _t
    # 同上：meta 在 listen 后落盘——sock exists 轮询会撞 ECONNREFUSED
    for _ in range(40):
        if (tmp_path / 'ord.session').exists():
            break
        _t.sleep(0.1)
    r = dm._send_recv('ord', {'argv': ['eval', '1']}, 10)
    assert order == ['arm', 'invoke']


@pytest.mark.unit
def test_fill_form_eq_in_locator_hint(runner, monkeypatch):
    """fill-form pair 模式 '@name=x' 会被 split 拆错——提示走 --data。"""
    from dp_cli.main import cli
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(_utils, 'load_refs', lambda s: {})
    r = runner.invoke(cli, ['fill-form', '@name=pw', '--no-wait'])
    # 已有 '@name=' 引导改用 --data 的 INVALID_ARGS 提示
    assert 'INVALID_ARGS' in r.output and '--data' in r.output


@pytest.mark.unit
def test_agent_state_format(runner, monkeypatch):
    """agent-state：Jev 控件表格式 + epoch 版本戳。"""
    from dp_cli.main import cli

    class N(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    tree = {'role': 'document', 'children': [
        {'role': 'button', 'name': 'Login', 'locator': '#btn'},
        {'role': 'textbox', 'name': 'Email', 'value': ''},
        {'role': 'StaticText', 'name': 'skip'},
        {'role': 'combobox', 'name': 'City', 'locator': '#sel'},
    ]}
    monkeypatch.setattr('dp_cli.snapshot.a11y.take_a11y_snapshot',
                        lambda *a, **k: {'tree': tree})
    _fp = FakePage()
    _fp.url = 'https://x.com'
    monkeypatch.setattr(_utils, 'get_browser', lambda s: _fp)
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr('dp_cli.agent.load_session', lambda s: {})
    monkeypatch.setattr('dp_cli.agent.save_session',
                        lambda *a, **k: None)
    monkeypatch.setattr('dp_cli.agent.save_refs',
                        lambda *a, **k: None)
    r = runner.invoke(cli, ['agent-state'])
    assert r.exit_code == 0, r.output
    import json as _j
    d = _j.loads(r.output)['data']
    roles = [o['role'] for o in d['options']]
    assert roles == ['button', 'textbox', 'combobox']  # StaticText 滤掉
    assert d['epoch'] and d['state']['url'] == 'https://x.com'


@pytest.mark.unit
def test_decide_external_and_unsure(runner, monkeypatch):
    """decide external：内联 answer → 动作回传；低置信 → AGENT_UNSURE。"""
    from dp_cli.main import cli

    monkeypatch.setattr('dp_cli.agent._page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'Go'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr('dp_cli.agent.load_session', lambda s: {})
    monkeypatch.setattr('dp_cli.agent.save_session',
                        lambda *a, **k: None)
    # conf 门依赖 fam≠bge——防 _RERANKER_FAM 跨用例泄漏
    monkeypatch.setattr('dp_cli.agent._RERANKER_FAM', None)

    r = runner.invoke(cli, ['decide', 'go', '--engine', 'external',
                            '--answer',
                            '{"action":"type","target":1,'
                            '"done":false,"probs":{"type":0.9}}'])
    assert r.exit_code == 0, r.output
    assert '"needs_value": true' in r.output
    assert '"ref": 1' in r.output

    r2 = runner.invoke(cli, ['decide', 'go', '--engine', 'external',
                             '--answer', '{"action":"click","target":1,'
                                         '"confidence":0.1}'])
    assert 'AGENT_UNSURE' in r2.output


@pytest.mark.unit
def test_decide_stuck_detection(runner, monkeypatch):
    """连续 N 步哈希不变 → stuck:true（确定性信号）。"""
    from dp_cli.main import cli
    import dp_cli.agent as am

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'Go'}],
                            'epoch': 'same', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session',
                        lambda s: {'agent_hash_hist': ['same', 'same']})
    monkeypatch.setattr(am, 'load_session',
                        lambda s: {'agent_hash_hist': ['same', 'same']})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    r = runner.invoke(cli, ['decide', 'go', '--engine', 'external',
                            '--answer',
                            '{"action":"click","target":1,'
                            '"confidence":0.9}'])
    assert '"stuck": true' in r.output


@pytest.mark.unit
def test_decide_cascade_returns_candidates(runner, monkeypatch):
    """--cascade：返回 top-3 候选集，不自动执行。"""
    from dp_cli.main import cli
    import dp_cli.agent as am

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'},
                                        {'ref': 2, 'role': 'link',
                                         'name': 'B'},
                                        {'ref': 3, 'role': 'link',
                                         'name': 'C'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'target': 2, 'probs': {'target': {'1': 0.6, '2': 0.3, '3': 0.1}}},
        {}))
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    r = runner.invoke(cli, ['decide', 'go', '--engine', 'external',
                            '--cascade'])
    assert r.exit_code == 0, r.output
    assert '"candidates"' in r.output and '"ref": 1' in r.output


@pytest.mark.unit
def test_decide_margin_gate(runner, monkeypatch):
    """margin 门控：top1-top2 差小 → AGENT_UNSURE 即使 conf 高。

    默认阈值校准为 0.005（eval-120：可拦 miss margin≤0.0028）——
    margin=0.002 仍拒；0.01/0.03 已放行（旧值 0.15 的假拒修复）。
    """
    from dp_cli.main import cli
    import dp_cli.agent as am

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'},
                                        {'ref': 2, 'role': 'link',
                                         'name': 'B'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)

    # margin=min(0.06, 0.002)=0.002 < 0.005 → AGENT_UNSURE
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9,
         'probs': {'action': {'click': 0.53, 'type': 0.47},
                   'target': {'1': 0.501, '2': 0.499}}},
        {}))
    r = runner.invoke(cli, ['decide', 'go', '--engine', 'external'])
    assert 'AGENT_UNSURE' in r.output

    # margin=0.01 ≥ 0.005 → 放行（旧默认 0.15 下会误拒）
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9,
         'probs': {'action': {'click': 0.55, 'type': 0.45},
                   'target': {'1': 0.505, '2': 0.495}}},
        {}))
    r2 = runner.invoke(cli, ['decide', 'go', '--engine', 'external'])
    assert r2.exit_code == 0, r2.output
    assert 'AGENT_UNSURE' not in r2.output and '"ref": 1' in r2.output


@pytest.mark.unit
def test_record_dp_export_fallback_chain(monkeypatch):
    """dp 导出含 '||' 备用定位器回退链，且 batch-clean（无 set -e）。"""
    from dp_cli.recorder import export_actions
    actions = [{'type': 'click',
                'best_locator': 'css:#btn',
                'element': {'locators': {'css': 'css:.a > b',
                                         'xpath': 'xpath://div[1]'}}}]
    out = export_actions(actions, 'dp')
    assert 'set -e' not in out
    line = [l for l in out.splitlines() if l.startswith('dpx click')][0]
    assert ' || ' in line and 'css:#btn' in line and 'xpath://div' in line


@pytest.mark.unit
def test_batch_or_fallback(runner, monkeypatch):
    """batch 'a || b'：首选失败自动降级执行备选。"""
    from dp_cli.main import cli
    calls = []

    def fake_invoke(argv):
        calls.append(argv)
        if argv and argv[0] == 'failcmd':
            return 1, {'status': 'error', 'code': 'X'}
        return 0, {'status': 'ok', 'data': argv[0]}

    monkeypatch.setattr('dp_cli.commands.batch._invoke', fake_invoke)
    import io, sys
    r = runner.invoke(cli, ['batch'], input='failcmd x || eval 1\n')
    assert '"exit": 0' in r.output
    assert '"used": "eval 1"' in r.output
    assert calls[0] == ['failcmd', 'x'] and calls[1] == ['eval', '1']


@pytest.mark.unit
def test_daemon_spawn_lock_no_fcntl(tmp_path, monkeypatch):
    """fcntl 缺失（Windows）→ O_EXCL 原子锁降级路径。"""
    import dp_cli.daemon as dm
    import builtins
    real_import = builtins.__import__

    def no_fcntl(name, *a, **k):
        if name == 'fcntl':
            raise ImportError('No module named fcntl')
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, '__import__', no_fcntl)
    lock = dm._acquire_spawn_lock(tmp_path / 's.lock', timeout=1)
    with lock:
        pass
    assert not (tmp_path / 's.lock').exists()  # 释放即删

    # 持有期第二把锁超时
    held = dm._acquire_spawn_lock(tmp_path / 's2.lock', timeout=1)
    held.__enter__()
    try:
        with pytest.raises(TimeoutError):
            with dm._acquire_spawn_lock(tmp_path / 's2.lock',
                                        timeout=0.2):
                pass
    finally:
        held.__exit__(None, None, None)


@pytest.mark.unit
def test_daemon_spawn_kwargs_platform():
    """spawn 脱离参数分平台：POSIX setsid / 非 POSIX creationflags。"""
    import dp_cli.daemon as dm
    import os as _os
    if _os.name == 'posix':
        assert dm._spawn_kwargs() == {'start_new_session': True}
    else:
        assert 'creationflags' in dm._spawn_kwargs() or \
            dm._spawn_kwargs() == {}


@pytest.mark.unit
def test_daemon_tcp_transport_windows(tmp_path, monkeypatch):
    """无 AF_UNIX 平台：daemon 走 TCP 回环，meta 写 port。"""
    import dp_cli.daemon as dm

    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    # _connect_endpoint 从 meta 读端口
    (tmp_path / 's.session').write_text(
        '{"pid":1,"transport":"tcp","port":54321}', encoding='utf-8')
    assert dm._connect_endpoint('s') == ('127.0.0.1', 54321)
    monkeypatch.setattr(dm, '_tcp_alive', lambda s: True)
    assert dm._daemon_ready('s')
    # meta 缺 port → 端点退化 0 号
    (tmp_path / 'bad.session').write_text('{"pid":1}', encoding='utf-8')
    assert dm._connect_endpoint('bad') == ('127.0.0.1', 0)
    # POSIX 分支不动
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', True)
    assert dm._connect_endpoint('s') == str(tmp_path / 's.sock')


@pytest.mark.unit
def test_daemon_send_recv_tcp_roundtrip(tmp_path, monkeypatch):
    """TCP 模式 _send_recv 能连真实 localhost 服务。"""
    import dp_cli.daemon as dm
    import socket as sk, threading as th, json as js

    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)

    srv = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    (tmp_path / 't.session').write_text(
        js.dumps({'port': port}), encoding='utf-8')

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b'{"exit":0,"result":{"ok":1}}\n')
        conn.close()
        srv.close()
    th.Thread(target=serve, daemon=True).start()

    r = dm._send_recv('t', {'argv': ['x']}, 5)
    assert r['exit'] == 0 and r['result']['ok'] == 1


@pytest.mark.unit
def test_decide_margin_source_field(runner, monkeypatch):
    """margin_source 显式标注：probs 双侧/单边/conf_fallback。"""
    from dp_cli.main import cli
    import dp_cli.agent as am

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'},
                                        {'ref': 2, 'role': 'link',
                                         'name': 'B'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)

    # 单边 probs → single_side
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9,
         'probs': {'action': {'click': 0.9, 'type': 0.1}}}, {}))
    r = runner.invoke(cli, ['decide', 'g', '--engine', 'external'])
    assert '"margin_source": "single_side"' in r.output

    # 无 probs → conf_fallback
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9}, {}))
    r2 = runner.invoke(cli, ['decide', 'g', '--engine', 'external'])
    assert '"margin_source": "conf_fallback"' in r2.output


@pytest.mark.unit
def test_spawn_lock_stale_reclaim(tmp_path, monkeypatch):
    """O_EXCL stale：持锁进程死掉（pid 不存活）→ 锁自动回收。"""
    import dp_cli.daemon as dm
    import builtins
    real_import = builtins.__import__

    def no_fcntl(name, *a, **k):
        if name == 'fcntl':
            raise ImportError
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_fcntl)

    lp = tmp_path / 's.lock'
    lp.write_text('-99999')   # 死 pid
    import os as _o, time as _t
    _o.utime(str(lp), (_t.time() - 10, _t.time() - 10))  # 老文件
    with dm._acquire_spawn_lock(lp, timeout=2):
        pass
    assert not lp.exists()


@pytest.mark.unit
def test_spawn_lock_eacces_contention(tmp_path, monkeypatch):
    """Windows EACCES 竞争窗口：文件存在但 O_EXCL 报 PermissionError
    （sharing/pending-delete）→ 走 stale 回收而非上抛；文件不存在
    才是真权限错——照抛。"""
    import dp_cli.daemon as dm
    import builtins
    import os as _o
    import time as _t
    real_import = builtins.__import__

    def no_fcntl(name, *a, **k):
        if name == 'fcntl':
            raise ImportError
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_fcntl)

    lp = tmp_path / 's.lock'
    # 正的大死 pid（>Linux pid_max；Windows OpenProcess 必失败）——
    # 走 lp>0 → _pid_alive 回收分支（stale_reclaim 用例覆盖 lp<=0 分支）
    lp.write_text('99999999', encoding='utf-8')
    _o.utime(str(lp), (_t.time() - 10, _t.time() - 10))

    real_open = _o.open
    state = {'n': 0}

    def win_open(p, flags, *a, **k):
        if str(p) == str(lp) and state['n'] == 0:
            state['n'] += 1
            raise PermissionError(13, 'Permission denied', str(p))
        return real_open(p, flags, *a, **k)

    monkeypatch.setattr(_o, 'open', win_open)
    with dm._acquire_spawn_lock(lp, timeout=2):
        pass
    assert not lp.exists()

    # 文件不存在的 EACCES = 真权限错——不进入重试，直接抛
    missing = tmp_path / 'ro.lock'

    def deny_open(p, flags, *a, **k):
        if str(p) == str(missing):
            raise PermissionError(13, 'Permission denied', str(p))
        return real_open(p, flags, *a, **k)

    monkeypatch.setattr(_o, 'open', deny_open)
    with pytest.raises(PermissionError):
        with dm._acquire_spawn_lock(missing, timeout=0.5):
            pass


@pytest.mark.unit
def test_spawn_lock_flock_nb_timeout(tmp_path, monkeypatch):
    """POSIX _flock 对齐 _excl 超时语义：LOCK_NB+轮询到 deadline
    抛 TimeoutError——旧版裸 LOCK_EX 阻塞无上限，锁持有者卡死会
    拖死整个调用方（netlog _append 传 timeout=5 走这条路径）。"""
    fcntl = pytest.importorskip('fcntl')
    import dp_cli.daemon as dm
    calls = []

    def fake_flock(fd, flags):
        calls.append(flags)
        if flags & fcntl.LOCK_UN:
            return
        raise BlockingIOError(11, 'Resource temporarily unavailable')

    monkeypatch.setattr(fcntl, 'flock', fake_flock)
    with pytest.raises(TimeoutError):
        with dm._acquire_spawn_lock(tmp_path / 's.lock', timeout=0.3):
            pass
    # 首调即 LOCK_EX|LOCK_NB 非阻塞获取；LOCK_UN 在 finally 兜底释放
    assert calls[0] == fcntl.LOCK_EX | fcntl.LOCK_NB
    assert calls[-1] == fcntl.LOCK_UN


@pytest.mark.unit
def test_daemon_netlog_requests_contract(tmp_path, monkeypatch):
    """daemon 托管 netlog：requests 读文件级 buffer 契约（NO_NETLOG 消除）。"""
    import dp_cli.netlog as nl
    import json as js
    p = tmp_path / 'netlog-dn.jsonl'
    p.write_text(js.dumps({'requestId': 'r1', 'url': 'https://a.com',
                           'method': 'GET', 'status': 200,
                           'type': 'Document'}) + '\n',
                 encoding='utf-8')
    monkeypatch.setattr(nl, '_path', lambda s: tmp_path / f'netlog-{s}.jsonl')
    entries = nl.read_entries('dn')
    assert entries and entries[0]['url'] == 'https://a.com'
    # 无文件 → None → NO_NETLOG（契约保留，daemon 已 arm 时不触发）
    assert nl.read_entries('missing') is None


@pytest.mark.unit
def test_daemon_skip_mcp_record(monkeypatch, tmp_path):
    """mcp/record-* 在 DPX_DAEMON=1 下仍走本地——不转发 daemon。"""
    from dp_cli.daemon import try_daemon
    monkeypatch.setenv('DPX_DAEMON', '1')
    monkeypatch.setattr('dp_cli.daemon._daemon_ready', lambda s: True)
    for cmd in ('mcp', 'record-start', 'record-stop', 'record-show',
                'record-status'):
        assert try_daemon([cmd]) is None, f'{cmd} 不应转发'


@pytest.mark.unit
def test_daemon_unknown_command_error(tmp_path, monkeypatch, sock_dir):
    """daemon 未知 argv 返回结构化错误而非挂起。"""
    import dp_cli.daemon as dm
    import socket as sk, threading as th, json as js

    monkeypatch.setattr(dm, '_dir', lambda: tmp_path)
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: sock_dir / f'{s}.sock')
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_IDLE_TTL', 99999)

    t = th.Thread(target=dm.run_daemon, args=('u',), daemon=True)
    t.start()
    import time as _t
    # 同上：meta 在 listen 后落盘——sock exists 轮询会撞 ECONNREFUSED
    for _ in range(40):
        if (tmp_path / 'u.session').exists():
            break
        _t.sleep(0.1)
    r = dm._send_recv('u', {'argv': ['nonexistcmd']}, 10)
    assert isinstance(r, dict) and r.get('exit') != 0
    assert r.get('result', {}).get('status') == 'error'


# 子进程 worker 源码——subprocess python -c 起真子进程，比
# multiprocessing spawn 稳定（spawn 对 test 模块 target 的
# unpickle 在 Windows 下有 bootstrap 冻结边角）
_NETLOG_WORKER_SRC = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import dp_cli.netlog as nl
d = Path(sys.argv[2])
nl._path = lambda s: d / f'netlog-{s}.jsonl'
nl._lock_path = lambda s: d / f'netlog-{s}.lock'
i = int(sys.argv[3])
for j in range(50):
    nl._append('x', {'requestId': f'p{i}-r{j}',
                     'url': f'https://a.com/{i}/{j}',
                     'method': 'GET'})
'''


@pytest.mark.unit
def test_netlog_cross_process_lock(tmp_path):
    """多进程并发 append+trim：文件锁下无撕裂。"""
    import subprocess
    import sys
    from pathlib import Path
    repo = str(Path(__file__).parent.parent)
    p = tmp_path / 'netlog-x.jsonl'

    procs = [subprocess.Popen(
        [sys.executable, '-c', _NETLOG_WORKER_SRC,
         repo, str(tmp_path), str(i)]) for i in range(4)]
    for q in procs:
        assert q.wait(timeout=60) == 0
    lines = p.read_text(encoding='utf-8').splitlines()
    import json as js
    # 全部行合法 JSON 无撕裂
    parsed = [js.loads(l) for l in lines]
    assert all(isinstance(d, dict) for d in parsed)
    # 完整性核心断言：无撕裂。行数允许少量丢失——_append 对
    # 锁超时/OSError 按 best-effort 静默丢弃（netlog 本为近似
    # ring buffer 设计），4 进程竞争下 Windows 轮询锁偶发超时
    assert len(lines) >= 150


@pytest.mark.unit
def test_daemon_status_version_field(tmp_path, monkeypatch):
    """daemon-status 响应含 version 字段（meta 写入）。"""
    import dp_cli.daemon as dm
    import json as js, os
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    (tmp_path / 'v.session').write_text(js.dumps(
        {'pid': os.getpid(), 'version': '1.2.3'}))
    (tmp_path / 'v.sock').touch()
    out = []
    monkeypatch.setattr(dm, 'ok', lambda d, **k: out.append(d))
    dm._status('v')
    assert out[0]['version'] == '1.2.3'


@pytest.mark.unit
def test_daemon_version_probe_warn(tmp_path, monkeypatch, capsys):
    """版本不匹配 stderr 警告（每进程每 session 一次）。"""
    import dp_cli.daemon as dm
    import json as js
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    (tmp_path / 'w.session').write_text(js.dumps({'version': '9.9.9'}))
    dm._VER_WARNED.clear()
    monkeypatch.setattr(dm, '_dpx_version', lambda: '1.0.0')
    dm._version_probe('w')
    err = capsys.readouterr().err
    assert 'daemon v9.9.9 vs dpx v1.0.0' in err
    assert 'daemon-stop -S w' in err


@pytest.mark.unit
def test_by_js_hidden_actionable_hint():
    """hidden 拦截错误含 --force 可行动提示（--by-js 共用该路径）。"""
    import inspect
    src = inspect.getsource(_utils)
    assert 'use --force to bypass actionability' in src


@pytest.mark.unit
def test_assert_text_flake_repro(runner, monkeypatch):
    """跨标签文本 flake 复现：page.html 搜不到，innerText 能搜到。"""
    from dp_cli.main import cli
    class _P:
        html = 'Hello <b>World</b> &amp; Co'   # 序列化 markup
        def run_js(self, j):
            return 'Hello World & Co'          # innerText 可见文本
    p = _P()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: p)
    monkeypatch.setattr('dp_cli.commands.assert_cmd._get_page',
                        lambda s: p)
    monkeypatch.setattr('dp_cli.commands.assert_cmd.frame_ctx',
                        lambda *a, **k: p)
    r = runner.invoke(cli, ['assert-text', 'Hello World & Co'])
    assert r.exit_code == 0, r.output
    assert '断言通过' in r.output


@pytest.mark.unit
def test_daemon_windows_ready_stale(tmp_path, monkeypatch):
    """Windows 路径：meta 在但 TCP 拒连+pid 死 → stale 清理。"""
    import dp_cli.daemon as dm
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    (tmp_path / 'w.session').write_text(
        '{"pid": -99999, "port": 1}')
    monkeypatch.setattr(dm, '_tcp_alive', lambda s: False)
    monkeypatch.setattr(dm, '_pid_alive', lambda p: False)
    assert dm._daemon_ready('w') is False
    assert not (tmp_path / 'w.session').exists()   # 已清


@pytest.mark.unit
def test_daemon_windows_stop_via_meta(tmp_path, monkeypatch):
    """Windows 无 .sock——meta 在+TCP 死 → stop 走清理不跳过。"""
    import dp_cli.daemon as dm
    from dp_cli.main import cli
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    (tmp_path / 'w.session').write_text(
        '{"pid": -99999, "port": 1}')
    monkeypatch.setattr(dm, '_send_recv', lambda *a: ('err', None))
    from click.testing import CliRunner
    r = CliRunner().invoke(cli, ['daemon-stop', '-S', 'w'])
    assert '清理' in r.output or 'stopped' in r.output
    assert not (tmp_path / 'w.session').exists()


@pytest.mark.unit
def test_kill_all_darwin_ps_path():
    """macOS kill-all 分流：ps 替代 /proc + 非 linux/darwin 显式跳过。"""
    import dp_cli.commands.browser as bc
    src = open(bc.__file__, encoding='utf-8').read()
    assert "sys.platform == 'darwin'" in src      # ps 分支
    assert "ps', '-p'" in src                      # ps 命令存在
    assert "sys.platform not in ('linux', 'darwin')" in src


@pytest.mark.unit
def test_pid_alive_windows_dead_handle(tmp_path, monkeypatch):
    """Windows 死 PID：OpenProcess 返句柄但 exit≠259 → False。"""
    import dp_cli.daemon as dm
    class _K:
        def OpenProcess(self, *a): return 42      # 假句柄（可拿到）
        def GetExitCodeProcess(self, h, ref):
            ref._obj.value = 0                   # 死进程 exit code
            return 1
        def CloseHandle(self, h): return 1
    assert dm._win_pid_alive(_K(), 1234) is False
    class _K2(_K):
        def GetExitCodeProcess(self, h, ref):
            ref._obj.value = 259               # STILL_ACTIVE
            return 1
    assert dm._win_pid_alive(_K2(), 1234) is True
    # OpenProcess 失败 → False
    class _K3(_K):
        def OpenProcess(self, *a): return 0
    assert dm._win_pid_alive(_K3(), 1234) is False


@pytest.mark.unit
def test_windows_ready_dead_daemon_cleanup(tmp_path, monkeypatch):
    """Windows 死 daemon：TCP 拒连+pid 死 → ready False+meta 清除。"""
    import dp_cli.daemon as dm
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    (tmp_path / 'd.session').write_text(
        '{"pid": 9999, "port": 54321}')
    monkeypatch.setattr(dm, '_tcp_alive', lambda s: False)
    monkeypatch.setattr(dm, '_pid_alive', lambda p: False)
    assert dm._daemon_ready('d') is False
    assert not (tmp_path / 'd.session').exists()


@pytest.mark.unit
def test_daemon_ready_ttl_cache(tmp_path, monkeypatch):
    """Windows 探活 2s TTL：二次调用不重复 TCP probe。"""
    import dp_cli.daemon as dm
    monkeypatch.setattr(dm, '_HAS_UNIX_SOCK', False)
    monkeypatch.setattr(dm, '_meta_path',
                        lambda s: tmp_path / f'{s}.session')
    monkeypatch.setattr(dm, '_sock_path',
                        lambda s: tmp_path / f'{s}.sock')
    (tmp_path / 'c.session').write_text(
        '{"pid": 1, "port": 1}')
    calls = []
    monkeypatch.setattr(dm, '_tcp_alive',
                        lambda s: calls.append(s) or True)
    dm._READY_CACHE.clear()
    assert dm._daemon_ready('c') is True
    assert dm._daemon_ready('c') is True
    assert len(calls) == 1            # 二次命中缓存
    # TTL 过期重探
    dm._READY_CACHE['c'] = (dm.time.monotonic() - 3, True)
    assert dm._daemon_ready('c') is True
    assert len(calls) == 2


def test_engine_reranker_advisory(monkeypatch):
    """reranker 引擎——mock CrossEncoder 验证打分→target+probs+conf。"""
    import dp_cli.agent as agent

    class FakeCE:
        def predict(self, pairs):
            # goal 含「登录」→ ref2(登录按钮) 分数最高
            return [0.1 if '首页' in p[1] else 9.0 if '登录' in p[1]
                    else 0.2 for p in pairs]
    monkeypatch.setattr(agent, '_RERANKER', ('st', FakeCE()))
    state = {'options': [{'ref': 1, 'role': 'link', 'name': '首页'},
                         {'ref': 2, 'role': 'button', 'name': '登录'}]}
    resp = agent._engine_reranker(state, '点登录')
    assert resp['target'] == 2
    assert resp['confidence'] > 0.9
    assert resp['probs']['target']['2'] > resp['probs']['target']['1']
    agent._RERANKER = None


def test_engine_reranker_missing_dep(monkeypatch):
    """缺 sentence-transformers 时优雅降级（结构化错误非崩溃）。"""
    import sys
    import dp_cli.agent as agent
    monkeypatch.setattr(agent, '_RERANKER', None)
    monkeypatch.setattr(agent, '_ort', lambda: None)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', None)
    resp = agent._engine_reranker({'options': [{'ref': 1, 'role': 'button',
                                              'name': 'x'}]}, 'goal')
    assert '_error' in resp and 'agent]' in resp['_error']
    agent._RERANKER = None


def test_reranker_backend_fallback(monkeypatch, tmp_path):
    """降级链：ORT 有 int8→int8；无 onnx 文件→ST；都无→结构化错误。"""
    import dp_cli.agent as agent

    monkeypatch.setattr(agent, '_RERANKER', None)
    monkeypatch.setattr(agent, '_ORT_MOD', None)
    # ORT 可用但无 onnx 文件 → 落 ST 分支（ST 缺失报 _error）
    monkeypatch.setattr(agent, '_ort', lambda: object())
    import sys
    monkeypatch.setitem(sys.modules, 'transformers', None)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', None)
    m = agent._reranker_model()
    assert isinstance(m, dict) and 'agent]' in m['_error']
    agent._RERANKER = None


def test_engine_jev_env_missing(monkeypatch):
    """jev env 未配——结构化 _error 非 NameError（JSON 输出契约）。"""
    import dp_cli.agent as agent
    monkeypatch.delenv('DPX_JEV_URL', raising=False)
    resp = agent._engine_jev({'state': 'x', 'questions': {}})
    assert '_error' in resp and 'DPX_JEV_URL' in resp['_error']
    monkeypatch.setenv('DPX_JEV_URL', 'https://api.example/v1/chat')
    monkeypatch.delenv('DPX_JEV_MODEL', raising=False)
    resp = agent._engine_jev({})
    assert '_error' in resp and 'DPX_JEV_MODEL' in resp['_error']


def test_engine_jev_mock_http(monkeypatch):
    """jev 假 HTTP 响应——choices.content JSON→同构 resp，请求体合规。"""
    import json as _j
    import dp_cli.agent as agent
    monkeypatch.setenv('DPX_JEV_URL', 'https://api.example/v1/chat/completions')
    monkeypatch.setenv('DPX_JEV_KEY', 'k1')
    monkeypatch.setenv('DPX_JEV_MODEL', 'm2')
    answer = {'action': 'click', 'target': 2, 'done': False,
              'stuck': False, 'confidence': 0.9}
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _j.dumps(
                {'choices': [{'message': {'content': _j.dumps(answer)}}]}
            ).encode()

    def fake_urlopen(req, timeout=None):
        seen['url'] = req.full_url
        seen['auth'] = req.headers.get('Authorization')
        seen['timeout'] = timeout
        body = _j.loads(req.data.decode())
        seen['model'] = body['model']
        seen['nmsg'] = len(body['messages'])
        seen['temp'] = body['temperature']
        return FakeResp()

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)
    resp = agent._engine_jev({'state': 'URL: u', 'questions': {}})
    assert resp == answer
    assert seen['url'] == 'https://api.example/v1/chat/completions'
    assert seen['auth'] == 'Bearer k1'
    assert seen['model'] == 'm2'
    assert seen['nmsg'] == 2 and seen['temp'] == 0
    assert seen['timeout'] == 30.0


def test_engine_jev_errors(monkeypatch):
    """jev 超时/5xx/坏 JSON/缺 choices——全收敛 _error 不裸 raise。"""
    import json as _j
    import urllib.error
    import dp_cli.agent as agent
    monkeypatch.setenv('DPX_JEV_URL', 'https://api.example/v1/x')
    monkeypatch.setenv('DPX_JEV_MODEL', 'm')

    monkeypatch.setattr('urllib.request.urlopen',
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            TimeoutError('timed out')))
    resp = agent._engine_jev({})
    assert '_error' in resp and '超时' in resp['_error']

    def raise_500(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, 'err', {}, None)
    monkeypatch.setattr('urllib.request.urlopen', raise_500)
    resp = agent._engine_jev({})
    assert '_error' in resp and 'HTTP 500' in resp['_error']

    class BadResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'not json'

    monkeypatch.setattr('urllib.request.urlopen',
                        lambda req, timeout=None: BadResp())
    resp = agent._engine_jev({})
    assert '_error' in resp and '非 JSON' in resp['_error']

    class EmptyResp(BadResp):
        def read(self):
            return _j.dumps({'choices': []}).encode()

    monkeypatch.setattr('urllib.request.urlopen',
                        lambda req, timeout=None: EmptyResp())
    resp = agent._engine_jev({})
    assert '_error' in resp and 'choices' in resp['_error']

    class NoisyResp(BadResp):
        def read(self):
            return _j.dumps({'choices': [{'message': {
                'content': '我建议：```json\n{"action":"click","ref":3}\n```'
            }}]}).encode()

    monkeypatch.setattr('urllib.request.urlopen',
                        lambda req, timeout=None: NoisyResp())
    resp = agent._engine_jev({})
    assert resp['action'] == 'click' and resp['target'] == 3  # ref→target 归一


def test_engine_jev_routing(monkeypatch):
    """engine=jev 路由走 _engine_jev（非 reranker 分支）+target→action 补。"""
    import dp_cli.agent as agent
    calls = []
    monkeypatch.setattr(
        agent, '_engine_jev',
        lambda req: calls.append('jev') or {'target': 1})
    monkeypatch.setattr(
        agent, '_engine_reranker',
        lambda *a: calls.append('reranker') or {})
    state = {'state': {'url': 'u', 'title': 't'},
             'options': [{'ref': 1, 'role': 'button', 'name': 'Go'}]}
    resp, request = agent._ask_questions(state, '点Go', 'jev', 's',
                                         None, None)
    assert calls == ['jev']                    # 未走 reranker/未缓存
    assert resp['action'] == 'click'           # role→action 代码映射补齐
    assert request['questions']['target']['type'] == 'choice'


def test_act_tier_boundaries():
    """conf 三档+margin_source 契约：probs 可 act，单边/降级上限 confirm。"""
    from dp_cli.agent import _act_tier
    assert _act_tier(0.98, 'probs') == 'act'
    assert _act_tier(0.98, 'single_side') == 'confirm'
    assert _act_tier(0.98, 'conf_fallback') == 'confirm'
    assert _act_tier(0.98, None) == 'confirm'
    assert _act_tier(0.5, 'probs') == 'confirm'
    assert _act_tier(0.2, 'probs') == 'refuse'
    assert _act_tier(0.2, 'single_side') == 'refuse'


@pytest.mark.unit
def test_conf_tier_three_way_boundaries():
    """confidence_tier high/medium/low 分档边界（conf+margin+unsure）。"""
    from dp_cli.agent import _conf_tier
    # high：conf≥0.7 且 margin≥0.1 双信号齐
    assert _conf_tier(0.7, 0.1) == 'high'
    assert _conf_tier(0.9, 0.5) == 'high'
    # medium：单信号达标——conf 高 margin 低 / margin 高 conf 中
    assert _conf_tier(0.8, 0.05) == 'medium'    # margin<0.1
    assert _conf_tier(0.5, 0.5) == 'medium'     # conf<0.7
    assert _conf_tier(0.35, 0.9) == 'medium'    # floor 边界上方
    # low：unsure 命中或 conf<0.35 floor
    assert _conf_tier(0.9, 0.9, unsure=True) == 'low'
    assert _conf_tier(0.34, 0.9) == 'low'
    assert _conf_tier(0.0, 0.0) == 'low'
    # bge 形态：conf_floor=0 时 conf 不触发 low，只判 unsure
    assert _conf_tier(0.2, 0.5, conf_floor=0.0) == 'medium'
    assert _conf_tier(0.2, 0.5, unsure=True,
                      conf_floor=0.0) == 'low'


@pytest.mark.unit
def test_decide_confidence_fields_and_alternatives(runner, monkeypatch):
    """结构化置信度：tier/should_escalate/alternatives——probs 排序、
    排除 top1、ranked 第2-3名上限。"""
    from dp_cli.main import cli
    import dp_cli.agent as am
    import json as _j

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'},
                                        {'ref': 2, 'role': 'link',
                                         'name': 'B'},
                                        {'ref': 3, 'role': 'link',
                                         'name': 'C'},
                                        {'ref': 4, 'role': 'link',
                                         'name': 'D'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(am, '_RERANKER_FAM', None)

    # conf=0.9 margin=0.5 → high；alternatives=rank2-3 排除 top1
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9,
         'probs': {'target': {'1': 0.6, '2': 0.1, '3': 0.05,
                              '4': 0.01}}}, {}))
    r = runner.invoke(cli, ['decide', 'go', '--engine', 'external'])
    assert r.exit_code == 0, r.output
    d = _j.loads(r.output)['data']
    assert d['confidence_tier'] == 'high'
    assert d['should_escalate'] is False
    alts = d['alternatives']
    assert len(alts) == 2                       # ranked 第2-3名上限
    assert [a['ref'] for a in alts] == [2, 3]   # 排除 top1，按分降序
    assert alts[0]['name'] == 'B' and alts[0]['score'] == 0.1

    # conf=0.5 → medium 不 escalate
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.5,
         'probs': {'target': {'1': 0.6, '2': 0.1}}}, {}))
    r2 = runner.invoke(cli, ['decide', 'go', '--engine', 'external'])
    d2 = _j.loads(r2.output)['data']
    assert d2['confidence_tier'] == 'medium'
    assert d2['should_escalate'] is False

    # low conf → AGENT_UNSURE 且 should_escalate/alternatives 仍入 detail
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.1,
         'probs': {'target': {'1': 0.4, '2': 0.3, '3': 0.2}}}, {}))
    r3 = runner.invoke(cli, ['decide', 'go', '--engine', 'external'])
    assert 'AGENT_UNSURE' in r3.output
    d3 = _j.loads(r3.output)
    detail = _j.loads(d3['detail'])
    assert detail['confidence_tier'] == 'low'
    assert detail['should_escalate'] is True
    assert [a['ref'] for a in detail['alternatives']] == [2, 3]


@pytest.mark.unit
def test_decide_alternatives_no_probs_lexical(runner, monkeypatch):
    """external 无 probs：alternatives 仍出——lexical 序兜底。"""
    from dp_cli.main import cli
    import dp_cli.agent as am
    import json as _j

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'Submit Order'},
                                        {'ref': 2, 'role': 'button',
                                         'name': 'Submit Form'},
                                        {'ref': 3, 'role': 'link',
                                         'name': 'Order History'},
                                        {'ref': 4, 'role': 'link',
                                         'name': 'zzq unrelated'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(am, '_RERANKER_FAM', None)
    # 无 probs、conf 够高——conf_fallback margin=conf → 放行
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.9}, {}))
    r = runner.invoke(cli, ['decide', 'submit order',
                            '--engine', 'external'])
    assert r.exit_code == 0, r.output
    d = _j.loads(r.output)['data']
    alts = d['alternatives']
    assert alts                                  # 无 probs 仍出备选
    assert all(a['ref'] != 1 for a in alts)      # top1 排除
    assert all(set(a) == {'ref', 'name', 'score'} for a in alts)
    # 'Submit Form'/'Order History' 字面近 goal，应在 lexical 序命中
    assert {a['name'] for a in alts} & {'Submit Form', 'Order History'}


@pytest.mark.unit
def test_decide_refuse_escalates(runner, monkeypatch):
    """t2 S14 实测修复：bge conf 0.05 过门（floor=0）→ tier=refuse 时
    confidence_tier 恒 low+should_escalate=true——refuse 语义即
    "置信不足该升级"，不再出现 refuse+medium+escalate=false 打架。"""
    from dp_cli.main import cli
    import dp_cli.agent as am
    import json as _j

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'},
                                        {'ref': 2, 'role': 'link',
                                         'name': 'B'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    # bge 形态：_conf_gate=0 → conf 0.05 不破 floor；margin 0.1 过
    # min_margin → 不 unsure——修复前恰好产出 refuse+medium+false
    monkeypatch.setattr(am, '_RERANKER_FAM', 'bge')
    monkeypatch.setattr(am, '_ask_questions', lambda *a, **k: (
        {'action': 'click', 'target': 1, 'confidence': 0.05,
         'probs': {'target': {'1': 0.5, '2': 0.4}}}, {}))
    r = runner.invoke(cli, ['decide', '点某物', '--engine', 'reranker'])
    assert r.exit_code == 0, r.output
    d = _j.loads(r.output)['data']
    assert d['tier'] == 'refuse'
    assert d['confidence_tier'] == 'low'
    assert d['should_escalate'] is True


@pytest.mark.unit
def test_decide_meta_tier_rule(runner, monkeypatch):
    """meta 规则短路路径：confidence_tier='rule'（非置信语义如实标）
    +should_escalate/alternatives 键齐（消费者按键存在性统一取）。"""
    from dp_cli.main import cli
    import dp_cli.agent as am
    import json as _j

    monkeypatch.setattr(am, '_page_state',
                        lambda p, s, max_opts=20, **kw: {
                            'state': {'url': 'u', 'title': 't', 'sig': 'x'},
                            'options': [{'ref': 1, 'role': 'button',
                                         'name': 'A'}],
                            'epoch': 'e1', 'truncated': False})
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'load_session', lambda s: {})
    monkeypatch.setattr(am, 'save_session', lambda *a, **k: None)
    # goal 含返回意图 → meta 短路 deterministic
    r = runner.invoke(cli, ['decide', '返回上一页', '--engine', 'external'])
    assert r.exit_code == 0, r.output
    d = _j.loads(r.output)['data']
    assert d['deterministic'] is True and d['action'] == 'back'
    assert d['confidence_tier'] == 'rule'
    assert d['should_escalate'] is False       # 三键齐——规则命中确定可信
    assert d['alternatives'] == []


# ─────────────────────────────────────────────────────────────────────────────
# P2 第三波处置测试：netlog prune / cookie 静默拒收 / _dl_path / refs sig
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_prune_armed_reaps_dead_sessions():
    """_armed 按活跃 session 集收编——已删除会话的标记键回收，
    活跃键不受影响。"""
    import dp_cli.netlog as nl
    nl._armed.clear()
    try:
        nl._armed.update({'live': 't1', 'dead': 't2', 'gone': 't3'})
        nl.prune_armed(['live'])
        assert nl._armed == {'live': 't1'}
        # 空活跃集→全收
        nl._armed['x'] = 't9'
        nl.prune_armed([])
        assert nl._armed == {}
    finally:
        nl._armed.clear()


@pytest.mark.unit
def test_maybe_arm_netlog_prunes_armed(monkeypatch):
    """serve._maybe_arm_netlog：arm 循环后 _armed 收编到 _PAGE_CACHE
    键集——stale session 键不再滞留长驻进程内存。"""
    import dp_cli.netlog as nl
    import dp_cli.commands.serve as sv
    nl._armed.clear()
    try:
        from types import SimpleNamespace
        nl._armed['dead-session'] = 't0'      # 模拟已删会话残留
        monkeypatch.setattr('dp_cli.session._PAGE_CACHE',
                            {'live': SimpleNamespace(latest_tab=None)})
        monkeypatch.setattr(nl, 'arm_netlog', lambda p, s: True)
        sv._maybe_arm_netlog()
        assert 'dead-session' not in nl._armed
    finally:
        nl._armed.clear()


@pytest.mark.unit
def test_cookies_import_silent_reject_counted(runner, monkeypatch,
                                              tmp_path):
    """cookies-import：page.set.cookies 不抛错但浏览器静默拒收
    （Network.setCookie success=False 路径）——导入后回读 jar 核验，
    未落地名字进 skipped_cookies 且不计入 imported。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class _Set:
        def cookies(self, c):
            pass                       # 不抛错——模拟静默拒收面

    class P:
        set = _Set()

        def cookies(self, all_domains=False):
            # jar 里只有 landed，dropped 被浏览器策略丢掉
            return [{'name': 'landed'}]

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    f = tmp_path / 'c.txt'
    f.write_text('landed=1; dropped=2', encoding='utf-8')
    r = runner.invoke(cli, ['cookies-import', str(f)])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['imported'] == 1
    assert d['skipped_cookies'] == ['dropped']


@pytest.mark.unit
def test_cookies_import_jar_check_failure_fallback(runner, monkeypatch,
                                                   tmp_path):
    """cookies-import：jar 回读核验本身失败（无 cookies API/异常）
    → 按 set.cookies 结果如实报，不阻塞不扣数。"""
    import dp_cli.commands.storage as storage_mod
    from dp_cli.main import cli

    class _Set:
        def cookies(self, c):
            pass

    class P:
        set = _Set()
        # 无 cookies() 方法——AttributeError 走核验失败回退

    monkeypatch.setattr(storage_mod, '_get_page', lambda s: P())
    f = tmp_path / 'c.txt'
    f.write_text('a=1; b=2', encoding='utf-8')
    r = runner.invoke(cli, ['cookies-import', str(f)])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['imported'] == 2
    assert 'skipped_cookies' not in d


@pytest.mark.unit
def test_dl_path_semantics(monkeypatch):
    """session._dl_path()：封装"download_path 恒 truthy≠已配置"教训——
    只有 download --dir 写入的 download_dir 键才算显式配置。"""
    import dp_cli.session as sess_mod
    monkeypatch.setattr(sess_mod, 'load_session',
                        lambda s: {'download_dir': '/tmp/dl'})
    assert sess_mod._dl_path('s') == '/tmp/dl'
    monkeypatch.setattr(sess_mod, 'load_session', lambda s: {})
    assert sess_mod._dl_path('s') is None
    monkeypatch.setattr(sess_mod, 'load_session',
                        lambda s: {'port': 9222})   # 无 download_dir 键
    assert sess_mod._dl_path('s') is None


@pytest.mark.unit
def test_download_result_configured_flag(runner, monkeypatch, tmp_path):
    """download 结果：无显式配置时 configured=False，download_dir
    回显 DP 运行时默认（cwd 不再冒充已配置）。"""
    import dp_cli.commands.emulate as em
    from dp_cli.main import cli
    from types import SimpleNamespace

    mgr = SimpleNamespace(missions={})
    page = SimpleNamespace(
        browser=SimpleNamespace(_dl_mgr=mgr),
        download_path='/some/cwd',
        set=SimpleNamespace(download_path=lambda p: None,
                            when_download_file_exists=lambda m: None),
    )
    monkeypatch.setattr(em, '_get_page', lambda s: page)
    monkeypatch.setattr('dp_cli.session._dl_path', lambda s: None)
    result = runner.invoke(cli, ['download', '--timeout', '0'])
    assert result.exit_code == 0
    out = json.loads(result.output)['data']
    assert out['download_dir'] == '/some/cwd'
    assert out['configured'] is False


@pytest.mark.unit
def test_agent_state_sig_includes_interactive_props(monkeypatch):
    """refs epoch sig：交互属性子集（disabled/hidden 等）变化重签——
    属性级 DOM 变更不再命中旧 epoch。"""
    import dp_cli.agent as ag

    def _tree(disabled):
        return {'tree': {'role': 'WebArea', 'children': [
            {'role': 'button', 'name': 'Go', 'value': '',
             'properties': {'disabled': disabled},
             'backendNodeId': 1, 'locator': 'css:#go',
             'children': []}]}}

    class _P:
        url = 'https://x/'
        title = 't'

    saved = {}
    monkeypatch.setattr(ag, 'save_refs', lambda s, u, r: saved.update(r))
    monkeypatch.setattr(ag, 'load_session', lambda s: {})
    monkeypatch.setattr(ag, 'save_session', lambda s, d: None)
    import dp_cli.snapshot.a11y as a11y
    monkeypatch.setattr(a11y, 'take_a11y_snapshot',
                        lambda p, max_depth=12: _tree(False))
    st1 = ag._page_state(_P(), 's')
    monkeypatch.setattr(a11y, 'take_a11y_snapshot',
                        lambda p, max_depth=12: _tree(True))
    st2 = ag._page_state(_P(), 's')
    assert st1['epoch'] != st2['epoch']
    assert st1['state']['sig'] != st2['state']['sig']


@pytest.mark.unit
def test_state_save_atomic_no_tmp_leftover(runner, monkeypatch, tmp_path):
    """state-save tmp+os.replace 原子产出——成品文件完整且无 .tmp
    残留（中断不留截断 state 文件）。"""
    import dp_cli.commands.misc as misc_mod
    from dp_cli.main import cli
    from types import SimpleNamespace

    page = SimpleNamespace(
        cookies=lambda all_domains=True: SimpleNamespace(
            as_dict=lambda: {'a': '1'}),
        url='https://x/',
        run_js=lambda *a, **k: '{}',
    )
    monkeypatch.setattr(misc_mod, '_get_page', lambda s: page)
    f = tmp_path / 'state.json'
    r = runner.invoke(cli, ['state-save', str(f)])
    assert r.exit_code == 0, r.output
    d = json.loads(f.read_text(encoding='utf-8'))
    assert d['cookies'] == {'a': '1'}
    assert not (tmp_path / 'state.json.tmp').exists()


@pytest.mark.unit
def test_har_dump_output_atomic_no_tmp_leftover(runner, monkeypatch,
                                                tmp_path):
    """har-dump --output tmp+os.replace 原子产出——成品完整且无
    .tmp 残留。"""
    import dp_cli.commands.network as net_mod
    from dp_cli.main import cli

    page = _HarPage(_HAR_EVENTS)
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    out_file = tmp_path / 't.har'
    r = runner.invoke(cli, ['har-dump', '--seconds', '0.05',
                            '--output', str(out_file)])
    assert r.exit_code == 0, r.output
    har = json.loads(out_file.read_text(encoding='utf-8'))
    assert har['log']['version'] == '1.2'
    assert not (tmp_path / 't.har.tmp').exists()
