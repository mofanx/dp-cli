"""
全局 pytest fixtures 与配置。
仅提供通用测试基础设施(临时目录、隔离的 HOME 等),不含具体业务测试。
"""
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_session_state(tmp_path: Path, monkeypatch):
    """autouse：会话目录/页面缓存/DP_CLI_* 环境变量全量隔离。

    session._session_dir() 是惰性 seam（不再 import 期固化），
    仍显式 patch 重定向到每用例独立 tmp——真实 ~/.dp_cli/sessions
    残留（真机冒烟、其他工程会话）不得进入任何用例；
    _PAGE_CACHE 与逃生开关同样复位。
    """
    import dp_cli.session as session_mod
    d = tmp_path / '.dp_cli' / 'sessions'
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_mod, '_session_dir', lambda: d)
    monkeypatch.delenv('DP_CLI_NO_STUB', raising=False)
    session_mod._PAGE_CACHE.clear()
    # bridge 注册表同样隔离——find_bridge/acquire_or_spawn 直读
    # Path.home()，不隔离会让用例污染并被真实 ~/.dp_cli/bridges 残留命中
    import dp_cli.bridge_registry as reg_mod
    bd = tmp_path / '.dp_cli' / 'bridges'
    bd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reg_mod, '_bridges_dir', lambda: bd)
    yield
    session_mod._PAGE_CACHE.clear()


@pytest.fixture
def tmp_work_dir(tmp_path: Path) -> Path:
    """提供一个干净的临时工作目录。"""
    return tmp_path


@pytest.fixture
def sock_dir(tmp_path: Path):
    """AF_UNIX bind 安全的 socket 目录（sun_path 上限敏感）。

    Linux sun_path=108B / macOS=104B——macOS runner 的 tmp_path 深度
    （/var/folders/…/pytest-0/test_xxx0/）加叶名即超限，真实
    bind 抛 'AF_UNIX path too long'（daemon 真机 socket 测试必修）。
    深度安全（≤96B 含叶名）直接复用 tmp_path，否则退到
    tempfile.gettempdir() 下的短前缀目录并负责回收。
    """
    import os as _os
    import shutil
    import tempfile
    if len(_os.fsencode(str(tmp_path / 'x.sock'))) <= 96:
        yield tmp_path
        return
    d = Path(tempfile.mkdtemp(prefix='dpx-t-'))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """将用户主目录指向临时目录，隔离对真实 HOME 的读写。

    Path.home() 的平台分流：POSIX 吃 HOME；Windows 吃 USERPROFILE
    （不存在才回落 HOMEDRIVE+HOMEPATH，不读 HOME）——两个变量都设
    才三平台生效。dp_cli 各模块的 home 路径已全改调用期惰性求值，
    本 fixture 对 session/daemon/netlog/agent 全部生效。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def write_json(tmp_path: Path):
    """便捷写入 JSON 文件并返回路径,供需要样例数据的测试使用。"""
    def _write(name: str, data) -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p
    return _write


# ─────────────────────────────────────────────────────────────────────────────
# 共享测试基建：FakePage 工厂 / 隔离会话目录 / 线程内静态 HTTP 服务
# ─────────────────────────────────────────────────────────────────────────────


class FakeEle:
    """命令层测试通用假元素：raw_text + states.is_displayed。"""

    def __init__(self, raw_text='', displayed=True):
        self.raw_text = raw_text

        class _S:
            is_displayed = displayed
        self.states = _S()


class FakeNoneEle:
    """模拟 DrissionPage 元素未命中时的 NoneElement（类名判定）。"""
    pass


FakeNoneEle.__name__ = 'NoneElement'


class FakePage:
    """命令层测试通用假页面。

    :param html/url/title: 页面属性
    :param ele: page.ele() 返回值（None → 返回 FakeNoneEle）
    :param eles: page.eles() 返回列表
    :param run_js_return: page.run_js() 返回值；传入异常实例则抛出
    """

    def __init__(self, html='', url='https://example.com', title='Demo',
                 ele=None, eles=None, run_js_return=None, listen=None):
        self.html = html
        self.url = url
        self.title = title
        self._ele = ele
        self._eles = eles if eles is not None else []
        self._run_js_return = run_js_return
        self.listen = listen

        class _States:
            ready_state = 'complete'
        self.states = _States()

        class _W:
            def doc_loaded(self, timeout=None):
                return True

            def url_change(self, *a, **k):
                return True

            def title_change(self, *a, **k):
                return True

            def ele_displayed(self, *a, **k):
                return True

            def ele_hidden(self, *a, **k):
                return True

            def downloads_done(self, *a, **k):
                return True
        self.wait = _W()

    def ele(self, locator, index=1, timeout=None):
        return self._ele if self._ele is not None else FakeNoneEle()

    def eles(self, locator, timeout=None):
        return self._eles

    def run_js(self, script, *a, **k):
        if isinstance(self._run_js_return, Exception):
            raise self._run_js_return
        return self._run_js_return


@pytest.fixture
def fake_page_factory():
    """FakePage 工厂：fake_page_factory(html='...', ele=FakeEle(...))。"""
    return FakePage


@pytest.fixture
def isolated_session_dir(tmp_path: Path, monkeypatch) -> Path:
    """把 dp_cli.session._session_dir() 重定向到临时目录。

    patch 惰性 seam 而非 setenv(HOME)——与 _isolate_session_state
    同机制，供需显式拿目录句柄的会话落盘类用例使用。
    """
    import dp_cli.session as session_mod
    d = tmp_path / 'sessions'
    d.mkdir()
    monkeypatch.setattr(session_mod, '_session_dir', lambda: d)
    return d


@pytest.fixture
def http_server(tmp_path: Path):
    """线程内静态文件服务器；返回 (serve_url_factory, shutdown 自动回收)。

    用法：
        def test(http_server):
            url = http_server({'index.html': '<title>t</title>'})
    """
    import functools
    import http.server
    import threading

    servers = []

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # 静默访问日志，避免污染 CliRunner 输出
            pass

    def _serve(files: dict, subdir: str = 'www') -> str:
        root = tmp_path / subdir
        root.mkdir(exist_ok=True)
        for name, body in files.items():
            (root / name).write_text(body, encoding='utf-8')
        srv = http.server.HTTPServer(
            ('127.0.0.1', 0), functools.partial(_Quiet, directory=str(root)))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append((srv, t))
        return f'http://127.0.0.1:{srv.server_port}'

    yield _serve

    for srv, t in servers:
        srv.shutdown()
        t.join(timeout=3)