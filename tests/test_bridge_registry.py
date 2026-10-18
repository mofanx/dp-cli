"""bridge_registry 原型验证：竞态 / stale 清理 / 原子写。

[实验改动点] 新增测试文件，仅测新模块；不改现有测试。

竞态语义（设计要点）：同一 user-data-dir 上并发 acquire_or_spawn，
spawn_fn 必须恰好被调用一次，其余调用方阻塞在键级 flock 上、拿到锁后
复用胜者写下的注册项。
"""
import json
import os
import threading
import time

import pytest

from dp_cli import bridge_registry as reg


@pytest.fixture
def bridges(tmp_path, monkeypatch):
    """把注册表目录隔离到 tmp_path。"""
    monkeypatch.setattr(reg, '_bridges_dir', lambda: tmp_path)
    return tmp_path


UDD = '/fake/profile/google-chrome'


def _fake_spawn(calls: list, delay: float = 0.0):
    def spawn(user_data_dir):
        calls.append(user_data_dir)
        if delay:
            time.sleep(delay)
        return os.getpid(), 40000 + len(calls)
    return spawn


# ── 1. 并发竞态：N 个调用方只 spawn 一次 ──────────────────────────────────


@pytest.mark.unit
def test_concurrent_acquire_single_spawn(bridges):
    """8 线程同时抢同一 profile：spawn 恰好 1 次，全员拿到同一 (pid,port)。"""
    calls: list = []
    results: list = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(reg.acquire_or_spawn(
            UDD, _fake_spawn(calls, delay=0.15), real_port=38081,
            validate_fn=lambda e, rp: True))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(calls) == 1, f'spawn 被调用了 {len(calls)} 次'
    assert len({(pid, port) for pid, port, _ in results}) == 1
    adopted = [r[2] for r in results]
    assert adopted.count(False) == 1 and adopted.count(True) == 7


@pytest.mark.unit
def test_concurrent_spawn_failure_all_fail(bridges):
    """spawn_fn 抛错时：锁释放，败者进来也各自 spawn（重试语义，不毒化）。"""
    calls: list = []
    results: list = []
    errors: list = []

    def flaky_spawn(udd):
        calls.append(udd)
        raise RuntimeError('auth timeout')

    def worker():
        try:
            results.append(reg.acquire_or_spawn(
                UDD, flaky_spawn, validate_fn=lambda e, rp: True))
        except RuntimeError:
            errors.append(1)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(10); t2.join(10)

    assert not results
    assert len(errors) == 2          # 两个调用方都收到 spawn 异常
    assert len(calls) >= 1           # 败者拿到锁后自行重试 spawn
    # 失败不留注册项
    assert reg.find_bridge(UDD, validate_fn=lambda e, rp: True) is None


# ── 2. stale 清理 ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_find_bridge_prunes_stale(bridges):
    reg.register_bridge(UDD, pid=99999, port=40123, real_port=38081)
    # 校验失败 → None 且文件被删
    assert reg.find_bridge(UDD, real_port=38081,
                           validate_fn=lambda e, rp: False) is None
    assert not reg._entry_path(UDD).exists()


@pytest.mark.unit
def test_find_bridge_real_port_mismatch(bridges):
    """Chrome 重启换端口：real_port 不一致 → stale。"""
    reg.register_bridge(UDD, pid=1, port=40123, real_port=38081)
    seen = {}

    def validate(entry, rp):
        seen['real_port'] = rp
        return entry.get('real_port') == rp

    assert reg.find_bridge(UDD, real_port=39000, validate_fn=validate) is None
    assert seen['real_port'] == 39000


@pytest.mark.unit
def test_corrupt_entry_treated_as_miss(bridges):
    p = reg._entry_path(UDD)
    p.write_text('{not json', encoding='utf-8')
    assert reg.find_bridge(UDD, validate_fn=lambda e, rp: True) is None
    # 损坏文件由 read 返回 None（不 unlink：保守，交给下次 spawn 覆盖）


