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
    assert not str(d).startswith(str(Path.home()))


@pytest.mark.unit
def test_page_cache_starts_empty():
    """_PAGE_CACHE 每用例重置——上个用例的假页面不会泄漏。"""
    assert sm._PAGE_CACHE == {}
    sm._PAGE_CACHE['x'] = object()   # 下个用例应看不到


@pytest.mark.unit
def test_page_cache_reset_between_tests():
    assert sm._PAGE_CACHE == {}
