# -*- coding:utf-8 -*-
"""浏览器环境模拟与网络控制命令: emulate / download / intercept

emulate   —— 经 CDP Emulation domain 覆盖 viewport/UA/locale/timezone/geolocation
download  —— 设置下载目录（DrissionPage DownloadManager → Browser.setDownloadBehavior）
             并等待下载任务完成
intercept —— Network domain 请求拦截基础版：URL 通配屏蔽 + 附加请求头

emulate/intercept 的配置写入 session，后续每条 dpx 命令经 _get_page 重放
（每个 dpx 命令是独立进程，CDP override 需在每条命令里重新应用才可靠）。
"""
import base64
import fnmatch
import json
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.output import ok, error, debug
from dp_cli.session import load_session, save_session
from dp_cli.commands._utils import session_option, _get_page


def _apply_emulate(page, cfg: dict) -> None:
    """按 cfg 应用 Emulation overrides（emulate 命令与 _get_page 重放共用）。

    :param cfg: {'viewport': (w, h), 'mobile': bool, 'ua': str, 'locale': str,
                 'timezone': str, 'geolocation': (lat, lon, acc)}
    """
    vp = cfg.get('viewport')
    if vp:
        w, h = vp
        # mobile 默认 True 保持既有行为；--no-mobile 可显式关闭（桌面视口）
        mobile = bool(cfg.get('mobile', True))
        page.run_cdp('Emulation.setDeviceMetricsOverride',
                     width=w, height=h,
                     deviceScaleFactor=cfg.get('dpr', 1), mobile=mobile)
    if 'touch' in cfg:
        # 显式 False 也要下发 disable——否则先移动后桌面设备切换时
        # touch override 残留，出现桌面 UA + maxTouchPoints>0 的矛盾指纹
        page.run_cdp('Emulation.setTouchEmulationEnabled',
                     enabled=bool(cfg['touch']), maxTouchPoints=5)
    tz = cfg.get('timezone')
    if tz:
        page.run_cdp('Emulation.setTimezoneOverride', timezoneId=tz)
    locale = cfg.get('locale')
    if locale:
        # LocaleOverride 控制 navigator.language；旧版 Chrome 无此域则忽略
        try:
            page.run_cdp('Emulation.setLocaleOverride', locale=locale)
        except Exception as e:
            debug('setLocaleOverride 不可用，仅回退到 Accept-Language', e)
    ua = cfg.get('ua')
    if ua or locale:
        kwargs = {}
        if ua:
            kwargs['userAgent'] = ua
        if locale:
            kwargs['acceptLanguage'] = locale
        if 'userAgent' not in kwargs:
            # setUserAgentOverride 要求 userAgent；取当前 UA 仅改 acceptLanguage
            kwargs['userAgent'] = page.run_js('navigator.userAgent')
        page.run_cdp('Emulation.setUserAgentOverride', **kwargs)
    geo = cfg.get('geolocation')
    if geo:
        lat, lon, acc = geo
        page.run_cdp('Emulation.setGeolocationOverride',
                     latitude=lat, longitude=lon, accuracy=acc)


def _apply_intercept(page, cfg: dict) -> None:
    """按 cfg 应用请求拦截（intercept 命令与 _get_page 重放共用）。

    :param cfg: {'block': [url 通配], 'headers': {k: v},
                 'mocks': [{'pattern','status','body','headers'}]}
    """
    page.run_cdp('Network.enable')
    page.run_cdp('Network.setBlockedURLs', urls=cfg.get('block') or [])
    page.run_cdp('Network.setExtraHTTPHeaders',
                 headers=cfg.get('headers') or {})
    mocks = cfg.get('mocks') or []
    allowed = cfg.get('allowed_domains') or []
    if mocks or allowed:
        patterns = [{'urlPattern': m['pattern'], 'requestStage': 'Request'}
                    for m in mocks]
        if allowed:
            # 白名单须拦全部请求才能逐条判定（mocks 只拦命中 pattern 的）
            patterns.append({'urlPattern': '*', 'requestStage': 'Request'})
        page.run_cdp('Fetch.enable', patterns=patterns)
        page.driver.set_callback(
            'Fetch.requestPaused',
            _make_paused_handler(page, mocks, allowed,
                                 cfg.get('allow_file', False)))
    else:
        # 无 mock/白名单规则时确保 Fetch 拦截关闭（重放 --off 后的干净状态）
        try:
            page.driver.set_callback('Fetch.requestPaused', None)
            page.run_cdp('Fetch.disable')
        except Exception:
            pass


