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
    result = runner.invoke(cli, ["click", "text:x"])
    assert result.exit_code == 3
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
    """set_callback 注册时立即回放预置事件的假 driver。"""

    def __init__(self, events=None):
        self.callbacks = {}
        self._events = events or {}

    def set_callback(self, event, cb):
        self.callbacks[event] = cb
        if cb and event in self._events:
            for kw in self._events[event]:
                cb(**kw)


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
    lines = out_file.read_text().splitlines()
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
