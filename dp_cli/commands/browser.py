# -*- coding:utf-8 -*-
"""浏览器生命周期命令: open / goto / reload / go-back / go-forward / close / close-all / list / delete-data / stealth"""
from pathlib import Path

import click

from dp_cli.session import (get_browser, close_browser, list_sessions,
                            delete_session, load_session, save_session,
                            discover_port_from_profile,
                            default_user_data_dir_for_channel)
from dp_cli.output import ok, error, format_page_info
from dp_cli.commands._utils import session_option, _get_page, normalize_url
from dp_cli.stealth import apply_stealth, PRESETS, DEFAULT_UA


def register(cli):

    @cli.command('open')
    @click.argument('url', required=False)
    @session_option
    @click.option('--headless', is_flag=True, help='无头模式')
    @click.option('--browser', 'browser_path', default=None, help='浏览器可执行文件路径')
    @click.option('--profile', 'user_data_dir', default=None, help='用户数据目录')
    @click.option('--proxy', default=None, help='代理服务器，如 http://127.0.0.1:7890')
    @click.option('--port', type=int, default=None, help='连接指定端口的已有浏览器实例')
    @click.option('--auto-connect', is_flag=True,
                  help='从用户常规启动的 Chrome 读取 DevToolsActivePort 自动发现端口'
                       '（需 Chrome 144+，用户在 chrome://inspect/#remote-debugging 启用）')
    @click.option('--channel', type=click.Choice(['stable', 'beta', 'dev', 'canary', 'chromium']),
                  default='stable', show_default=True,
                  help='配合 --auto-connect 使用，定位默认 user-data-dir')
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
        第二步：用 dp 接管：
          dp open --port 9222

        \b
        【复用用户自己的浏览器 - 方式 B：--auto-connect（Chrome 144+ 推荐）】
        无需特殊启动参数，正常打开 Chrome 即可：
          1. 打开 Chrome，访问 chrome://inspect/#remote-debugging
          2. 勾选 "Allow remote debugging for this browser instance"
          3. dp open --auto-connect
        dp 会从 Chrome 的 user-data-dir 自动读取 DevToolsActivePort 拿到端口。
        指定非 stable 渠道：dp open --auto-connect --channel beta
        指定自定义 profile：dp open --auto-connect --probe-dir ~/my-chrome-profile

        \b
        【dp 自动管理浏览器】
          dp open
          dp open https://example.com
          dp open https://example.com --headless
          dp -s work open https://github.com

        \b
        【连接远程 headless VPS Chrome 并反检测】
          VPS:   google-chrome --headless=new --remote-debugging-port=9222 \\
                   --no-sandbox --disable-dev-shm-usage \\
                   --disable-blink-features=AutomationControlled \\
                   --user-data-dir=~/.config/google-chrome
          本地:  ssh -NL 9322:127.0.0.1:9222 vps-host
                 dp open --port 9322 --stealth
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
                # 记录到 session，使 _get_page 在后续每次 dp 命令自动重注册
                # （CDP init_js 绑定到 CDP session，每个 dp 命令需要重新注册）
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
          dp goto https://example.com
          dp goto example.com
          dp goto example.com --timeout 60
        """
        page = _get_page(session)
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
    def cmd_close(session, del_data, force):
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
            result = close_browser(session)
            if result:
                extra = '，bridge 已停' if auto_connect else ''
                ok(msg=f'已断开与 [{session}] 的连接（浏览器仍运行{extra}）。'
                       f'用 --force 可尝试彻底关闭浏览器。')
            else:
                error('断开失败', code='CLOSE_FAILED')
        else:
            result = close_browser(session, del_data=del_data)
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
    def cmd_stealth(session, preset, ua, langs, webgl_vendor, webgl_renderer, feature):
        """对当前会话施加反自动化检测补丁。

        \b
        典型用法:
          dp stealth                         # full 预设（推荐）
          dp stealth --preset mild           # 最小改动
          dp stealth --ua "Mozilla/5.0 ..."  # 自定义 UA
          dp stealth --feature webdriver --feature plugins  # 只开部分

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
        features = set(feature) if feature else PRESETS[preset]
        langs_list = [s.strip() for s in langs.split(',')] if langs else None
        try:
            info = apply_stealth(
                page, features=features, ua=ua, langs=langs_list,
                webgl_vendor=webgl_vendor, webgl_renderer=webgl_renderer,
            )
            # 保存到 session，使 _get_page 在后续每个 dp 命令自动重注册
            # （CDP init_js 绑定到 CDP session，每个 dp 命令需要重新注册）
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