# 页面运行必需的内部 scheme 白名单（无 host 概念，不构成出域风险）
_INTERNAL_SCHEMES = {'data', 'blob', 'about', 'chrome', 'edge',
                     'chrome-extension', 'moz-extension', 'devtools',
                     'javascript', 'view-source'}


def _host_allowed(url: str, allowed: list, allow_file: bool = False) -> bool:
    """URL host 是否命中白名单（后缀匹配：example.com 覆盖子域）。

    策略（fail-closed）：
    - 解析失败/无 host 的 http(s) 形态 → 拒绝（畸形 URL 是典型绕过面）
    - 内部 scheme（data:/blob:/about:/chrome-extension: 等）→ 放行（页面必需）
    - file:// → 默认拒绝（本地文件是高敏面），allow_file=True 放行
    - 其他未知 scheme（ftp:/custom-app: 等）→ 拒绝
    """
    from urllib.parse import urlparse
    try:
        parts = urlparse(url)
    except Exception:
        debug(f'白名单判定：URL 解析失败，拒绝 {url[:80]}')
        return False
    scheme = (parts.scheme or '').lower()
    if scheme in _INTERNAL_SCHEMES:
        return True
    if scheme == 'file':
        return allow_file
    if scheme not in ('http', 'https'):
        # ws/wss 等非 http 但受域名约束的也按 host 判定；无 host 拒绝
        if scheme in ('ws', 'wss'):
            pass  # 落到下方 host 判定
        elif not scheme:
            pass  # protocol-relative / 相对路径 → host 判定
        else:
            debug(f'白名单判定：未知 scheme={scheme}，拒绝 {url[:80]}')
            return False
    host = (parts.hostname or '').lower()
    if not host:
        # http(s)/ws(s)/相对形态但没有 host → 畸形，拒绝
        debug(f'白名单判定：无 host（{url[:80]}），拒绝')
        return False
    for d in allowed:
        d = d.lower().lstrip('*.')
        if host == d or host.endswith('.' + d):
            return True
    return False


def _make_paused_handler(page, mocks: list, allowed: list,
                         allow_file: bool = False):
    """统一 requestPaused 回调：白名单外 abort → mock fulfill → 放行。"""
    def _on_paused(**kw):
        rid = kw.get('requestId')
        url = (kw.get('request') or {}).get('url', '')
        if allowed and not _host_allowed(url, allowed, allow_file):
            try:
                page.run_cdp('Fetch.failRequest', requestId=rid,
                             errorReason='BlockedByClient')
            except Exception as e:
                debug(f'failRequest 失败 url={url}', e)
            return
        for m in mocks:
            if fnmatch.fnmatch(url, m.get('pattern', '*')):
                body = (m.get('body') or '').encode('utf-8')
                headers = [{'name': k, 'value': str(v)}
                           for k, v in (m.get('headers') or {}).items()]
                try:
                    page.run_cdp(
                        'Fetch.fulfillRequest', requestId=rid,
                        responseCode=int(m.get('status', 200)),
                        body=base64.b64encode(body).decode('ascii'),
                        responseHeaders=headers)
                except Exception as e:
                    debug(f'fulfillRequest 失败 url={url}', e)
                return
        try:
            page.run_cdp('Fetch.continueRequest', requestId=rid)
        except Exception as e:
            debug(f'continueRequest 失败 url={url}', e)
    return _on_paused


