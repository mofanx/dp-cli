"""
bridge 端到端集成测试

用一个假的 "Chrome browser-level WebSocket" 服务模拟 chrome://inspect 的上游，
放在后台线程的独立 asyncio loop 上运行。然后启动真 bridge 子进程，通过
HTTP 验证合成端点、通过 WebSocket 验证 page-level 客户端的 session 多路复用。

假上游只实现 bridge 会用到的几个 CDP 方法：
  - Browser.getVersion
  - Target.getTargets
  - Target.attachToTarget (flatten=True)
  - Target.detachFromTarget
  - Runtime.evaluate（带 sessionId，用于证明 session→target 路由生效）
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests

from websockets.asyncio.server import serve as ws_serve
from websockets.asyncio.client import connect as ws_connect

from dp_cli import bridge_manager


class FakeChrome:
    """后台线程里跑 asyncio event loop，监听 /devtools/browser/<uuid>。"""

    def __init__(self):
        self.browser_uuid = str(uuid.uuid4())
        self.browser_ws_path = f'/devtools/browser/{self.browser_uuid}'
        self.port: int | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._sessions: dict[str, str] = {}
        self._next_sid = 0

        self._targets = [
            {'targetId': 'TARGET_PAGE_A', 'type': 'page', 'title': 'A',
             'url': 'https://a.test/', 'attached': False},
            {'targetId': 'TARGET_PAGE_B', 'type': 'page', 'title': 'B',
             'url': 'https://b.test/', 'attached': False},
        ]

    async def _handler(self, ws):
        req = getattr(ws, 'request', None)
        req_path = req.path if req is not None else getattr(ws, 'path', '')
        if req_path != self.browser_ws_path:
            await ws.close(code=1008, reason='bad path')
            return
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mid = msg.get('id')
            method = msg.get('method')
            params = msg.get('params') or {}
            sess_id = msg.get('sessionId')

            if method == 'Browser.getVersion':
                await ws.send(json.dumps({'id': mid, 'result': {
                    'product': 'Chrome/fake-147',
                    'protocolVersion': '1.3',
                    'userAgent': 'Mozilla/5.0 (fake)',
                    'jsVersion': '12.0',
                    'revision': '@fake',
                }}))
            elif method == 'Target.getTargets':
                await ws.send(json.dumps({'id': mid, 'result': {
                    'targetInfos': self._targets,
                }}))
            elif method == 'Target.attachToTarget':
                self._next_sid += 1
                sid = f'SID{self._next_sid:04d}'
                self._sessions[sid] = params['targetId']
                await ws.send(json.dumps({'id': mid, 'result': {
                    'sessionId': sid,
                }}))
            elif method == 'Target.detachFromTarget':
                self._sessions.pop(params.get('sessionId', ''), None)
                await ws.send(json.dumps({'id': mid, 'result': {}}))
            elif method == 'Runtime.evaluate':
                tid = self._sessions.get(sess_id, '?')
                await ws.send(json.dumps({
                    'id': mid,
                    'sessionId': sess_id,
                    'result': {'result': {
                        'type': 'string',
                        'value': f'echo:{tid}:{params.get("expression")}',
                    }},
                }))
            else:
                await ws.send(json.dumps({'id': mid, 'error': {
                    'code': -32601, 'message': f'Method not found: {method}',
                }}))

    def start(self):
        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _boot():
                self._server = await ws_serve(self._handler, '127.0.0.1', 0)
                socks = list(self._server.sockets)
                self.port = socks[0].getsockname()[1]
                self._ready.set()

            self._loop.run_until_complete(_boot())
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5), 'fake chrome did not start'

    def stop(self):
        loop = self._loop
        if loop is None:
            return

        async def _shutdown():
            self._server.close()
            await self._server.wait_closed()

        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            fut.result(timeout=3)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=3)


def _make_profile(tmp: Path, port: int, ws_path: str) -> Path:
    udd = tmp / 'fake-profile'
    udd.mkdir()
    (udd / 'DevToolsActivePort').write_text(f'{port}\n{ws_path}\n')
    return udd


@pytest.mark.timeout(30)
def test_bridge_end_to_end(tmp_path):
    fake = FakeChrome()
    fake.start()
    bridge_pid: int | None = None
    try:
        udd = _make_profile(tmp_path, fake.port, fake.browser_ws_path)

        bridge_pid, bport = bridge_manager.start_bridge(
            udd, listen_port=0, ready_timeout=15,
        )

        # 1) HTTP /json/version
        vr = requests.get(f'http://127.0.0.1:{bport}/json/version', timeout=5)
        assert vr.status_code == 200, vr.text
        vj = vr.json()
        assert 'Chrome' in vj['Browser']
        assert vj['webSocketDebuggerUrl'].endswith(fake.browser_ws_path)

        # 2) HTTP /json → 两个 page target
        lr = requests.get(f'http://127.0.0.1:{bport}/json', timeout=5)
        assert lr.status_code == 200
        lst = lr.json()
        assert {t['id'] for t in lst} == {'TARGET_PAGE_A', 'TARGET_PAGE_B'}
        for t in lst:
            assert t['webSocketDebuggerUrl'] == (
                f'ws://127.0.0.1:{bport}/devtools/page/{t["id"]}'
            )

        # 3) WebSocket 多路复用：两个 page 客户端并发 evaluate，
        #    结果必须路由到各自的 target（证明 sessionId 映射正确）
        async def page_roundtrip(target_id: str, expr: str) -> str:
            url = f'ws://127.0.0.1:{bport}/devtools/page/{target_id}'
            async with ws_connect(url, max_size=None) as ws:
                await ws.send(json.dumps({
                    'id': 42,
                    'method': 'Runtime.evaluate',
                    'params': {'expression': expr},
                }))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert msg['id'] == 42
                # bridge 应剥掉 sessionId 再回客户端
                assert 'sessionId' not in msg
                return msg['result']['result']['value']

        async def run_parallel():
            return await asyncio.gather(
                page_roundtrip('TARGET_PAGE_A', 'aaa'),
                page_roundtrip('TARGET_PAGE_B', 'bbb'),
            )

        r1, r2 = asyncio.new_event_loop().run_until_complete(run_parallel())
        assert r1 == 'echo:TARGET_PAGE_A:aaa'
        assert r2 == 'echo:TARGET_PAGE_B:bbb'

    finally:
        if bridge_pid:
            bridge_manager.stop_bridge(bridge_pid, timeout=3)
        fake.stop()
