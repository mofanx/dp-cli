"""
session.py 单元测试
重点：会话文件 CRUD、端口发现、user-data-dir 探测、refs 管理
"""
import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from dp_cli import session


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """把模块级 _SESSION_DIR 重定向到临时目录,隔离真实用户目录。"""
    d = tmp_path / "sessions"
    monkeypatch.setattr(session, "_SESSION_DIR", d)
    return d


def _fake_co_factory():
    """返回一个假 ChromiumOptions 工厂:所有 set_* 方法都是 no-op Mock。"""
    def factory(*args, **kwargs):
        co = Mock()
        # 关键属性:get_browser 新建分支会读 co.user_data_path
        co.user_data_path = "/tmp/fake-profile"
        return co
    return factory


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 会话文件 CRUD
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_save_and_load_session(session_dir):
    """save 后 load 回来内容一致(中文不转义、可读)。"""
    data = {
        'browser_port': 9222,
        'active_tab': 'tab1',
        'user_connected': True,
        'message': '测试中文'
    }
    session.save_session('test', data)
    loaded = session.load_session('test')
    assert loaded == data


@pytest.mark.unit
def test_load_session_missing_returns_empty(session_dir):
    """不存在的会话 → {}。"""
    assert session.load_session('nonexistent') == {}


@pytest.mark.unit
def test_load_session_corrupt_returns_empty(session_dir):
    """写入非法 JSON → load 返回 {}(不抛异常)。"""
    session_dir.mkdir(parents=True, exist_ok=True)
    f = session_dir / 'corrupt.json'
    f.write_text('not valid json', encoding='utf-8')
    assert session.load_session('corrupt') == {}


@pytest.mark.unit
def test_delete_session_existing_returns_true(session_dir):
    """存在时删除返回 True 且文件消失。"""
    session.save_session('to_delete', {'data': 'value'})
    f = session_dir / 'to_delete.json'
    assert f.exists()
    assert session.delete_session('to_delete') == True
    assert not f.exists()


@pytest.mark.unit
def test_delete_session_missing_returns_false(session_dir):
    """不存在时返回 False。"""
    assert session.delete_session('nonexistent') == False


@pytest.mark.unit
def test_list_sessions(session_dir):
    """写入 2~3 个会话后,list_sessions() 返回含 name 字段且条数正确。"""
    session.save_session('session1', {'port': 9222})
    session.save_session('session2', {'port': 9223})
    session.save_session('session3', {'port': 9224})
    
    sessions = session.list_sessions()
    assert len(sessions) == 3
    session_names = {s['name'] for s in sessions}
    assert 'session1' in session_names
    assert 'session2' in session_names
    assert 'session3' in session_names
    # 验证包含 name 字段
    for s in sessions:
        assert 'name' in s


@pytest.mark.unit
def test_list_sessions_empty(session_dir):
    """空目录时返回空列表。"""
    sessions = session.list_sessions()
    assert sessions == []


@pytest.mark.unit
def test_list_sessions_skips_corrupt(session_dir):
    """目录里混入一个坏 JSON,不应导致 list 抛异常(坏的被跳过)。"""
    session.save_session('good', {'port': 9222})
    # 写入一个损坏的文件
    (session_dir / 'bad.json').write_text('invalid json', encoding='utf-8')
    
    sessions = session.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]['name'] == 'good'


@pytest.mark.unit
def test_get_session_dir_creates(session_dir):
    """调用后目录被创建(exists() 为 True)。"""
    d = session.get_session_dir()
    assert d.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 discover_port_from_profile
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_discover_port_happy(tmp_path):
    """写 DevToolsActivePort,第一行 9222,返回 9222。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    port_file = profile_dir / "DevToolsActivePort"
    port_file.write_text("9222\n", encoding='utf-8')
    
    port = session.discover_port_from_profile(profile_dir)
    assert port == 9222


@pytest.mark.unit
def test_discover_port_missing_file_raises(tmp_path):
    """文件不存在 → FileNotFoundError。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    
    with pytest.raises(FileNotFoundError):
        session.discover_port_from_profile(profile_dir)


