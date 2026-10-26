

def test_daemon_stop_platform_branch(monkeypatch, tmp_path):
    """daemon stop win32 分派——真实调用路径，断言 taskkill 参数。"""
    import sys
    monkeypatch.setattr(sys, 'platform', 'win32')
    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr('os.kill', lambda *a: (_ for _ in ()
                        .throw(AssertionError('POSIX kill 不应被调'))))
    import json, click
    from click.testing import CliRunner
    from dp_cli import daemon

    sock = tmp_path / 't.sock'; sock.write_text('')
    meta = tmp_path / 't.meta.json'
    meta.write_text(json.dumps({'pid': 4321, 'version': 'x'}))
    monkeypatch.setattr(daemon, '_sock_path', lambda s: sock)
    monkeypatch.setattr(daemon, '_meta_path', lambda s: meta)
    monkeypatch.setattr(daemon, '_send_recv', lambda *a, **k: None)
    monkeypatch.setattr(daemon, '_READY_CACHE', {})
    grp = click.Group()
    daemon.register(grp)
    r = CliRunner().invoke(grp, ['daemon-stop', '-S', 't'])
    assert r.exit_code == 0, r.output
    assert calls == [['taskkill', '/F', '/T', '/PID', '4321']], calls
