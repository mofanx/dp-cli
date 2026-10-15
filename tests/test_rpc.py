# -*- coding:utf-8 -*-
"""dp rpc-* JSRPC 桥命令的回归测试"""
import json

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
import dp_cli.commands.rpc as rpc_mod


runner = CliRunner()


class _RpcPage:
    """记录 run_js/add_init_js/remove_init_js/Runtime.evaluate 的假页面。"""
    def __init__(self, eval_value=None):
        self.eval_value = eval_value
        self.js_calls = []          # run_js 的 expression
        self.init_js = []           # add_init_js 的 script
        self.removed = []           # remove_init_js 的 js_id
        self._n = 0

    def run_js(self, js, **kw):
        self.js_calls.append(js)
        return True

    def add_init_js(self, script):
        self._n += 1
        self.init_js.append(script)
        return f'jsid-{self._n}'

    def remove_init_js(self, js_id):
        self.removed.append(js_id)

    def run_cdp(self, cmd, **kw):
        # _cdp_eval 的返回形态
        v = self.eval_value
        return {'result': {'value': v}} if not isinstance(v, dict) \
            or 'exceptionDetails' not in v else v


def _patch(monkeypatch, page, sess=None, saved=None):
    import dp_cli.commands._utils as _utils
    monkeypatch.setattr(rpc_mod, '_get_page', lambda s: page)
    monkeypatch.setattr(rpc_mod, 'load_session', lambda s: dict(sess or {}))
    monkeypatch.setattr(rpc_mod, 'save_session',
                        lambda s, d: saved.update(d) if saved is not None
                        else None)
    # _replay_persist_scripts 走 _utils 命名空间
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.update(d) if saved is not None
                        else None)


# ── rpc-register ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_rpc_register_current_page(monkeypatch):
    """非持久化：仅当前页 run_js 注册，__dp_rpc 命名空间挂载。"""
    page = _RpcPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['rpc-register', 'add', '(a,b)=>a+b'])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)['data']
    assert data['persist'] is False and data['js_id'] is None
    assert len(page.js_calls) == 1
    assert 'window.__dp_rpc["add"]' in page.js_calls[0]
    assert '((a,b)=>a+b)' in page.js_calls[0]
    assert not page.init_js


@pytest.mark.unit
def test_rpc_register_persist_writes_session_and_replayable(monkeypatch):
    """--persist：add_init_js + 当前页立即可用 + sess['rpc'] 持久化。"""
    page = _RpcPage()
    saved = {}
    _patch(monkeypatch, page, sess={}, saved=saved)
    r = runner.invoke(cli, ['rpc-register', 'sign',
                            'function(p){return md5(p)}', '--persist'])
    assert r.exit_code == 0
    assert len(page.init_js) == 1            # init-js 持久化
    assert len(page.js_calls) == 1           # 当前页同时注册
    assert page.init_js[0] == page.js_calls[0]
    spec = saved['rpc']['sign']
    assert spec['body'] == 'function(p){return md5(p)}'
    assert spec['js_id'] == 'jsid-1'


@pytest.mark.unit
def test_rpc_register_bare_statement_wrapped():
    """裸语句包为 function(...__args){...}。"""
    body = rpc_mod._wrap_body('return __args[0]*2')
    assert body.startswith('(function(...__args){')
    assert 'return __args[0]*2' in body


@pytest.mark.unit
def test_rpc_register_js_error(monkeypatch):
    """注入抛错 → RPC_FAILED exit1。"""
    class _Bad(_RpcPage):
        def run_js(self, js, **kw):
            raise RuntimeError('syntax error')
    _patch(monkeypatch, _Bad())
    r = runner.invoke(cli, ['rpc-register', 'f', '((('])
    assert r.exit_code == 1
    assert json.loads(r.output)['code'] == 'RPC_FAILED'


# ── rpc-call ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_rpc_call_array_args_expand(monkeypatch):
    """JSON 数组 → 位置参数展开 __f(...[1,2])，返回值结构化透传。"""
    page = _RpcPage(eval_value=3)
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['rpc-call', 'add', '[1,2]'])
    assert r.exit_code == 0
    data = json.loads(r.output)['data']
    assert data == {'name': 'add', 'args': [1, 2], 'result': 3}


@pytest.mark.unit
def test_rpc_call_nonarray_single_arg(monkeypatch):
    """非数组 JSON → 单参数包装。"""
    captured = {}

    class _P(_RpcPage):
        def run_cdp(self, cmd, **kw):
            captured['expr'] = kw['expression']
            return {'result': {'value': 'ok'}}
    _patch(monkeypatch, _P())
    r = runner.invoke(cli, ['rpc-call', 'f', '{"a":1}'])
    assert r.exit_code == 0
    assert '__f(...[{"a": 1}])' in captured['expr'] or \
           '__f(...[{"a":1}])' in captured['expr']
    assert json.loads(r.output)['data']['args'] == [{'a': 1}]