@pytest.mark.unit
def test_discover_port_empty_raises(tmp_path):
    """空文件 → ValueError。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    port_file = profile_dir / "DevToolsActivePort"
    port_file.write_text("\n", encoding='utf-8')
    
    with pytest.raises(ValueError, match='为空'):
        session.discover_port_from_profile(profile_dir)


@pytest.mark.unit
def test_discover_port_non_int_raises(tmp_path):
    """第一行非数字 → ValueError。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    port_file = profile_dir / "DevToolsActivePort"
    port_file.write_text("not_a_number\n", encoding='utf-8')
    
    with pytest.raises(ValueError, match='不是有效端口号'):
        session.discover_port_from_profile(profile_dir)


@pytest.mark.unit
def test_discover_port_out_of_range_raises(tmp_path):
    """端口 0 或 70000 → ValueError。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    port_file = profile_dir / "DevToolsActivePort"
    
    # 测试端口 0
    port_file.write_text("0\n", encoding='utf-8')
    with pytest.raises(ValueError, match='端口号越界'):
        session.discover_port_from_profile(profile_dir)
    
    # 测试端口 70000
    port_file.write_text("70000\n", encoding='utf-8')
    with pytest.raises(ValueError, match='端口号越界'):
        session.discover_port_from_profile(profile_dir)


@pytest.mark.unit
def test_discover_port_with_second_line(tmp_path):
    """文件有第二行（WebSocket 路径）时仍正确解析第一行端口。"""
    profile_dir = tmp_path / "chrome_profile"
    profile_dir.mkdir()
    port_file = profile_dir / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/abc-123\n", encoding='utf-8')
    
    port = session.discover_port_from_profile(profile_dir)
    assert port == 9222


# ─────────────────────────────────────────────────────────────────────────────
# 2.4 default_user_data_dir_for_channel
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_user_data_dir_linux_stable_hit(tmp_path, monkeypatch):
    """linux stable 命中。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    chrome_dir = tmp_path / '.config' / 'google-chrome'
    chrome_dir.mkdir(parents=True)
    
    result = session.default_user_data_dir_for_channel('stable')
    assert result == chrome_dir


@pytest.mark.unit
def test_user_data_dir_not_exist_returns_none(tmp_path, monkeypatch):
    """不创建目录 → 返回 None。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    result = session.default_user_data_dir_for_channel('stable')
    assert result is None


@pytest.mark.unit
def test_user_data_dir_edge_hit(tmp_path, monkeypatch):
    """edge channel 命中。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    edge_dir = tmp_path / '.config' / 'microsoft-edge'
    edge_dir.mkdir(parents=True)
    
    result = session.default_user_data_dir_for_channel('edge')
    assert result == edge_dir


