# -*- coding:utf-8 -*-
"""UI↔API 联动 hints（docs/UI_API_LINKAGE.md v1.1，P0+P1）。

touched_apis（L1）：click/fill/type 成功响应 data.hints.touched_apis——
mock netlog 行 + 动作时刻窗口；窗口命中/超窗不中/netlog 缺省。
adapter_hint（L0）：decide/open 响应 data.hints.adapter_hint——
mock adapter 注册表 host 匹配；decide 缓存命中仍当次重算。

全程 mock——netlog 走 read_entries seam，adapter 走 isolated_home
注册表，不依赖真浏览器/网络。
"""
import json
import time

import pytest
from click.testing import CliRunner

import dp_cli.agent as agent
import dp_cli.hints as hints_mod
import dp_cli.commands._utils as _utils
import dp_cli.commands.browser as browser_mod

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _hint_env(monkeypatch):
    """收网宽限归零（mock 直读 entries 无需等）+ decide 缓存复位。"""
    monkeypatch.setattr(hints_mod, '_GRACE_S', 0)
    agent._DECIDE_CACHE.clear()
    yield
    agent._DECIDE_CACHE.clear()


@pytest.fixture
def adir(isolated_home):
    d = isolated_home / '.dp_cli' / 'adapters'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_adapter(adir, name='x-draft', url_template=None, **over):
    a = {'name': name, 'recorded_at': '2026-01-01T00:00:00Z',
         'endpoint': {
             'method': 'PATCH',
             'url_template': url_template
                             or 'https://api.x.com/draft?id={id}',
             'params_keys': ['id']}}
    a.update(over)
    (adir / f'{name}.json').write_text(
        json.dumps(a, ensure_ascii=False), encoding='utf-8')
    return a


def _xhr(rid, url, wts, status=200, rtype='XHR', method='GET'):
    """req+resp 两行 netlog entries（wts=wallTime 挂钟时戳）。"""
    return [{'kind': 'req', 'id': rid, 'url': url, 'method': method,
             'type': rtype, 'ts': 12345.0, 'wts': wts},
            {'kind': 'resp', 'id': rid, 'status': status}]


def _mock_netlog(monkeypatch, entries):
    monkeypatch.setattr('dp_cli.netlog.read_entries',
                        lambda session: entries)


# ── Fake 对象 ──────────────────────────────────────────────────

class _Click:
    def __init__(self, outer):
        self._o = outer

    def __call__(self, *a, **k):
        self._o.log.append('click')

    def at(self, *a, **k):
        self._o.log.append('at')


class _Ele:
    """click/fill 假元素：run_js→None（actionability 保守放行）。"""

    def __init__(self):
        self.log = []
        self.click = _Click(self)

    def run_js(self, script, *a, **k):
        return None

    def input(self, value, clear=True, by_js=False):
        self.log.append(f'input:{value}')


class _Page:
    """命令层假页面——ele/actions/states 三件套。"""

    def __init__(self, ele=None, url='https://api.x.com/p'):
        self.url = url
        self.title = 't'
        self.latest_tab = None
        self._ele = ele

        class _States:
            ready_state = 'complete'
        self.states = _States()

        class _Actions:
            def __init__(s):
                s.typed = []

            def type(s, text):
                s.typed.append(text)
        self.actions = _Actions()

    def ele(self, locator, index=1, timeout=None):
        return self._ele


def _mock_session_page(monkeypatch, page):
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})


# ── L1 touched_apis：click/fill/type ───────────────────────────

def test_click_touched_apis_window_hit(monkeypatch):
    """窗口内 XHR → data.hints.touched_apis（method/url/status）。"""
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/draft', time.time() + 0.1))
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#b'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    hits = d['hints']['touched_apis']
    assert hits[0]['url'] == 'https://api.x.com/draft'
    assert hits[0]['method'] == 'GET' and hits[0]['status'] == 200


