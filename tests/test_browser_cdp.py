# -*- coding:utf-8 -*-
"""
t3 新增命令测试：stealth --check / emulate --device /
permissions / clipboard-* / media / bypass-csp / throttle / file-chooser。

全程 FakePage 记录 CDP 调用，不依赖真实浏览器。
"""
import json

import pytest
from click.testing import CliRunner

import dp_cli.commands.browser as bmod
import dp_cli.commands.emulate as emod
import dp_cli.commands._utils as _utils


class FakePage:
    """记录 run_cdp 调用序列的假页面。"""

    def __init__(self, run_js_return=None, url='https://example.com/',
                 ele=None):
        self.url = url
        self.latest_tab = None
        self._run_js_return = run_js_return
        self._ele = ele
        self.cdp_calls = []          # [(method, kwargs)]
        self._eval_responses = {}    # method → 返回值覆盖

        class _States:
            ready_state = 'complete'
        self.states = _States

        class _Driver:
            def __init__(self):
                self.callbacks = {}

            def set_callback(self, name, fn):
                self.callbacks[name] = fn
        self.driver = _Driver()

    @property
    def title(self):
        return 'Demo'

    def ele(self, locator, index=1, timeout=10):
        return self._ele if self._ele is not None else _NoneEle()

    def run_js(self, script, *a, **k):
        if isinstance(self._run_js_return, Exception):
            raise self._run_js_return
        return self._run_js_return

    def run_cdp(self, method, **kw):
        self.cdp_calls.append((method, kw))
        resp = self._eval_responses.get(method)
        if isinstance(resp, Exception):
            raise resp
        return resp or {}

    def methods(self):
        return [m for m, _ in self.cdp_calls]

    def kwargs_of(self, method):
        for m, kw in self.cdp_calls:
            if m == method:
                return kw
        return None


class _NoneEle:
    pass
_NoneEle.__name__ = 'NoneElement'


class FakeEle:
    def __init__(self):
        self.clicked = False

    def click(self):
        self.clicked = True


@pytest.fixture
def runner():
    return CliRunner()


def _out(result):
    return json.loads(result.output)


# ─────────────────────────────────────────────────────────────────────────────
# stealth --check
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_stealth_check_clean(runner, monkeypatch):
    """正常桌面 Chrome 指纹 → 全部 ok，无 fail。"""
    from dp_cli.main import cli
    raw = {
        'webdriver': False,
        'userAgent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        'platform': 'Linux x86_64',
        'userAgentData': {'brands': [], 'mobile': False, 'platform': 'Linux'},
        'languages': ['zh-CN', 'zh'],
        'plugins': 5, 'mimeTypes': 2,
        'hardwareConcurrency': 16, 'deviceMemory': 16,
        'maxTouchPoints': 0, 'timezone': 'Asia/Shanghai',
        'outerDims': [1920, 1080], 'innerDims': [1920, 980],
        'hasChromeObj': True, 'notificationPermission': 'default',
        'webgl': {'vendor': 'Intel Inc.', 'renderer': 'Intel Iris OpenGL Engine'},
        'stealthMarker': {'applied': ['webdriver'], 'errors': {}},
    }
    page = FakePage(run_js_return=raw)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['stealth', '--check'])
    assert result.exit_code == 0
    data = _out(result)['data']
    statuses = {c['check']: c['status'] for c in data['checks']}
    assert statuses['webdriver'] == 'ok'
    assert statuses['user-agent'] == 'ok'
    assert data['summary']['fail'] == 0


@pytest.mark.unit
def test_stealth_check_detects_headless(runner, monkeypatch):
    """HeadlessChrome UA + webdriver=true + SwiftShader → fail/warn 检出。"""
    from dp_cli.main import cli
    raw = {
        'webdriver': True,
        'userAgent': 'Mozilla/5.0 HeadlessChrome/147.0.0.0',
        'platform': 'Linux x86_64',
        'userAgentData': None,
        'languages': [], 'plugins': 0, 'mimeTypes': 0,
        'hardwareConcurrency': 2, 'deviceMemory': 4,
        'timezone': 'UTC',
        'outerDims': [0, 0], 'innerDims': [800, 600],
        'hasChromeObj': False, 'notificationPermission': 'denied',
        'webgl': {'vendor': 'Google Inc.', 'renderer': 'SwiftShader'},
        'stealthMarker': None,
    }
    page = FakePage(run_js_return=raw)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['stealth', '--check'])
    assert result.exit_code == 0
    data = _out(result)['data']
    statuses = {c['check']: c['status'] for c in data['checks']}
    assert statuses['webdriver'] == 'fail'
    assert statuses['user-agent'] == 'fail'
    assert statuses['window-dims'] == 'fail'
    assert statuses['webgl'] == 'warn'
    assert data['summary']['fail'] >= 3


