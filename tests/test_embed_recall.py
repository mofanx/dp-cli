"""embedding 召回层——两级架构（召回→细排）+降级路径。"""
import sys
import types

from dp_cli import agent


class _FakeEmbed:
    """按名字长度打分——goal 'hit_X' 与含 X 的候选最近。"""
    def encode(self, texts, normalize_embeddings=True):
        import numpy as np
        # 简向量：命中 'hit' 的候选向量 [1,0]，其他 [0,1]
        return np.array([[1.0, 0.0] if 'hit' in t else [0.0, 1.0]
                         for t in texts])


def _opts(n):
    return [{'ref': f'ref:{i}', 'role': 'button', 'name': f'b{i}'}
            for i in range(n)]


def test_recall_below_threshold_noop(monkeypatch):
    """候选 ≤30 不走召回——opts/texts 原样返回。"""
    opts, texts = _opts(10), ['a'] * 10
    o2, t2 = agent._recall_topk('x', texts, opts)
    assert o2 is opts and t2 is texts


def test_recall_over_threshold_topk(monkeypatch):
    """候选 >30 时 embedding 召回 top-8——embedding 不可用降级全量。"""
    monkeypatch.setattr(agent, '_EMBED', _FakeEmbed())
    opts = _opts(40)
    opts[7]['name'] = 'hit_target'
    texts = [f"{o['role']} {o['name']}" for o in opts]
    o2, t2 = agent._recall_topk('hit', texts, opts, topk=8)
    assert len(o2) == 8 and len(t2) == 8
    assert any(o['name'] == 'hit_target' for o in o2)


def test_recall_fallback_full(monkeypatch):
    """embedding 模型不可用（_EMBED=None）→ 降级返回全量候选。"""
    monkeypatch.setattr(agent, '_EMBED', None)
    monkeypatch.setattr(agent, '_EMBED_MISS', True)
    opts, texts = _opts(40), ['a'] * 40
    o2, t2 = agent._recall_topk('x', texts, opts)
    assert len(o2) == 40 and len(t2) == 40


def test_recall_limits_predict_pairs(monkeypatch):
    """e2e：40 候选+召回→predict 只见 8 对（细排输入收窄实证）。"""
    monkeypatch.setattr(agent, '_EMBED', _FakeEmbed())
    seen = []

    class _M:
        def predict(self, pairs):
            seen.append(len(pairs))
            return [0.9] + [0.1] * (len(pairs) - 1)

    monkeypatch.setattr(agent, '_reranker_model', lambda: ('st', _M()))
    opts = _opts(40)
    opts[3]['name'] = 'hit_x'
    st = {'options': opts}
    r = agent._engine_reranker(st, 'hit_x')
    assert seen == [8]
    assert r['target']


def _fake_st_module(calls, fail=None):
    """假 sentence_transformers 模块——计 SentenceTransformer 实例化次数。"""
    mod = types.ModuleType('sentence_transformers')

    class _ST:
        def __init__(self, *a, **k):
            calls.append(1)
            if fail:
                raise fail
            self._inner = _FakeEmbed()

        def encode(self, texts, normalize_embeddings=True):
            return self._inner.encode(texts)

    mod.SentenceTransformer = _ST
    return mod


def test_embed_model_singleton_loads_once(monkeypatch):
    """懒加载单例：同进程两次 _embed_model() 只实例化一次
    （serve/daemon/REPL 内复用，CLI 单发的 ~4s 是进程生命周期成本）。"""
    calls = []
    monkeypatch.setitem(sys.modules, 'sentence_transformers',
                        _fake_st_module(calls))
    monkeypatch.setattr(agent, '_EMBED', None)
    monkeypatch.setattr(agent, '_EMBED_MISS', False)
    m1 = agent._embed_model()
    m2 = agent._embed_model()
    assert m1 is m2 and len(calls) == 1


def test_dpx_no_embed_disables_recall(monkeypatch):
    """DPX_NO_EMBED=1 显式禁用——不尝试加载模型，召回层整段跳过。"""
    calls = []
    monkeypatch.setitem(sys.modules, 'sentence_transformers',
                        _fake_st_module(calls))
    monkeypatch.setattr(agent, '_EMBED', None)
    monkeypatch.setattr(agent, '_EMBED_MISS', False)
    monkeypatch.setenv('DPX_NO_EMBED', '1')
    assert agent._embed_model() is None
    assert calls == []                                # 连加载都没尝试
    opts, texts = _opts(60), ['a'] * 60
    o2, t2 = agent._recall_topk('x', texts, opts)
    assert len(o2) == 60 and len(t2) == 60            # 降级全量
    assert calls == []


def test_embed_load_failure_warns_stderr(monkeypatch, capsys):
    """embed 加载失败 → stderr 真警告一次（非 debug 静默），
    并置 _EMBED_MISS 不再重试。"""
    calls = []
    monkeypatch.setitem(sys.modules, 'sentence_transformers',
                        _fake_st_module(calls, fail=RuntimeError('boom')))
    monkeypatch.setattr(agent, '_EMBED', None)
    monkeypatch.setattr(agent, '_EMBED_MISS', False)
    monkeypatch.delenv('DPX_NO_EMBED', raising=False)
    assert agent._embed_model() is None
    assert agent._EMBED_MISS is True
    err = capsys.readouterr().err
    assert 'embedding 召回模型加载失败' in err
    agent._embed_model()                              # 不重试不重复告警
    assert capsys.readouterr().err == ''
    assert len(calls) == 1


def test_recall_trigger_uses_raw_total(monkeypatch):
    """触发判定看截断前原始数：20 opts（top-k 截断后）+total=55 →
    召回仍触发；total≤阈值时不触发——修"opts≤top_k<阈值永不触发"。"""
    monkeypatch.setattr(agent, '_EMBED', _FakeEmbed())
    opts = _opts(20)
    opts[7]['name'] = 'hit_target'
    texts = [f"{o['role']} {o['name']}" for o in opts]
    o2, t2 = agent._recall_topk('hit', texts, opts, topk=8, total=55)
    assert len(o2) == 8 and len(t2) == 8              # 截断后 20 → 召回 8
    assert any(o['name'] == 'hit_target' for o in o2)
    o3, t3 = agent._recall_topk('hit', texts, opts, topk=8, total=25)
    assert len(o3) == 20 and len(t3) == 20            # 原始数≤阈值不触发


def test_page_state_recall_pool_expands(monkeypatch):
    """_page_state recall_pool=True：原始 refs>阈值时无视 top_k 截断，
    全量进召回池（options_total 记原始数）；False 保持 top_k 截断。"""
    import dp_cli.snapshot.a11y as a11y

    def _tree(n):
        return {'tree': {'role': 'WebArea', 'children': [
            {'role': 'button', 'name': f'b{i}', 'value': '',
             'backendNodeId': i, 'locator': f'css:#b{i}',
             'children': []} for i in range(55)]}}

    class _P:
        url = 'https://x/'
        title = 't'

    monkeypatch.setattr(a11y, 'take_a11y_snapshot',
                        lambda p, max_depth=12: _tree(55))
    monkeypatch.setattr(agent, 'save_refs', lambda s, u, r: None)
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda s, d: None)
    st = agent._page_state(_P(), 's', max_opts=20, recall_pool=False)
    assert len(st['options']) == 20 and st['truncated'] is True
    assert st['options_total'] == 55
    st = agent._page_state(_P(), 's', max_opts=20, recall_pool=True)
    assert len(st['options']) == 55 and st['truncated'] is False
    assert st['options_total'] == 55
