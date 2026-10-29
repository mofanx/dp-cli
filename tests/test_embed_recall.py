"""embedding 召回层——两级架构（召回→细排）+降级路径。"""
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
