# -*- coding:utf-8 -*-
"""真机归因四修（B1-B4）。

B1  meta-rules 命中需 ref 的元素动作却 ref=None → 降 conf 走
    AGENT_UNSURE（tier=low/should_escalate=true），非 conf=1.0 空 ref；
    纯动作类（done/wait/back/scroll/blocked）不受影响
B2  LLM/JEV/external 引擎 parse/网络失败 → 降级 reranker 链路
    （响应标 degraded/engine_requested/fallback_reason），
    reranker 也失败才 UNSURE；reranker 自身故障维持 DECIDE_FAILED
B3  tab-select --index 旗 = 序号位置参数别名；两给校验一致
B4  eval 多语句 JS（顶层 return）→ Illegal return statement 时
    自动 IIFE 兜底重试；运行时错误不双跑

全程 mock——不依赖真浏览器/网络/决策模型。
"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.agent as agent
import dp_cli.commands._utils as _utils
import dp_cli.commands.page as page_mod
import dp_cli.commands.tab as tab_mod

pytestmark = pytest.mark.unit


# ── decide 通道公共 mock（同 test_hints._decide_mocks 形态）──────

class _Page:
    def __init__(self, url='https://x/'):
        self.url = url
        self.latest_tab = None


def _decide_mocks(monkeypatch, url='https://x/', epoch='e1', n=3):
    state = {'state': {'url': url, 'title': 't', 'sig': epoch},
             'options': [{'ref': i + 1, 'role': 'button',
                          'name': f'b{i}'} for i in range(n)],
             'epoch': epoch, 'truncated': False, 'options_total': n}
    monkeypatch.setattr(agent, '_page_state',
                        lambda p, s, max_opts=20, **kw: state)
    monkeypatch.setattr(_utils, 'get_browser', lambda s: _Page(url=url))
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'save_refs', lambda *a, **k: None)
    monkeypatch.setattr(agent, '_RERANKER_FAM', None)
    agent._DECIDE_CACHE.clear()
    return state


def _reranker_resp(**over):
    r = {'target': 1,
         'probs': {'target': {'1': 0.9, '2': 0.08, '3': 0.02}},
         'confidence': 0.9, 'backend': 'st',
         'done': False, 'stuck': False}
    r.update(over)
    return r


# ── B1：meta-rules 元素动作空 ref → UNSURE ──────────────────────

def test_b1_meta_ref_missing_degrades_unsure(monkeypatch):
    """meta 命中 ref 需求动作但 ref=None → AGENT_UNSURE 非 conf1.0 空 ref。"""
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_meta_decide', lambda s, g: {
        'action': 'click', 'ref': None,
        'deterministic': True, 'reason': '规则族命中无可执行元素'})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'external',
                                 '--answer', '{}'])
    out = json.loads(r.output)
    assert out['code'] == 'AGENT_UNSURE'
    detail = json.loads(out['detail'])
    assert detail['engine'] == 'meta-rules'
    assert detail['action'] == 'click' and detail['ref'] is None
    assert detail['confidence_tier'] == 'low'
    assert detail['should_escalate'] is True
    assert detail['ref_missing'] is True
    # 不再放行 conf=1.0 空 ref 假确定性建议
    assert detail['confidence'] == 0.0


def test_b1_meta_pure_action_unaffected(monkeypatch):
    """纯动作类规则（wait/done/back/scroll 无 ref 需求）照旧 conf=1.0。"""
    _decide_mocks(monkeypatch)
    from dp_cli.main import cli
    # '等页面加载' 自然命中 _WAIT_KW ——真实规则路径非 monkeypatch
    r = CliRunner().invoke(cli, ['decide', '等页面加载完再说',
                                 '--engine', 'external', '--answer', '{}'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    d = out['data']
    assert d['engine'] == 'meta-rules' and d['action'] == 'wait'
    assert d['confidence'] == 1.0 and d['confidence_tier'] == 'rule'
    assert d['should_escalate'] is False


# ── B2：LLM/JEV 引擎失败降级 reranker ───────────────────────────

def test_b2_jev_failure_falls_back_reranker(monkeypatch):
    """jev 答复非 JSON → 自动降级 reranker，响应如实标 degraded。"""
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_jev', lambda req: {
        '_error': 'Jev 答复非 JSON——模型未按 schema 输出'})
    calls = []
    monkeypatch.setattr(
        agent, '_engine_reranker',
        lambda s, g: calls.append(g) or _reranker_resp())
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'jev'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    d = out['data']
    assert calls == ['点Go']                       # reranker 真被调用
    assert d['engine'] == 'reranker'               # 实际作答引擎
    assert d['engine_requested'] == 'jev'
    assert d['degraded'] is True
    assert 'Jev 答复非 JSON' in d['fallback_reason']
    assert d['advisory'] is True                   # reranker 语义保留
    # margin_source 契约不破——reranker 只出 target 单边 probs
    assert d['margin_source'] == 'single_side'


def test_b2_external_parse_failure_falls_back(monkeypatch):
    """external 端空/坏答复（真机 '答复 JSON 解析失败'）→ 降级 reranker。"""
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: _reranker_resp())
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'external',
                                 '--answer', '{bad json'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    d = out['data']
    assert d['engine'] == 'reranker'
    assert d['engine_requested'] == 'external'
    assert d['degraded'] is True
    assert '答复 JSON 解析失败' in d['fallback_reason']


def test_b2_both_engines_fail_unsure(monkeypatch):
    """原引擎失败 + reranker 亦失败 → AGENT_UNSURE（双引擎皆不可用）。"""
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_jev', lambda req: {
        '_error': 'Jev API 连接失败: refused'})
    monkeypatch.setattr(agent, '_engine_reranker', lambda s, g: {
        '_error': '缺 sentence-transformers'})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'jev'])
    out = json.loads(r.output)
    assert out['code'] == 'AGENT_UNSURE'
    detail = json.loads(out['detail'])
    assert detail['degraded'] is True
    assert detail['engine_requested'] == 'jev'
    assert detail['engine'] == 'reranker'
    assert detail['confidence_tier'] == 'low'
    assert detail['should_escalate'] is True
    assert 'sentence-transformers' in detail['fallback_error']


def test_b2_reranker_own_failure_stays_decide_failed(monkeypatch):
    """reranker 本身就是请求引擎时无更低档可退——维持 DECIDE_FAILED。"""
    _decide_mocks(monkeypatch)
    monkeypatch.setattr(agent, '_engine_reranker', lambda s, g: {
        '_error': '缺 sentence-transformers'})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    out = json.loads(r.output)
    assert out['code'] == 'DECIDE_FAILED'


# ── B3：tab-select --index 别名 ─────────────────────────────────

class _TabSet:
    def __init__(self):
        self.activated = 0

    def activate(self):
        self.activated += 1


class _Tab:
    def __init__(self, tid):
        self.tab_id = tid
        self.url = f'https://{tid}.example/'
        self.title = f'title-{tid}'
        self.set = _TabSet()


class _TabPage:
    latest_tab = None

    def __init__(self, ids):
        self.tab_ids = list(ids)
        self._tabs = {t: _Tab(t) for t in ids}

    def get_tab(self, tid):
        return self._tabs[tid]


def _tab_mocks(monkeypatch, ids=('AAA', 'BBB', 'CCC')):
    page = _TabPage(ids)
    monkeypatch.setattr(tab_mod, '_get_page', lambda s, **kw: page)
    monkeypatch.setattr(tab_mod, 'load_session', lambda s: {})
    saved = []
    monkeypatch.setattr(tab_mod, 'save_session',
                        lambda s, d: saved.append(d))
    return page, saved


def test_b3_tab_select_index_flag(monkeypatch):
    """--index N ≡ 位置参数 N——真机 'dpx tab-select --index 1' Usage 归因。"""
    page, saved = _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', '--index', '1'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['id'] == 'BBB'
    assert saved and saved[-1]['active_tab'] == 'BBB'


def test_b3_tab_select_positional_still_works(monkeypatch):
    """位置参数原样兼容。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', '0'])
    out = json.loads(r.output)
    assert out['status'] == 'ok' and out['data']['id'] == 'AAA'


