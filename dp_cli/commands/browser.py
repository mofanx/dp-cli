# -*- coding:utf-8 -*-
"""浏览器生命周期与 CDP 原生能力命令:

open / goto / reload / go-back / go-forward / close / close-all / list /
delete-data / stealth / permissions / clipboard-get / clipboard-set /
media / bypass-csp / throttle / file-chooser

media / bypass-csp / throttle 的配置写入 session['browser_cdp']，
由 _get_page 每条命令重放（每个 dpx 进程是独立 CDP session，必须重放）。
"""
import json
import sys
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.session import (get_browser, close_browser, list_sessions,
                            delete_session, load_session, save_session,
                            discover_port_from_profile,
                            default_user_data_dir_for_channel,
                            sniff_auto_user_data_dir)
from dp_cli.output import ok, error, format_page_info, debug
from dp_cli.commands._utils import (session_option, _get_page, normalize_url,
                                    resolve_locator, CdpWaitGate)
from dp_cli.stealth import apply_stealth, stealth_check, PRESETS, DEFAULT_UA


# permissions 命令支持的权限名（CDP Browser.grantPermissions 的 descriptor name）
PERMISSION_NAMES = {
    'geolocation', 'notifications', 'clipboard-read', 'clipboard-write',
    'midi', 'midi-sysex', 'camera', 'microphone', 'background-sync',
    'persistent-storage', 'local-fonts', 'idle-detection', 'sensors',
    'accelerometer', 'gyroscope', 'magnetometer', 'accessibility-events',
    'audio-capture', 'video-capture', 'payment-handler', 'clipboard-sanitized-write',
}

# throttle 网络预设：latency(ms), downloadThroughput(B/s), uploadThroughput(B/s)
THROTTLE_PROFILES = {
    'offline':  {'offline': True,  'latency': 0,   'downloadThroughput': 0,      'uploadThroughput': 0},
    'slow-3g':  {'offline': False, 'latency': 400, 'downloadThroughput': 50 * 1024,   'uploadThroughput': 50 * 1024},
    'fast-3g':  {'offline': False, 'latency': 150, 'downloadThroughput': 200 * 1024,  'uploadThroughput': 96 * 1024},
    '4g':       {'offline': False, 'latency': 60,  'downloadThroughput': 4 * 1024 * 1024, 'uploadThroughput': 3 * 1024 * 1024},
}


def _origin_from_url(url: str) -> str | None:
    """从 URL 提取 origin（scheme://host[:port]）；非 http(s) 返回 None。"""
    from urllib.parse import urlsplit
    try:
        p = urlsplit(url or '')
    except Exception:
        return None
    if p.scheme not in ('http', 'https') or not p.netloc:
        return None
    return f'{p.scheme}://{p.netloc}'


def _cdp_eval(page, expression: str):
    """Runtime.evaluate + awaitPromise + returnByValue，返回 JS 值。

    页面级 CDP session 可调 Browser.* / Emulation.* / Page.* 域方法。
    JS 侧抛异常时抛 RuntimeError（exceptionDetails）。
    """
    res = page.run_cdp('Runtime.evaluate', expression=expression,
                       awaitPromise=True, returnByValue=True)
    if isinstance(res, dict) and res.get('exceptionDetails'):
        ed = res['exceptionDetails']
        txt = ((ed.get('exception') or {}).get('description')
               or ed.get('text') or 'JS 异常')
        raise RuntimeError(txt)
    if isinstance(res, dict):
        return (res.get('result') or {}).get('value')
    return None


def _apply_browser_cdp(page, cfg: dict) -> None:
    """重放 media / bypass_csp / throttle 会话级 CDP 设置。

    :param cfg: sess['browser_cdp'] ——
        {'media': {'media': str, 'features': {name: value}},
         'bypass_csp': bool,
         'throttle': {'network': {...} | None, 'cpu': int}}
    """
    media = cfg.get('media')
    if media is not None:
        kwargs = {}
        if media.get('media'):
            kwargs['media'] = media['media']
        feats = media.get('features')
        if feats:
            kwargs['features'] = [{'name': k, 'value': v} for k, v in feats.items()]
        page.run_cdp('Emulation.setEmulatedMedia', **kwargs)

    if cfg.get('bypass_csp'):
        try:
            page.run_cdp('Page.enable')
        except Exception as e:
            debug('bypass-csp 重放 Page.enable 失败', e)
        page.run_cdp('Page.setBypassCSP', enabled=True)

    throttle = cfg.get('throttle') or {}
    net = throttle.get('network')
    if net:
        try:
            page.run_cdp('Network.enable')
        except Exception as e:
            debug('throttle 重放 Network.enable 失败', e)
        page.run_cdp('Network.emulateNetworkConditions', **net)
    cpu = throttle.get('cpu')
    if cpu:
        page.run_cdp('Emulation.setCPUThrottlingRate', rate=cpu)


