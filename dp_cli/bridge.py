"""
chrome://inspect/#remote-debugging 桥接模块

Chrome 144+ 的 chrome://inspect 远程调试模式只暴露 browser-level WebSocket，
缺少 /json、/json/list 等 HTTP REST API，且禁止直接创建 page-level WS。
DrissionPage / puppeteer / Playwright 等 CDP 客户端都依赖这些接口。

本模块实现一个本地代理：
  - 合成 HTTP /json、/json/version、/json/list、/json/new、/json/activate、
    /json/close 等端点
  - 通过 Target.attachToTarget(flatten=True) 在单条 browser-level WS 上做
    sessionId + id 双重多路复用，为每个 page 客户端提供独立 session
  - 客户端完全感知不到背后的多路复用

可作为子进程启动:
    python -m dp_cli.bridge --user-data-dir ~/.config/google-chrome --listen 0

启动成功后会向 stdout 打印一行标识:
    BRIDGE_READY host=127.0.0.1 port=12345

父进程通过读该行识别 bridge 已就绪。
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from websockets.asyncio.client import connect as ws_connect

log = logging.getLogger('dp_cli.bridge')


# ─────────────────────────────────────────────────────────────────────────────
# DevToolsActivePort
# ─────────────────────────────────────────────────────────────────────────────

def read_devtools_active_port(user_data_dir: Path) -> tuple[int, str]:
    f = user_data_dir / 'DevToolsActivePort'
    lines = [l.strip() for l in f.read_text().splitlines() if l.strip()]
    if len(lines) < 2:
        raise ValueError(f'{f} 格式异常')
    return int(lines[0]), lines[1]


# ─────────────────────────────────────────────────────────────────────────────
# CDP 多路复用核心
# ─────────────────────────────────────────────────────────────────────────────

class CDPBridge:
    def __init__(self, real_port: int, browser_ws_path: str) -> None:
        self.real_port = real_port
        self.browser_ws_path = browser_ws_path
        self.ws: Any = None
        self._upstream_id = itertools.count(1)
        self._pending_upstream: dict[int, dict] = {}
        self._session_to_client: dict[str, web.WebSocketResponse] = {}
        self._client_to_session: dict[int, str] = {}
        self._browser_clients: set[web.WebSocketResponse] = set()
        self._bridge_pending: dict[int, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self._connected_evt = asyncio.Event()

    async def connect(self) -> None:
        if self.ws is not None:
            return
        uri = f'ws://127.0.0.1:{self.real_port}{self.browser_ws_path}'
        log.info('connecting to real Chrome: %s', uri)
        log.info('  ⏳ 首次连接请在 Chrome 中点击 "Allow" 授权远程调试')
        self.ws = await ws_connect(uri, open_timeout=120, max_size=None)
        log.info('  ✅ 已连接真 Chrome')
        self._connected_evt.set()
        asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle_upstream_msg(msg)
        except Exception as e:
            log.warning('upstream recv loop ended: %s', e)
        finally:
            for ws in list(self._browser_clients):
                try:
                    await ws.close(code=1011, message=b'upstream closed')
                except Exception:
                    pass
            self._browser_clients.clear()
            for sid, ws in list(self._session_to_client.items()):
                try:
                    await ws.close(code=1011, message=b'upstream closed')
                except Exception:
                    pass
            self._session_to_client.clear()
            self.ws = None
            self._connected_evt.clear()

    async def _handle_upstream_msg(self, msg: dict) -> None:
        msg_id = msg.get('id')
        sess_id = msg.get('sessionId')

        if msg_id is not None:
            if msg_id in self._bridge_pending:
                fut = self._bridge_pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)
                return
            pending = self._pending_upstream.pop(msg_id, None)
            if pending:
                client_ws = pending['client_ws']
                client_id = pending['client_id']
                response = {'id': client_id}
                if 'result' in msg:
                    response['result'] = msg['result']
                if 'error' in msg:
                    response['error'] = msg['error']
                try:
                    if not client_ws.closed:
                        await client_ws.send_str(json.dumps(response))
                except Exception as e:
                    log.debug('send response to client failed: %s', e)
                return
            log.debug('orphan response id=%s', msg_id)
            return

        if sess_id and sess_id in self._session_to_client:
            client_ws = self._session_to_client[sess_id]
            ev = dict(msg)
            ev.pop('sessionId', None)
            try:
                if not client_ws.closed:
                    await client_ws.send_str(json.dumps(ev))
            except Exception:
                pass
            return

        if not sess_id:
            ev_str = json.dumps(msg)
            for c in list(self._browser_clients):
                try:
                    if not c.closed:
                        await c.send_str(ev_str)
                except Exception:
                    pass

    async def call(self, method: str, params: dict | None = None,
                   session_id: str | None = None) -> dict:
        await self._connected_evt.wait()
        mid = next(self._upstream_id)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._bridge_pending[mid] = fut
        payload: dict = {'id': mid, 'method': method}
        if params:
            payload['params'] = params
        if session_id:
            payload['sessionId'] = session_id
        async with self._send_lock:
            await self.ws.send(json.dumps(payload))
        resp = await asyncio.wait_for(fut, timeout=15)
        if 'error' in resp:
            raise RuntimeError(f'CDP {method} error: {resp["error"]}')
        return resp.get('result', {})

    async def forward_from_client(self, client_msg: dict,
                                  client_ws: web.WebSocketResponse,
                                  session_id: str | None) -> None:
        client_id = client_msg.get('id')
        if client_id is None:
            log.debug('client msg without id: %s', client_msg.get('method'))
            return
        up_id = next(self._upstream_id)
        self._pending_upstream[up_id] = {
            'client_id': client_id,
            'client_ws': client_ws,
        }
        out = dict(client_msg)
        out['id'] = up_id
        if session_id:
            out['sessionId'] = session_id
        async with self._send_lock:
            try:
                await self.ws.send(json.dumps(out))
            except Exception as e:
                log.error('forward to upstream failed: %s', e)
                self._pending_upstream.pop(up_id, None)
                err = {'id': client_id, 'error': {'code': -32000, 'message': str(e)}}
                try:
                    await client_ws.send_str(json.dumps(err))
                except Exception:
                    pass

    async def attach_page_client(self, target_id: str,
                                 client_ws: web.WebSocketResponse) -> str | None:
        try:
            result = await self.call('Target.attachToTarget',
                                     {'targetId': target_id, 'flatten': True})
        except Exception as e:
            log.error('attachToTarget(%s) failed: %s', target_id, e)
            return None
        sid = result['sessionId']
        self._session_to_client[sid] = client_ws
        self._client_to_session[id(client_ws)] = sid
        log.info('attached page %s via session %s', target_id[:16], sid[:16])
        return sid

    async def detach_page_client(self, client_ws: web.WebSocketResponse) -> None:
        sid = self._client_to_session.pop(id(client_ws), None)
        if not sid:
            return
        self._session_to_client.pop(sid, None)
        try:
            await self.call('Target.detachFromTarget', {'sessionId': sid})
        except Exception as e:
            log.debug('detach ignored: %s', e)

    def register_browser_client(self, client_ws: web.WebSocketResponse) -> None:
        self._browser_clients.add(client_ws)

    def unregister_browser_client(self, client_ws: web.WebSocketResponse) -> None:
        self._browser_clients.discard(client_ws)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _bridge_addr(request: web.Request) -> tuple[str, int]:
    st = request.app['state']
    host = request.host
    if ':' in host:
        h, _, p = host.rpartition(':')
        return h, int(p)
    return host, st['listen_port']


async def handle_version(request: web.Request) -> web.Response:
    st = request.app['state']
    bh, bp = _bridge_addr(request)
    return web.json_response({
        'Browser': st.get('browser_version', 'Chrome/Unknown'),
        'Protocol-Version': '1.3',
        'User-Agent': st.get('user_agent', ''),
        'V8-Version': st.get('v8_version', ''),
        'WebKit-Version': st.get('webkit_version', ''),
        'webSocketDebuggerUrl': f'ws://{bh}:{bp}{st["browser_ws_path"]}',
    })


def _translate_target(t: dict, bh: str, bp: int) -> dict | None:
    ttype = t.get('type', '')
    if ttype not in ('page', 'iframe', 'webview', 'background_page'):
        return None
    tid = t['targetId']
    return {
        'id': tid,
        'type': ttype,
        'title': t.get('title', ''),
        'url': t.get('url', ''),
        'webSocketDebuggerUrl': f'ws://{bh}:{bp}/devtools/page/{tid}',
        'devtoolsFrontendUrl': f'/devtools/inspector.html?ws={bh}:{bp}/devtools/page/{tid}',
        'attached': t.get('attached', False),
    }


async def handle_list(request: web.Request) -> web.Response:
    st = request.app['state']
    bridge: CDPBridge = st['bridge']
    try:
        res = await bridge.call('Target.getTargets')
    except Exception as e:
        log.error('Target.getTargets failed: %s', e)
        return web.json_response([], status=500)
    bh, bp = _bridge_addr(request)
    out = []
    for t in res.get('targetInfos', []):
        tr = _translate_target(t, bh, bp)
        if tr:
            out.append(tr)
    return web.json_response(out)


async def handle_new(request: web.Request) -> web.Response:
    st = request.app['state']
    bridge: CDPBridge = st['bridge']
    raw = request.query_string or 'about:blank'
    if raw.startswith('url='):
        url = raw[4:]
    elif '=' in raw:
        url = 'about:blank'
    else:
        url = raw
    try:
        res = await bridge.call('Target.createTarget', {'url': url})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)
    bh, bp = _bridge_addr(request)
    tid = res['targetId']
    return web.json_response({
        'id': tid,
        'type': 'page',
        'title': '',
        'url': url,
        'webSocketDebuggerUrl': f'ws://{bh}:{bp}/devtools/page/{tid}',
    })


async def handle_close(request: web.Request) -> web.Response:
    st = request.app['state']
    bridge: CDPBridge = st['bridge']
    tid = request.match_info['target_id']
    try:
        await bridge.call('Target.closeTarget', {'targetId': tid})
    except Exception as e:
        return web.Response(text=f'error: {e}', status=500)
    return web.Response(text='Target is closing')


async def handle_activate(request: web.Request) -> web.Response:
    st = request.app['state']
    bridge: CDPBridge = st['bridge']
    tid = request.match_info['target_id']
    try:
        await bridge.call('Target.activateTarget', {'targetId': tid})
    except Exception as e:
        return web.Response(text=f'error: {e}', status=500)
    return web.Response(text='Target activated')


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket 入口
# ─────────────────────────────────────────────────────────────────────────────

PAGE_PATH_RE = re.compile(r'^/devtools/page/(?P<tid>[^/]+)$')
BROWSER_PATH_RE = re.compile(r'^/devtools/browser/(?P<uid>[^/]+)$')


async def handle_ws(request: web.Request) -> web.StreamResponse:
    st = request.app['state']
    bridge: CDPBridge = st['bridge']
    path = request.rel_url.path

    client_ws = web.WebSocketResponse(max_msg_size=0)
    await client_ws.prepare(request)

    session_id: str | None = None
    kind: str
    if m := PAGE_PATH_RE.match(path):
        kind = 'page'
        tid = m.group('tid')
        session_id = await bridge.attach_page_client(tid, client_ws)
        if not session_id:
            await client_ws.close(code=1011, message=b'attachToTarget failed')
            return client_ws
    elif BROWSER_PATH_RE.match(path):
        kind = 'browser'
        bridge.register_browser_client(client_ws)
    else:
        await client_ws.close(code=1008, message=b'unknown ws path')
        return client_ws

    log.info('client connected: %s (kind=%s)', path, kind)

    try:
        async for msg in client_ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue
                await bridge.forward_from_client(payload, client_ws, session_id)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                try:
                    payload = json.loads(msg.data)
                    await bridge.forward_from_client(payload, client_ws, session_id)
                except Exception:
                    pass
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.ERROR):
                break
    finally:
        if kind == 'page':
            await bridge.detach_page_client(client_ws)
        else:
            bridge.unregister_browser_client(client_ws)
        log.info('client disconnected: %s', path)

    return client_ws


# ─────────────────────────────────────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────────────────────────────────────

def _extract_port(site: web.TCPSite) -> int:
    """获取 TCPSite 实际绑定的端口。

    注意：``site.name`` 对 ``port=0`` 会返回 0（不更新为实际端口），
    所以必须从底层 socket 拿。
    """
    server = getattr(site, '_server', None)
    if server is not None and getattr(server, 'sockets', None):
        return server.sockets[0].getsockname()[1]
    # 兜底：尝试解析 site.name
    name = site.name
    return int(name.rsplit(':', 1)[-1])


async def main_async(user_data_dir: Path, host: str, port: int) -> None:
    real_port, browser_ws_path = read_devtools_active_port(user_data_dir)
    log.info('DevToolsActivePort: port=%d ws_path=%s', real_port, browser_ws_path)

    bridge = CDPBridge(real_port, browser_ws_path)
    await bridge.connect()
    version = await bridge.call('Browser.getVersion')

    app = web.Application()
    app['state'] = {
        'bridge': bridge,
        'listen_port': port,
        'browser_ws_path': browser_ws_path,
        'browser_version': version.get('product', 'Chrome/Unknown'),
        'user_agent': version.get('userAgent', ''),
        'v8_version': version.get('jsVersion', ''),
        'webkit_version': version.get('revision', ''),
    }

    app.router.add_get('/json/version', handle_version)
    app.router.add_get('/json', handle_list)
    app.router.add_get('/json/list', handle_list)
    app.router.add_get('/json/new', handle_new)
    app.router.add_put('/json/new', handle_new)
    app.router.add_post('/json/new', handle_new)
    app.router.add_get('/json/close/{target_id}', handle_close)
    app.router.add_get('/json/activate/{target_id}', handle_activate)
    app.router.add_get('/devtools/{path:.*}', handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    actual_port = _extract_port(site) if port == 0 else port
    app['state']['listen_port'] = actual_port

    log.info('Bridge listening at http://%s:%d (proxy → real Chrome :%d)',
             host, actual_port, real_port)

    # 就绪标记：供父进程识别
    print(f'BRIDGE_READY host={host} port={actual_port}', flush=True)

    # 等待终止信号
    stop_evt = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_evt.set)
        except NotImplementedError:
            pass
    try:
        await stop_evt.wait()
    finally:
        await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s',
                        datefmt='%H:%M:%S',
                        stream=sys.stderr)
    p = argparse.ArgumentParser(prog='dp_cli.bridge')
    p.add_argument('--user-data-dir', type=Path, required=True,
                   help='Chrome user-data-dir，用来读取 DevToolsActivePort')
    p.add_argument('--listen', type=int, default=0,
                   help='监听端口，0 表示随机分配')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    try:
        asyncio.run(main_async(args.user_data_dir, args.host, args.listen))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
