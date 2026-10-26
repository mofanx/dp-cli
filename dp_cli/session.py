# -*- coding:utf-8 -*-
"""
dp-cli 会话管理模块
通过固定端口连接复用已运行的浏览器实例，实现跨命令状态共享。

多上下文设计约束：
  dp-cli 以命名 session（-S/--session）做隔离，每个会话独立 user-data-dir
  （~/.dp_cli/sessions/profiles/<name>）+ auto_port 独立浏览器进程，
  等价于 Playwright 的 profile 级隔离。Chromium 的 BrowserContext
  （Target.createBrowserContext，同一进程内多隔离上下文，类似 incognito）
  DrissionPage 未暴露对应 API，故不提供 --context/--profile 参数；
  需要隔离时请使用 -S <name> 起独立会话。
"""
import json
import os
import sys
from pathlib import Path
from time import sleep, perf_counter

from dp_cli.logs import debug


# 会话状态文件默认目录
_SESSION_DIR = Path.home() / '.dp_cli' / 'sessions'


def get_session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def get_session_file(name: str) -> Path:
    return get_session_dir() / f'{name}.json'


def load_session(name: str) -> dict:
    """读取会话信息"""
    f = get_session_file(name)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_session(name: str, data: dict) -> None:
    """保存会话信息"""
    get_session_file(name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def delete_session(name: str) -> bool:
    """删除会话文件"""
    f = get_session_file(name)
    if f.exists():
        f.unlink()
        return True
    return False


def list_sessions() -> list:
    """列出所有会话"""
    d = get_session_dir()
    sessions = []
    for f in d.glob('*.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            sessions.append({'name': f.stem, **data})
        except Exception as e:
            debug(f'会话文件 {f.name} 解析失败，已跳过', e)
    return sessions


def _detect_headless(port: int) -> bool:
    """通过 /json/version 探测浏览器是否 headless 模式。

    探测失败一律返回 False。注意此 False 可能是"探测失败"而非"确为
    headed"：inspect 模式 Chrome 本身无 HTTP REST，且 headless 浏览器
    被误判 headed 时 DrissionPage 可能执行 quit→restart。不引入 WS
    级探测无法区分，故保守返回 False 并记 debug；浏览器不可达的危险
    路径已由 _connect_browser 的 ConnectionError 分支覆盖。
    """
    try:
        import requests
        resp = requests.get(f'http://127.0.0.1:{port}/json/version', timeout=3)
        return 'headless' in resp.json().get('User-Agent', '').lower()
    except Exception as e:
        debug(f'_detect_headless(port={port}) 探测失败，按非 headless 处理', e)
        return False


def _port_open(ip: str, port: int, timeout: float = 0.3) -> bool:
    """TCP 快速探测端口是否可达（connection refused 立即返回 False）。

    用于在 DrissionPage test_connect（固定空转 browser_connect_timeout=30s）
    之前快速失败死端口路径：~0.1s vs 30s。
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, int(port))) == 0
    except OSError:
        return False


def _probe_cdp_http(port: int, timeout: float = 1.5) -> bool:
    """严格探测端口是否标准 CDP HTTP 端点（/json/version 返回合法 JSON）。

    TCP 开 ≠ 可 attach：inspect 限流端口 /json 一律 404，其他服务监听
    也走不通。不预检的话 DrissionPage test_connect 会空转满
    browser_connect_timeout(30s) 才报错。~ms 级成本换掉 30s 空转。
    """
    try:
        import requests
        r = requests.get(f'http://127.0.0.1:{port}/json/version',
                         timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json()
        if not (isinstance(data, dict) and 'Browser' in data):
            return False
        # 与 DrissionPage test_connect 的成功条件等价：/json 必须含
        # page/webview 目标。零 tab 浏览器（全关/异常）此前仍空转 30s。
        tabs = requests.get(f'http://127.0.0.1:{port}/json',
                            timeout=timeout).json()
        return any(t.get('type') in ('page', 'webview') for t in tabs)
    except Exception:
        return False


def _attach_page(co):
    """existing_only attach：把 DrissionPage test_connect 重试窗口
    30s → 8s。

    所有调用点都过了 TCP+HTTP 预检，attach 理应即刻成功；8s 留给
    "预检通过但 attach 前端口刚死/竞态失败"的重试余量，失败也比
    默认 30s 快败。新建浏览器路径不经过此函数（保持 30s 启动余量）。
    """
    from DrissionPage import ChromiumPage
    from DrissionPage._functions.settings import Settings as _S
    old = _S.browser_connect_timeout
    _S.browser_connect_timeout = 8
    try:
        return ChromiumPage(co)
    finally:
        _S.browser_connect_timeout = old


def _bridge_upstream_alive(bridge_port: int, timeout: float = 1.5) -> bool:
    """探测 bridge 的上游 WS 是否还连着（GET /healthz）。

    旧版本 bridge 没有 /healthz（404）或探测失败：按"未知"放行，沿用
    现有 attach 路径——宁可多试一次也不误杀健康 bridge（重建要用户
    重新点 Allow）。
    """
    try:
        import requests
        r = requests.get(f'http://127.0.0.1:{bridge_port}/healthz',
                         timeout=timeout)
        if r.status_code == 404:
            return True
        return r.status_code == 200 and r.json().get('upstream') is True
    except Exception:
        return True


# ── chrome://inspect 远程调试：端口自动发现 ─────────────────────────────────
#
# Chrome 144+ 支持用户在 chrome://inspect/#remote-debugging 点击
# "Allow remote debugging for this browser instance"，无需 --remote-debugging-port
# 启动参数。开启后 Chrome 会在 user-data-dir 下写入 DevToolsActivePort 文件：
#   第一行：分配的端口号
#   第二行：WebSocket 路径（如 /devtools/browser/<uuid>）
# 我们读取该文件的端口号后，走现有的 set_local_port 连接路径即可。


def default_user_data_dir_for_channel(channel: str = 'stable') -> Path | None:
    """返回指定 channel 的 Chrome/Edge 默认 user-data-dir。

    :param channel: stable / beta / dev / canary / chromium /
                    edge / edge-beta / edge-dev / edge-canary
                    （edge-canary 无 Linux 版，Windows 为 SxS）
    :return: 用户数据目录 Path；不存在时返回 None
    """
    home = Path.home()
    candidates: dict[str, list[Path]] = {}
    if sys.platform.startswith('linux'):
        candidates = {
            'stable':       [home / '.config' / 'google-chrome'],
            'beta':         [home / '.config' / 'google-chrome-beta'],
            'dev':          [home / '.config' / 'google-chrome-unstable'],
            'canary':       [home / '.config' / 'google-chrome-canary'],
            'chromium':     [home / '.config' / 'chromium'],
            'edge':         [home / '.config' / 'microsoft-edge'],
            'edge-beta':    [home / '.config' / 'microsoft-edge-beta'],
            'edge-dev':     [home / '.config' / 'microsoft-edge-dev'],
            'edge-canary':  [],   # Edge 无 Linux canary
        }
    elif sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
        candidates = {
            'stable':       [base / 'Google' / 'Chrome'],
            'beta':         [base / 'Google' / 'Chrome Beta'],
            'dev':          [base / 'Google' / 'Chrome Dev'],
            'canary':       [base / 'Google' / 'Chrome Canary'],
            'chromium':     [base / 'Chromium'],
            'edge':         [base / 'Microsoft Edge'],
            'edge-beta':    [base / 'Microsoft Edge Beta'],
            'edge-dev':     [base / 'Microsoft Edge Dev'],
            'edge-canary':  [base / 'Microsoft Edge Canary'],
        }
    elif sys.platform.startswith('win'):
        local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local'))
        candidates = {
            'stable':       [local / 'Google' / 'Chrome' / 'User Data'],
            'beta':         [local / 'Google' / 'Chrome Beta' / 'User Data'],
            'dev':          [local / 'Google' / 'Chrome Dev' / 'User Data'],
            'canary':       [local / 'Google' / 'Chrome SxS' / 'User Data'],
            'chromium':     [local / 'Chromium' / 'User Data'],
            'edge':         [local / 'Microsoft' / 'Edge' / 'User Data'],
            'edge-beta':    [local / 'Microsoft' / 'Edge Beta' / 'User Data'],
            'edge-dev':     [local / 'Microsoft' / 'Edge Dev' / 'User Data'],
            'edge-canary':  [local / 'Microsoft' / 'Edge SxS' / 'User Data'],
        }

    for p in candidates.get(channel, []):
        if p.exists():
            return p
    return None


# --auto-connect 不传 --channel 时的嗅探顺序：覆盖全部已装渠道。
# stable 系优先（多数用户的主力浏览器在 stable），Edge 紧随同级
# Chrome 渠道；chromium 排最后（多见于测试环境，抢答风险最高）。
_AUTO_SNIFF_CHANNELS = ('stable', 'edge', 'beta', 'edge-beta', 'dev',
                        'edge-dev', 'canary', 'edge-canary', 'chromium')


def _is_port_alive(port: int, timeout: float = 0.3) -> bool:
    """轻量 TCP 探活：能 connect 上即视为活的。"""
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except Exception:
        return False


def sniff_auto_user_data_dir() -> tuple[Path | None, list[tuple[str, Path, str]]]:
    """auto channel：按 _AUTO_SNIFF_CHANNELS 顺序查找首个含可用 DevToolsActivePort 的目录。

    会校验端口实际可连通，避免命中浏览器已关闭后残留的陈旧文件。

    :return: (命中目录或 None, 诊断记录列表)
        诊断记录每项为 (channel, path, reason)，reason 取值：
          - 'hit'        命中（文件存在且端口活）
          - 'no_dir'     用户数据目录不存在
          - 'no_port'    目录存在但缺 DevToolsActivePort
          - 'stale'      DevToolsActivePort 存在但端口不通（陈旧残留）
          - 'bad_file'   DevToolsActivePort 文件解析失败
    """
    diag: list[tuple[str, Path, str]] = []
    for ch in _AUTO_SNIFF_CHANNELS:
        p = default_user_data_dir_for_channel(ch)
        if p is None:
            diag.append((ch, Path('<not found>'), 'no_dir'))
            continue
        f = p / 'DevToolsActivePort'
        if not f.exists():
            diag.append((ch, p, 'no_port'))
            continue
        try:
            port = discover_port_from_profile(p)
        except (FileNotFoundError, ValueError):
            diag.append((ch, p, 'bad_file'))
            continue
        if not _is_port_alive(port):
            diag.append((ch, p, 'stale'))
            continue
        diag.append((ch, p, 'hit'))
        return p, diag
    return None, diag


def discover_port_from_profile(user_data_dir: str | os.PathLike) -> int:
    """从 Chrome user-data-dir 的 DevToolsActivePort 文件读取端口号。

    要求 Chrome 144+ 已在 chrome://inspect/#remote-debugging 点击
    "Allow remote debugging for this browser instance" 启用远程调试。

    :raise FileNotFoundError: DevToolsActivePort 文件不存在（远程调试未开启）
    :raise ValueError: 文件内容格式异常
    """
    p = Path(user_data_dir) / 'DevToolsActivePort'
    if not p.exists():
        raise FileNotFoundError(
            f'未找到 {p}。请确认：\n'
            f'  1. 浏览器正在运行，且 user-data-dir 匹配：{user_data_dir}\n'
            f'  2. 已在地址栏打开 chrome://inspect/#remote-debugging\n'
            f'     （Edge 用 edge://inspect/#devices）并点击\n'
            f'     "Allow remote debugging for this browser instance"\n'
            f'     （需 Chrome/Edge 144+）'
        )
    lines = [l.strip() for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    if not lines:
        raise ValueError(f'{p} 为空')
    try:
        port = int(lines[0])
    except ValueError as e:
        raise ValueError(f'{p} 第一行不是有效端口号: {lines[0]!r}') from e
    if port <= 0 or port > 65535:
        raise ValueError(f'{p} 端口号越界: {port}')
    return port


def _connect_via_bridge(session_name: str, probe_dir: str, sess: dict):
    """在 probe_dir 模式下自动检测 inspect 限流，起 bridge，返回 ChromiumPage。

    失败时抛异常，由调用方决定 fallback。成功时会更新并保存 session：
      sess['port']        = 若直连则真端口；若起了 bridge 则 bridge 端口
      sess['real_port']   = 真 Chrome 端口（诊断用）
      sess['bridge_pid']  = bridge 子进程 pid（如起了）
      sess['bridge_port'] = bridge 监听端口（= sess['port']）
    """
    from DrissionPage._configs.chromium_options import ChromiumOptions
    from dp_cli.bridge_manager import (detect_inspect_mode, start_bridge,
                                        is_bridge_alive, stop_bridge)

    real_port = discover_port_from_profile(probe_dir)

    # 快速失败：DevToolsActivePort 残留但浏览器已退出——TCP 秒拒，
    # 不必走 DrissionPage test_connect 的 30s 空转
    if not _port_open('127.0.0.1', real_port):
        raise ConnectionError(
            f'端口 {real_port} 无法连接（DevToolsActivePort 残留）。\n'
            f'浏览器可能已退出，请重新打开 Chrome/Edge 后重试。')

    # 已有 bridge 且仍存活 + 端口有效 → 直接复用
    old_bridge_pid = sess.get('bridge_pid')
    old_bridge_port = sess.get('bridge_port')
    old_real_port = sess.get('real_port')
    if (old_bridge_pid and old_bridge_port and is_bridge_alive(old_bridge_pid)
            and old_real_port == real_port
            and _port_open('127.0.0.1', old_bridge_port)
            and _bridge_upstream_alive(old_bridge_port)):
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(old_bridge_port)
            co.existing_only(True)
            return _attach_page(co)
        except Exception:
            # 复用失败，停旧的再重起
            stop_bridge(old_bridge_pid)

    # 老 bridge 存在但 real_port 变了（Chrome 重启）→ 先清掉
    if old_bridge_pid and is_bridge_alive(old_bridge_pid):
        stop_bridge(old_bridge_pid)

    # 跨 session 复用：session 级 miss 后查注册表（其他 dpx 会话
    # 可能已授权起过 bridge——直接采用免再点 Allow）
    from dp_cli import bridge_registry as reg
    entry = reg.find_bridge(probe_dir, real_port=real_port)
    if entry is not None and entry['port'] != old_bridge_port:
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(int(entry['port']))
            co.existing_only(True)
            page = _attach_page(co)
            reg._note_session(entry, session_name)
            sess['port'] = int(entry['port'])
            sess['real_port'] = real_port
            sess['bridge_pid'] = int(entry['pid'])
            sess['bridge_port'] = int(entry['port'])
            save_session(session_name, sess)
            return page
        except Exception as e:
            debug(f'registry bridge 复用失败 port={entry["port"]}，转 spawn', e)
            # 共享 bridge 不连坐：有其他 session 持有时只摘除本 session
            # 名，不删注册项也不停进程——它们还指着这条 bridge
            others = [s for s in entry.get('sessions', [])
                      if s != session_name]
            if others:
                try:
                    entry['sessions'].remove(session_name)
                    reg._write_json_atomic(
                        reg._entry_path(probe_dir), entry)
                except Exception as ee:
                    debug('注册项 session 摘除失败（忽略）', ee)
            else:
                reg.unregister_bridge(probe_dir)

    # 检测真端口是否 inspect 限流模式
    is_inspect = detect_inspect_mode(real_port)

    if not is_inspect:
        # 经典 --remote-debugging-port 模式：直连真端口即可
        co = ChromiumOptions(read_file=False)
        co.set_local_port(real_port)
        co.existing_only(True)
        if _detect_headless(real_port):
            co.headless(True)
        page = _attach_page(co)
        sess.pop('bridge_pid', None)
        sess.pop('bridge_port', None)
        sess['port'] = real_port
        sess['real_port'] = real_port
        save_session(session_name, sess)
        return page

    # inspect 模式：注册表锁内"查→spawn→登记"——并发多 session
    # 只起一次 bridge（其余 adopt），避免双授权弹窗
    pid, bport, adopted = reg.acquire_or_spawn(
        probe_dir, spawn_fn=start_bridge, real_port=real_port,
        session_name=session_name)
    try:
        co = ChromiumOptions(read_file=False)
        co.set_local_port(bport)
        co.existing_only(True)
        page = _attach_page(co)
    except Exception:
        # 共享 bridge（adopt）attach 失败不得连坐：停进程/删注册项
        # 仅在本 session 是唯一持有者时执行，否则只摘除本 session 名
        ent = reg.find_bridge(probe_dir, real_port=real_port)
        others = [s for s in (ent or {}).get('sessions', [])
                  if s != session_name]
        if not adopted and not others:
            stop_bridge(pid)
            reg.unregister_bridge(probe_dir)
        elif ent is not None and session_name in ent.get('sessions', []):
            ent['sessions'].remove(session_name)
            try:
                reg._write_json_atomic(
                    reg._entry_path(probe_dir), ent)
            except Exception as e:
                debug('注册项 session 摘除失败（忽略）', e)
        raise

    sess['port'] = bport
    sess['real_port'] = real_port
    sess['bridge_pid'] = pid
    sess['bridge_port'] = bport
    save_session(session_name, sess)
    return page


# 进程内浏览器缓存：dpx batch/exec 单进程顺序执行多条命令时复用 attach
# 结果，消除每条命令重新建立 CDP websocket 的开销。普通 CLI 每个进程
# 只调用一次 get_browser，缓存写入后即退出，无行为变化。
_PAGE_CACHE: dict = {}


def _evict_browser_cache(session_name: str = None) -> None:
    """清除浏览器缓存项；session_name 为 None 时清空全部。"""
    if session_name is None:
        _PAGE_CACHE.clear()
    else:
        _PAGE_CACHE.pop(session_name, None)


def get_browser(session_name: str = 'default', headless: bool = False,
                browser_path: str = None, user_data_dir: str = None,
                proxy: str = None, port: int = None):
    """获取或创建浏览器实例（含进程内缓存）。

    无显式建立参数（port/browser_path/user_data_dir/proxy/headless）的
    调用先查缓存，命中则用 CDP 探活后直接复用；显式建立参数（如
    open --port/--user-data-dir）始终真实建立连接并刷新缓存。
    """
    explicit = port or browser_path or user_data_dir or proxy or headless
    if not explicit:
        cached = _PAGE_CACHE.get(session_name)
        if cached is not None:
            try:
                cached.tab_ids  # CDP Target.getTargets 探活，成本远小于 attach
                return cached
            except Exception:
                _PAGE_CACHE.pop(session_name, None)

    page = _connect_browser(session_name, headless=headless,
                            browser_path=browser_path,
                            user_data_dir=user_data_dir,
                            proxy=proxy, port=port)
    _PAGE_CACHE[session_name] = page
    return page


def _dir_writable(d) -> bool:
    """目录可写探测——建删一个临时文件，任何失败即 False。"""
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
        p = Path(d) / f'.wprobe_{os.getpid()}'
        p.write_bytes(b'x')
        p.unlink()
        return True
    except Exception:
        return False


def _spawn_page(page_cls, co):
    """ChromiumPage 实例化兜底——/tmp 配额/不可写导致 spawn 失败时，
    切到可写候选临时目录重试一次（不静默写系统盘、不改系统配置）。
    候选序：DPX_TMPDIR 环境变量 → ~/.dp_cli/tmp → tempfile 其他候选。
    重试仍败则抛带根因说明的 ConnectionError（保留原始 detail）。
    """
    import tempfile
    try:
        return page_cls(co)
    except Exception as e1:
        tmp = tempfile.gettempdir()
        if _dir_writable(tmp):
            raise                                  # /tmp 可写——非配额问题，原样抛
        candidates = [
            os.environ.get('DPX_TMPDIR'),
            str(Path.home() / '.dp_cli' / 'tmp'),
        ]
        alt = next((c for c in candidates
                    if c and _dir_writable(c)), None)
        if not alt:
            raise ConnectionError(
                f'浏览器启动失败：临时目录 {tmp} 不可写（疑似配额/只读），'
                f'且候选目录（DPX_TMPDIR、~/.dp_cli/tmp）均不可用。\n'
                f'处理建议：export DPX_TMPDIR=<可写目录> 后重试\n'
                f'原始错误: {e1}') from e1
        debug(f'TMPDIR {tmp} 不可写，回落到 {alt} 重试 spawn')
        os.environ['TMPDIR'] = alt                 # 只影响本进程及子进程
        tempfile.tempdir = alt                     # python tempfile 用新路径
        try:
            return page_cls(co)
        except Exception as e2:
            raise ConnectionError(
                f'浏览器启动失败：{tmp} 不可写，回落 {alt} 重试仍失败。\n'
                f'处理建议：export DPX_TMPDIR=<可写目录> 或检查浏览器可执行文件\n'
                f'原始错误: {e1}\n回落错误: {e2}') from e2


def _connect_browser(session_name: str = 'default', headless: bool = False,
                     browser_path: str = None, user_data_dir: str = None,
                     proxy: str = None, port: int = None):
    """
    获取或创建浏览器实例。

    连接优先级：
    1. 指定 port → 直接连接该端口（失败则报错，不 fallback）
    2. 已有会话记录的 port → 尝试复用（user_connected/auto-connect
       会话失败则报 ConnectionError；dpx 自管会话失败则清记录新建）
    3. 新建浏览器实例
    """
    from DrissionPage import ChromiumPage
    from DrissionPage._configs.chromium_options import ChromiumOptions

    # === 情况1：用户明确指定端口（连接用户自己的浏览器） ===
    if port:
        # 快速失败：端口死则 ~0.1s 报错，避免 DrissionPage test_connect
        # 对 connection refused 也空转 30s（browser_connect_timeout）
        if not _port_open('127.0.0.1', port):
            raise ConnectionError(
                f'端口 {port} 无法连接（TCP 拒绝）。\n'
                f'请确认浏览器已使用 --remote-debugging-port={port} 启动。\n'
                f'启动命令示例:\n'
                f'  google-chrome --remote-debugging-port={port}\n'
                f'  microsoft-edge --remote-debugging-port={port}\n'
                f'  chromium --remote-debugging-port={port}')
        # TCP 开 ≠ CDP：inspect 限流端口/其他服务监听时 /json 全 404，
        # DrissionPage test_connect 会空转 30s——先 1.5s 预检拿清晰错误
        if not _probe_cdp_http(port):
            raise ConnectionError(
                f'端口 {port} 不是标准 CDP 端点（/json/version 无有效响应）。\n'
                f'若这是 chrome://inspect/#remote-debugging 授权端口'
                f'（Chrome 144+，无 HTTP REST 接口），请改用：\n'
                f'  dpx open -a\n'
                f'若浏览器以 --remote-debugging-port={port} 启动但此处报错，\n'
                f'可能是浏览器仍在初始化，稍后重试即可。')
        co = ChromiumOptions(read_file=False)
        co.set_local_port(port)
        co.existing_only(True)
        # 探测浏览器 headless 状态，同步到 options，避免 DrissionPage
        # 因 headless 不匹配执行 quit→restart 导致无头浏览器被关闭
        if _detect_headless(port):
            co.headless(True)
        try:
            page = _attach_page(co)
        except Exception as e:
            raise ConnectionError(
                f'无法连接到端口 {port} 的浏览器实例。\n'
                f'请确认浏览器已使用 --remote-debugging-port={port} 启动。\n'
                f'启动命令示例:\n'
                f'  google-chrome --remote-debugging-port={port}\n'
                f'  microsoft-edge --remote-debugging-port={port}\n'
                f'  chromium --remote-debugging-port={port}\n'
                f'原始错误: {e}'
            ) from e
        # 记录到会话（后续命令无需再指定端口）
        save_session(session_name, {
            'port': port,
            'headless': headless,
            'user_data_dir': user_data_dir,
            'user_connected': True,  # 标记：这是用户自己的浏览器
        })
        return page

    # === 情况2：尝试复用已有会话 ===
    sess = load_session(session_name)
    saved_port = sess.get('port')
    probe_dir = sess.get('probe_dir')  # auto-connect 模式记录的 user-data-dir

    # 2a) 先尝试 saved_port 直连（死端口先 TCP 探测，免 30s 空转）。
    # 注意：探测失败只置 skip_saved 标记，不清空 saved_port——后面
    # "user_connected 不可达"守卫要靠原始端口值判断，清空了会让
    # user_connected 会话静默落到"新建浏览器"分支顶替用户的浏览器。
    skip_saved = False
    if saved_port and not _port_open('127.0.0.1', saved_port):
        debug(f'saved_port={saved_port} TCP 拒绝，跳过直连尝试')
        skip_saved = True
    elif saved_port and saved_port == sess.get('bridge_port') \
            and not _bridge_upstream_alive(saved_port):
        # saved_port 是 bridge 端口且上游已断（Chrome 重启等）——
        # 必须先查 healthz：否则下面的 /json 预检会被 bridge 内部
        # Target.getTargets 15s 超时拖住。上游断则走 probe_dir 重建
        debug(f'saved_port={saved_port} bridge 上游已断，跳过直连')
        skip_saved = True
    elif saved_port and not _probe_cdp_http(saved_port):
        # TCP 开但 /json 无有效响应/无 page 目标（inspect 端口/
        # 非 CDP 服务/零 tab），直连必然触发 test_connect 30s 空转
        # ——直接跳过走复用路径
        debug(f'saved_port={saved_port} 非可 attach 的 CDP 端点，跳过直连')
        skip_saved = True
    if saved_port and not skip_saved:
        try:
            co = ChromiumOptions(read_file=False)
            co.set_local_port(saved_port)
            co.existing_only(True)
            # headless 优先读会话缓存：dpx 自管会话创建时已记录，
            # 命中可省一次 /json/version HTTP 探测。user_connected /
            # probe_dir 路径保持探测——用户的浏览器模式不由我们控制。
            cached_hl = sess.get('headless')
            if cached_hl is not None and not sess.get('user_connected') \
                    and not probe_dir:
                if cached_hl:
                    co.headless(True)
            elif _detect_headless(saved_port):
                co.headless(True)
            page = _attach_page(co)
            return page
        except Exception as e:
            debug(f'saved_port={saved_port} 直连失败，尝试其他复用路径', e)

    # 2b) auto-connect 模式：重新发现端口，必要时自动起 bridge
    # 用户重启 Chrome 后端口会变，此路径让 dpx 命令无缝继续工作
    if probe_dir:
        auto_connect_flag = sess.get('auto_connect')
        try:
            return _connect_via_bridge(session_name, probe_dir, sess)
        except Exception as e:
            # auto-connect 是用户明确要求连自己的浏览器，不应静默 fallback
            # 到新开浏览器；抛出让上层显示清晰错误
            if auto_connect_flag:
                raise ConnectionError(
                    f'auto-connect 失败: {e}\n'
                    f'probe_dir={probe_dir}\n'
                    f'提示: 如需手动调试，运行:\n'
                    f'  python -m dp_cli.bridge --user-data-dir {probe_dir} --listen 0 -v'
                ) from e

    # 所有复用路径都失败。
    # 会话 attach 的是用户自己的浏览器（--port/--auto-connect 写入
    # user_connected/probe_dir）：静默新建空浏览器会让后续命令操作
    # 完全错误的目标（且丢登录态），比报错更糟——与 auto_connect
    # 分支同处理，直接报 ConnectionError。会话保留供重试；放弃可
    # dpx close 或 dpx open --new。
    # 例外：显式带建立参数（--user-data-dir/--headless/--proxy/
    # --browser-path）的调用视为用户主动要求新建，放行新建分支。
    want_new = bool(browser_path or user_data_dir or proxy or headless)
    if (saved_port or probe_dir) and not want_new and \
            (sess.get('user_connected') or probe_dir):
        raise ConnectionError(
            f'会话 [{session_name}] 连接的用户浏览器已不可达'
            f'（port={saved_port}）。\n'
            f'该会话 attach 的是用户自己的浏览器，不会用新实例顶替。\n'
            f'处理建议：\n'
            f'  - 重启浏览器（--remote-debugging-port / chrome://inspect '
            f'远程调试）后重试\n'
            f'  - 放弃会话：dpx close -S {session_name}，再 dpx open 新建'
        )

    # dpx 自管会话：清会话记录，落到新建分支（profile 目录不变，登录态保留）
    if saved_port or probe_dir:
        delete_session(session_name)

    # === 情况3：新建浏览器实例 ===
    co = ChromiumOptions()
    if browser_path:
        co.set_browser_path(browser_path)
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    else:
        # 每个会话有独立的用户数据目录
        uid = get_session_dir() / 'profiles' / session_name
        uid.mkdir(parents=True, exist_ok=True)
        co.set_user_data_path(str(uid))

    if proxy:
        co.set_proxy(proxy)

    if headless:
        co.headless(True)

    # CI/容器环境 Chrome 沙箱常起不来：root 用户或 CI 环境变量时补
    # --no-sandbox/--disable-dev-shm-usage（dev shm 在容器里默认 64MB）
    import os
    if os.environ.get('CI') or (hasattr(os, 'geteuid') and os.geteuid() == 0):
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')

    co.auto_port(True)

    page = _spawn_page(ChromiumPage, co)

    # 保存会话端口
    port = int(page.browser.address.split(':')[-1])
    save_session(session_name, {
        'port': port,
        'headless': headless,
        'user_data_dir': str(co.user_data_path) if co.user_data_path else None,
    })

    return page


# ── Ref 映射管理 ─────────────────────────────────────────────────────────────


def save_refs(session_name: str, url: str, refs: dict) -> None:
    """保存快照编号映射到 refs.json"""
    from datetime import datetime
    data = {
        'url': url,
        'timestamp': datetime.now().isoformat(),
        'refs': refs,
    }
    refs_dir = get_session_dir() / 'refs'
    refs_dir.mkdir(exist_ok=True)
    f = refs_dir / f'{session_name}.json'
    f.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def load_refs(session_name: str) -> dict:
    """加载快照编号映射，返回 {ref_id: {locator, role, name, backendNodeId}}"""
    f = get_session_dir() / 'refs' / f'{session_name}.json'
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding='utf-8'))
        return data.get('refs', {})
    except Exception:
        return {}


def close_browser(session_name: str = 'default', del_data: bool = False,
                  force: bool = False, stop_bridge_flag: bool = False) -> bool:
    """关闭指定会话的浏览器。

    - auto-connect / user_connected 模式（默认）：只停 bridge 子进程 +
      清 session，不 quit 真 Chrome（浏览器是用户的）。
    - ``force=True``：user_connected 会话也真正 quit 浏览器进程
      （dpx close --force 的语义）。
    - dpx 自管模式：调用 page.browser.quit() 彻底结束浏览器进程。
    """
    from DrissionPage._configs.chromium_options import ChromiumOptions

    sess = load_session(session_name)
    _evict_browser_cache(session_name)
    if not sess:
        return False
    port = sess.get('port')
    bridge_pid = sess.get('bridge_pid')
    user_connected = sess.get('user_connected') or bool(sess.get('probe_dir'))

    # auto-connect / user_connected 且未 --force：不 quit 真 Chrome
    if user_connected and not force:
        # bridge 默认保留：存活 bridge 是本机免 Allow 的控制通道，
        # 其他 session/下次连接可直接复用。--stop-bridge 或
        # DPX_CLOSE_STOPS_BRIDGE=1 回退旧语义（close 即停 bridge）
        stop_b = (stop_bridge_flag
                  or os.environ.get('DPX_CLOSE_STOPS_BRIDGE'))
        if bridge_pid and stop_b:
            from dp_cli.bridge_manager import stop_bridge
            try:
                stop_bridge(bridge_pid)
                if sess.get('probe_dir'):
                    from dp_cli.bridge_registry import unregister_bridge
                    unregister_bridge(sess['probe_dir'])
            except Exception as e:
                debug(f'stop_bridge(pid={bridge_pid}) 失败', e)
        elif bridge_pid and sess.get('probe_dir'):
            # 保留 bridge：仅把本 session 从注册项摘除
            try:
                from dp_cli.bridge_registry import find_bridge, \
                    _entry_path, _write_json_atomic
                ent = find_bridge(sess['probe_dir'])
                if ent and session_name in ent.get('sessions', []):
                    ent['sessions'].remove(session_name)
                    _write_json_atomic(
                        _entry_path(sess['probe_dir']), ent)
            except Exception as e:
                debug('注册项 session 摘除失败（忽略）', e)
        delete_session(session_name)
        return True

    # force 路径同样先停 bridge（auto-connect 会话），再走 quit
    if bridge_pid:
        from dp_cli.bridge_manager import stop_bridge
        try:
            stop_bridge(bridge_pid)
        except Exception as e:
            debug(f'stop_bridge(pid={bridge_pid}) 失败', e)

    if not port or not _port_open('127.0.0.1', port):
        # 端口死则浏览器已不在——跳过 attach（否则 test_connect 空转 30s）
        delete_session(session_name)
        return False

    try:
        co = ChromiumOptions(read_file=False)
        co.set_local_port(port)
        co.existing_only(True)
        # 与 _connect_browser 同理：探测 headless 同步到 options，
        # 否则对 headless 浏览器 attach 会因模式不匹配失败
        if _detect_headless(port):
            co.headless(True)
        page = _attach_page(co)
        page.browser.quit(del_data=del_data)
    except Exception as e:
        debug(f'browser.quit(port={port}) 失败，仍清理会话', e)

    delete_session(session_name)
    return True