def register(cli):
    @cli.group('bridge')
    def bridge_grp():
        """bridge 子进程管理（auto-connect/inspect 模式的代理）。"""
        pass

    @bridge_grp.command('list')
    def bridge_list():
        """列出全部注册 bridge（跨 session 共享的代理进程）。"""
        from dp_cli.bridge_registry import (iter_entries,
                                            _default_validate, _bridges_dir)
        entries = iter_entries()
        out, pruned = [], 0
        for e in entries:
            alive = _default_validate(e, e.get('real_port'))
            if not alive:
                # 死件就地清——注册表不堆积 stale（find_bridge 同款语义）
                try:
                    (_bridges_dir() / e['_file']).unlink()
                    pruned += 1
                except OSError:
                    pass
                continue
            out.append({'pid': e.get('pid'), 'port': e.get('port'),
                        'real_port': e.get('real_port'),
                        'user_data_dir': e.get('user_data_dir'),
                        'sessions': e.get('sessions', [])})
        ok({'count': len(out), 'pruned': pruned, 'bridges': out})

    @bridge_grp.command('stop')
    @click.option('--pid', type=int, default=None, help='按 pid 停（默认停全部注册项）')
    @click.option('--user-data-dir', default=None, help='按 user-data-dir 停')
    def bridge_stop(pid, user_data_dir):
        """停止注册的 bridge 子进程。"""
        from dp_cli.bridge_registry import iter_entries, unregister_bridge
        from dp_cli.bridge_manager import stop_bridge as _stop
        stopped = []
        for e in iter_entries():
            if pid is not None and e.get('pid') != pid:
                continue
            if user_data_dir and e.get('user_data_dir') != user_data_dir:
                continue
            try:
                _stop(int(e['pid']))
                unregister_bridge(e['user_data_dir'])
                stopped.append({'pid': e['pid'], 'port': e.get('port')})
            except Exception as ex:
                debug(f'stop bridge pid={e.get("pid")} 失败', ex)
        ok({'stopped': stopped, 'count': len(stopped)})


    @cli.command('open')
    @click.argument('url', required=False)
    @session_option
    @click.option('--headless', is_flag=True, help='无头模式')
    @click.option('--browser', 'browser_path', default=None, help='浏览器可执行文件路径')
    @click.option('--profile', 'user_data_dir', default=None, help='用户数据目录')
    @click.option('--proxy', default=None, help='代理服务器，如 http://127.0.0.1:7890')
    @click.option('--port', type=int, default=None, help='连接指定端口的已有浏览器实例')
    @click.option('--auto-connect', '-a', is_flag=True,
                  help='从用户常规启动的 Chrome/Edge 读取 DevToolsActivePort 自动发现端口'
                       '（需 Chrome/Edge 144+，用户在 chrome://inspect/#remote-debugging 或 '
                       'edge://inspect/#devices 启用）')
    @click.option('--channel',
                  type=click.Choice(['auto', 'stable', 'beta', 'dev', 'canary',
                                     'chromium', 'edge', 'edge-beta', 'edge-dev',
                                     'edge-canary']),
                  default='auto', show_default=True,
                  help='配合 --auto-connect 使用，定位默认 user-data-dir；'
                       'auto = 嗅探全部已装渠道（stable→edge→beta→dev→canary），'
                       '取首个含 DevToolsActivePort 的目录')
    @click.option('--probe-dir', 'probe_dir', default=None,
                  help='配合 --auto-connect 使用，显式指定要探测的 user-data-dir '
                       '（覆盖 --channel 的默认路径）')
    @click.option('--new', is_flag=True, help='强制创建新实例（不复用已有会话）')
    @click.option('--stealth', is_flag=True, help='连接后自动启用反自动化检测补丁（full 预设）')
    def cmd_open(url, session, headless, browser_path, user_data_dir, proxy, port,
                 auto_connect, channel, probe_dir, new, stealth):
        """打开浏览器并可选导航到 URL。

        \b
        【复用用户自己的浏览器 - 方式 A：--remote-debugging-port 启动】
        第一步：用调试模式启动你自己的 Chrome/Chromium：
          google-chrome --remote-debugging-port=9222
        第二步：用 dpx 接管：
          dpx open --port 9222

        \b
        【复用用户自己的浏览器 - 方式 B：--auto-connect（Chrome/Edge 144+ 推荐）】
        无需特殊启动参数，正常打开 Chrome 或 Edge 即可：
          1. 打开浏览器：
             - Chrome：访问 chrome://inspect/#remote-debugging
             - Edge:   访问 edge://inspect/#devices
          2. 勾选 "Allow remote debugging for this browser instance"
          3. dpx open --auto-connect            # 默认 auto：嗅探全部已装渠道
             dpx open --auto-connect --channel edge   # 强制使用 Edge stable
             dpx open --auto-connect --channel edge-dev  # 使用 Edge dev
             dpx open --auto-connect --channel beta   # 使用 Chrome beta
             dpx open --auto-connect --probe-dir ~/my-profile   # 自定义 profile

        \b
        【dpx 自动管理浏览器】
          dpx open
          dpx open https://example.com
          dpx open https://example.com --headless
          dpx -s work open https://github.com

        \b
        【连接远程 headless VPS Chrome 并反检测】
          VPS:   google-chrome --headless=new --remote-debugging-port=9222 \\
                   --no-sandbox --disable-dev-shm-usage \\
                   --disable-blink-features=AutomationControlled \\
                   --user-data-dir=~/.config/google-chrome
          本地:  ssh -NL 9322:127.0.0.1:9222 vps-host
                 dpx open --port 9322 --stealth
        """
        if new:
            delete_session(session)

        # --auto-connect：走 probe_dir 路径，get_browser 会自动检测 inspect
        # 模式并起 bridge 代理（chrome://inspect 模式无 HTTP REST API）
        if auto_connect:
            if port:
                error('--auto-connect 不能和 --port 同时使用',
                      code='CONFLICTING_OPTIONS')
                return
            if probe_dir:
                dir_to_probe = Path(probe_dir).expanduser()
            elif channel == 'auto':
                # 自动嗅探：按 _AUTO_SNIFF_CHANNELS 顺序，取首个含
                # DevToolsActivePort 且端口可连的目录
                hit, diag = sniff_auto_user_data_dir()
                if hit is None:
                    detail = '\n'.join(f'  - {ch:7s} {p}  [{reason}]'
                                       for ch, p, reason in diag)
                    error('自动嗅探未找到含 DevToolsActivePort 的 user-data-dir。\n'
                          '请先在 Chrome (chrome://inspect/#remote-debugging) 或 '
                          'Edge (edge://inspect/#devices) 启用远程调试，\n'
                          '或用 --channel/--probe-dir 显式指定。\n'
                          f'嗅探记录：\n{detail}',
                          code='PROFILE_NOT_FOUND')
                    return
                dir_to_probe = hit
            else:
                dir_to_probe = default_user_data_dir_for_channel(channel)
                if not dir_to_probe:
                    error(f'未找到 {channel} 渠道的默认 user-data-dir，'
                          f'请用 --probe-dir 显式指定',
                          code='PROFILE_NOT_FOUND')
                    return
            # 提前验证能读到 DevToolsActivePort，给用户清晰错误
            try:
                discover_port_from_profile(dir_to_probe)
            except (FileNotFoundError, ValueError) as e:
                error('自动发现端口失败', code='AUTOCONNECT_FAILED', detail=str(e))
                return
            # 写入 session，让 get_browser 走 probe_dir 分支
            sess = load_session(session) or {}
            sess['probe_dir'] = str(dir_to_probe)
            sess['auto_connect'] = True
            sess['user_connected'] = True
            save_session(session, sess)

        try:
            page = get_browser(session, headless=headless, browser_path=browser_path,
                               user_data_dir=user_data_dir, proxy=proxy, port=port)
        except Exception as e:
            error(f'启动浏览器失败: {e}', code='BROWSER_START_FAILED', detail=str(e))
            return

        stealth_info = None
        if stealth:
            try:
                stealth_info = apply_stealth(page, features=PRESETS['full'])
                # 记录到 session，使 _get_page 在后续每次 dpx 命令自动重注册
                # （CDP init_js 绑定到 CDP session，每个 dpx 命令需要重新注册）
                sess = load_session(session) or {}
                sess['stealth'] = {
                    'preset': 'full',
                    'features': sorted(PRESETS['full']),
                }
                save_session(session, sess)
            except Exception as e:
                error(f'应用 stealth 失败: {e}', code='STEALTH_FAILED', detail=str(e))
                return

        if url:
            _icfg = (load_session(session) or {}).get('intercept') or {}
            _allowed = _icfg.get('allowed_domains') or []
            if _allowed:
                from dp_cli.commands.emulate import _host_allowed
                if not _host_allowed(normalize_url(url), _allowed,
                                     _icfg.get('allow_file', False)):
                    error(f'目标域名不在白名单: {url}',
                          code='FORBIDDEN_DOMAIN',
                          detail=f'allowed={_allowed}')
                    return
            try:
                page.get(normalize_url(url))
            except Exception as e:
                error(f'导航失败: {e}', code='NAVIGATE_FAILED', detail=str(e))
                return

        info = format_page_info(page)
        if stealth_info:
            info['stealth'] = stealth_info
        ok(info, msg='浏览器已就绪' + ('（已启用 stealth）' if stealth else ''))

    @cli.command()
    @click.argument('url')
    @session_option
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    @click.option('--retry', default=3, help='重试次数', show_default=True)
    def goto(url, session, timeout, retry):
        """导航到指定 URL。

        \b
        示例:
          dpx goto https://example.com
          dpx goto example.com
          dpx goto example.com --timeout 60
        """
        page = _get_page(session)
        _icfg = (load_session(session) or {}).get('intercept') or {}
        _allowed = _icfg.get('allowed_domains') or []
        if _allowed:
            from dp_cli.commands.emulate import _host_allowed
            if not _host_allowed(normalize_url(url), _allowed,
                                 _icfg.get('allow_file', False)):
                error(f'目标域名不在白名单: {url}', code='FORBIDDEN_DOMAIN',
                      detail=f'allowed={_allowed}')
                return
        try:
            page.get(normalize_url(url), timeout=timeout, retry=retry)
            ok(format_page_info(page))
        except Exception as e:
            error(f'导航到 {url} 失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command()
    @session_option
    def reload(session):
        """刷新当前页面。"""
        page = _get_page(session)
        try:
            page.get(page.url)
            ok(format_page_info(page))
        except Exception as e:
            error(f'刷新失败', code='RELOAD_FAILED', detail=str(e))

    @cli.command('go-back')
    @session_option
    def go_back(session):
        """浏览器后退。"""
        page = _get_page(session)
        try:
            page.back()
            ok(format_page_info(page))
        except Exception as e:
            error('后退失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command('go-forward')
    @session_option
    def go_forward(session):
        """浏览器前进。"""
        page = _get_page(session)
        try:
            page.forward()
            ok(format_page_info(page))
        except Exception as e:
            error('前进失败', code='NAVIGATE_FAILED', detail=str(e))

    @cli.command('close')
    @session_option
    @click.option('--del-data', is_flag=True, help='同时删除用户数据目录')
    @click.option('--force', is_flag=True, help='强制关闭浏览器（user_connected 模式下默认只断开连接）')
    @click.option('--stop-bridge', is_flag=True,
                  help='同时停止 bridge 子进程（默认保留供其他 session 复用）')
    def cmd_close(session, del_data, force, stop_bridge):
        """关闭浏览器会话。

        如果是通过 --port 连接的用户自己的浏览器，默认只断开连接不关闭浏览器。
        用 --force 才会真正关闭浏览器进程。
        """
        sess = load_session(session)
        if not sess:
            error(f'会话 [{session}] 不存在', code='SESSION_NOT_FOUND')
            return
        user_connected = sess.get('user_connected', False)
        auto_connect = sess.get('auto_connect', False)
        if user_connected and not force:
            # auto-connect/--port 默认只断开（不 quit 用户的 Chrome）；
            # close_browser 会同时停 bridge 子进程（若有）
            result = close_browser(session, stop_bridge_flag=stop_bridge)
            if result:
                extra = ('，bridge 已停' if (auto_connect and stop_bridge)
                         else '，bridge 保留（可复用）' if auto_connect else '')
                ok(msg=f'已断开与 [{session}] 的连接（浏览器仍运行{extra}）。'
                       f'用 --force 可尝试彻底关闭浏览器。')
            else:
                error('断开失败', code='CLOSE_FAILED')
        else:
            result = close_browser(session, del_data=del_data, force=force,
                                   stop_bridge_flag=stop_bridge)
            if result:
                ok(msg=f'会话 [{session}] 已关闭')
            else:
                error(f'关闭失败', code='CLOSE_FAILED')

    @cli.command('close-all')
    def close_all():
        """关闭所有会话。"""
        sessions = list_sessions()
        closed = []
        for s in sessions:
            close_browser(s['name'])
            closed.append(s['name'])
        ok({'closed': closed}, msg=f'已关闭 {len(closed)} 个会话')

    @cli.command('kill-all')
    def kill_all():
        """强制清理：关闭全部会话 + 停掉全部 bridge + 强杀 dpx 自管的
        残留浏览器进程（user-data-dir 在本工具管理的 profiles/autoPortData
        下；用户自己的浏览器/bridge 之外的进程不受影响）。"""
        import os, signal, subprocess
        from dp_cli.session import get_session_dir
        from dp_cli.bridge_registry import iter_entries, unregister_bridge
        from dp_cli.bridge_manager import stop_bridge

        result = {'sessions_closed': [], 'bridges_stopped': 0,
                  'procs_killed': 0}

        # 1) 会话优雅关闭
        for s in list_sessions():
            try:
                close_browser(s['name'], force=True)
                result['sessions_closed'].append(s['name'])
            except Exception as e:
                debug(f"kill-all close {s['name']} 失败", e)

        # 2) 停全部注册 bridge
        for e in iter_entries():
            try:
                stop_bridge(int(e['pid']))
                unregister_bridge(e['user_data_dir'])
                result['bridges_stopped'] += 1
            except Exception as ex:
                debug(f"kill-all stop bridge pid={e.get('pid')} 失败", ex)

        # 3) 强杀 dpx 自管的残留浏览器进程
        #    特征：cmdline 含 --user-data-dir=<sessions/profiles|autoPortData>
        if sys.platform.startswith('win'):
            # pgrep+/proc+SIGKILL 是 POSIX 语义——Windows 走 bridge
            # registry 的 taskkill 路径（上面已覆盖），进程扫描跳过
            debug('kill-all 进程扫描暂不支持 Windows')
            result['procs_skipped'] = 'platform'
            ok(result, msg='kill-all 完成')
            return
        profiles_root = str(get_session_dir() / 'profiles')
        markers = (profiles_root, 'DrissionPage/autoPortData',
                   'DrissionPage/userData')
        try:
            out = subprocess.run(
                ['pgrep', '-f', 'user-data-dir'],
                capture_output=True, text=True, timeout=5).stdout.split()
            for pid_s in out:
                try:
                    pid = int(pid_s)
                    if pid == os.getpid():
                        continue
                    cmdline = open(f'/proc/{pid}/cmdline', 'rb').read()
                    if any(m.encode() in cmdline for m in markers):
                        os.kill(pid, signal.SIGKILL)
                        result['procs_killed'] += 1
                except Exception:
                    continue
        except Exception as e:
            debug('kill-all 进程扫描失败', e)

        ok(result, msg='kill-all 完成')

    @cli.command('list')
    def cmd_list():
        """列出所有活跃会话。"""
        sessions = list_sessions()
        ok({'sessions': sessions, 'count': len(sessions)})

    @cli.command('delete-data')
    @session_option
    def delete_data(session):
        """删除会话的用户数据目录。"""
        close_browser(session, del_data=True)
        ok(msg=f'会话 [{session}] 数据已删除')

    @cli.command('stealth')
    @session_option
    @click.option('--preset', type=click.Choice(['mild', 'full']), default='full',
                  show_default=True, help='mild=仅 webdriver+UA+window_dims；full=全套')
    @click.option('--ua', default=None,
                  help=f'自定义 User-Agent（默认去掉 Headless: {DEFAULT_UA[:60]}...）')
    @click.option('--langs', default=None,
                  help='navigator.languages，逗号分隔，如 "zh-CN,zh,en"')
    @click.option('--webgl-vendor', default=None, help='伪造的 WebGL VENDOR')
    @click.option('--webgl-renderer', default=None, help='伪造的 WebGL RENDERER')
    @click.option('--feature', multiple=True,
                  type=click.Choice(['webdriver', 'ua', 'chrome_runtime',
                                     'permissions', 'plugins', 'languages',
                                     'webgl', 'window_dims']),
                  help='精细指定要启用的单个特性（可多次）。指定后覆盖 --preset')
    @click.option('--check', is_flag=True,
                  help='只读指纹一致性自检：采集 webdriver/UA/platform/timezone/WebGL '
                       '等信号并输出报告，不打补丁')
    def cmd_stealth(session, preset, ua, langs, webgl_vendor, webgl_renderer, feature,
                    check):
        """对当前会话施加反自动化检测补丁。

        \b
        典型用法:
          dpx stealth                         # full 预设（推荐）
          dpx stealth --preset mild           # 最小改动
          dpx stealth --ua "Mozilla/5.0 ..."  # 自定义 UA
          dpx stealth --feature webdriver --feature plugins  # 只开部分

        \b
        修补的特征（full）:
          webdriver / UA / chrome.runtime / permissions / plugins
          languages / WebGL VENDOR&RENDERER / window.outerWidth&Height

        \b
        注意:
          - 补丁通过 Page.addScriptToEvaluateOnNewDocument 注入，对当前页和
            所有后续导航生效；无需重复执行
          - 部分 VPS 启动参数也强烈建议配合:
              --disable-blink-features=AutomationControlled
          - 高级指纹检测（Canvas/Audio/字体）本命令不覆盖，需真实 GPU/Xvfb 环境
        """
        page = _get_page(session)
        if check:
            try:
                ok(stealth_check(page))
            except Exception as e:
                error('stealth 自检失败', code='STEALTH_CHECK_FAILED',
                      detail=str(e))
            return
        features = set(feature) if feature else PRESETS[preset]
        langs_list = [s.strip() for s in langs.split(',')] if langs else None
        try:
            info = apply_stealth(
                page, features=features, ua=ua, langs=langs_list,
                webgl_vendor=webgl_vendor, webgl_renderer=webgl_renderer,
            )
            # 保存到 session，使 _get_page 在后续每个 dpx 命令自动重注册
            # （CDP init_js 绑定到 CDP session，每个 dpx 命令需要重新注册）
            sess = load_session(session) or {}
            cfg = {
                'preset': preset if not feature else 'custom',
                'features': sorted(features),
            }
            if ua:
                cfg['ua'] = ua
            if langs_list:
                cfg['langs'] = langs_list
            if webgl_vendor:
                cfg['webgl_vendor'] = webgl_vendor
            if webgl_renderer:
                cfg['webgl_renderer'] = webgl_renderer
            sess['stealth'] = cfg
            save_session(session, sess)
            ok(info, msg=f'stealth 已启用（{len(features)} 个特性）')
        except Exception as e:
            error(f'应用 stealth 失败', code='STEALTH_FAILED', detail=str(e))

    @cli.command('permissions')
    @session_option
    @click.option('--grant', 'grants', multiple=True,
                  help='授权权限名（可多次/逗号分隔），如 geolocation,notifications；'
                       '--list 查看全部支持名')
    @click.option('--origin', default=None,
                  help='授权作用的 origin（默认取当前页面 URL 的 origin）')
    @click.option('--reset', is_flag=True, help='重置全部已授权限')
    @click.option('--list', 'list_names', is_flag=True, help='列出支持的权限名')
    def cmd_permissions(session, grants, origin, reset, list_names):
        """浏览器权限授权（CDP Browser.grantPermissions / resetPermissions）。

        \b
        示例:
          dpx permissions --list
          dpx permissions --grant geolocation,notifications
          dpx permissions --grant clipboard-read --origin https://example.com
          dpx permissions --reset
        """
        if list_names:
            ok({'permissions': sorted(PERMISSION_NAMES)})
            return
        names = []
        for g in grants:
            names.extend(s.strip() for s in g.split(',') if s.strip())
        if not reset and not names:
            error('未指定操作：用 --grant <名> 授权或 --reset 重置',
                  code='INVALID_ARGS')
            return
        unknown = [n for n in names if n not in PERMISSION_NAMES]
        if unknown:
            error(f'未知权限名: {", ".join(unknown)}；用 --list 查看支持的权限',
                  code='INVALID_ARGS')
            return
        page = _get_page(session)
        try:
            if reset:
                page.run_cdp('Browser.resetPermissions')
                ok({'reset': True}, msg='已重置全部权限')
                return
            if origin is None:
                origin = _origin_from_url(getattr(page, 'url', ''))
                if not origin:
                    error('当前页非 http(s)，无法推导 origin，请用 --origin 指定',
                          code='INVALID_ARGS')
                    return
            page.run_cdp('Browser.grantPermissions',
                         origin=origin, permissions=names)
            ok({'origin': origin, 'granted': names},
               msg=f'已授权 {len(names)} 项权限（origin={origin}）')
        except Exception as e:
            error('权限操作失败', code='PERMISSIONS_FAILED', detail=str(e))

    def _grant_clipboard_perm(page, perm: str):
        """尽力授予剪贴板权限；失败仅 debug 记录（部分站点仍可用）。"""
        origin = _origin_from_url(getattr(page, 'url', ''))
        try:
            kwargs = {'permission': {'name': perm},
                      'setting': 'granted'}
            if origin:
                kwargs['origin'] = origin
            page.run_cdp('Browser.setPermission', **kwargs)
        except Exception as e:
            debug(f'Browser.setPermission({perm}) 失败（继续尝试 JS API）', e)

    @cli.command('clipboard-get')
    @session_option
    def clipboard_get(session):
        """读取系统剪贴板文本（navigator.clipboard.readText + 权限授予）。

        \b
        示例:
          dpx clipboard-get
        """
        page = _get_page(session)
        try:
            _grant_clipboard_perm(page, 'clipboard-read')
            text = _cdp_eval(page, 'navigator.clipboard.readText()')
            ok({'text': text})
        except Exception as e:
            error('读取剪贴板失败', code='CLIPBOARD_FAILED', detail=str(e))

    @cli.command('clipboard-set')
    @click.argument('text')
    @session_option
    def clipboard_set(text, session):
        """写入系统剪贴板文本（navigator.clipboard.writeText + 权限授予）。

        \b
        示例:
          dpx clipboard-set "hello world"
        """
        import json as _json
        page = _get_page(session)
        try:
            _grant_clipboard_perm(page, 'clipboard-write')
            _cdp_eval(page,
                      f'navigator.clipboard.writeText({_json.dumps(text)})')
            ok({'text': text}, msg='已写入剪贴板')
        except Exception as e:
            error('写入剪贴板失败', code='CLIPBOARD_FAILED', detail=str(e))

    @cli.command('media')
    @session_option
    @click.option('--color-scheme', type=click.Choice(['dark', 'light', 'no-preference']),
                  default=None, help='prefers-color-scheme 媒体特性')
    @click.option('--type', 'media_type',
                  type=click.Choice(['screen', 'print']), default=None,
                  help='模拟媒体类型（如 print 可用来预览打印样式）')
    @click.option('--reduced-motion', is_flag=True,
                  help='开启 prefers-reduced-motion')
    @click.option('--off', is_flag=True, help='清除媒体模拟')
    def cmd_media(session, color_scheme, media_type, reduced_motion, off):
        """CSS 媒体模拟（CDP Emulation.setEmulatedMedia），写入会话自动重放。

        \b
        示例:
          dpx media --color-scheme dark
          dpx media --type print
          dpx media --color-scheme dark --reduced-motion
          dpx media --off
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        cdp_cfg = dict(sess.get('browser_cdp') or {})
        try:
            if off:
                page.run_cdp('Emulation.setEmulatedMedia', media='', features=[])
                cdp_cfg.pop('media', None)
            else:
                feats = dict((cdp_cfg.get('media') or {}).get('features') or {})
                if color_scheme:
                    feats['prefers-color-scheme'] = color_scheme
                if reduced_motion:
                    feats['prefers-reduced-motion'] = 'reduce'
                media_cfg = {
                    'media': media_type if media_type is not None
                             else (cdp_cfg.get('media') or {}).get('media'),
                    'features': feats,
                }
                if not media_cfg['media'] and not feats:
                    ok({'media': None}, msg='当前无媒体模拟')
                    return
                _apply_browser_cdp(page, {'media': media_cfg})
                cdp_cfg['media'] = media_cfg
            if cdp_cfg:
                sess['browser_cdp'] = cdp_cfg
            else:
                sess.pop('browser_cdp', None)
            save_session(session, sess)
            ok({'media': cdp_cfg.get('media')},
               msg='媒体模拟已清除' if off else '媒体模拟已应用')
        except Exception as e:
            error('媒体模拟失败', code='MEDIA_FAILED', detail=str(e))

    @cli.command('bypass-csp')
    @session_option
    @click.option('--off', is_flag=True, help='关闭 CSP 绕过')
    def cmd_bypass_csp(session, off):
        """绕过页面 Content-Security-Policy（CDP Page.setBypassCSP），写入会话重放。

        \b
        示例:
          dpx bypass-csp          # 开启
          dpx bypass-csp --off    # 关闭
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        cdp_cfg = dict(sess.get('browser_cdp') or {})
        try:
            page.run_cdp('Page.setBypassCSP', enabled=not off)
            cdp_cfg['bypass_csp'] = not off
            if not off:
                sess['browser_cdp'] = cdp_cfg
            else:
                cdp_cfg.pop('bypass_csp', None)
                if cdp_cfg:
                    sess['browser_cdp'] = cdp_cfg
                else:
                    sess.pop('browser_cdp', None)
            save_session(session, sess)
            ok({'bypass_csp': not off},
               msg='CSP 绕过已开启' if not off else 'CSP 绕过已关闭')
        except Exception as e:
            error('CSP 绕过设置失败', code='BYPASS_CSP_FAILED', detail=str(e))

    @cli.command('throttle')
    @session_option
    @click.option('--profile', 'profile',
                  type=click.Choice(sorted(THROTTLE_PROFILES)), default=None,
                  help='网络预设：offline / slow-3g / fast-3g / 4g')
    @click.option('--latency', type=int, default=None, help='自定义延迟 ms')
    @click.option('--down', type=int, default=None, help='自定义下行带宽 kbps')
    @click.option('--up', type=int, default=None, help='自定义上行带宽 kbps')
    @click.option('--cpu', type=int, default=None,
                  help='CPU 降速倍数（如 4 = 4x slower；1 恢复）')
    @click.option('--off', is_flag=True, help='关闭全部节流')
    def cmd_throttle(session, profile, latency, down, up, cpu, off):
        """网络/CPU 节流（Network.emulateNetworkConditions + CPUThrottlingRate）。

        设置写入会话，后续 dpx 命令自动重放。

        \b
        示例:
          dpx throttle --profile slow-3g
          dpx throttle --latency 200 --down 1024 --up 512
          dpx throttle --cpu 4
          dpx throttle --off
        """
        page = _get_page(session)
        sess = load_session(session) or {}
        cdp_cfg = dict(sess.get('browser_cdp') or {})
        th = dict(cdp_cfg.get('throttle') or {})
        try:
            if off:
                page.run_cdp('Network.emulateNetworkConditions',
                             offline=False, latency=0,
                             downloadThroughput=-1, uploadThroughput=-1)
                page.run_cdp('Emulation.setCPUThrottlingRate', rate=1)
                th = {}
            else:
                custom = latency is not None or down is not None or up is not None
                if profile and custom:
                    error('--profile 与 --latency/--down/--up 互斥',
                          code='INVALID_ARGS')
                    return
                if profile:
                    th['network'] = dict(THROTTLE_PROFILES[profile])
                elif custom:
                    th['network'] = {
                        'offline': False,
                        'latency': latency or 0,
                        'downloadThroughput': (down * 1024 // 8) if down else -1,
                        'uploadThroughput': (up * 1024 // 8) if up else -1,
                    }
                if cpu is not None:
                    if cpu < 1:
                        error('--cpu 必须 >= 1', code='INVALID_ARGS')
                        return
                    th['cpu'] = cpu
                if not th:
                    ok({'throttle': None}, msg='当前无节流设置')
                    return
                _apply_browser_cdp(page, {'throttle': th})
            if th:
                cdp_cfg['throttle'] = th
                sess['browser_cdp'] = cdp_cfg
            else:
                cdp_cfg.pop('throttle', None)
                if cdp_cfg:
                    sess['browser_cdp'] = cdp_cfg
                else:
                    sess.pop('browser_cdp', None)
            save_session(session, sess)
            ok({'throttle': th or None},
               msg='节流已关闭' if off else '节流已应用并写入会话')
        except Exception as e:
            error('节流设置失败', code='THROTTLE_FAILED', detail=str(e))

    @cli.command('file-chooser')
    @click.argument('files', nargs=-1, required=True)
    @session_option
    @click.option('--locator', 'loc', required=True,
                  help='触发文件选择的元素定位器（点击后弹出文件对话框）')
    @click.option('--timeout', default=10, show_default=True,
                  help='等待文件对话框打开的秒数')
    def cmd_file_chooser(files, session, loc, timeout):
        """拦截文件选择对话框并喂入文件（Page.setInterceptFileChooserDialog）。

        用于 click 触发系统文件对话框的场景（非 input[type=file] 直传，
        那种用 dpx upload）。流程：开启拦截 → 点击元素 → 等
        Page.fileChooserOpened → DOM.setFileInputFiles。

        \b
        示例:
          dpx file-chooser ~/pic.png --locator "css:.upload-btn"
          dpx file-chooser a.png b.png --locator "ref:12"   # multiple 输入
        """
        paths = []
        for f in files:
            p = Path(f).expanduser()
            if not p.is_file():
                error(f'文件不存在: {f}', code='FILE_NOT_FOUND')
                return
            paths.append(str(p))

        page = _get_page(session)
        driver = getattr(page, 'driver', None)
        if driver is None or not hasattr(driver, 'set_callback'):
            error('当前页面对象不支持 CDP 事件回调', code='FILE_CHOOSER_FAILED')
            return

        opened: dict = {}

        def _on_opened(**kw):
            opened.update(kw)

        try:
            page.run_cdp('Page.enable')
            driver.set_callback('Page.fileChooserOpened', _on_opened)
            page.run_cdp('Page.setInterceptFileChooserDialog', enabled=True)

            real_loc = resolve_locator(loc, session, page)
            ele = page.ele(real_loc)
            if ele is None or ele.__class__.__name__ == 'NoneElement':
                error(f'元素未找到: {loc}', code='ELEMENT_NOT_FOUND')
                return
            ele.click()

            end = perf_counter() + timeout
            while perf_counter() < end and 'backendNodeId' not in opened:
                _sleep(0.1)
            if 'backendNodeId' not in opened:
                error('文件对话框未打开（点击未触发文件选择）',
                      code='FILE_CHOOSER_FAILED')
                return
            if opened.get('mode') == 'selectSingle' and len(paths) > 1:
                debug('对话框为单选模式，仅使用第一个文件')
                paths = paths[:1]
            page.run_cdp('DOM.setFileInputFiles', files=paths,
                         backendNodeId=opened['backendNodeId'])
            ok({'files': paths, 'mode': opened.get('mode')},
               msg='已填入文件')
        except SystemExit:
            raise
        except Exception as e:
            error('文件选择处理失败', code='FILE_CHOOSER_FAILED',
                  detail=str(e))
        finally:
            try:
                page.run_cdp('Page.setInterceptFileChooserDialog', enabled=False)
            except Exception as e:
                debug('关闭文件对话框拦截失败', e)
            try:
                driver.set_callback('Page.fileChooserOpened', None)
            except Exception as e:
                debug('清除 fileChooserOpened 回调失败', e)

    @cli.command('cf-wait')
    @session_option
    @click.option('--timeout', default=30.0, show_default=True,
                  type=float, help='等待挑战通过的最长秒数')
    @click.option('--poll', default=0.5, show_default=True, type=float,
                  help='检测轮询间隔秒数（事件驱动不可用时的回落节拍）')
    @click.option('--no-events', 'force_poll', is_flag=True,
                  help='禁用事件驱动，强制纯轮询')
    def cf_wait(session, timeout, poll, force_poll):
        """检测并等待 Cloudflare Turnstile/挑战页通过。

        识别挑战页标志：challenges.cloudflare.com / turnstile iframe、
        "Just a moment"/"请稍候"/"Checking your browser" 标题。挑战
        标志消失（含自然跳转回原页面）即返回通过；超时退出码 5。

        \b
        示例:
          dpx open https://site --stealth && dpx cf-wait --timeout 60
        """
        import re as _re
        page = _get_page(session)
        # as_expr=True 走 Runtime.evaluate：顶层 return 是非法语法（会抛
        # JavaScriptError 被保守分支吞成"仍在挑战"），必须用括号表达式
        detect_js = (
            '({f:!!document.querySelector('
            '\'iframe[src*="challenges.cloudflare.com"],'
            'iframe[src*="cf-challenge"],'
            'iframe[src*="turnstile"]\'),'
            't:document.title||""})')
        # 事件驱动 watcher 的"挑战已清除"条件（页内探测，变真即唤醒）
        clear_cond = (
            '!document.querySelector('
            + json.dumps('iframe[src*="challenges.cloudflare.com"],'
                         'iframe[src*="cf-challenge"],'
                         'iframe[src*="turnstile"]')
            + ')&&!/just a moment|请稍候|checking your browser'
              '|verify you are human/i.test(document.title||"")')

        start = perf_counter()
        end = start + timeout
        ever = False
        last_title = ''
        gate = CdpWaitGate(page)
        armed = gate.arm(clear_cond, force_poll=force_poll)
        try:
            while True:
                try:
                    st = page.run_js(detect_js, as_expr=True)
                    if isinstance(st, str):
                        st = json.loads(st)
                    challenging = bool(
                        st.get('f') or
                        _re.search(
                            r'just a moment|请稍候|checking your '
                            r'browser|verify you are human',
                            st.get('t', ''), _re.I))
                    last_title = st.get('t', '') or last_title
                    if challenging:
                        ever = True
                except Exception:
                    # 挑战跳转期间 JS 上下文瞬断属正常；未知状态保守
                    # 视为"仍在挑战"，继续轮询而非误判通过
                    challenging = True
                if not challenging:
                    break
                if perf_counter() >= end:
                    error(f'Cloudflare 挑战未在 {timeout}s 内通过'
                          f'（title={last_title!r}）。建议先 dpx stealth '
                          f'或检查代理/指纹', code='WAIT_TIMEOUT')
                    return
                if armed:
                    gate.wait(min(poll, end - perf_counter()))
                else:
                    _sleep(poll)
            ok({'passed': True, 'was_challenged': ever,
                'waited_seconds': round(perf_counter() - start, 2),
                'title': last_title})
        except SystemExit:
            raise
        except Exception as e:
            error('CF 挑战等待失败', code='WAIT_FAILED', detail=str(e))
        finally:
            gate.disarm()