@pytest.mark.unit
def test_rpc_call_native_fallback_path(monkeypatch):
    """合法 JS 点路径：注册表优先、原生函数回落（__dp_rpc[name]||JSON.stringify）。"""
    captured = {}

    class _P(_RpcPage):
        def run_cdp(self, cmd, **kw):
            captured['expr'] = kw['expression']
            return {'result': {'value': '{"a":1}'}}
    _patch(monkeypatch, _P())
    r = runner.invoke(cli, ['rpc-call', 'JSON.stringify', '[{"a":1}]'])
    assert r.exit_code == 0
    assert '||JSON.stringify' in captured['expr']


@pytest.mark.unit
def test_rpc_call_invalid_path_no_fallback(monkeypatch):
    """非合法路径名只查 __dp_rpc，不拼接原生路径（防注入）。"""
    captured = {}

    class _P(_RpcPage):
        def run_cdp(self, cmd, **kw):
            captured['expr'] = kw['expression']
            return {'result': {'value': None}}
    _patch(monkeypatch, _P())
    runner.invoke(cli, ['rpc-call', 'weird name;alert(1)', '[]'])
    assert 'alert' not in captured['expr'] or '__dp_rpc' in captured['expr']
    assert '||weird' not in captured['expr']


@pytest.mark.unit
def test_rpc_call_not_found_exit4(monkeypatch):
    """__dp_rpc_nf 哨兵 → RPC_NOT_FOUND exit4。"""
    page = _RpcPage(eval_value={'__dp_rpc_nf': 1})
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['rpc-call', 'ghost'])
    assert r.exit_code == 4
    assert json.loads(r.output)['code'] == 'RPC_NOT_FOUND'


@pytest.mark.unit
def test_rpc_call_invalid_args_json():
    """--args 非法 JSON → INVALID_ARGS exit2。"""
    r = runner.invoke(cli, ['rpc-call', 'f', '[bad'])
    assert r.exit_code == 2
    assert json.loads(r.output)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_rpc_call_js_exception_exit1(monkeypatch):
    """JS 执行异常（exceptionDetails）→ RPC_CALL_FAILED exit1。"""
    page = _RpcPage(eval_value={'exceptionDetails': {'text': 'boom'}})
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['rpc-call', 'f', '[]'])
    assert r.exit_code == 1
    assert json.loads(r.output)['code'] == 'RPC_CALL_FAILED'


# ── rpc-list / rpc-unregister ────────────────────────────────────────────

@pytest.mark.unit
def test_rpc_list_merges_persisted(monkeypatch):
    """当前页注册 + 持久化合并；已持久化但页内缺失标 missing。"""
    page = _RpcPage(eval_value=['add', 'sign'])
    sess = {'rpc': {'sign': {'body': 'x', 'js_id': 'j1'},
                    'lost': {'body': 'y', 'js_id': 'j2'}}}
    _patch(monkeypatch, page, sess=sess)
    r = runner.invoke(cli, ['rpc-list'])
    assert r.exit_code == 0
    items = {i['name']: i for i in json.loads(r.output)['data']['rpc']}
    assert items['add']['persist'] is False
    assert items['sign']['persist'] is True
    assert items['lost']['missing'] is True


@pytest.mark.unit
def test_rpc_unregister_removes_initjs_and_session(monkeypatch):
    """delete 当前页 + CDP remove init-js + sess['rpc'] pop。"""
    captured = {}

    class _P(_RpcPage):
        def run_cdp(self, cmd, **kw):
            if cmd == 'Runtime.evaluate':
                captured['expr'] = kw.get('expression')
            else:
                captured.setdefault('rm', []).append(
                    kw.get('identifier'))
            return {'result': {'value': True}}
    saved = {}
    sess = {'rpc': {'sign': {'body': 'x', 'js_id': 'j9'},
                    'keep': {'body': 'y', 'js_id': 'j8'}}}
    _patch(monkeypatch, _P(), sess=sess, saved=saved)
    r = runner.invoke(cli, ['rpc-unregister', 'sign'])
    assert r.exit_code == 0
    assert 'delete' in captured['expr'] and '"sign"' in captured['expr']
    assert captured['rm'] == ['j9']
    assert saved['rpc'] == {'keep': {'body': 'y', 'js_id': 'j8'}}


@pytest.mark.unit
def test_rpc_unregister_no_persist_record(monkeypatch):
    """无持久化记录：仅当前页 delete，不写 session。"""
    saved = {}
    _patch(monkeypatch, _RpcPage(), sess={}, saved=saved)
    r = runner.invoke(cli, ['rpc-unregister', 'gone'])
    assert r.exit_code == 0
    assert 'rpc' not in saved


