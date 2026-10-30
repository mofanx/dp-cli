# -*- coding:utf-8 -*-
"""decide 决策缓存——(session,url,epoch,goal,engine) LRU。

reranker CE 打分是 (goal,candidates) 纯函数——同 key 二次 decide
免重编码；epoch 变/session 变天然失效；DPX_NO_CACHE=1 与
--no-cache 双开关；cached:true 可观测。
"""
import pytest
from click.testing import CliRunner

import dp_cli.agent as agent

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_cache():
    """模块级 _DECIDE_CACHE 跨用例复位——防缓存命中泄漏。"""
    agent._DECIDE_CACHE.clear()
    yield
    agent._DECIDE_CACHE.clear()


def _state(epoch='e1', url='https://x/', n=3):
    return {'state': {'url': url, 'title': 't', 'sig': epoch},
            'options': [{'ref': i + 1, 'role': 'button',
                         'name': f'b{i}'} for i in range(n)],
            'epoch': epoch, 'truncated': False, 'options_total': n}


def _resp():
    return {'target': 1,
            'probs': {'target': {'1': 0.9, '2': 0.08, '3': 0.02}},
            'confidence': 0.9, 'backend': 'st',
            'done': False, 'stuck': False}


def _engine_counter(monkeypatch):
    """_engine_reranker 计数 mock——命中即不重打分。"""
    calls = []

    def _fake(state, goal):
        calls.append(goal)
        return _resp()

    monkeypatch.setattr(agent, '_engine_reranker', _fake)
    return calls


def test_cache_hit_skips_engine(monkeypatch):
    """同 key 二次调用：引擎只跑一次，命中标记 _cache_hit。"""
    calls = _engine_counter(monkeypatch)
    r1, _ = agent._ask_questions(_state(), '点Go', 'reranker', 's1',
                                 None, None)
    r2, _ = agent._ask_questions(_state(), '点Go', 'reranker', 's1',
                                 None, None)
    assert calls == ['点Go']                       # 引擎只调一次
    assert '_cache_hit' not in r1
    assert r2['_cache_hit'] is True
    # action 映射已入缓存——命中路径不重复 _goal_action 也完整
    assert r2['action'] == 'click' and r2['target'] == 1
    # 命中副本是深拷贝——改返回不污染缓存
    r2['probs']['target']['1'] = 0.0
    r3, _ = agent._ask_questions(_state(), '点Go', 'reranker', 's1',
                                 None, None)
    assert r3['probs']['target']['1'] == 0.9


def test_epoch_change_invalidates(monkeypatch):
    """refs_epoch 变即 key 变——页面变天然失效，引擎重跑。"""
    calls = _engine_counter(monkeypatch)
    agent._ask_questions(_state(epoch='e1'), 'g', 'reranker', 's1',
                         None, None)
    r, _ = agent._ask_questions(_state(epoch='e2'), 'g', 'reranker',
                                's1', None, None)
    assert calls == ['g', 'g']                     # 两次都真打分
    assert '_cache_hit' not in r


def test_session_isolation(monkeypatch):
    """session 名进 key——同页面同 goal 跨 session 不共享缓存。"""
    calls = _engine_counter(monkeypatch)
    agent._ask_questions(_state(), 'g', 'reranker', 's1', None, None)
    r, _ = agent._ask_questions(_state(), 'g', 'reranker', 's2',
                                None, None)
    assert calls == ['g', 'g']
    assert '_cache_hit' not in r


def test_lru_evicts_oldest(monkeypatch):
    """LRU 上限：超 cap 驱逐最旧——最老 key 再调用重新打分。"""
    calls = _engine_counter(monkeypatch)
    monkeypatch.setattr(agent, '_DECIDE_CACHE_MAX', 2)
    st = _state()
    agent._ask_questions(st, 'g1', 'reranker', 's', None, None)
    agent._ask_questions(st, 'g2', 'reranker', 's', None, None)
    agent._ask_questions(st, 'g3', 'reranker', 's', None, None)
    assert len(agent._DECIDE_CACHE) == 2
    r, _ = agent._ask_questions(st, 'g1', 'reranker', 's', None, None)
    assert '_cache_hit' not in r                   # g1 已被逐出
    assert len(calls) == 4
    # g1 重写入把 g2 挤成最旧——g3 仍命中，g2 已被逐
    r3, _ = agent._ask_questions(st, 'g3', 'reranker', 's', None, None)
    assert r3['_cache_hit'] is True
    assert len(calls) == 4
    r2, _ = agent._ask_questions(st, 'g2', 'reranker', 's', None, None)
    assert '_cache_hit' not in r2
    assert len(calls) == 5


