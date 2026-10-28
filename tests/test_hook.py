# -*- coding:utf-8 -*-
"""dp hook-* JS hook 逆向工具命令的回归测试"""
import json

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
import dp_cli.commands.hook as hook_mod


runner = CliRunner()


class _HookPage:
    """记录 run_cdp/add_init_js/remove_init_js 的假页面。"""
    def __init__(self, eval_value='ok'):
        self.eval_value = eval_value
        self.cdp_exprs = []
        self.init_js = []
        self.removed = []
        self._n = 0

    def run_cdp(self, cmd, **kw):
        if cmd == 'Page.removeScriptToEvaluateOnNewDocument':
            self.removed.append(kw.get('identifier'))
        self.cdp_exprs.append(kw.get('expression', ''))
        v = self.eval_value
        if isinstance(v, dict) and 'exceptionDetails' in v:
            return v
        return {'result': {'value': v}}

    def add_init_js(self, script):
        self._n += 1
        self.init_js.append(script)
        return f'jsid-{self._n}'

    def remove_init_js(self, js_id):
        self.removed.append(js_id)


def _patch(monkeypatch, page, sess=None, saved=None):
    import dp_cli.commands._utils as _utils
    monkeypatch.setattr(hook_mod, '_get_page', lambda s: page)
    monkeypatch.setattr(hook_mod, 'load_session', lambda s: dict(sess or {}))
    monkeypatch.setattr(hook_mod, 'save_session',
                        lambda s, d: saved.update(d) if saved is not None
                        else None)
    # _replay_persist_scripts 走 _utils 命名空间
    monkeypatch.setattr(_utils, 'save_session',
                        lambda s, d: saved.update(d) if saved is not None
                        else None)


def _out(r):
    return json.loads(r.output)


# ── hook-set 各类型 JS 生成 ─────────────────────────────────────────────

@pytest.mark.unit
def test_hook_set_cookie_js(monkeypatch):
    """cookie hook：Document.prototype desc + name 正则过滤。"""
    page = _HookPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'cookie',
                            '--name', 'session|token'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['type'] == 'cookie' and d['id'].startswith('cookie-')
    js = page.cdp_exprs[-1]
    assert 'Document.prototype' in js and 'cookie' in js
    assert 'new RegExp("session|token")' in js
    assert '__dp_hook' in js and '_emit' in js


@pytest.mark.unit
def test_hook_set_func_js(monkeypatch):
    """func hook：点路径函数包装 + args 序列化。"""
    page = _HookPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'func',
                            '--path', 'CryptoJS.MD5'])
    assert r.exit_code == 0, r.output
    js = page.cdp_exprs[-1]
    assert '"CryptoJS.MD5"' in js
    assert '__dp_orig' in js
    assert 'arguments' in js


@pytest.mark.unit
def test_hook_set_prop_js(monkeypatch):
    """prop hook：原型链 desc 查找 + when 过滤。"""
    page = _HookPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'prop',
                            '--path', 'Navigator.prototype.webdriver',
                            '--when', 'get'])
    assert r.exit_code == 0, r.output
    js = page.cdp_exprs[-1]
    assert 'Object.getPrototypeOf' in js
    assert 'W_GET=true' in js and 'W_SET=false' in js


@pytest.mark.unit
def test_hook_set_xhr_js(monkeypatch):
    """xhr hook：XHR.open/send + fetch + URL/method 正则。"""
    page = _HookPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'xhr',
                            '--url', '/api/', '--method', 'POST'])
    assert r.exit_code == 0, r.output
    js = page.cdp_exprs[-1]
    assert 'XMLHttpRequest.prototype.open' in js
    assert 'XMLHttpRequest.prototype.send' in js
    assert 'window.fetch' in js
    assert 'new RegExp("/api/")' in js
    assert 'new RegExp("POST")' in js


# ── hook-set 参数校验 ───────────────────────────────────────────────────