def test_click_touched_apis_out_of_window(monkeypatch):
    """超窗 XHR（10s 前）→ hints 整体缺省（不误报）。"""
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/old', time.time() - 10))
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#b'])
    assert r.exit_code == 0, r.output
    assert 'hints' not in json.loads(r.output)['data']


def test_click_no_netlog_hints_absent(monkeypatch):
    """netlog 未挂载（read_entries→None）→ hints 缺省。"""
    _mock_netlog(monkeypatch, None)
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#b'])
    assert r.exit_code == 0, r.output
    assert 'hints' not in json.loads(r.output)['data']


def test_click_document_row_not_counted(monkeypatch):
    """窗口内 Document 行非 XHR → hints 缺省。"""
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/doc', time.time(),
                      rtype='Document'))
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#b'])
    assert 'hints' not in json.loads(r.output)['data']


def test_hints_env_disable(monkeypatch):
    """DPX_API_HINT=0 整层关闭——窗口内 XHR 也不出 hints。"""
    monkeypatch.setenv('DPX_API_HINT', '0')
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/draft', time.time()))
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#b'])
    assert 'hints' not in json.loads(r.output)['data']


def test_fill_touched_apis_hit(monkeypatch):
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/save', time.time()))
    _mock_session_page(monkeypatch, _Page(ele=_Ele()))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['fill', 'css:#in', 'hello'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['hints']['touched_apis'][0]['url'] == \
        'https://api.x.com/save'


def test_type_touched_apis_hit(monkeypatch):
    _mock_netlog(monkeypatch,
                 _xhr('1', 'https://api.x.com/ac', time.time()))
    _mock_session_page(monkeypatch, _Page())
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['type', 'abc'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['hints']['touched_apis'][0]['url'] == 'https://api.x.com/ac'


def test_touched_apis_ts_fallback(monkeypatch):
    """无 wts 的老行回退 ts——ts 为 unix 形态时仍按窗口判。"""
    entries = [{'kind': 'req', 'id': '1',
                'url': 'https://a.com/x', 'method': 'GET',
                'type': 'Fetch', 'ts': time.time()}]
    _mock_netlog(monkeypatch, entries)
    out = hints_mod.touched_apis('s', time.time() - 0.1, time.time())
    assert out and out[0]['url'] == 'https://a.com/x'


# ── L0 adapter_hint：decide/open ───────────────────────────────

def _decide_mocks(monkeypatch, url='https://api.x.com/p', epoch='e1'):
    """decide e2e 通道 mock（_page_state/get_browser/session 存储）。"""
    state = {'state': {'url': url, 'title': 't', 'sig': epoch},
             'options': [{'ref': i + 1, 'role': 'button',
                          'name': f'b{i}'} for i in range(3)],
             'epoch': epoch, 'truncated': False, 'options_total': 3}
    monkeypatch.setattr(agent, '_page_state',
                        lambda p, s, max_opts=20, **kw: state)
    monkeypatch.setattr(_utils, 'get_browser', lambda s: _Page(url=url))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'save_refs', lambda *a, **k: None)
    monkeypatch.setattr(agent, '_RERANKER_FAM', None)


def _reranker_resp(**over):
    r = {'target': 1,
         'probs': {'target': {'1': 0.9, '2': 0.08, '3': 0.02}},
         'confidence': 0.9, 'backend': 'st',
         'done': False, 'stuck': False}
    r.update(over)
    return r


def test_decide_adapter_hint_host_match(monkeypatch, adir):
    """当前页 host 命中 adapter url_template host → adapter_hint。"""
    _write_adapter(adir)
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: _reranker_resp())
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    hint = d['hints']['adapter_hint']
    assert hint == {'name': 'x-draft', 'method': 'PATCH',
                    'params_keys': ['id']}


def test_decide_adapter_hint_no_match(monkeypatch, adir):
    """host 不匹配/无 adapter → hints 缺省。"""
    _write_adapter(adir, url_template='https://other.com/api')
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: _reranker_resp())
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    assert 'hints' not in json.loads(r.output)['data']