@pytest.mark.unit
def test_stealth_check_ua_platform_mismatch(runner, monkeypatch):
    """UA 声称 Windows 但 userAgentData.platform=Linux → consistency fail。"""
    from dp_cli.main import cli
    raw = {
        'webdriver': False,
        'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'Chrome/147.0.0.0 Safari/537.36',
        'platform': 'Win32',
        'userAgentData': {'brands': [], 'mobile': False, 'platform': 'Linux'},
        'languages': ['en'], 'plugins': 5, 'mimeTypes': 2,
        'hardwareConcurrency': 8, 'deviceMemory': 8,
        'timezone': 'UTC',
        'outerDims': [1920, 1080], 'innerDims': [1920, 980],
        'hasChromeObj': True, 'notificationPermission': 'default',
        'webgl': {'vendor': 'Google Inc.', 'renderer': 'ANGLE'},
        'stealthMarker': None,
    }
    page = FakePage(run_js_return=raw)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['stealth', '--check'])
    assert result.exit_code == 0
    statuses = {c['check']: c['status']
                for c in _out(result)['data']['checks']}
    assert statuses['ua-platform-consistency'] == 'fail'


@pytest.mark.unit
def test_stealth_check_js_error(runner, monkeypatch):
    """run_js 抛异常 → STEALTH_CHECK_FAILED 退出 1。"""
    from dp_cli.main import cli
    page = FakePage(run_js_return=RuntimeError('js boom'))
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['stealth', '--check'])
    assert result.exit_code == 1
    out = _out(result)
    assert out['code'] == 'STEALTH_CHECK_FAILED'