def test_b3_tab_select_both_consistent(monkeypatch):
    """同义两给且一致 → 放行。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', '2', '--index', '2'])
    out = json.loads(r.output)
    assert out['status'] == 'ok' and out['data']['id'] == 'CCC'


def test_b3_tab_select_both_conflict(monkeypatch):
    """同义两给不一致 → INVALID_ARGS 不猜。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', '1', '--index', '2'])
    out = json.loads(r.output)
    assert out['code'] == 'INVALID_ARGS'


def test_b3_tab_select_index_with_none_conflict(monkeypatch):
    """TARGET=none（解绑语义）与 --index 语义冲突 → INVALID_ARGS。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', 'none', '--index', '1'])
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


def test_b3_tab_select_no_target_errors(monkeypatch):
    """TARGET 与 --index 双缺 → INVALID_ARGS（不再是裸 Usage）。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select'])
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


def test_b3_tab_select_index_out_of_range(monkeypatch):
    """--index 越界走与位置参数相同的 TAB_NOT_FOUND。"""
    _tab_mocks(monkeypatch)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['tab-select', '--index', '9'])
    assert json.loads(r.output)['code'] == 'TAB_NOT_FOUND'


# ── B4：eval 多语句 IIFE 兜底 ───────────────────────────────────

class _JSPage:
    """run_js mock：as_expr 下遇顶层 return 抛 Illegal return statement，
    IIFE 包裹可执行——模拟 Runtime.evaluate 真实语义。"""

    latest_tab = None
    url = 'https://x/'
    title = 't'

    def __init__(self):
        self.calls = []

    def run_js(self, script, *a, as_expr=False, timeout=None):
        self.calls.append(script)
        if not as_expr:
            return 'body-result'                 # function 体路径
        if script.startswith('(function(){'):
            return 'iife-result'                 # IIFE 兜底成功
        if 'return' in script:
            raise Exception(
                "Uncaught SyntaxError: Illegal return statement")
        return 'expr-result'