def test_decide_adapter_hint_on_unsure(monkeypatch, adir):
    """拒答（AGENT_UNSURE）detail 同带 adapter_hint。"""
    _write_adapter(adir)
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: _reranker_resp(
                            probs={'target': {'1': 0.501, '2': 0.499}}))
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    out = json.loads(r.output)
    assert out['code'] == 'AGENT_UNSURE'
    detail = json.loads(out['detail'])
    assert detail['hints']['adapter_hint']['name'] == 'x-draft'


def test_decide_cache_hit_still_recomputes_hint(monkeypatch, adir):
    """L0.5：缓存命中仍当次重算——删 adapter 后命中不再提示。"""
    path = _write_adapter(adir)
    _decide_mocks(monkeypatch)
    calls = []
    monkeypatch.setattr(
        agent, '_engine_reranker',
        lambda s, g: calls.append(g) or _reranker_resp())
    from dp_cli.main import cli
    runner = CliRunner()
    r1 = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    assert json.loads(r1.output)['data']['hints']['adapter_hint']
    # 删 adapter——同 (url,epoch,goal) 二次 decide 走缓存命中
    (adir / 'x-draft.json').unlink()
    r2 = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    d2 = json.loads(r2.output)['data']
    assert d2['cached'] is True                 # 决策缓存命中
    assert 'hints' not in d2                    # hint 现算：不再误指
    assert calls == ['点Go']                    # 引擎未重打分
    assert path


def test_open_adapter_hint(monkeypatch, adir):
    """open 响应 data.hints.adapter_hint——当前页 host 命中。"""
    _write_adapter(adir)
    monkeypatch.setattr(browser_mod, 'get_browser',
                        lambda *a, **k: _Page(url='https://api.x.com/p'))
    monkeypatch.setattr(browser_mod, 'load_session', lambda s: {})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['open'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['hints']['adapter_hint']['name'] == 'x-draft'


def test_open_adapter_hint_absent(monkeypatch):
    """无 adapter 注册表 → hints 缺省（不空对象）。"""
    monkeypatch.setattr(browser_mod, 'get_browser',
                        lambda *a, **k: _Page(url='https://api.x.com/p'))
    monkeypatch.setattr(browser_mod, 'load_session', lambda s: {})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['open'])
    assert r.exit_code == 0, r.output
    assert 'hints' not in json.loads(r.output)['data']


# ── 单元级：adapter_hint 匹配逻辑 ──────────────────────────────

def test_adapter_hint_subdomain_match(adir):
    """host 互为父域后缀互通——zhuanlan.zhihu.com 命中 zhihu.com。"""
    _write_adapter(adir, url_template='https://zhihu.com/api/x')
    h = hints_mod.adapter_hint('https://zhuanlan.zhihu.com/draft')
    assert h and h['name'] == 'x-draft'
    h = hints_mod.adapter_hint('https://zhihu.com/p')
    assert h and h['name'] == 'x-draft'


def test_adapter_hint_goal_weighting(adir):
    """多命中时 goal token 命中 adapter 名的加权优先。"""
    _write_adapter(adir, name='a-alpha',
                   url_template='https://api.x.com/a')
    _write_adapter(adir, name='x-search',
                   url_template='https://api.x.com/s')
    h = hints_mod.adapter_hint('https://api.x.com/p', goal='search')
    assert h['name'] == 'x-search'        # goal token 加权
    h = hints_mod.adapter_hint('https://api.x.com/p')
    assert h['name'] == 'a-alpha'         # 无 goal→注册表序首个


def test_adapter_hint_bad_url_none():
    assert hints_mod.adapter_hint('') is None
    assert hints_mod.adapter_hint('about:blank') is None


def test_attach_hints_empty_not_mounted():
    d = {'a': 1}
    assert hints_mod.attach_hints(d, adapter_hint=None,
                                  touched_apis=None) is d
    assert 'hints' not in d
    hints_mod.attach_hints(d, adapter_hint={'name': 'x'})
    assert d['hints'] == {'adapter_hint': {'name': 'x'}}