# ── _get_page 重放 ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_page_replays_rpc(monkeypatch):
    """sess['rpc'] 持久化 → _get_page 逐条 add_init_js 重放并更新 js_id。"""
    from dp_cli.commands import _utils

    page = _RpcPage()
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session',
                        lambda s: {'rpc': {
                            'a': {'body': '()=>1', 'js_id': 'old-a'},
                            'b': {'body': '()=>2', 'js_id': 'old-b'}}})
    monkeypatch.setattr(_utils, 'save_session', lambda s, d: None)
    sess = {'rpc': {'a': {'body': '()=>1', 'js_id': 'old-a'},
                    'b': {'body': '()=>2', 'js_id': 'old-b'}}}
    monkeypatch.setattr(_utils, 'load_session', lambda s: dict(sess))
    target = _utils._get_page('t')
    # 打包：N 段合并为一次 add_init_js（省 N-1 个 CDP RTT）
    assert len(page.init_js) == 1
    bundle = page.init_js[0]
    assert 'window.__dp_rpc["a"]' in bundle
    assert 'window.__dp_rpc["b"]' in bundle
    # 每段 try/catch 包裹，保持原独立注册的运行时隔离语义
    assert bundle.count('try{') >= 2 and 'catch(e){}' in bundle
    # js_id 共享 bundle id（unregister 移除整包后重放补回剩余段）
    assert sess['rpc']['a']['js_id'] == 'jsid-1'
    assert sess['rpc']['b']['js_id'] == 'jsid-1'


@pytest.mark.unit
def test_get_page_replay_bundle_isolates_bad_spec(monkeypatch):
    """单个 spec 生成失败不拖垮其余段——rpc 坏条目被跳过，
    hooks 好条目仍进同一个 bundle。"""
    from dp_cli.commands import _utils

    page = _RpcPage()
    sess = {
        'rpc': {
            'good': {'body': '()=>1'},
            # 坏 spec：_register_js 对任何 body 都不抛——
            # 用 hooks 侧的坏 path 触发生成期异常来验证隔离
        },
        'hooks': {
            'bad': {'type': 'func', 'spec': {}},   # 缺 path → KeyError
            'ok-1': {'type': 'xhr', 'spec': {'url': '/a/'}},
        },
    }
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(_utils, 'save_session', lambda s, d: None)
    _utils._get_page('t')
    assert len(page.init_js) == 1
    bundle = page.init_js[0]
    assert 'window.__dp_rpc["good"]' in bundle       # rpc 段在
    assert 'XMLHttpRequest.prototype' in bundle       # hooks 好段在
    assert 'try{' in bundle                           # 分段包裹
    # 两个合法 spec 共享同一 bundle id
    assert sess['rpc']['good']['js_id'] == 'jsid-1'
    assert sess['hooks']['ok-1']['js_id'] == 'jsid-1'


# ── _remove_init_js：跨进程清除（绕 _init_jss 门控）─────────────────────────

@pytest.mark.unit
def test_remove_init_js_bypasses_gate(monkeypatch):
    """_init_jss 为空（跨进程）仍直接调 CDP——不静默失效。"""
    from dp_cli.commands import _utils
    page = _RpcPage()
    page._init_jss = []            # 跨进程新对象，空注册表
    calls = []
    orig = page.run_cdp
    page.run_cdp = lambda c, **kw: (calls.append((c, kw)), orig(c, **kw))[1]
    assert _utils._remove_init_js(page, 'jsid-1') is True
    assert calls == [('Page.removeScriptToEvaluateOnNewDocument',
                      {'identifier': 'jsid-1'})]


@pytest.mark.unit
def test_remove_init_js_syncs_init_jss():
    """本进程注册过的 id 移除后同步清 _init_jss。"""
    from dp_cli.commands import _utils
    page = _RpcPage()
    page._init_jss = ['jsid-1', 'jsid-2']
    assert _utils._remove_init_js(page, 'jsid-1') is True
    assert page._init_jss == ['jsid-2']


@pytest.mark.unit
def test_remove_init_js_stale_id_best_effort():
    """identifier 失效（session 已死）→ 吞异常返回 False，不抛。"""
    from dp_cli.commands import _utils
    page = _RpcPage()
    page._init_jss = []
    def boom(cmd, **kw):
        raise RuntimeError('No script with given id')
    page.run_cdp = boom
    assert _utils._remove_init_js(page, 'jsid-dead') is False
    assert _utils._remove_init_js(page, None) is False   # 空 id 短路
    assert _utils._remove_init_js(page, '') is False


