# -*- coding:utf-8 -*-
"""真机归因修复（B1-B4、B7、B9、B10）。

B1  meta-rules 命中需 ref 的元素动作却 ref=None → 降 conf 走
    AGENT_UNSURE（tier=low/should_escalate=true），非 conf=1.0 空 ref；
    纯动作类（done/wait/back/scroll/blocked）不受影响
B2  LLM/JEV/external 引擎 parse/网络失败 → 降级 reranker 链路
    （响应标 degraded/engine_requested/fallback_reason），
    reranker 也失败才 UNSURE；reranker 自身故障维持 DECIDE_FAILED
B3  tab-select --index 旗 = 序号位置参数别名；两给校验一致
B4  eval 多语句 JS（顶层 return）→ Illegal return statement 时
    自动 IIFE 兜底重试；运行时错误不双跑
B7  click/fill actionability occluded → 自动降级 by-js 重试一次，
    响应标 fallback:'by_js'+occluded:true（advisory 非静默）；
    重定位也失败才按原 occluded 报错
B9  tab 绑定漂移——_cur_tab 对 get_tab 异常不再无条件清绑定：
    tab_ids 在册但 attach 失败 → 保留绑定 + 返回 None →
    _get_page fail-closed 报 TAB_FAILED（不静默漂 latest_tab）；
    get_tab 假阴性时绕名单直接 attach 复核；真关才清绑定回退
B10 fill 遮挡降级 by_js → input 前先 run_js 显式清场
    （value=''+input 事件），再 input(clear=False,by_js=True)；
    二阶：正常路径 clear=True 同样前置显式清场（React 受控框
    追加污染根治），clear=False 显式传才不清场
B12 daemon meta 落 build_id（git describe，非 git 降级 version）
    + client 握手比对——同 version 内代码漂移 stderr 告警
    建议 daemon-stop（不硬拒）；老 meta 无 build_id 字段放行
B13 wait --idle 长连接豁免——WS 握手（type=WebSocket/wss://
    scheme/Upgrade 头）与 SSE（EventSource/event-stream Accept）
    永不 loadingFinished 也不占 pending，普通请求完成即收敛
B14 adapter body JSON 顶层标量键 = kw=v 注入面——record 端
    body_params_keys 落盘+提示，回放端 kw=v 替换 JSON 子键
    （类型对齐录制值），命中键不再进 extra 警告

全程 mock——不依赖真浏览器/网络/决策模型。
"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.agent as agent
import dp_cli.commands._utils as _utils
import dp_cli.commands.browser as browser_mod
import dp_cli.commands.element as element_mod
import dp_cli.commands.network as net_mod
import dp_cli.commands.page as page_mod
import dp_cli.commands.tab as tab_mod
import dp_cli.daemon as daemon_mod

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


# ── B7：click/fill 遮挡自动降级 by-js ─────────────────────────

class _OccEle:
    """actionability JS 恒报 occluded（elementFromPoint 命中浮层）；
    hit_check=False 重定位时 wait_actionable 内部会把 occluded 转 ok——
    与真机同一 JS 判定路径，非绕过 wait_actionable 的直接 mock。"""
    def __init__(self):
        self.clicked = []
        self.inputs = []

    def run_js(self, script, *a, **k):
        if 'elementFromPoint' in script:
            return {'ok': False, 'reason': 'occluded',
                    'occluder': {'tag': 'div', 'id': 'mask',
                                 'cls': 'overlay'},
                    'rect': [0, 0, 10, 10], 'center': [5, 5]}
        self.clicked.append(script)
        return None

    def input(self, value, clear=True, by_js=False):
        self.inputs.append({'value': value, 'clear': clear,
                            'by_js': by_js})


class _ElePage:
    def __init__(self, ele):
        self._ele = ele
        self.latest_tab = None
        self.url = 'https://x/'
        self.title = 't'

    def ele(self, locator, index=1, timeout=10):
        return self._ele


def _ele_mocks(monkeypatch, ele):
    page = _ElePage(ele)
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    return page


def test_b7_click_occluded_falls_back_by_js(monkeypatch):
    """遮挡 → 自动 hit_check=False 重定位 + JS 派发，fallback 字段标注。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#buy', '--timeout', '2'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    d = out['data']
    assert d['fallback'] == 'by_js'
    assert d['occluded'] is True
    assert d['occluder']['id'] == 'mask'
    # 真实执行的是 JS 派发，不是 ele.click()
    assert ele.clicked == ['this.click()']