@pytest.mark.unit
def test_user_data_dir_darwin_stable_hit(tmp_path, monkeypatch):
    """darwin stable 命中。"""
    monkeypatch.setattr(session.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    chrome_dir = tmp_path / 'Library' / 'Application Support' / 'Google' / 'Chrome'
    chrome_dir.mkdir(parents=True)
    
    result = session.default_user_data_dir_for_channel('stable')
    assert result == chrome_dir


@pytest.mark.unit
def test_user_data_dir_windows_stable_hit(tmp_path, monkeypatch):
    """windows stable 命中。"""
    monkeypatch.setattr(session.sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    
    chrome_dir = tmp_path / 'Google' / 'Chrome' / 'User Data'
    chrome_dir.mkdir(parents=True)
    
    result = session.default_user_data_dir_for_channel('stable')
    assert result == chrome_dir


# ─────────────────────────────────────────────────────────────────────────────
# 2.5 sniff_auto_user_data_dir
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_sniff_hit(tmp_path, monkeypatch):
    """让 stable 目录存在且有 DevToolsActivePort、_is_port_alive 返回 True →
    返回 (path, diag) 且 diag 末项 reason == 'hit'。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    # 创建 chrome 目录和 DevToolsActivePort
    chrome_dir = tmp_path / '.config' / 'google-chrome'
    chrome_dir.mkdir(parents=True)
    port_file = chrome_dir / "DevToolsActivePort"
    port_file.write_text("9222\n", encoding='utf-8')
    
    # mock _is_port_alive 返回 True
    monkeypatch.setattr(session, "_is_port_alive", lambda port: True)
    
    result, diag = session.sniff_auto_user_data_dir()
    assert result == chrome_dir
    assert len(diag) == 1
    assert diag[0] == ('stable', chrome_dir, 'hit')


@pytest.mark.unit
def test_sniff_no_dir(tmp_path, monkeypatch):
    """default_user_data_dir_for_channel 全返回 None → 命中目录为 None,diag 含 'no_dir'。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # 不创建任何目录
    
    result, diag = session.sniff_auto_user_data_dir()
    assert result is None
    assert len(diag) == 2  # stable 和 edge 都 no_dir
    assert all(reason == 'no_dir' for _, _, reason in diag)


@pytest.mark.unit
def test_sniff_stale(tmp_path, monkeypatch):
    """目录与文件都在,但 _is_port_alive 返回 False → diag 含 'stale',结果 None。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    # 创建 chrome 目录和 DevToolsActivePort
    chrome_dir = tmp_path / '.config' / 'google-chrome'
    chrome_dir.mkdir(parents=True)
    port_file = chrome_dir / "DevToolsActivePort"
    port_file.write_text("9222\n", encoding='utf-8')
    
    # mock _is_port_alive 返回 False
    monkeypatch.setattr(session, "_is_port_alive", lambda port: False)
    
    result, diag = session.sniff_auto_user_data_dir()
    assert result is None
    assert len(diag) == 2  # stable stale, edge no_dir
    assert diag[0] == ('stable', chrome_dir, 'stale')
    assert diag[1][2] == 'no_dir'


@pytest.mark.unit
def test_sniff_no_port(tmp_path, monkeypatch):
    """目录在但无 DevToolsActivePort 文件 → diag 含 'no_port'。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    # 创建 chrome 目录但不创建 DevToolsActivePort
    chrome_dir = tmp_path / '.config' / 'google-chrome'
    chrome_dir.mkdir(parents=True)
    
    result, diag = session.sniff_auto_user_data_dir()
    assert result is None
    assert len(diag) == 2  # stable no_port, edge no_dir
    assert diag[0] == ('stable', chrome_dir, 'no_port')
    assert diag[1][2] == 'no_dir'


@pytest.mark.unit
def test_sniff_bad_file(tmp_path, monkeypatch):
    """目录在但 DevToolsActivePort 文件解析失败 → diag 含 'bad_file'。"""
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    # 创建 chrome 目录和损坏的 DevToolsActivePort
    chrome_dir = tmp_path / '.config' / 'google-chrome'
    chrome_dir.mkdir(parents=True)
    port_file = chrome_dir / "DevToolsActivePort"
    port_file.write_text("invalid\n", encoding='utf-8')
    
    result, diag = session.sniff_auto_user_data_dir()
    assert result is None
    assert len(diag) == 2  # stable bad_file, edge no_dir
    assert diag[0] == ('stable', chrome_dir, 'bad_file')
    assert diag[1][2] == 'no_dir'


# ─────────────────────────────────────────────────────────────────────────────
# 2.6 _is_port_alive
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_is_port_alive_true():
    """用标准库起一个监听 socket,取实际端口,断言 _is_port_alive(port) 为 True。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    
    try:
        assert session._is_port_alive(port) is True
    finally:
        sock.close()


@pytest.mark.unit
def test_is_port_alive_false():
    """对一个几乎不可能在监听的端口断言 False。"""
    # 使用一个很可能未被使用的端口
    assert session._is_port_alive(65432) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2.7 _detect_headless
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_detect_headless_true(monkeypatch):
    """mock requests.get,返回含 HeadlessChrome 的 User-Agent → 返回 True。"""
    fake_response = Mock()
    fake_response.json.return_value = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/120.0.0"}
    
    fake_get = Mock(return_value=fake_response)
    monkeypatch.setattr("requests.get", fake_get)
    
    assert session._detect_headless(9222) is True


@pytest.mark.unit
def test_detect_headless_false(monkeypatch):
    """User-Agent 不含 headless → False。"""
    fake_response = Mock()
    fake_response.json.return_value = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0"}
    
    fake_get = Mock(return_value=fake_response)
    monkeypatch.setattr("requests.get", fake_get)
    
    assert session._detect_headless(9222) is False


@pytest.mark.unit
def test_detect_headless_network_error(monkeypatch):
    """让 requests.get 抛异常 → 返回 False(不崩)。"""
    fake_get = Mock(side_effect=Exception("Network error"))
    monkeypatch.setattr("requests.get", fake_get)
    
    assert session._detect_headless(9222) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2.8 save_refs / load_refs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_save_and_load_refs(session_dir):
    """save_refs 后 load_refs 返回相同 refs 字典。"""
    refs = {
        'ref:1': {'selector': '#button', 'text': 'Submit'},
        'ref:2': {'selector': '#input', 'text': 'Username'}
    }
    session.save_refs('test_session', 'https://example.com', refs)
    
    loaded = session.load_refs('test_session')
    assert loaded == refs


@pytest.mark.unit
def test_load_refs_missing_returns_empty(session_dir):
    """无文件 → {}。"""
    result = session.load_refs('nonexistent')
    assert result == {}


@pytest.mark.unit
def test_load_refs_corrupt_returns_empty(session_dir):
    """坏 JSON → {}。"""
    # 手动创建一个损坏的 refs 文件
    refs_dir = session_dir / 'refs'
    refs_dir.mkdir(parents=True, exist_ok=True)
    refs_file = refs_dir / 'test_session.json'
    refs_file.write_text('invalid json', encoding='utf-8')
    
    result = session.load_refs('test_session')
    assert result == {}


@pytest.mark.unit
def test_save_refs_includes_url_and_timestamp(session_dir):
    """读回 refs.json 原始文件,断言含 url 与 timestamp 字段。"""
    refs = {'ref:1': {'selector': '#button'}}
    session.save_refs('test_session', 'https://example.com', refs)
    
    refs_file = session_dir / 'refs' / 'test_session.json'
    data = json.loads(refs_file.read_text(encoding='utf-8'))
    
    assert data['url'] == 'https://example.com'
    assert 'timestamp' in data
    assert data['refs'] == refs


# ─────────────────────────────────────────────────────────────────────────────
# 2.9 close_browser (选做但建议)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_close_browser_no_session_returns_false(session_dir):
    """无会话文件 → False。"""
    result = session.close_browser('nonexistent')
    assert result is False


@pytest.mark.unit
def test_close_browser_user_connected_stops_bridge_not_quit(session_dir, monkeypatch):
    """含 user_connected: True, bridge_pid: 1234 的会话;
    mock stop_bridge,断言:stop_bridge 被调用、会话文件被删除、返回 True、未 import/调用真 ChromiumPage。"""
    # 创建会话文件
    session.save_session('test', {
        'user_connected': True,
        'bridge_pid': 1234,
        'port': 9222
    })
    
    # mock stop_bridge
    stop_bridge_called = []
    def fake_stop_bridge(pid, timeout=None):
        stop_bridge_called.append(pid)
        return True
    
    monkeypatch.setattr("dp_cli.bridge_manager.stop_bridge", fake_stop_bridge)
    
    # 调用 close_browser
    result = session.close_browser('test')
    
    # 断言
    assert result is True
    assert stop_bridge_called == [1234]
    assert not (session_dir / 'test.json').exists()


@pytest.mark.unit
def test_close_browser_user_connected_stop_bridge_exception(session_dir, monkeypatch):
    """stop_bridge 抛异常时应被捕获，不影响删除会话文件。"""
    session.save_session('test', {
        'user_connected': True,
        'bridge_pid': 1234,
        'port': 9222
    })
    
    # mock stop_bridge 抛异常
    def fake_stop_bridge(pid, timeout=None):
        raise Exception("Bridge stop failed")
    
    monkeypatch.setattr("dp_cli.bridge_manager.stop_bridge", fake_stop_bridge)
    
    result = session.close_browser('test')
    assert result is True
    assert not (session_dir / 'test.json').exists()


@pytest.mark.unit
def test_close_browser_not_user_connected_no_port(session_dir, monkeypatch):
    """非 user_connected 且无 port → 删除会话返回 False。"""
    session.save_session('test', {
        'user_connected': False,
        'port': None
    })
    
    result = session.close_browser('test')
    assert result is False
    assert not (session_dir / 'test.json').exists()


@pytest.mark.unit
def test_close_browser_not_user_connected_with_port_exception(session_dir, monkeypatch):
    """非 user_connected 且有 port，但 ChromiumPage 抛异常 → 仍删除会话返回 True。"""
    session.save_session('test', {
        'user_connected': False,
        'port': 9222
    })
    
    # mock ChromiumPage 抛异常
    def fake_chromium_page(*args, **kwargs):
        raise Exception("Browser not available")
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    
    result = session.close_browser('test')
    assert result is True
    assert not (session_dir / 'test.json').exists()


# ─────────────────────────────────────────────────────────────────────────────
# T3b 追加: 浏览器分支测试
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_browser_with_port_success(session_dir, monkeypatch):
    """传 port=9222, mock ChromiumPage 返回假 page、mock _detect_headless 返回 False;
    断言:返回的就是假 page,且会话被保存为 user_connected=True、port=9222。"""
    fake_page = Mock()
    fake_page.browser.address = "127.0.0.1:9222"
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    monkeypatch.setattr(session, "_detect_headless", lambda port: False)
    
    result = session.get_browser('test', port=9222)
    
    assert result == fake_page
    # 验证会话被保存
    saved = session.load_session('test')
    assert saved['user_connected'] == True
    assert saved['port'] == 9222


@pytest.mark.unit
def test_get_browser_with_port_connect_error(session_dir, monkeypatch):
    """mock ChromiumPage 抛异常 → 断言 get_browser(port=9222) 抛 ConnectionError。"""
    def fake_chromium_page(*args, **kwargs):
        raise Exception("Connection failed")
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    monkeypatch.setattr(session, "_detect_headless", lambda port: False)
    
    with pytest.raises(ConnectionError, match="无法连接到端口 9222"):
        session.get_browser('test', port=9222)


@pytest.mark.unit
def test_get_browser_reuse_saved_port(session_dir, monkeypatch):
    """预存会话 {'port': 9333}; mock ChromiumPage 成功、_detect_headless False;
    断言返回假 page(走的是复用路径,不新建)。"""
    session.save_session('test', {'port': 9333})
    
    fake_page = Mock()
    fake_page.browser.address = "127.0.0.1:9333"
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    monkeypatch.setattr(session, "_detect_headless", lambda port: False)
    
    result = session.get_browser('test')
    
    assert result == fake_page
    # 验证走的是复用路径（没有新建，port 仍是 9333）
    saved = session.load_session('test')
    assert saved['port'] == 9333


@pytest.mark.unit
def test_get_browser_create_new(session_dir, monkeypatch):
    """无会话、无 port、无 probe_dir;
    mock ChromiumOptions 用 _fake_co_factory()、mock ChromiumPage 返回假 page;
    断言:返回假 page,且会话被保存且 port == 9222。"""
    fake_page = Mock()
    fake_page.browser.address = "127.0.0.1:9222"
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", _fake_co_factory())
    
    result = session.get_browser('test')
    
    assert result == fake_page
    # 验证会话被保存且 port 正确
    saved = session.load_session('test')
    assert saved['port'] == 9222


@pytest.mark.unit
def test_get_browser_auto_connect_failure_raises(session_dir, monkeypatch):
    """预存会话 {'probe_dir': '/x', 'auto_connect': True};
    mock _connect_via_bridge 抛异常 → 断言 get_browser() 抛 ConnectionError。"""
    session.save_session('test', {
        'probe_dir': '/x',
        'auto_connect': True
    })
    
    monkeypatch.setattr(session, "_connect_via_bridge", 
                         lambda *args, **kwargs: (_ for _ in ()).throw(Exception("Bridge failed")))
    
    with pytest.raises(ConnectionError, match="auto-connect 失败"):
        session.get_browser('test')


@pytest.mark.unit
def test_connect_via_bridge_classic(session_dir, monkeypatch):
    """mock discover_port_from_profile 返回 9222;
    mock detect_inspect_mode 返回 False;
    mock _detect_headless 返回 False;
    mock ChromiumPage / ChromiumOptions;
    传入空 sess dict,调用 _connect_via_bridge;
    断言:返回假 page、sess['port'] == 9222、sess['real_port'] == 9222、会话被保存。"""
    fake_page = Mock()
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        co.headless = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    monkeypatch.setattr(session, "discover_port_from_profile", lambda *args, **kwargs: 9222)
    monkeypatch.setattr("dp_cli.bridge_manager.detect_inspect_mode", lambda *args, **kwargs: False)
    monkeypatch.setattr(session, "_detect_headless", lambda *args, **kwargs: False)
    
    sess = {}
    result = session._connect_via_bridge('test', '/probe', sess)
    
    assert result == fake_page
    assert sess['port'] == 9222
    assert sess['real_port'] == 9222
    # 验证会话被保存
    saved = session.load_session('test')
    assert saved['port'] == 9222


@pytest.mark.unit
def test_connect_via_bridge_inspect(session_dir, monkeypatch):
    """mock discover_port_from_profile 返回 9222;
    mock detect_inspect_mode 返回 True;
    mock dp_cli.bridge_manager.start_bridge 返回 (12345, 9555)(pid, bridge_port);
    mock ChromiumPage / ChromiumOptions;
    断言:sess['bridge_pid']==12345、sess['bridge_port']==9555、sess['port']==9555、sess['real_port']==9222。"""
    fake_page = Mock()
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    monkeypatch.setattr(session, "discover_port_from_profile", lambda *args, **kwargs: 9222)
    monkeypatch.setattr("dp_cli.bridge_manager.detect_inspect_mode", lambda *args, **kwargs: True)
    monkeypatch.setattr("dp_cli.bridge_manager.start_bridge", lambda *args, **kwargs: (12345, 9555))
    
    sess = {}
    result = session._connect_via_bridge('test', '/probe', sess)
    
    assert result == fake_page
    assert sess['bridge_pid'] == 12345
    assert sess['bridge_port'] == 9555
    assert sess['port'] == 9555
    assert sess['real_port'] == 9222


@pytest.mark.unit
def test_close_browser_quit_success(session_dir, monkeypatch):
    """预存 {'port': 9222, 'user_connected': False};
    mock ChromiumPage 返回假 page(page.browser.quit 为 Mock);
    断言:quit 被调用、会话被删除、返回 True。"""
    session.save_session('test', {
        'port': 9222,
        'user_connected': False
    })
    
    fake_browser = Mock()
    fake_page = Mock()
    fake_page.browser = fake_browser
    fake_page.browser.quit = Mock()
    
    def fake_chromium_page(*args, **kwargs):
        return fake_page
    
    def fake_chromium_options(*args, **kwargs):
        co = Mock()
        co.set_local_port = Mock()
        co.existing_only = Mock()
        return co
    
    monkeypatch.setattr("DrissionPage.ChromiumPage", fake_chromium_page)
    monkeypatch.setattr("DrissionPage._configs.chromium_options.ChromiumOptions", fake_chromium_options)
    
    result = session.close_browser('test')
    
    assert result is True
    fake_browser.quit.assert_called_once()
    assert not (session_dir / 'test.json').exists()