def _make_mock_handler(page, mocks: list):
    """生成 Fetch.requestPaused 回调：URL 匹配某条 mock 规则则 fulfillRequest，
    否则 continueRequest 放行。"""
    def _on_paused(**kw):
        rid = kw.get('requestId')
        url = (kw.get('request') or {}).get('url', '')
        for m in mocks:
            if fnmatch.fnmatch(url, m.get('pattern', '*')):
                body = (m.get('body') or '').encode('utf-8')
                headers = [{'name': k, 'value': str(v)}
                           for k, v in (m.get('headers') or {}).items()]
                try:
                    page.run_cdp(
                        'Fetch.fulfillRequest', requestId=rid,
                        responseCode=int(m.get('status', 200)),
                        body=base64.b64encode(body).decode('ascii'),
                        responseHeaders=headers)
                except Exception as e:
                    debug(f'fulfillRequest 失败 url={url}', e)
                return
        try:
            page.run_cdp('Fetch.continueRequest', requestId=rid)
        except Exception as e:
            debug(f'continueRequest 失败 url={url}', e)
    return _on_paused


def _clear_emulate(page) -> None:
    """清除 emulate 设置的全部 override（尽力而为，单项失败不阻塞）。"""
    for cmd, kwargs in (
        ('Emulation.clearDeviceMetricsOverride', {}),
        ('Emulation.clearGeolocationOverride', {}),
        ('Emulation.setTouchEmulationEnabled', {'enabled': False}),
        ('Emulation.setTimezoneOverride', {'timezoneId': ''}),
        ('Emulation.setLocaleOverride', {'locale': ''}),
        ('Emulation.setUserAgentOverride', {'userAgent': ''}),
    ):
        try:
            page.run_cdp(cmd, **kwargs)
        except Exception as e:
            debug(f'清除 {cmd} 失败', e)


# 设备预设：viewport + dpr + mobile + touch + UA 一键组合。
# UA 用 Chrome 147 基线，与 stealth.DEFAULT_UA 版本对齐。
DEVICE_PRESETS = {
    'iphone-15': {
        'viewport': (393, 852), 'dpr': 3, 'mobile': True, 'touch': True,
        'ua': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) '
              'AppleWebKit/605.1.15 (KHTML, like Gecko) '
              'CriOS/147.0.0.0 Mobile/15E148 Safari/604.1',
    },
    'iphone-se': {
        'viewport': (375, 667), 'dpr': 2, 'mobile': True, 'touch': True,
        'ua': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) '
              'AppleWebKit/605.1.15 (KHTML, like Gecko) '
              'CriOS/147.0.0.0 Mobile/15E148 Safari/604.1',
    },
    'pixel-8': {
        'viewport': (412, 915), 'dpr': 2.625, 'mobile': True, 'touch': True,
        'ua': 'Mozilla/5.0 (Linux; Android 15; Pixel 8) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/147.0.0.0 Mobile Safari/537.36',
    },
    'galaxy-s24': {
        'viewport': (360, 780), 'dpr': 3, 'mobile': True, 'touch': True,
        'ua': 'Mozilla/5.0 (Linux; Android 15; SM-S928B) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/147.0.0.0 Mobile Safari/537.36',
    },
    'ipad': {
        'viewport': (820, 1180), 'dpr': 2, 'mobile': True, 'touch': True,
        'ua': 'Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) '
              'AppleWebKit/605.1.15 (KHTML, like Gecko) '
              'CriOS/147.0.0.0 Mobile/15E148 Safari/604.1',
    },
    'desktop-chrome': {
        'viewport': (1920, 1080), 'dpr': 1, 'mobile': False, 'touch': False,
        'ua': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
    },
}