def test_b7_fill_occluded_falls_back_by_js(monkeypatch):
    """fill 遮挡 → input(by_js=True) 降级，fallback/occluded 标注。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['fill', 'css:#kw', '手机', '--timeout', '2'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    d = out['data']
    assert d['fallback'] == 'by_js' and d['occluded'] is True
    # B10：降级路径先 run_js 清场（value=''+input 事件），
    # 再 input(clear=False, by_js=True)——不再靠 DP input 自带
    # clear 语义（真机追加不清空归因）
    assert ele.clicked == [
        'this.value="";this.dispatchEvent('
        'new Event("input",{bubbles:true}))']
    assert ele.inputs == [{'value': '手机', 'clear': False, 'by_js': True}]


def test_b7_click_by_js_explicit_no_fallback_mark(monkeypatch):
    """显式 --by-js 首过即 hit_check=False——不标 fallback（非降级）。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#buy', '--by-js',
                                 '--timeout', '2'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert 'fallback' not in out['data']
    assert ele.clicked == ['this.click()']


def test_b7_occluded_retry_also_fails_reports_original(monkeypatch):
    """重定位也失败（元素消失）→ 按原 occluded 上报（信息量比
    not_found 有用），且不执行动作。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    calls = []

    def fake_resolve(ctx, locator, index=1, timeout=10.0, no_wait=False,
                     hit_check=True):
        calls.append(hit_check)
        if len(calls) == 1:
            return ele, {'reason': 'occluded',
                         'occluder': {'tag': 'div', 'id': 'mask',
                                      'cls': 'overlay'}}
        return None, {'reason': 'not_found'}

    monkeypatch.setattr(element_mod, '_resolve_ele', fake_resolve)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['click', 'css:#buy'])
    out = json.loads(r.output)
    assert out['status'] == 'error'
    assert out['code'] == 'CLICK_FAILED'
    assert '被遮挡' in out['message']
    assert 'mask' in (out.get('detail') or '')
    assert calls == [True, False]          # 首过命中检测 → 降级跳过
    assert ele.clicked == []               # 未执行任何点击


def test_b7_non_occluded_fail_no_retry(monkeypatch):
    """hidden/disabled 等非遮挡失败不降级——一次判定直接上报。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    calls = []

    def fake_resolve(ctx, locator, index=1, timeout=10.0, no_wait=False,
                     hit_check=True):
        calls.append(hit_check)
        return ele, {'reason': 'hidden'}

    monkeypatch.setattr(element_mod, '_resolve_ele', fake_resolve)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['fill', 'css:#kw', 'x'])
    out = json.loads(r.output)
    assert out['code'] == 'FILL_FAILED'
    assert '不可交互' in out['message']
    assert calls == [True]                 # 无降级重试
    assert ele.inputs == []


# ── B9：tab 绑定漂移——_cur_tab fail-closed ─────────────────────

class _BoundTab:
    """单 tab mock：记录 run_js 命中，区分 eval 打到哪个 tab。"""
    def __init__(self, tid):
        self.tab_id = tid
        self.calls = []
        self.latest_tab = None
        self.url = f'https://{tid}/'
        self.title = tid

    def run_js(self, script, *a, as_expr=False, timeout=None, **k):
        self.calls.append(script)
        return f'{self.tab_id}-result'

    def ele(self, locator, timeout=None):
        return self           # --locator 分支：self 即元素