# ── 3. 原子写 / adopt ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_register_atomic_no_tmp_residue(bridges):
    reg.register_bridge(UDD, pid=1, port=40123)
    files = [f.name for f in bridges.iterdir()]
    assert not any('.tmp' in f for f in files)
    entry = json.loads(reg._entry_path(UDD).read_text())
    assert entry['port'] == 40123


@pytest.mark.unit
def test_second_acquire_adopts_and_notes_session(bridges):
    calls: list = []
    pid, port, adopted = reg.acquire_or_spawn(
        UDD, _fake_spawn(calls), session_name='default',
        validate_fn=lambda e, rp: True)
    assert adopted is False
    pid2, port2, adopted2 = reg.acquire_or_spawn(
        UDD, _fake_spawn(calls), session_name='work',
        validate_fn=lambda e, rp: True)
    assert (pid2, port2) == (pid, port)
    assert adopted2 is True
    assert len(calls) == 1
    entry = json.loads(reg._entry_path(UDD).read_text())
    assert set(entry['sessions']) == {'default', 'work'}


@pytest.mark.unit
def test_late_caller_no_wait(bridges, capsys):
    """锁空闲时不打印等待提示；锁被占时才提示。"""
    calls: list = []
    reg.acquire_or_spawn(UDD, _fake_spawn(calls),
                         validate_fn=lambda e, rp: True)
    capsys.readouterr()  # 清掉
    reg.acquire_or_spawn(UDD, _fake_spawn(calls),
                         validate_fn=lambda e, rp: True)
    assert '另一个 dpx 进程' not in capsys.readouterr().err


# ── 4. flock 互斥性（线程内独立 open 也互斥——验证锁语义本身）────────────


@pytest.mark.unit
def test_lock_mutual_exclusion(bridges):
    order: list = []
    barrier = threading.Barrier(2)

    def holder():
        barrier.wait()
        with reg._locked(UDD):
            order.append('hold')
            time.sleep(0.3)
            order.append('release')

    def contender():
        barrier.wait()
        time.sleep(0.05)  # 确保 holder 先拿锁
        with reg._locked(UDD, blocking_msg=False):
            order.append('contender')

    t1, t2 = threading.Thread(target=holder), threading.Thread(target=contender)
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert order == ['hold', 'release', 'contender']


@pytest.mark.unit
def test_adopt_attach_failure_preserves_shared_entry(tmp_path, monkeypatch):
    """两 session adopt 同一 bridge：一方 attach 失败只摘除自己，
    对方的注册项与 bridge 进程都不受影响（peer-review P1 修复）。"""
    import dp_cli.session as sm
    import dp_cli.bridge_registry as reg

    udd = tmp_path / 'profile'
    # session A 已持有 bridge（注册项含 A）
    reg.register_bridge(udd, pid=4321, port=5555, real_port=9222,
                        session_name='sA')
    stopped = []
    import dp_cli.bridge_manager as bm
    monkeypatch.setattr(bm, 'stop_bridge',
                        lambda *a, **k: stopped.append(a))
    # 四道闸全放行（pid 活/端口开/healthz/real_port）
    monkeypatch.setattr(bm, 'is_bridge_alive', lambda pid: True)
    monkeypatch.setattr(sm, '_port_open', lambda *a, **k: True)
    monkeypatch.setattr(sm, '_bridge_upstream_alive', lambda *a, **k: True)
    monkeypatch.setattr(sm, 'discover_port_from_profile', lambda d: 9222)
    monkeypatch.setattr(bm, 'detect_inspect_mode', lambda p: True)

    def fake_spawn(d):
        raise AssertionError('adopt 命中不应 spawn')
    # acquire_or_spawn 走真注册表 → adopt 命中
    # attach 失败（ChromiumPage 不可 attach）
    monkeypatch.setattr(sm, '_attach_page',
                        lambda co: (_ for _ in ()).throw(
                            ConnectionError('attach fail')))
    monkeypatch.setattr(sm, '_detect_headless', lambda p: False)

    with pytest.raises(ConnectionError):
        sm._connect_via_bridge('sB', str(udd), {})

    # 断言：注册项仍在、A 的持有保留、bridge 未停
    ent = reg.find_bridge(udd, real_port=9222)
    assert ent is not None and 'sA' in ent['sessions']
    assert stopped == []