@pytest.mark.unit
def test_unregister_uses_remove_helper(monkeypatch):
    """rpc-unregister 走 _remove_init_js：CDP 直达而非 DP remove_init_js。"""
    page = _RpcPage()
    cdp_calls = []
    orig = page.run_cdp
    page.run_cdp = lambda c, **kw: (cdp_calls.append(c),
                                    orig(c, **kw))[1]
    sess = {'rpc': {'add': {'body': '(a)=>a', 'js_id': 'jsid-9'}}}
    saved = {}
    _patch(monkeypatch, page, sess=sess, saved=saved)
    r = runner.invoke(cli, ['rpc-unregister', 'add'])
    assert r.exit_code == 0, r.output
    # CDP removeScriptToEvaluateOnNewDocument 直达（不经 _init_jss 门控）
    assert 'Page.removeScriptToEvaluateOnNewDocument' in cdp_calls


@pytest.mark.unit
def test_bundle_js_id_persisted_for_cross_process(monkeypatch):
    """_get_page 重放后 spec['js_id']=bundle_id 落盘——跨进程 unregister
    拿到当前生效 bundle id 才能移除整包。"""
    from dp_cli.commands import _utils
    page = _RpcPage()
    sess = {'rpc': {'f': {'body': '()=>1', 'js_id': 'jsid-old'}}}
    saved = {}
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session', lambda s: dict(sess))
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.update(d))
    _utils._get_page('t')
    # save_session 被调且 spec['js_id'] 持久化为 bundle_id
    assert saved['rpc']['f']['js_id'] == 'jsid-1'


@pytest.mark.unit
def test_unregister_bundle_removes_whole_then_replay_restores(monkeypatch):
    """bundle 移除整包语义：unregister 移除 bundle_id → 下次 _get_page
    重放剩余段补回。"""
    from dp_cli.commands import _utils
    page = _RpcPage()
    # sess 中 A/B 共享 bundle_id（重放后落盘态）
    sess = {'rpc': {'a': {'body': '()=>1', 'js_id': 'bnd-1'},
                    'b': {'body': '()=>2', 'js_id': 'bnd-1'}}}
    calls = []
    orig = page.run_cdp
    page.run_cdp = lambda c, **kw: (calls.append(c), orig(c, **kw))[1]
    # 模拟跨进程 unregister a：移除 bundle_id 整包
    assert _utils._remove_init_js(page, sess['rpc']['a']['js_id']) is True
    assert calls[-1] == 'Page.removeScriptToEvaluateOnNewDocument'
    # B 的 spec 仍在 sess → 重放补回（bundle 重注册）
    monkeypatch.setattr(_utils, 'get_browser', lambda s: page)
    monkeypatch.setattr(_utils, 'load_session',
                        lambda s: {'rpc': {'b': sess['rpc']['b']}})
    monkeypatch.setattr(_utils, 'save_session', lambda s, d: None)
    _utils._get_page('t')
    # 新 bundle 只含 b 段
    assert len(page.init_js) == 1
    assert 'window.__dp_rpc["b"]' in page.init_js[0]
    assert '"a"' not in page.init_js[0]


@pytest.mark.unit
def test_unregister_replays_survivors_immediately(monkeypatch):
    """unregister 移除整包后同命令内重注册同包剩余段——不等下次
    _get_page，消除间隔内导航丢失 persist init-js 的窗口。"""
    page = _RpcPage()
    sess = {'rpc': {'a': {'body': '()=>1', 'js_id': 'bnd-1'},
                    'b': {'body': '()=>2', 'js_id': 'bnd-1'}}}
    saved = {}
    _patch(monkeypatch, page, sess=sess, saved=saved)
    r = runner.invoke(cli, ['rpc-unregister', 'a'])
    assert r.exit_code == 0, r.output
    # 移除整包后立即重注册：新 bundle 只含 b 段
    assert len(page.init_js) == 1
    assert 'window.__dp_rpc["b"]' in page.init_js[0]
    assert '"a"' not in page.init_js[0]
    # 新 bundle_id 已回写
    assert saved['rpc']['b']['js_id'] == 'jsid-1'


@pytest.mark.unit
def test_unregister_no_replay_for_foreign_js_id(monkeypatch):
    """only_ids 限定：其它 js_id 的 spec 脚本仍存活，不重复注册。"""
    page = _RpcPage()
    sess = {'rpc': {'a': {'body': '()=>1', 'js_id': 'bnd-1'},
                    'b': {'body': '()=>2', 'js_id': 'j-sep'}}}
    saved = {}
    _patch(monkeypatch, page, sess=sess, saved=saved)
    r = runner.invoke(cli, ['rpc-unregister', 'a'])
    assert r.exit_code == 0, r.output
    # b 的 js_id 不在被移除包内 → 不重复注册
    assert page.init_js == []
    assert saved['rpc']['b']['js_id'] == 'j-sep'
