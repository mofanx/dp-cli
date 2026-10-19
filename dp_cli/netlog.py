# -*- coding:utf-8 -*-
"""被动网络日志 ring buffer：serve/常驻进程托管 Network 事件循环缓冲。

写端（serve 进程内）：Network.enable + requestWillBeSent/responseReceived
回调把请求摘要 append 到 ~/.dp_cli/netlog-{session}.jsonl，超过上限
（600 行水位）时截断保留尾部 ~500 条（半写删旧防膨胀，近似 ring）。

读端（任意 dpx 进程）：requests / request <id> 读文件——无需 CDP 连接。
无常驻进程缓冲文件时降级提示用 listen。
"""
import json
import threading
from pathlib import Path

_MAX_LINES = 500      # ring 上限
_TRIM_HIGH = 600      # 超过即截到 _MAX_LINES
_lock = threading.Lock()
_armed = set()        # session → 已挂载监听


def _path(session: str) -> Path:
    return Path.home() / '.dp_cli' / f'netlog-{session}.jsonl'


def _append(session: str, entry: dict) -> None:
    p = _path(session)
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        try:
            with p.open('a', encoding='utf-8') as f:
                f.write(line + '\n')
            if p.stat().st_size > 0:
                _maybe_trim(p)
        except OSError:
            pass


def _maybe_trim(p: Path) -> None:
    try:
        lines = p.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    if len(lines) <= _TRIM_HIGH:
        return
    try:
        p.write_text('\n'.join(lines[-_MAX_LINES:]) + '\n',
                     encoding='utf-8')
    except OSError:
        pass


def _entry_req(kw: dict) -> dict:
    req = kw.get('request') or {}
    return {'kind': 'req', 'id': kw.get('requestId'),
            'ts': kw.get('timestamp'),
            'url': req.get('url'), 'method': req.get('method'),
            'type': kw.get('type'),
            'headers': req.get('headers'),
            'postData': req.get('postData'),
            'hasPostData': kw.get('hasPostData')}


def _entry_resp(kw: dict) -> dict:
    resp = kw.get('response') or {}
    return {'kind': 'resp', 'id': kw.get('requestId'),
            'ts': kw.get('timestamp'),
            'url': resp.get('url'), 'status': resp.get('status'),
            'statusText': resp.get('statusText'),
            'mimeType': resp.get('mimeType'),
            'headers': resp.get('headers'),
            'encodedDataLength': resp.get('encodedDataLength'),
            'fromCache': resp.get('fromDiskCache')
                           or resp.get('fromPrefetchCache')}


def _entry_fail(kw: dict) -> dict:
    return {'kind': 'fail', 'id': kw.get('requestId'),
            'ts': kw.get('timestamp'),
            'errorText': kw.get('errorText'),
            'canceled': kw.get('canceled')}


def arm_netlog(page, session: str) -> bool:
    """给 page 挂 Network 事件采集（幂等，每 session 一次）。serve 循环内调用。"""
    if session in _armed:
        return True
    try:
        page.run_cdp('Network.enable')
        drv = page.driver
        drv.set_callback(
            'Network.requestWillBeSent',
            lambda **kw: _append(session, _entry_req(kw)))
        drv.set_callback(
            'Network.responseReceived',
            lambda **kw: _append(session, _entry_resp(kw)))
        drv.set_callback(
            'Network.loadingFailed',
            lambda **kw: _append(session, _entry_fail(kw)))
        _armed.add(session)
        return True
    except Exception:
        return False


def read_entries(session: str) -> list:
    """读 ring buffer 全部条目（时间序）。文件不存在返回 None。"""
    p = _path(session)
    if not p.exists():
        return None
    out = []
    try:
        for line in p.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return None
    return out