def _eval_mocks(monkeypatch, page):
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})


def test_b4_eval_multistatement_iife_fallback(monkeypatch):
    """'var x=…;return x' → Illegal return 自动 IIFE 重跑成功。"""
    page = _JSPage()
    _eval_mocks(monkeypatch, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(
        cli, ['eval', 'var x=40+2; return x'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['result'] == 'iife-result'
    assert page.calls == ['var x=40+2; return x',
                          '(function(){var x=40+2; return x})()']


def test_b4_eval_foreach_collect_iife(monkeypatch):
    """forEach 收集+return 形态同样兜底。"""
    page = _JSPage()
    _eval_mocks(monkeypatch, page)
    from dp_cli.main import cli
    script = 'var out=[];document.querySelectorAll("a").forEach(a=>out.push(a.href));return out'
    r = CliRunner().invoke(cli, ['eval', script])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert page.calls[-1] == f'(function(){{{script}}})()'


def test_b4_eval_single_expr_unchanged(monkeypatch):
    """单表达式照旧一次直行——不包 IIFE。"""
    page = _JSPage()
    _eval_mocks(monkeypatch, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'ok'
    assert out['data']['result'] == 'expr-result'
    assert page.calls == ['document.title']


def test_b4_eval_leading_return_unchanged(monkeypatch):
    """'return expr' 头部形态照旧走 function 体路径——现有行为不变。"""
    page = _JSPage()
    _eval_mocks(monkeypatch, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'return document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'ok'
    assert out['data']['result'] == 'body-result'
    assert page.calls == ['return document.title']


def test_b4_eval_runtime_error_not_retried(monkeypatch):
    """运行时错误（非语法类）不重跑——副作用脚本不双跑。"""

    class _ErrPage(_JSPage):
        def run_js(self, script, *a, as_expr=False, timeout=None):
            self.calls.append(script)
            raise Exception('Uncaught ReferenceError: foo is not defined')

    page = _ErrPage()
    _eval_mocks(monkeypatch, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'foo.bar()'])
    out = json.loads(r.output)
    assert out['code'] == 'JS_FAILED'
    assert page.calls == ['foo.bar()']             # 只跑一次