def register(cli):

    @cli.command('emulate')
    @session_option
    @click.option('--device', type=click.Choice(sorted(DEVICE_PRESETS)),
                  default=None,
                  help='设备预设（viewport+dpr+mobile+UA 一键组合）：'
                       + ', '.join(sorted(DEVICE_PRESETS)))
    @click.option('--viewport', default=None,
                  help='视口尺寸，如 375x812（默认启用 mobile metrics，可 --no-mobile 关闭）')
    @click.option('--mobile/--no-mobile', default=None,
                  help='配合 --viewport：是否启用 mobile metrics（默认沿用既有配置/启用）')
    @click.option('--ua', default=None, help='User-Agent 覆盖')
    @click.option('--locale', default=None, help='locale，如 zh-CN（navigator.language + Accept-Language）')
    @click.option('--timezone', default=None, help='时区 ID，如 Asia/Shanghai')
    @click.option('--geolocation', default=None, help='纬度,经度[,精度米]，如 39.9042,116.4074')
    @click.option('--reset', is_flag=True, help='清除全部 emulate 覆盖')
    def emulate(session, device, viewport, mobile, ua, locale, timezone,
                geolocation, reset):
        """模拟设备环境（设备预设/viewport/UA/locale/时区/地理位置）。

        设置写入会话，后续 dpx 命令自动重放；--reset 清除。

        \b
        示例:
          dpx emulate --device iphone-15      # 设备预设一键组合
          dpx emulate --device desktop-chrome
          dpx emulate --viewport 375x812 --ua "Mozilla/5.0 (iPhone...)"
          dpx emulate --viewport 1920x1080 --no-mobile   # 桌面视口
          dpx emulate --locale zh-CN --timezone Asia/Shanghai
          dpx emulate --geolocation 39.9042,116.4074
          dpx emulate --reset
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        try:
            if reset:
                _clear_emulate(page)
                sess.pop('emulate', None)
                save_session(session, sess)
                ok({'reset': True}, msg='emulate 覆盖已清除')
                return

            cfg = dict(sess.get('emulate') or {})
            applied = {}
            if device:
                for k, v in DEVICE_PRESETS[device].items():
                    cfg[k] = list(v) if isinstance(v, tuple) else v
                applied['device'] = device
            if viewport:
                parts = viewport.lower().split('x')
                if len(parts) != 2 or not all(p.isdigit() for p in parts):
                    error('--viewport 格式应为 WxH，如 375x812', code='INVALID_ARGS')
                cfg['viewport'] = (int(parts[0]), int(parts[1]))
                applied['viewport'] = viewport
            if mobile is not None:
                cfg['mobile'] = mobile
                applied['mobile'] = mobile
            if ua:
                cfg['ua'] = ua
                applied['ua'] = ua
            if locale:
                cfg['locale'] = locale
                applied['locale'] = locale
            if timezone:
                cfg['timezone'] = timezone
                applied['timezone'] = timezone
            if geolocation:
                parts = [p.strip() for p in geolocation.split(',')]
                if len(parts) not in (2, 3):
                    error('--geolocation 格式应为 纬度,经度[,精度]', code='INVALID_ARGS')
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                    acc = float(parts[2]) if len(parts) == 3 else 100.0
                except ValueError:
                    error('--geolocation 坐标必须是数字', code='INVALID_ARGS')
                cfg['geolocation'] = (lat, lon, acc)
                applied['geolocation'] = geolocation

            if not applied:
                ok({'emulate': cfg or None},
                   msg='当前 emulate 配置（未改动）' if cfg else '当前无 emulate 配置')
                return

            _apply_emulate(page, cfg)
            sess['emulate'] = cfg
            save_session(session, sess)
            ok({'applied': applied, 'emulate': cfg}, msg='emulate 已应用并写入会话')
        except Exception as e:
            error('emulate 设置失败', code='EMULATE_FAILED', detail=str(e))

    @cli.command('download')
    @session_option
    @click.option('--dir', 'download_dir', default=None,
                  help='下载目录（不传则仅等待/查看当前任务）')
    @click.option('--timeout', default=30, help='等待下载完成秒数', show_default=True)
    @click.option('--exists', 'when_exists', default=None,
                  type=click.Choice(['rename', 'overwrite', 'skip']),
                  help='同名文件处理策略')
    def download(session, download_dir, timeout, when_exists):
        """设置下载目录并等待下载完成。

        先触发下载的操作（如 dpx click），再执行本命令；或本命令先运行、
        随后在 --timeout 窗口内产生下载。依赖 DrissionPage DownloadManager
        （Browser.setDownloadBehavior + downloadProgress 事件）。

        \b
        示例:
          dpx download --dir ~/Downloads --timeout 60
          dpx download --dir ./out --exists overwrite
        """
        page = _get_page(session)
        try:
            if download_dir:
                Path(download_dir).mkdir(parents=True, exist_ok=True)
                page.set.download_path(download_dir)
                # 持久化到 session——跨进程 wait --download 据此前置判定
                # 并在新进程里重新武装 _dl_mgr（进程退出回调即失）
                sess = load_session(session) or {}
                sess['download_dir'] = str(Path(download_dir).resolve())
                save_session(session, sess)
            if when_exists:
                page.set.when_download_file_exists(when_exists)

            # _dl_mgr 是 DrissionPage 私有 API，版本间可能缺失，做防御性回退
            mgr = getattr(getattr(page, 'browser', None), '_dl_mgr', None)
            missions = []
            if mgr is not None:
                missions = list(getattr(mgr, 'missions', {}).values())
                end = perf_counter() + timeout
                while perf_counter() < end:
                    missions = list(getattr(mgr, 'missions', {}).values())
                    if missions and all(getattr(m, 'is_done', True) for m in missions):
                        break
                    _sleep(0.2)

            completed = [{
                'name': getattr(m, 'name', None),
                'url': getattr(m, 'url', None),
                'state': getattr(m, 'state', None),
                'path': getattr(m, 'final_path', None) or str(getattr(m, 'folder', '') or ''),
            } for m in missions]
            result = {
                'download_dir': download_dir or str(getattr(page, 'download_path', '')),
                'missions': completed,
            }
            if missions and not all(getattr(m, 'is_done', True) for m in missions):
                result['warning'] = f'{timeout}s 内下载未完成'
            elif not missions:
                result['note'] = ('等待窗口内未检测到下载任务' if mgr is not None
                                  else '当前 DrissionPage 无 _dl_mgr，无法跟踪下载任务')
            ok(result)
        except Exception as e:
            error('下载设置/等待失败', code='DOWNLOAD_FAILED', detail=str(e))

    @cli.command('intercept')
    @session_option
    @click.option('--block', 'blocks', multiple=True,
                  help='屏蔽 URL 通配模式（可多次），如 "*.png"、"*ads*"')
    @click.option('--header', 'headers', multiple=True,
                  help='附加请求头 k=v（可多次），如 "X-Token=abc"')
    @click.option('--mock', 'mocks', multiple=True,
                  help='新增 mock 规则：匹配 URL 通配的请求直接返回 --mock-body '
                       '（可多次，与本次 --mock-status/--mock-body/--mock-header 组合成规则）')
    @click.option('--mock-status', type=int, default=200, show_default=True,
                  help='mock 响应状态码')
    @click.option('--mock-body', default=None,
                  help='mock 响应体文本，如 \'{"code":0}\'')
    @click.option('--mock-header', 'mock_headers', multiple=True,
                  help='mock 响应头 k=v（可多次）')
    @click.option('--mock-clear', is_flag=True, help='仅清空 mock 规则')
    @click.option('--allow-domain', 'allow_domains', multiple=True,
                  help='域名白名单（可多次）——设置后非白名单请求一律 abort'
                       '（安全护栏：防页面把 agent 带离可信域）')
    @click.option('--allow-file', is_flag=True,
                  help='白名单生效时放行 file:// 本地文件（默认拒绝）')
    @click.option('--allow-clear', is_flag=True, help='清空域名白名单')
    @click.option('--off', is_flag=True,
                  help='关闭拦截：清空 block/headers/mock 并 Fetch.disable')
    def intercept(session, blocks, headers, mocks, mock_status, mock_body,
                  mock_headers, mock_clear, allow_domains, allow_file,
                  allow_clear, off):
        """请求拦截：屏蔽 URL 通配 / 附加请求头 / mock 响应（Fetch domain）。

        设置写入会话，后续 dpx 命令自动重放。mock 规则经 Fetch.enable
        在请求阶段拦截并 fulfillRequest，与 --block/--header 并存。

        \b
        示例:
          dpx intercept --block "*.png" --block "*google-analytics*"
          dpx intercept --header "Authorization=Bearer xxx"
          dpx intercept --mock "*api/user*" --mock-body '{"id":1}'
          dpx intercept --mock "*api/list*" --mock-status 404 --mock-body "nf"
          dpx intercept --mock-clear
          dpx intercept --off
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        try:
            if off:
                _apply_intercept(
                    page, {'block': [], 'headers': {}, 'mocks': []})
                sess.pop('intercept', None)
                save_session(session, sess)
                ok({'intercept': None}, msg='拦截已关闭')
                return

            cfg = dict(sess.get('intercept') or {})
            cfg.setdefault('block', [])
            cfg.setdefault('headers', {})
            cfg.setdefault('mocks', [])
            cfg.setdefault('allowed_domains', [])

            if mock_clear:
                cfg['mocks'] = []
            if allow_clear:
                cfg['allowed_domains'] = []
                cfg.pop('allow_file', None)
            for d in allow_domains:
                d = d.strip().lower().lstrip('*.')
                if d and d not in cfg['allowed_domains']:
                    cfg['allowed_domains'].append(d)
            if allow_file:
                cfg['allow_file'] = True

            # --mock-status/--mock-body/--mock-header 须配合 --mock 使用
            mock_extras = (mock_body is not None or mock_headers
                           or mock_status != 200)
            if mock_extras and not mocks:
                error('--mock-status/--mock-body/--mock-header '
                      '需与 --mock 同时使用', code='INVALID_ARGS')
                return

            mh = {}
            for h in mock_headers:
                if '=' not in h:
                    error(f'--mock-header 格式应为 k=v: {h}',
                          code='INVALID_ARGS')
                    return
                k, v = h.split('=', 1)
                mh[k.strip()] = v
            for p in mocks:
                cfg['mocks'].append({
                    'pattern': p, 'status': mock_status,
                    'body': mock_body or '', 'headers': dict(mh)})

            for p in blocks:
                if p not in cfg['block']:
                    cfg['block'].append(p)
            for h in headers:
                if '=' not in h:
                    error(f'--header 格式应为 k=v: {h}', code='INVALID_ARGS')
                    return
                k, v = h.split('=', 1)
                cfg['headers'][k.strip()] = v

            changed = bool(blocks or headers or mocks or mock_clear
                           or allow_domains or allow_file or allow_clear)
            if not changed:
                has_cfg = (cfg['block'] or cfg['headers'] or cfg['mocks']
                           or cfg['allowed_domains'])
                ok({'intercept': cfg if has_cfg else None},
                   msg='当前拦截配置（未改动）' if has_cfg else '当前无拦截配置')
                return

            _apply_intercept(page, cfg)
            sess['intercept'] = cfg
            save_session(session, sess)
            ok({'intercept': cfg},
               msg=f"已拦截 {len(cfg['block'])} 个模式，"
                   f"附加 {len(cfg['headers'])} 个请求头，"
                   f"mock {len(cfg['mocks'])} 条规则，"
                   f"白名单 {len(cfg['allowed_domains'])} 域")
        except Exception as e:
            error('拦截设置失败', code='INTERCEPT_FAILED', detail=str(e))
