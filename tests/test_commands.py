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

    class P:
        url = 'https://x'
        response = None

    out_file = tmp_path / 'empty.bin'
    result = net_mod._emit_response(P(), str(out_file))
    assert result['status_code'] is None
    assert result['saved_to'] == str(out_file)
    assert out_file.read_bytes() == b''


@pytest.mark.unit
def test_emit_response_none_response_inline():
    """_emit_response：response=None 且无 --output → body=None，不崩。"""
    import dp_cli.commands.network as net_mod

    class P:
        url = 'https://x'
        response = None

    result = net_mod._emit_response(P(), None)
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
