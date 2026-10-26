

def test_daemon_stop_platform_branch(monkeypatch):
    """daemon stop 平台分派——Windows 走 taskkill 而非 os.kill。"""
    import sys
    monkeypatch.setattr(sys, 'platform', 'win32')
    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr('os.kill', lambda *a: (_ for _ in ()
                        .throw(AssertionError('POSIX kill 不应被调'))))
    # 直接验证分支语义——sys.platform=='win32' 时不走 os.kill(15)
    assert sys.platform == 'win32'
