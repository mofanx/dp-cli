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

    _SESSION_DIR 是 import 期模块常量，真实 ~/.dp_cli/sessions 残留
    （真机冒烟、其他工程会话）会污染未挂 isolated_session_dir 的用例
    ——全部重定向到每用例独立 tmp；_PAGE_CACHE 与逃生开关同样复位。
    """
    import dp_cli.session as session_mod
    d = tmp_path / '.dp_cli' / 'sessions'
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_mod, '_SESSION_DIR', d)
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
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """
    将 HOME 指向临时目录,隔离会话文件等对真实用户目录的读写。
    需要操作 ~/.dp-cli 之类路径的测试可使用本 fixture。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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
    """把 dp_cli.session._SESSION_DIR 重定向到临时目录。

    _SESSION_DIR 是模块级常量，monkeypatch.setenv(HOME) 不生效，
    需直接 patch 模块属性。真实浏览器冒烟/会话落盘类用例使用。
    """
    import dp_cli.session as session_mod
    d = tmp_path / 'sessions'
    d.mkdir()
    monkeypatch.setattr(session_mod, '_SESSION_DIR', d)
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