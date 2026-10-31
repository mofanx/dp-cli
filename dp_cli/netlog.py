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
_armed = {}           # session → tab_id（监听挂在当时 tab 的 driver 上，
                      # tab 关闭/切换后 WebSocket 死但标记不能沿用，
                      # 每次 arm 比对当前 tab_id，变了就重挂）


def _path(session: str) -> Path:
    return Path.home() / '.dp_cli' / f'netlog-{session}.jsonl'


def _lock_path(session: str) -> Path:
    return Path.home() / '.dp_cli' / f'netlog-{session}.lock'


def _append(session: str, entry: dict) -> None:
    p = _path(session)
    line = json.dumps(entry, ensure_ascii=False)
    # 进程内线程锁 + 跨进程文件锁双保险（serve/daemon 并存同文件）
    with _lock:
        try:
            from dp_cli.daemon import _acquire_spawn_lock
            with _acquire_spawn_lock(_lock_path(session), timeout=5):
                with p.open('a', encoding='utf-8') as f:
                    f.write(line + '\n')
                if p.stat().st_size > 0:
                    _maybe_trim(p)
        except (OSError, TimeoutError):
            pass


def _maybe_trim(p: Path) -> None:
    try:
        lines = p.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    if len(lines) <= _TRIM_HIGH:
        return
    try:
        # tmp+rename 原子重写——并发读永远见到完整旧文件或新文件
        tmp = p.with_suffix(p.suffix + '.tmp')
        tmp.write_text('\n'.join(lines[-_MAX_LINES:]) + '\n',
                       encoding='utf-8')
        tmp.replace(p)
    except OSError:
        pass


def _entry_req(kw: dict) -> dict:
    req = kw.get('request') or {}
    return {'kind': 'req', 'id': kw.get('requestId'),
            'ts': kw.get('timestamp'),
            # wts=wallTime 挂钟时戳——hints.touched_apis 因果窗口锚
            # （ts 是 CDP monotonic，与 time.time() 不同域不可比）
            'wts': kw.get('wallTime'),
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


def arm_netlog(page, session: str, force: bool = False) -> bool:
    """给 page 挂 Network 事件采集（幂等，每 session+tab 一次）。

    serve 循环内调用。`_armed` 记 session→tab_id：调用方每次传当前
    latest_tab，tab 变了（旧 driver WebSocket 已死）自动重挂。
    force=True 先摘除标记再挂——供回调被第三方覆盖后（如 har-dump
    单槽 set_callback）的恢复路径使用。
    """
    tab_id = getattr(page, 'tab_id', None)
    if force:
        _armed.pop(session, None)
    elif session in _armed and _armed[session] == tab_id:
        return True

    def _safe(build):
        def _cb(**kw):
            try:
                _append(session, build(kw))
            except Exception:
                # 写入路径异常——摘除标记让下次 _maybe_arm_netlog 重挂
                _armed.pop(session, None)
        return _cb

    try:
        page.run_cdp('Network.enable')
        drv = page.driver
        drv.set_callback(
            'Network.requestWillBeSent', _safe(_entry_req))
        drv.set_callback(
            'Network.responseReceived', _safe(_entry_resp))
        drv.set_callback(
            'Network.loadingFailed', _safe(_entry_fail))
        try:
            _path(session).touch(exist_ok=True)
        except OSError:
            pass
        _armed[session] = tab_id
        return True
    except Exception:
        _armed.pop(session, None)
        return False


def prune_armed(active_sessions) -> None:
    """`_armed` 按活跃 session 集收编——session 删除/关闭后残留键回收
    （长驻 serve/daemon 进程的内存微漏，重启即清但长跑会积）。

    调用点：serve._maybe_arm_netlog 每条命令后——active 集即
    _PAGE_CACHE 键（本进程实际 attach 的会话全集）。
    """
    live = set(active_sessions)
    for k in list(_armed):
        if k not in live:
            _armed.pop(k, None)


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