# ─────────────────────────────────────────────────────────────────────────────
# emulate --device
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_emulate_device_iphone(runner, monkeypatch, tmp_path):
    """--device iphone-15：viewport/dpr/mobile/touch/UA 一键写入。"""
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(emod, '_get_page', lambda s: page)
    monkeypatch.setattr(emod, 'load_session', lambda s: {})
    monkeypatch.setattr(emod, 'save_session',
                        lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['emulate', '--device', 'iphone-15'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Emulation.setDeviceMetricsOverride')
    assert kw['width'] == 393 and kw['height'] == 852
    assert kw['deviceScaleFactor'] == 3 and kw['mobile'] is True
    assert 'Emulation.setTouchEmulationEnabled' in page.methods()
    uakw = page.kwargs_of('Emulation.setUserAgentOverride')
    assert 'iPhone' in uakw['userAgent']
    cfg = saved['emulate']
    assert cfg['dpr'] == 3 and cfg['mobile'] is True and cfg['touch'] is True


@pytest.mark.unit
def test_emulate_device_desktop(runner, monkeypatch):
    """desktop-chrome 预设：mobile=False，无 touch 启用。"""
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(emod, '_get_page', lambda s: page)
    monkeypatch.setattr(emod, 'load_session', lambda s: {})
    monkeypatch.setattr(emod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['emulate', '--device', 'desktop-chrome'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Emulation.setDeviceMetricsOverride')
    assert kw['mobile'] is False and kw['deviceScaleFactor'] == 1
    # touch 显式下发 enabled=False：清除移动预设残留的 touch override
    assert page.kwargs_of('Emulation.setTouchEmulationEnabled')['enabled'] is False
    assert saved['emulate']['touch'] is False


@pytest.mark.unit
def test_emulate_device_invalid(runner, monkeypatch):
    """未知设备名 → click Choice 拒绝，exit 2。"""
    from dp_cli.main import cli
    monkeypatch.setattr(emod, '_get_page', lambda s: FakePage())
    result = runner.invoke(cli, ['emulate', '--device', 'nokia-3310'])
    assert result.exit_code == 2


# ─────────────────────────────────────────────────────────────────────────────
# permissions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_permissions_list(runner):
    from dp_cli.main import cli
    result = runner.invoke(cli, ['permissions', '--list'])
    assert result.exit_code == 0
    assert 'geolocation' in _out(result)['data']['permissions']


@pytest.mark.unit
def test_permissions_grant(runner, monkeypatch):
    """--grant 带 origin 推导：当前页 URL → grantPermissions。"""
    from dp_cli.main import cli
    page = FakePage(url='https://a.example.com/path?q=1')
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(
        cli, ['permissions', '--grant', 'geolocation,notifications'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Browser.grantPermissions')
    assert kw['origin'] == 'https://a.example.com'
    assert kw['permissions'] == ['geolocation', 'notifications']


@pytest.mark.unit
def test_permissions_unknown_name(runner, monkeypatch):
    from dp_cli.main import cli
    monkeypatch.setattr(bmod, '_get_page', lambda s: FakePage())
    result = runner.invoke(cli, ['permissions', '--grant', 'nonsense'])
    assert result.exit_code == 2
    assert _out(result)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_permissions_reset(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['permissions', '--reset'])
    assert result.exit_code == 0
    assert 'Browser.resetPermissions' in page.methods()


@pytest.mark.unit
def test_permissions_no_args(runner):
    from dp_cli.main import cli
    result = runner.invoke(cli, ['permissions'])
    assert result.exit_code == 2
    assert _out(result)['code'] == 'INVALID_ARGS'


# ─────────────────────────────────────────────────────────────────────────────
# clipboard-get / clipboard-set
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_clipboard_get(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    page._eval_responses['Runtime.evaluate'] = {
        'result': {'value': 'hello clipboard'}}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['clipboard-get'])
    assert result.exit_code == 0
    assert _out(result)['data']['text'] == 'hello clipboard'
    kw = page.kwargs_of('Runtime.evaluate')
    assert kw['awaitPromise'] is True


@pytest.mark.unit
def test_clipboard_set(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['clipboard-set', 'abc"123'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Runtime.evaluate')
    assert 'writeText' in kw['expression']
    assert 'abc\\"123' in kw['expression']


@pytest.mark.unit
def test_clipboard_js_exception(runner, monkeypatch):
    """Runtime.evaluate 返回 exceptionDetails → CLIPBOARD_FAILED。"""
    from dp_cli.main import cli
    page = FakePage()
    page._eval_responses['Runtime.evaluate'] = {
        'exceptionDetails': {'exception': {'description': 'NotAllowedError'}}}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    result = runner.invoke(cli, ['clipboard-get'])
    assert result.exit_code == 1
    assert _out(result)['code'] == 'CLIPBOARD_FAILED'


# ─────────────────────────────────────────────────────────────────────────────
# media
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_media_dark(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['media', '--color-scheme', 'dark'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Emulation.setEmulatedMedia')
    assert {'name': 'prefers-color-scheme', 'value': 'dark'} in kw['features']
    assert saved['browser_cdp']['media']['features']['prefers-color-scheme'] == 'dark'


@pytest.mark.unit
def test_media_off(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session',
                        lambda s: {'browser_cdp': {'media': {'features': {}}}})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['media', '--off'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Emulation.setEmulatedMedia')
    assert kw['media'] == '' and kw['features'] == []
    assert 'browser_cdp' not in saved


# ─────────────────────────────────────────────────────────────────────────────
# bypass-csp
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_bypass_csp_on_off(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['bypass-csp'])
    assert result.exit_code == 0
    assert page.kwargs_of('Page.setBypassCSP')['enabled'] is True
    assert saved['browser_cdp']['bypass_csp'] is True


# ─────────────────────────────────────────────────────────────────────────────
# throttle
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_throttle_profile(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['throttle', '--profile', 'slow-3g'])
    assert result.exit_code == 0
    kw = page.kwargs_of('Network.emulateNetworkConditions')
    assert kw['latency'] == 400
    assert saved['browser_cdp']['throttle']['network']['latency'] == 400


@pytest.mark.unit
def test_throttle_cpu(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: None)
    result = runner.invoke(cli, ['throttle', '--cpu', '4'])
    assert result.exit_code == 0
    assert page.kwargs_of('Emulation.setCPUThrottlingRate')['rate'] == 4


@pytest.mark.unit
def test_throttle_profile_and_custom_conflict(runner, monkeypatch):
    from dp_cli.main import cli
    monkeypatch.setattr(bmod, '_get_page', lambda s: FakePage())
    result = runner.invoke(
        cli, ['throttle', '--profile', 'slow-3g', '--latency', '1'])
    assert result.exit_code == 2
    assert _out(result)['code'] == 'INVALID_ARGS'


@pytest.mark.unit
def test_throttle_off(runner, monkeypatch):
    from dp_cli.main import cli
    page = FakePage()
    saved = {}
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'load_session',
                        lambda s: {'browser_cdp': {'throttle': {'cpu': 4}}})
    monkeypatch.setattr(bmod, 'save_session', lambda s, d: saved.update(d))
    result = runner.invoke(cli, ['throttle', '--off'])
    assert result.exit_code == 0
    assert page.kwargs_of('Emulation.setCPUThrottlingRate')['rate'] == 1
    assert 'browser_cdp' not in saved


# ─────────────────────────────────────────────────────────────────────────────
# file-chooser
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_file_chooser_happy(runner, monkeypatch, tmp_path):
    """点击触发 fileChooserOpened → DOM.setFileInputFiles。"""
    from dp_cli.main import cli
    f = tmp_path / 'a.png'
    f.write_bytes(b'png')
    ele = FakeEle()
    page = FakePage(ele=ele)
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'resolve_locator', lambda l, s, p: l)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})

    # 点击后模拟事件回调
    orig_click = ele.click

    def click_then_fire():
        orig_click()
        cb = page.driver.callbacks.get('Page.fileChooserOpened')
        cb(backendNodeId=99, mode='selectSingle')

    ele.click = click_then_fire
    result = runner.invoke(
        cli, ['file-chooser', str(f), '--locator', 'css:.btn'])
    assert result.exit_code == 0
    kw = page.kwargs_of('DOM.setFileInputFiles')
    assert kw['backendNodeId'] == 99
    assert kw['files'] == [str(f)]
    # 拦截已关闭 + 回调已清
    assert page.kwargs_of('Page.setInterceptFileChooserDialog')['enabled'] is True
    assert page.driver.callbacks['Page.fileChooserOpened'] is None


@pytest.mark.unit
def test_file_chooser_missing_file(runner, monkeypatch):
    from dp_cli.main import cli
    result = runner.invoke(
        cli, ['file-chooser', '/nonexistent/x.png', '--locator', 'css:.b'])
    assert result.exit_code == 2
    assert _out(result)['code'] == 'FILE_NOT_FOUND'


@pytest.mark.unit
def test_file_chooser_timeout(runner, monkeypatch, tmp_path):
    """点击未触发对话框 → FILE_CHOOSER_FAILED。"""
    from dp_cli.main import cli
    f = tmp_path / 'a.png'
    f.write_bytes(b'png')
    page = FakePage(ele=FakeEle())
    monkeypatch.setattr(bmod, '_get_page', lambda s: page)
    monkeypatch.setattr(bmod, 'resolve_locator', lambda l, s, p: l)
    monkeypatch.setattr(bmod, 'load_session', lambda s: {})
    result = runner.invoke(
        cli, ['file-chooser', str(f), '--locator', 'css:.btn',
              '--timeout', '0'])
    assert result.exit_code == 1
    assert _out(result)['code'] == 'FILE_CHOOSER_FAILED'


# ─────────────────────────────────────────────────────────────────────────────
# _apply_browser_cdp 重放
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_apply_browser_cdp_replay():
    """_apply_browser_cdp：media+bypass_csp+throttle 一键重放。"""
    page = FakePage()
    bmod._apply_browser_cdp(page, {
        'media': {'media': 'print',
                  'features': {'prefers-color-scheme': 'dark'}},
        'bypass_csp': True,
        'throttle': {'network': {'latency': 100}, 'cpu': 2},
    })
    mkw = page.kwargs_of('Emulation.setEmulatedMedia')
    assert mkw['media'] == 'print'
    assert {'name': 'prefers-color-scheme', 'value': 'dark'} in mkw['features']
    assert page.kwargs_of('Page.setBypassCSP')['enabled'] is True
    assert page.kwargs_of('Network.emulateNetworkConditions')['latency'] == 100
    assert page.kwargs_of('Emulation.setCPUThrottlingRate')['rate'] == 2


@pytest.mark.unit
def test_get_page_replays_browser_cdp(monkeypatch):
    """_get_page 检测 sess['browser_cdp'] 时调用重放。"""
    calls = []
    monkeypatch.setattr(_utils, 'get_browser', lambda s: FakePage())
    monkeypatch.setattr(_utils, 'load_session',
                        lambda s: {'browser_cdp': {'bypass_csp': True}})
    monkeypatch.setattr(bmod, '_apply_browser_cdp',
                        lambda page, cfg: calls.append(cfg))
    _utils._get_page('default')
    assert calls == [{'bypass_csp': True}]


# ─────────────────────────────────────────────────────────────────────────────
# _cdp_eval 工具
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_cdp_eval_value_and_exception():
    page = FakePage()
    page._eval_responses['Runtime.evaluate'] = {'result': {'value': 42}}
    assert bmod._cdp_eval(page, 'x') == 42
    page._eval_responses['Runtime.evaluate'] = {
        'exceptionDetails': {'text': 'boom'}}
    with pytest.raises(RuntimeError):
        bmod._cdp_eval(page, 'x')