class _BoundPage:
    """浏览器级 page mock：双 tab 场景。

    active_tab 绑 tabB；latest_tab=tabA（外部活动 tab——B9 漂移终点）。
    get_tab_exc 注入 get_tab 瞬断；tab_ids 可控（tab 是否在册）。
    """
    def __init__(self, tabs, tab_ids=None, get_tab_exc=None):
        self._tabs = tabs
        self.latest_tab = tabs['tabA']
        self._tab_ids = list(tabs) if tab_ids is None else tab_ids
        self._get_tab_exc = get_tab_exc
        self.browser = self       # ChromiumTab(page.browser, tid) 直 attach 用
        self.calls = []

    @property
    def tab_ids(self):
        return self._tab_ids

    def get_tab(self, tid):
        if self._get_tab_exc:
            raise self._get_tab_exc
        return self._tabs[tid]


def _b9_mocks(monkeypatch, page, sess):
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: dict(sess))


def test_b9_eval_hits_bound_tab(monkeypatch):
    """active_tab=tabB、latest_tab=tabA → eval 打在 tabB（绑定生效）。"""
    tabs = {'tabA': _BoundTab('tabA'), 'tabB': _BoundTab('tabB')}
    page = _BoundPage(tabs)
    _b9_mocks(monkeypatch, page, {'active_tab': 'tabB'})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['result'] == 'tabB-result'
    assert tabs['tabB'].calls == ['document.title']
    assert tabs['tabA'].calls == []          # 无漂移


def test_b9_eval_locator_branch_bound_tab(monkeypatch):
    """eval --locator 元素分支同样打在绑定 tab。"""
    tabs = {'tabA': _BoundTab('tabA'), 'tabB': _BoundTab('tabB')}
    page = _BoundPage(tabs)
    _b9_mocks(monkeypatch, page, {'active_tab': 'tabB'})
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'this.id', '--locator', '#kw'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['result'] == 'tabB-result'
    assert tabs['tabA'].calls == []


def test_b9_get_tab_transient_direct_attach_recovers(monkeypatch):
    """get_tab 假阴性（名单过滤/瞬断）但 tab 在册 → 绕名单直 attach
    取回 tabB，绑定保留（不清 active_tab）、eval 不漂移。"""
    tabs = {'tabA': _BoundTab('tabA'), 'tabB': _BoundTab('tabB')}
    page = _BoundPage(tabs, get_tab_exc=RuntimeError('NO_SUCH_TAB'))
    _b9_mocks(monkeypatch, page, {'active_tab': 'tabB'})
    saved = []
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.append(dict(d)))
    # get_tab 抛错后 _cur_tab 走 ChromiumTab(page.browser, tid) 直 attach
    import DrissionPage._pages.chromium_tab as _ct
    monkeypatch.setattr(_ct, 'ChromiumTab',
                        lambda browser, tid: tabs[tid])
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['result'] == 'tabB-result'
    assert tabs['tabA'].calls == []
    assert saved == []                        # 绑定未被清/回写


def test_b9_bound_tab_unreachable_fails_closed(monkeypatch):
    """绑定 tab 在册但两路 attach 均败 → TAB_FAILED 显式报错，
    绑定保留（可重试），绝不静默漂 latest_tab 打 tabA。"""
    tabs = {'tabA': _BoundTab('tabA'), 'tabB': _BoundTab('tabB')}
    page = _BoundPage(tabs, get_tab_exc=RuntimeError('boom'))
    _b9_mocks(monkeypatch, page, {'active_tab': 'tabB'})
    saved = []
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.append(dict(d)))
    import DrissionPage._pages.chromium_tab as _ct

    def _no_attach(browser, tid):
        raise RuntimeError('attach failed')
    monkeypatch.setattr(_ct, 'ChromiumTab', _no_attach)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'error'
    assert out['code'] == 'TAB_FAILED'
    assert 'tabB' in out['message']
    assert tabs['tabA'].calls == []          # 没有漂到 latest_tab
    assert saved == []                       # 绑定保留