def test_no_cache_env_disables(monkeypatch):
    """DPX_NO_CACHE=1 整层关闭——同 key 也重复打分。"""
    calls = _engine_counter(monkeypatch)
    monkeypatch.setenv('DPX_NO_CACHE', '1')
    agent._ask_questions(_state(), 'g', 'reranker', 's', None, None)
    r, _ = agent._ask_questions(_state(), 'g', 'reranker', 's',
                                None, None)
    assert calls == ['g', 'g']
    assert '_cache_hit' not in r
    assert not agent._DECIDE_CACHE                  # 连写都没写


def test_no_cache_flag_bypasses(monkeypatch):
    """no_cache=True（decide --no-cache）：读/写双绕。"""
    calls = _engine_counter(monkeypatch)
    agent._ask_questions(_state(), 'g', 'reranker', 's', None, None,
                         no_cache=True)
    r, _ = agent._ask_questions(_state(), 'g', 'reranker', 's',
                                None, None, no_cache=True)
    assert calls == ['g', 'g']
    assert '_cache_hit' not in r
    assert not agent._DECIDE_CACHE


def test_error_resp_not_cached(monkeypatch):
    """_error 答复不入缓存——模型加载失败等瞬态不固化。"""
    calls = []
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: calls.append(g)
                        or {'_error': 'boom'})
    agent._ask_questions(_state(), 'g', 'reranker', 's', None, None)
    agent._ask_questions(_state(), 'g', 'reranker', 's', None, None)
    assert calls == ['g', 'g']
    assert not agent._DECIDE_CACHE


def test_external_engine_never_cached(monkeypatch):
    """非 reranker 引擎不挂缓存——external 走 stdin 答复不可缓存。"""
    agent._ask_questions(_state(), 'g', 'external', 's',
                         '{"action":"click","target":1}', None)
    assert not agent._DECIDE_CACHE


class _FakePage:
    url = 'https://x/'
    title = 't'


def _cli_mocks(monkeypatch, epoch='e1'):
    """decide e2e 通道 mock——_page_state/get_browser/session 存储。"""
    import dp_cli.commands._utils as _utils
    monkeypatch.setattr(agent, '_page_state',
                        lambda p, s, max_opts=20, **kw: _state(epoch))
    monkeypatch.setattr(_utils, 'get_browser', lambda s: _FakePage())
    monkeypatch.setattr(_utils, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'load_session', lambda s: {})
    monkeypatch.setattr(agent, 'save_session', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'save_refs', lambda *a, **k: None)
    monkeypatch.setattr(agent, '_RERANKER_FAM', None)


def test_decide_cli_cached_true(monkeypatch):
    """e2e：同 session 同 goal 二次 decide → cached:true + 引擎单次。"""
    from dp_cli.main import cli
    _cli_mocks(monkeypatch)
    calls = _engine_counter(monkeypatch)
    runner = CliRunner()
    r1 = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    assert r1.exit_code == 0, r1.output
    assert '"cached": false' in r1.output
    r2 = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    assert r2.exit_code == 0, r2.output
    assert '"cached": true' in r2.output
    assert calls == ['点Go']                       # CE 未重打分


def test_decide_cli_no_cache_flag(monkeypatch):
    """e2e：--no-cache 二次 decide 仍重打分，cached:false。"""
    from dp_cli.main import cli
    _cli_mocks(monkeypatch)
    calls = _engine_counter(monkeypatch)
    runner = CliRunner()
    runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    r = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker',
                            '--no-cache'])
    assert r.exit_code == 0, r.output
    assert '"cached": false' in r.output
    assert calls == ['点Go', '点Go']


def test_decide_cli_cached_still_gates_margin(monkeypatch):
    """缓存命中仍过 margin/tier 现算——低 margin resp 命中照样拒。"""
    from dp_cli.main import cli
    _cli_mocks(monkeypatch)
    calls = []
    monkeypatch.setattr(agent, '_engine_reranker',
                        lambda s, g: calls.append(g) or {
                            'action': 'click', 'target': 1,
                            'confidence': 0.9,
                            'probs': {'target': {'1': 0.503,
                                                 '2': 0.497}},
                            'done': False, 'stuck': False})
    runner = CliRunner()
    runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker'])
    # margin=0.006 ≥ 0.005 放行；换 --min-margin 0.01 命中缓存仍拒
    r = runner.invoke(cli, ['decide', '点Go', '--engine', 'reranker',
                            '--min-margin', '0.01'])
    assert calls == ['点Go']                       # 缓存命中
    assert 'AGENT_UNSURE' in r.output              # 但门控现算生效
