"""隔离基建自证：autouse fixture 把会话状态挡在真实 HOME 之外。"""
from pathlib import Path

import pytest

import dp_cli.session as sm


@pytest.mark.unit
def test_session_dir_is_per_test_tmp():
    """get_session_dir 必须落在 pytest tmp 而非真实 ~/.dp_cli——
    真实 HOME 的会话残留不再可见。"""
    d = sm.get_session_dir()
    assert str(d).startswith('/tmp/pytest') or 'pytest' in str(d)
    # Windows 的 %TEMP% 本就在 C:\Users\<user>\ 下——不能断言"不在 HOME"，
    # 只需保证不是默认的真实会话目录
    assert d != Path.home() / '.dp_cli' / 'sessions'


@pytest.mark.unit
def test_page_cache_starts_empty():
    """_PAGE_CACHE 每用例重置——上个用例的假页面不会泄漏。"""
    assert sm._PAGE_CACHE == {}
    sm._PAGE_CACHE['x'] = object()   # 下个用例应看不到


@pytest.mark.unit
def test_page_cache_reset_between_tests():
    assert sm._PAGE_CACHE == {}


@pytest.mark.unit
def test_isolated_home_drives_lazy_paths(isolated_home, monkeypatch):
    """setenv(HOME/USERPROFILE) 后所有惰性 Path.home() 路径必须落新 HOME。

    win/mac CI 教训：Path.home() 若在模块顶层固化（_SESSION_DIR/_DIR/
    _MODELS_DIR 旧形态），setenv 补丁无效，用例静默读写真实用户目录。
    现全部改调用期惰性求值——本用例逐模块断言路径前缀在新 HOME 下。
    """
    import dp_cli.agent as ag
    import dp_cli.daemon as dm
    import dp_cli.netlog as nl

    assert Path.home() == isolated_home
    # session 的 autouse seam 还原为真实实现，验证其同样跟随 HOME
    monkeypatch.setattr(sm, '_session_dir', sm._default_session_dir)

    home = str(isolated_home)
    for p in (sm._session_dir(),
              dm._meta_path('s'),
              nl._path('s'), nl._lock_path('s'),
              ag._models_dir()):
        assert str(p).startswith(home), f'{p} 不在隔离 HOME 下'
    # _sock_path 有 sun_path 兜底——sock 路径超 AF_UNIX 上限时
    # 退 TMPDIR 短前缀是正确行为而非隔离失效（深 HOME/深 tmp_path
    # 的 macOS CI 会命中）；未超限时仍须跟 HOME。
    import os
    import tempfile
    sp = dm._sock_path('s')
    deep = len(os.fsencode(str(
        isolated_home / '.dp_cli' / 'daemon' / 's.sock'))) > 100
    if deep:
        assert str(sp).startswith(str(Path(tempfile.gettempdir()))), \
            f'{sp} 深 HOME 下应退 TMPDIR 短前缀'
    else:
        assert str(sp).startswith(home), f'{sp} 不在隔离 HOME 下'


@pytest.mark.unit
def test_no_import_time_home_constants():
    """防回归：dp_cli 模块顶层禁止 Path.home()/expanduser 赋值。

    import 期固化的 home 常量会让 isolated_home/CI HOME 隔离全部
    失效——AST 扫全包顶层赋值语句，出现即失败。
    """
    import ast

    import dp_cli
    root = Path(dp_cli.__file__).parent
    for f in root.rglob('*.py'):
        tree = ast.parse(f.read_text(encoding='utf-8'), filename=str(f))
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) \
                    and getattr(node, 'value', None) is not None:
                s = ast.dump(node.value)
                assert 'home' not in s and 'expanduser' not in s, \
                    f'{f}:{node.lineno} 顶层固化了 home 路径——改为函数内惰性求值'