def test_b9_dead_tab_clears_binding_falls_back(monkeypatch):
    """tab 真关（不在 tab_ids）→ 清绑定存盘 + 回退 latest_tab——
    原失效语义保留。"""
    tabs = {'tabA': _BoundTab('tabA')}
    page = _BoundPage(tabs, tab_ids=['tabA'],
                      get_tab_exc=RuntimeError('gone'))
    sess = {'active_tab': 'tabB'}
    _b9_mocks(monkeypatch, page, sess)
    saved = []
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.append(dict(d)))
    import DrissionPage._pages.chromium_tab as _ct

    def _no_attach(browser, tid):
        raise RuntimeError('attach failed')
    monkeypatch.setattr(_ct, 'ChromiumTab', _no_attach)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['eval', 'document.title'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['result'] == 'tabA-result'
    assert saved and 'active_tab' not in saved[-1]   # 失效绑定已清


# ── B10：fill 清场（降级 + 正常路径）─────────────────────────────

def test_b10_normal_fill_clear_js_before_input(monkeypatch):
    """B10 二阶：正常（非降级）fill 路径 clear=True 同样前置显式
    清场——run_js 清场在 input 之前（顺序断言）。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    monkeypatch.setattr(element_mod, '_resolve_ele',
                        lambda *a, **k: (ele, None))
    seq = []

    orig_run_js = ele.run_js
    def run_js(script, *a, **k):
        if 'elementFromPoint' not in script:
            seq.append(('run_js', script))
        return orig_run_js(script, *a, **k)
    ele.run_js = run_js
    orig_input = ele.input
    def _input(value, clear=True, by_js=False):
        seq.append(('input', value, clear, by_js))
        return orig_input(value, clear=clear, by_js=by_js)
    ele.input = _input

    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['fill', 'css:#kw', 'x'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert 'fallback' not in out['data']
    # 显式清场先跑，随后 input(clear=False) 走 DP 追加填入——
    # 不再依赖 DP input(clear=True) 的清场语义（真机 React 受控
    # 框追加串联归因）
    assert seq == [
        ('run_js', 'this.value="";this.dispatchEvent('
                   'new Event("input",{bubbles:true}))'),
        ('input', 'x', False, False)]


def test_b10_normal_fill_clear_false_no_clear_js(monkeypatch):
    """clear=False 显式传 → 无清场 run_js，input(clear=False) 追加。

    --clear 是 is_flag default=True 关不掉，直调 callback 注入
    clear=False 验证语义分支。
    """
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    monkeypatch.setattr(element_mod, '_resolve_ele',
                        lambda *a, **k: (ele, None))
    from dp_cli.main import cli
    cli.commands['fill'].callback(
        locator='css:#kw', value='v2', session='default', index=1,
        clear=False, by_js=False, frame=None, timeout=10,
        no_wait=False, human=False, human_min=0.1, human_max=0.3)
    assert ele.inputs == [{'value': 'v2', 'clear': False,
                           'by_js': False}]
    assert ele.clicked == []               # 无清场 run_js


def test_b10_fallback_clear_runs_before_input(monkeypatch):
    """降级路径：清场 run_js 在 input 调用之前（顺序断言）。"""
    ele = _OccEle()
    _ele_mocks(monkeypatch, ele)
    seq = []

    orig_run_js = ele.run_js
    def run_js(script, *a, **k):
        if 'elementFromPoint' not in script:
            seq.append(('run_js', script))
        return orig_run_js(script, *a, **k)
    ele.run_js = run_js
    orig_input = ele.input
    def _input(value, clear=True, by_js=False):
        seq.append(('input', value, clear, by_js))
        return orig_input(value, clear=clear, by_js=by_js)
    ele.input = _input

    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['fill', 'css:#kw', 'u3', '--timeout', '2'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert seq == [
        ('run_js', 'this.value="";this.dispatchEvent('
                   'new Event("input",{bubbles:true}))'),
        ('input', 'u3', False, True)]


# ── B11：跑批归因三连（auto-connect 首 tab 劫持 / sleep -S /
#   wait --idle 心跳白名单）────────────────────────────────────

class _Ready:
    ready_state = 'complete'


class _NavTab:
    def __init__(self, tid):
        self.tab_id = tid
        self.got = []
        self.url = f'https://tab-{tid}.example/'
        self.title = f't-{tid}'
        self.states = _Ready()

    def get(self, url, *a, **k):
        self.got.append(url)


class _NavPage:
    """ChromiumPage 替身：get 记录在首个 tab（劫持路径），
    new_tab/get_tab 记专属 tab。"""

    def __init__(self, tabs=()):
        self.url = 'https://first-tab.example/'
        self.title = 'user-first'
        self.states = _Ready()
        self.got = []
        self.new_tabs = []
        self._tabs = {t.tab_id: t for t in tabs}

    def get(self, url, *a, **k):
        self.got.append(url)

    def new_tab(self, url=None, **kw):
        t = _NavTab(f'NEW{len(self.new_tabs) + 1}')
        t.got.append(url)
        self._tabs[t.tab_id] = t
        self.new_tabs.append(t)
        return t

    def get_tab(self, tid):
        return self._tabs.get(tid)


def _open_mocks(monkeypatch, sess_store, page):
    monkeypatch.setattr(browser_mod, 'get_browser', lambda *a, **k: page)
    monkeypatch.setattr(browser_mod, 'load_session',
                        lambda s: dict(sess_store))
    monkeypatch.setattr(browser_mod, 'save_session',
                        lambda s, d: sess_store.update(d))
    monkeypatch.setattr(browser_mod, 'discover_port_from_profile',
                        lambda p: 9222)


def test_b11_open_auto_connect_navigates_new_tab(monkeypatch):
    """auto-connect 无 active_tab → new_tab+绑定，用户首个 tab 不动。"""
    sess_store, page = {}, _NavPage()
    _open_mocks(monkeypatch, sess_store, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(
        cli, ['open', 'https://example.com', '--auto-connect',
              '--probe-dir', '/tmp/x'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert page.got == []                    # 首个 tab 未被导航
    assert len(page.new_tabs) == 1
    assert page.new_tabs[0].got == ['https://example.com']
    assert sess_store['active_tab'] == 'NEW1'   # 专属 tab 已绑定
    assert out['data']['url'] == 'https://tab-NEW1.example/'


def test_b11_open_auto_connect_bound_tab_navigates(monkeypatch):
    """auto-connect 已有 active_tab → 绑定 tab 内导航，不再开新 tab。"""
    bound = _NavTab('T1')
    sess_store = {'auto_connect': True, 'active_tab': 'T1'}
    page = _NavPage(tabs=[bound])
    _open_mocks(monkeypatch, sess_store, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(
        cli, ['open', 'https://example.com', '--auto-connect',
              '--probe-dir', '/tmp/x'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert page.got == [] and page.new_tabs == []
    assert bound.got == ['https://example.com']


def test_b11_open_auto_connect_stale_binding_falls_new_tab(monkeypatch):
    """active_tab 绑定失效（get_tab None）→ 清绑定走 new_tab，
    不回退劫持首个 tab。"""
    sess_store = {'auto_connect': True, 'active_tab': 'DEAD'}
    page = _NavPage()
    _open_mocks(monkeypatch, sess_store, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(
        cli, ['open', 'https://example.com', '--auto-connect',
              '--probe-dir', '/tmp/x'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert page.got == []
    assert len(page.new_tabs) == 1
    assert sess_store['active_tab'] == 'NEW1'


def test_b11_open_managed_browser_get_unchanged(monkeypatch):
    """非 auto-connect（dpx 自管实例）→ page.get 原语义回归。"""
    sess_store, page = {}, _NavPage()
    _open_mocks(monkeypatch, sess_store, page)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['open', 'https://example.com'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert page.got == ['https://example.com']
    assert page.new_tabs == []
    assert 'active_tab' not in sess_store


def test_b11_sleep_accepts_session_flag():
    """dpx sleep 0.01 -S x ——runner 统一补 -S 不再 INVALID_ARGS。"""
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['sleep', '0.01', '-S', 'taskpool'])
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    assert out['data']['slept'] == 0.01


# ── wait --idle 心跳白名单 ────────────────────────────────────

class _IdleDriver:
    def __init__(self):
        self.cbs = {}

    def set_callback(self, name, cb):
        if cb is None:
            self.cbs.pop(name, None)
        else:
            self.cbs[name] = cb


class _IdlePage:
    def __init__(self):
        self.driver = _IdleDriver()

    def run_cdp(self, *a, **k):
        return {}


def test_b11_idle_ignores_heartbeat():
    """心跳/埋点请求不计入 pending——长驻 heartbeat 页面也能收敛空闲。"""
    import threading, time
    page = _IdlePage()
    result = {}
    th = threading.Thread(
        target=lambda: result.setdefault(
            'v', _utils.wait_network_idle(page, idle_time=0.2,
                                          timeout=3)),
        daemon=True)
    th.start()
    time.sleep(0.05)
    cbs = page.driver.cbs
    # 真实请求发起并完成——随后只剩心跳
    cbs['Network.requestWillBeSent'](
        requestId='r1', request={'url': 'https://a.com/api/data'})
    cbs['Network.loadingFinished'](requestId='r1')
    # 长驻心跳（永不 finished）——修复前 pending 恒非空必超时
    for i in range(10):
        if 'v' in result:
            break
        cbs['Network.requestWillBeSent'](
            requestId=f'hb{i}',
            request={'url': 'https://xhs.example/beacon/heartbeat'})
        time.sleep(0.05)
    th.join(3)
    assert result.get('v') is True


def test_b11_idle_whitelist_off_preserves_semantics():
    """ignore_re=None 关闭过滤——心跳不 finish 时仍按原语义超时。"""
    import threading, time
    page = _IdlePage()
    result = {}

    def _run():
        try:
            result['v'] = _utils.wait_network_idle(
                page, idle_time=0.2, timeout=0.5, ignore_re=None)
        except TimeoutError:
            result['v'] = 'timeout'

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    time.sleep(0.05)
    page.driver.cbs['Network.requestWillBeSent'](
        requestId='hb1',
        request={'url': 'https://xhs.example/beacon/heartbeat'})
    th.join(3)
    assert result.get('v') == 'timeout'


# ── B12：daemon build_id 握手——同 version 内代码漂移告警 ─────────

def test_b12_build_id_falls_back_to_version(monkeypatch):
    """非 git 环境/打包版 git describe 失败 → build_id 降级 version
    （双侧同值不告警，向后兼容兜底）。"""
    monkeypatch.setattr(daemon_mod.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(
                            FileNotFoundError('no git')))
    assert daemon_mod._dpx_build_id() == daemon_mod._dpx_version()
    # git describe 成功 → commit 级标识透出
    class _R:
        returncode = 0
        stdout = '20b6012-dirty\n'
    monkeypatch.setattr(daemon_mod.subprocess, 'run',
                        lambda *a, **k: _R())
    assert daemon_mod._dpx_build_id() == '20b6012-dirty'


def test_b12_version_probe_build_drift_warns(tmp_path, monkeypatch):
    """version 一致但 build_id 漂移 → stderr 建议 daemon-stop（不硬拒）。"""
    meta = tmp_path / 's.session'
    meta.write_text(json.dumps({
        'version': daemon_mod._dpx_version(), 'build_id': 'old-sha'}))
    monkeypatch.setattr(daemon_mod, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon_mod, '_dpx_build_id', lambda: 'new-sha')
    warned = []
    monkeypatch.setattr('dp_cli.output._write_err', warned.append)
    daemon_mod._VER_WARNED.discard('s')
    daemon_mod._version_probe('s')
    assert warned and '漂移' in warned[0] and 'old-sha' in warned[0]


def test_b12_version_probe_no_build_id_passes(tmp_path, monkeypatch):
    """老 daemon meta 无 build_id → 现语义放行不告警（向后兼容）。"""
    meta = tmp_path / 's2.session'
    meta.write_text(json.dumps({'version': daemon_mod._dpx_version()}))
    monkeypatch.setattr(daemon_mod, '_meta_path', lambda s: meta)
    warned = []
    monkeypatch.setattr('dp_cli.output._write_err', warned.append)
    daemon_mod._VER_WARNED.discard('s2')
    daemon_mod._version_probe('s2')
    assert warned == []


def test_b12_version_probe_same_build_id_silent(tmp_path, monkeypatch):
    """build_id 一致 → 静默（新 daemon 配新 client 不误报）。"""
    meta = tmp_path / 's3.session'
    meta.write_text(json.dumps({
        'version': daemon_mod._dpx_version(), 'build_id': 'same'}))
    monkeypatch.setattr(daemon_mod, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon_mod, '_dpx_build_id', lambda: 'same')
    warned = []
    monkeypatch.setattr('dp_cli.output._write_err', warned.append)
    daemon_mod._VER_WARNED.discard('s3')
    daemon_mod._version_probe('s3')
    assert warned == []


def test_b12_status_surfaces_build_id(tmp_path, monkeypatch, capsys):
    """daemon-status 响应带 build_id（老 meta 无字段→null 不崩）。"""
    sock = tmp_path / 's4.sock'
    sock.write_text('')                     # POSIX 存在性标记
    meta = tmp_path / 's4.session'
    meta.write_text(json.dumps({'pid': -1, 'version': 'v1',
                                'build_id': 'b1'}))
    monkeypatch.setattr(daemon_mod, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon_mod, '_sock_path', lambda s: sock)
    monkeypatch.setattr(daemon_mod, '_sock_path_eff', lambda s: sock)
    daemon_mod._status('s4')
    out = json.loads(capsys.readouterr().out)
    assert out['data']['build_id'] == 'b1'


# ── B13：wait --idle 长连接（WS/SSE）豁免 ───────────────────────

def test_b13_idle_ws_sse_exempt():
    """wss/EventSource 永不 loadingFinished 也不挡 idle——
    普通请求完成后按原时序收敛。"""
    import threading, time
    page = _IdlePage()
    result = {}
    th = threading.Thread(
        target=lambda: result.setdefault(
            'v', _utils.wait_network_idle(page, idle_time=0.2,
                                          timeout=3)),
        daemon=True)
    th.start()
    time.sleep(0.05)
    cbs = page.driver.cbs
    # type=WebSocket（CDP 直标）
    cbs['Network.requestWillBeSent'](
        requestId='w1', type='WebSocket',
        request={'url': 'wss://zhihu.example/ws'})
    # wss:// scheme + 无 type（老内核事件面）
    cbs['Network.requestWillBeSent'](
        requestId='w2',
        request={'url': 'wss://search.jd.example/socket'})
    # Upgrade 头兜底（http(s) URL 的 WS 握手形态）
    cbs['Network.requestWillBeSent'](
        requestId='w3',
        request={'url': 'https://x.example/ws-upgrade',
                 'headers': {'Upgrade': 'WebSocket'}})
    # SSE：type=EventSource / Accept: text/event-stream
    cbs['Network.requestWillBeSent'](
        requestId='e1', type='EventSource',
        request={'url': 'https://jd.example/sse'})
    cbs['Network.requestWillBeSent'](
        requestId='e2',
        request={'url': 'https://y.example/stream',
                 'headers': {'Accept': 'text/event-stream'}})
    # 普通请求发起并完成——修复前 w*/e* 恒占 pending 必超时
    cbs['Network.requestWillBeSent'](
        requestId='r1', request={'url': 'https://a.com/api/data'})
    cbs['Network.loadingFinished'](requestId='r1')
    fin = cbs['Network.loadingFinished']
    th.join(3)
    assert result.get('v') is True
    # 长连接迟到的 finished 走 ignored 回收不炸
    fin(requestId='w1')


def test_b13_long_lived_helper_predicate():
    """_long_lived_request 四判据 + 反例（普通 XHR 不误豁免）。"""
    f = _utils._long_lived_request
    assert f({'url': 'wss://a/ws'}, None)
    assert f({'url': 'https://a/sse'}, 'EventSource')
    assert f({'url': 'https://a/ws',
              'headers': {'upgrade': 'websocket'}}, None)
    assert f({'url': 'https://a/sse',
              'headers': {'ACCEPT': 'text/event-stream'}}, 'XHR')
    assert not f({'url': 'https://a/api'}, 'XHR')
    assert not f({'url': 'https://a/api',
                  'headers': {'Accept': 'application/json'}}, 'Fetch')


# ── B14：adapter body JSON 参数化（record 标注 + 回放注入）──────

def test_b14_record_marks_body_params(isolated_home, monkeypatch):
    """POST JSON body adapter：body_params_keys 落盘 + data 面透出。"""
    entries = [{'kind': 'req', 'id': '1',
                'url': 'https://api.jd.example/tag',
                'method': 'POST', 'type': 'XHR', 'ts': 1.0,
                'postData': '{"keyword":"被子","page":1}'},
               {'kind': 'resp', 'id': '1', 'status': 200}]
    monkeypatch.setattr('dp_cli.netlog.read_entries',
                        lambda s: entries)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['api-record', 'jd-search-tag'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert d['body_params_keys'] == ['keyword', 'page']
    ep = d['adapter']['endpoint']
    assert ep['body_params_keys'] == ['keyword', 'page']
    saved = json.loads(
        (isolated_home / '.dp_cli' / 'adapters'
         / 'jd-search-tag.json').read_text(encoding='utf-8'))
    assert saved['endpoint']['body_params_keys'] == \
        ['keyword', 'page']


def test_b14_replay_injects_body_params(isolated_home, monkeypatch):
    """kw=v 回放注入 JSON 子键——page 保持 int 类型、URL 无槽位也
    不 INVALID_ARGS、命中键不进 extra 警告。"""
    adir = isolated_home / '.dp_cli' / 'adapters'
    adir.mkdir(parents=True)
    (adir / 'jd-search-tag.json').write_text(json.dumps({
        'name': 'jd-search-tag', 'recorded_at': 'x',
        'endpoint': {
            'method': 'POST',
            'url_template': 'https://api.jd.example/tag',
            'params_keys': [],
            'body': '{"keyword":"被子","page":1}',
            'body_params_keys': ['keyword', 'page']}}))
    captured = {}

    class _FP:
        def run_js(self, script, *a, **k):
            captured['script'] = script
            return {'status': 200, 'ok': True,
                    'url': 'https://api.jd.example/tag', 'body': '{}'}

    monkeypatch.setattr(net_mod, '_get_page', lambda s: _FP())
    from dp_cli.main import cli
    r = CliRunner().invoke(
        cli, ['api', 'jd-search-tag', 'keyword=枕头', 'page=3'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert 'warnings' not in d        # 两键均被消费，无 extra 警告
    import re as _re
    m = _re.match(r'return fetch\((\{.*?\}|".*?"),(\{.*\})\)\.then',
                  captured['script'])
    opts = json.loads(m.group(2))
    assert opts['method'] == 'POST'
    assert json.loads(opts['body']) == {'keyword': '枕头', 'page': 3}


def test_b14_replay_unmatched_key_still_warns(isolated_home,
                                              monkeypatch):
    """body/模板都未命中的 kw=v 仍走 extra 警告（不静默吞参）。"""
    adir = isolated_home / '.dp_cli' / 'adapters'
    adir.mkdir(parents=True)
    (adir / 'a1.json').write_text(json.dumps({
        'name': 'a1', 'endpoint': {
            'method': 'POST', 'url_template': 'https://x.com/t',
            'params_keys': [],
            'body': '{"keyword":"k"}',
            'body_params_keys': ['keyword']}}))

    class _FP:
        def __init__(self):
            self.scripts = []

        def run_js(self, script, *a, **k):
            self.scripts.append(script)
            return {'status': 200, 'ok': True, 'url': 'u', 'body': ''}

    page = _FP()
    monkeypatch.setattr(net_mod, '_get_page', lambda s: page)
    from dp_cli.main import cli
    r = CliRunner().invoke(cli, ['api', 'a1', 'keyword=v', 'zzz=1'])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    assert any('zzz' in w for w in d['warnings'])
    assert not any('keyword' in w for w in d['warnings'])