@pytest.mark.unit
def test_hook_set_func_requires_path():
    """func/prop 缺 --path 或非法路径 → INVALID_ARGS，不 attach。"""
    r = runner.invoke(cli, ['hook-set', '--type', 'func'])
    assert r.exit_code == 2
    assert _out(r)['code'] == 'INVALID_ARGS'

    r = runner.invoke(cli, ['hook-set', '--type', 'prop',
                            '--path', 'a-b.c'])  # 非法字符
    assert r.exit_code == 2


@pytest.mark.unit
def test_hook_set_exists_id_conflict(monkeypatch):
    """页内已存在同 id hook → INVALID_ARGS。"""
    page = _HookPage(eval_value='exists')
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'cookie', '--id', 'my-h'])
    assert r.exit_code == 2
    assert 'my-h' in _out(r)['message']


@pytest.mark.unit
def test_hook_set_js_failure(monkeypatch):
    """JS 侧返回失败原因（no-desc/not-fn）→ HOOK_FAILED。"""
    page = _HookPage(eval_value='not-fn')
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'func',
                            '--path', 'foo.bar'])
    assert r.exit_code == 1
    assert _out(r)['code'] == 'HOOK_FAILED'
    assert 'not-fn' in _out(r)['message']


@pytest.mark.unit
def test_hook_set_persist_session(monkeypatch):
    """--persist：add_init_js + sess['hooks'] 写入。"""
    saved = {}
    page = _HookPage()
    _patch(monkeypatch, page, sess={}, saved=saved)
    r = runner.invoke(cli, ['hook-set', '--type', 'xhr',
                            '--url', '/sign/', '--persist'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['persist'] is True and d['js_id'] == 'jsid-1'
    assert len(page.init_js) == 1
    hid = d['id']
    cfg = saved['hooks'][hid]
    assert cfg['type'] == 'xhr'
    assert cfg['spec']['url'] == '/sign/'   # 原始 pattern，非 /re/ 字面量
    assert cfg['js_id'] == 'jsid-1'


# ── hook-list ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_hook_list_merges_persisted(monkeypatch):
    """当前页 hooks + 持久化记录合并；missing 标记。"""
    page = _HookPage(eval_value=[
        {'id': 'xhr-1', 'type': 'xhr', 'path': None, 'buf': 7}])
    _patch(monkeypatch, page,
           sess={'hooks': {'xhr-1': {'type': 'xhr', 'spec': {}, 'js_id': 'j1'},
                           'ck-9': {'type': 'cookie', 'spec': {}, 'js_id': 'j2'}}})
    r = runner.invoke(cli, ['hook-list'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    ids = {h['id']: h for h in d['hooks']}
    assert ids['xhr-1']['persist'] is True and ids['xhr-1']['buf'] == 7
    assert ids['ck-9']['missing'] is True


@pytest.mark.unit
def test_hook_set_func_nonwritable_guard(monkeypatch):
    """func hook 生成 JS 含赋值回读验证（non-writable 静默失败防护）。"""
    page = _HookPage()
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'func',
                            '--path', 'CryptoJS.MD5'])
    assert r.exit_code == 0, r.output
    assert 'obj[nm]!==wrapped' in page.cdp_exprs[-1]


@pytest.mark.unit
def test_hook_clear_js_cookie_delete_restore():
    """cookie hook 恢复优先 delete 自有 accessor（还原原型 desc 保真）。"""
    js = hook_mod._clear_js('c-1')
    assert 'delete document.cookie' in js
    assert 'spec.desc' in js  # delete 失败兜底回写原 desc


# ── hook-clear ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_hook_clear_requires_target():
    """无 --id/--all → INVALID_ARGS。"""
    r = runner.invoke(cli, ['hook-clear'])
    assert r.exit_code == 2


@pytest.mark.unit
def test_hook_clear_by_id(monkeypatch):
    """单 hook 移除：恢复 JS + init-js 移除 + session 清理。"""
    saved = {}
    page = _HookPage(eval_value='ok')
    _patch(monkeypatch, page,
           sess={'hooks': {'xhr-1': {'type': 'xhr', 'spec': {},
                                     'js_id': 'j1'}}},
           saved=saved)
    r = runner.invoke(cli, ['hook-clear', '--id', 'xhr-1'])
    assert r.exit_code == 0, r.output
    assert page.removed == ['j1']
    assert 'hooks' not in saved   # 清空后移除整个 hooks 键


@pytest.mark.unit
def test_hook_clear_not_found(monkeypatch):
    """页内不存在 → HOOK_NOT_FOUND exit4。"""
    page = _HookPage(eval_value='nf')
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-clear', '--id', 'ghost'])
    assert r.exit_code == 4
    assert _out(r)['code'] == 'HOOK_NOT_FOUND'


@pytest.mark.unit
def test_hook_clear_stale_persisted(monkeypatch):
    """页内 hook 已消失但 session 有 persist 记录：--id 清除记录+init-js，不报错。"""
    saved = {}
    page = _HookPage(eval_value='nf')   # 页内不存在
    _patch(monkeypatch, page,
           sess={'hooks': {'xhr-9': {'type': 'xhr', 'spec': {},
                                     'js_id': 'j9'}}},
           saved=saved)
    r = runner.invoke(cli, ['hook-clear', '--id', 'xhr-9'])
    assert r.exit_code == 0, r.output
    assert _out(r)['data']['cleared'] == ['xhr-9']
    assert page.removed == ['j9']
    assert 'hooks' not in saved


@pytest.mark.unit
def test_hook_clear_all(monkeypatch):
    """--all：枚举页内 hooks 逐个恢复。"""
    seq = iter([
        [{'id': 'a', 'type': 'xhr', 'buf': 3},
         {'id': 'b', 'type': 'cookie', 'buf': 3}],   # _list_js
        'ok', 'ok',                                  # _clear_js × 2
    ])
    page = _HookPage()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(hook_mod, '_get_page', lambda s: page)
    monkeypatch.setattr(hook_mod, 'load_session', lambda s: {})
    monkeypatch.setattr(hook_mod, 'save_session', lambda s, d: None)
    orig = page.run_cdp
    def cdp(cmd, **kw):
        return {'result': {'value': next(seq)}}
    monkeypatch.setattr(page, 'run_cdp', cdp)
    r = runner.invoke(cli, ['hook-clear', '--all'])
    monkeypatch.undo()
    assert r.exit_code == 0, r.output
    assert set(_out(r)['data']['cleared']) == {'a', 'b'}


# ── hook-dump ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_hook_dump_inline(monkeypatch):
    """默认内联输出 events。"""
    events = [{'seq': 0, 'hook': 'xhr-1', 'kind': 'xhr',
               'data': {'url': '/api/sign'}}]
    page = _HookPage(eval_value=events)
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-dump'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['count'] == 1
    assert d['events'][0]['data']['url'] == '/api/sign'


@pytest.mark.unit
def test_hook_dump_to_file(monkeypatch, tmp_path):
    """--output 落盘 JSON。"""
    events = [{'seq': 0, 'hook': 'h', 'kind': 'set', 'data': {}}]
    page = _HookPage(eval_value=events)
    _patch(monkeypatch, page)
    f = tmp_path / 'hooks.json'
    r = runner.invoke(cli, ['hook-dump', '--output', str(f)])
    assert r.exit_code == 0, r.output
    assert json.loads(f.read_text()) == events
    assert 'events' not in _out(r)['data']


@pytest.mark.unit
def test_hook_dump_clear(monkeypatch):
    """--clear 导出后清缓冲。"""
    page = _HookPage(eval_value=[{'seq': 0}])
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-dump', '--clear'])
    assert r.exit_code == 0, r.output
    assert any('buf=[]' in e for e in page.cdp_exprs)


# ── _get_page 重放 ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_hook_js_syntax_valid(tmp_path):
    """生成的注入 JS 经 node --check 语法验证（拼接串括号平衡回归）。"""
    import shutil, subprocess
    if not shutil.which('node'):
        pytest.skip('node 不可用')
    src = '\n'.join([
        hook_mod._hook_js('c-1', 'cookie', {'name': 'a|b'}),
        hook_mod._hook_js('f-1', 'func', {'path': 'CryptoJS.MD5'}),
        hook_mod._hook_js('p-1', 'prop',
                          {'path': 'Navigator.prototype.webdriver',
                           'when': 'get'}),
        hook_mod._hook_js('x-1', 'xhr', {'url': '/a/', 'method': 'POST'}),
        hook_mod._clear_js('c-1'),
        hook_mod._list_js(),
    ])
    f = tmp_path / 'gen.js'
    f.write_text(src)
    r = subprocess.run(['node', '--check', str(f)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.unit
def test_hook_replay_restores(monkeypatch):
    """sess['hooks'] 经 _get_page add_init_js 重放并刷新 js_id。"""
    from dp_cli.commands._utils import _get_page

    class _P(_HookPage):
        tab_ids = ['t1']

    page = _P()
    monkeypatch.setattr(hook_mod, 'add_init_js', page.add_init_js,
                        raising=False)
    # 直接调 _hook_js 验证重放脚本可生成
    js = hook_mod._hook_js('ck-1', 'cookie', {'name': 'sess'})
    assert 'Document.prototype' in js and 'sess' in js
    js = hook_mod._hook_js('x-1', 'xhr', {'url': '/a/', 'method': 'POST'})
    assert 'XMLHttpRequest' in js and 'fetch' in js


@pytest.mark.unit
def test_hook_clear_replays_survivors_immediately(monkeypatch):
    """hook-clear 移除整包后同命令内重注册同包剩余段。"""
    page = _HookPage()
    sess = {'hooks': {
        'c-1': {'type': 'cookie', 'spec': {'name': 'a'}, 'js_id': 'bnd-1'},
        'x-1': {'type': 'xhr', 'spec': {'url': '/a/'}, 'js_id': 'bnd-1'}}}
    saved = {}
    _patch(monkeypatch, page, sess=sess, saved=saved)
    r = runner.invoke(cli, ['hook-clear', '--id', 'c-1'])
    assert r.exit_code == 0, r.output
    assert page.removed == ['bnd-1']
    # 立即重注册 x-1 剩余段
    assert len(page.init_js) == 1
    assert 'XMLHttpRequest' in page.init_js[0]
    assert 'cookie' not in page.init_js[0]
    assert saved['hooks']['x-1']['js_id'] == 'jsid-1'


class _KeysAwarePage(_HookPage):
    """按表达式区分返回：keys 探针返回 JSON 键集，其余返回 ok。"""

    def __init__(self, keys):
        super().__init__('ok')
        self._keys = keys

    def run_cdp(self, cmd, **kw):
        expr = kw.get('expression', '')
        if 'Object.keys' in expr:
            return {'result': {'value': json.dumps(self._keys)}}
        return super().run_cdp(cmd, **kw)


@pytest.mark.unit
def test_hook_auto_id_fills_gap_no_collision(monkeypatch):
    """碰撞修复：cookie-1/cookie-3 存活（中间曾 clear）时，自动 id
    应选首个空闲 cookie-2 而非按总数+1 生成 cookie-4 或误撞 cookie-3。"""
    page = _KeysAwarePage(['cookie-1', 'cookie-3'])
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'cookie'])
    assert r.exit_code == 0, r.output
    d = _out(r)
    assert d['data']['id'] == 'cookie-2'


@pytest.mark.unit
def test_hook_auto_id_reserves_persisted_ids(monkeypatch):
    """会话持久化记录占号：页内 hooks 为空但会话登记了 cookie-1
    （持久化 hook 导航后未回放/另一页签）→ 新 hook 不得复用 cookie-1
    覆盖会话记录。"""
    page = _KeysAwarePage([])
    saved = {}
    _patch(monkeypatch, page,
           sess={'hooks': {'cookie-1': {'type': 'cookie', 'spec': {}}}},
           saved=saved)
    r = runner.invoke(cli, ['hook-set', '--type', 'cookie', '--persist'])
    assert r.exit_code == 0, r.output
    d = _out(r)
    assert d['data']['id'] == 'cookie-2'
    assert 'cookie-1' in saved['hooks'] and 'cookie-2' in saved['hooks']


@pytest.mark.unit
def test_hook_auto_id_still_unique_same_type_sequence(monkeypatch):
    """连续同类型 auto-id 递增生效：cookie-1 存活 → 新 hook = cookie-2。"""
    page = _KeysAwarePage(['cookie-1'])
    _patch(monkeypatch, page)
    r = runner.invoke(cli, ['hook-set', '--type', 'cookie'])
    assert r.exit_code == 0
    assert _out(r)['data']['id'] == 'cookie-2'


# ── P2：event_handlers 回调吞异常计数透出 ──────────────────

class _ErrsPage(_HookPage):
    """errs 探针表达式返回固定计数，其余走默认桩。"""

    def __init__(self, buf_val, errs_val):
        super().__init__(buf_val)
        self._errs = errs_val

    def run_cdp(self, cmd, **kw):
        expr = kw.get('expression', '')
        if 'errs' in expr:
            return {'result': {'value': self._errs}}
        return super().run_cdp(cmd, **kw)


@pytest.mark.unit
def test_hook_js_emit_catch_counts_errs():
    """P2：四类 hook 注入脚本的事件回调 except 分支不再裸吞——
    catch 内计 H.errs（页内错误计数通道）。"""
    for htype, spec in (('cookie', {}),
                        ('func', {'path': 'a.b'}),
                        ('prop', {'path': 'a.b'}),
                        ('xhr', {})):
        js = hook_mod._hook_js('h-1', htype, spec)
        assert 'H.errs=(H.errs||0)+1' in js, htype


@pytest.mark.unit
def test_hook_err_counts_and_debugs(monkeypatch):
    """P2：_hook_err 统一出口——每次调用 _hook_err_count+1 且
    走 debug（异常对象透传，不再是静默裸吞）。"""
    rec = []
    monkeypatch.setattr(hook_mod, 'debug',
                        lambda m, e=None: rec.append((m, e)))
    monkeypatch.setattr(hook_mod, '_hook_err_count', 0)
    hook_mod._hook_err('auto-id 探测失败', RuntimeError('x'))
    hook_mod._hook_err('缓冲清空失败')
    assert hook_mod._hook_err_count == 2
    assert rec[0][0] == 'auto-id 探测失败'
    assert isinstance(rec[0][1], RuntimeError)


@pytest.mark.unit
def test_hook_dump_exposes_hook_errors(monkeypatch):
    """P2：hook-dump 响应 data.hook_errors = 页内 H.errs +
    Python _hook_err_count——两侧吞异常计数透出。"""
    page = _ErrsPage(buf_val=[{'seq': 0, 'kind': 'xhr'}], errs_val=2)
    _patch(monkeypatch, page)
    monkeypatch.setattr(hook_mod, '_hook_err_count', 3)
    r = runner.invoke(cli, ['hook-dump'])
    assert r.exit_code == 0, r.output
    d = _out(r)['data']
    assert d['hook_errors'] == 5
    assert d['count'] == 1


@pytest.mark.unit
def test_hook_list_exposes_hook_errors(monkeypatch):
    """P2：hook-list 响应同样带 hook_errors；页内 errs 读不出
    （非数值/异常）时静默归零不阻塞主响应。"""
    page = _ErrsPage(buf_val=[
        {'id': 'xhr-1', 'type': 'xhr', 'path': None, 'buf': 1}],
        errs_val=4)
    _patch(monkeypatch, page)
    monkeypatch.setattr(hook_mod, '_hook_err_count', 1)
    r = runner.invoke(cli, ['hook-list'])
    assert r.exit_code == 0, r.output
    assert _out(r)['data']['hook_errors'] == 5

    # errs 返回非数值（老版本命名空间无 errs 字段外的异常形态）
    page2 = _ErrsPage(buf_val=[], errs_val='weird')
    _patch(monkeypatch, page2)
    r = runner.invoke(cli, ['hook-list'])
    assert r.exit_code == 0, r.output
    assert _out(r)['data']['hook_errors'] == 1   # 只计 Python 侧
