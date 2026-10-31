# -*- coding:utf-8 -*-
"""UI↔API 联动 hint 层（docs/UI_API_LINKAGE.md v1.1，L0 adapter_hint /
L1 touched_apis + L0.5 现算契约）。

advisory 旁路提示——纯加性 data.hints 命名空间，绝不改行为路径：

  touched_apis   L1 因果捕获——click/fill/type 成功响应附"动作时刻
                 ±窗口内的 XHR 行"（netlog 被动缓冲内存筛）
  adapter_hint   L0 前置路由——decide/open 响应附"当前页 host 命中
                 的 adapter"（~/.dp_cli/adapters/ 注册表）

契约：
  - hints 整体缺省=无提示（非空对象）——attach_hints 全空不挂
  - 现算禁缓存（L0.5）：hint 依赖注册表/netlog 易变外部态，decide
    缓存只存引擎 resp，响应组装处当次重算注入（同 margin/tier）
  - DPX_API_HINT=0 整层关闭（响应体与旧版完全一致）
  - 全函数异常收敛——advisory 绝不炸主路径
"""
import os
import re
import time
from urllib.parse import urlsplit

_API_TYPES = {'xhr', 'fetch'}     # 与 commands.network._API_TYPES 对齐
_WIN_BEFORE = 0.5                  # 动作前因果窗口（秒）
_WIN_AFTER = 2.0                   # 动作后因果窗口（秒）
# netlog 收网宽限——netlog 由 serve/daemon 经 CDP 回调异步落盘，
# 动作返回即读会漏掉刚触发的请求行；DPX_API_HINT_GRACE=0 关。
_GRACE_S = float(os.environ.get('DPX_API_HINT_GRACE', '0.35'))


def hints_enabled() -> bool:
    """DPX_API_HINT=0 整层关闭。"""
    return os.environ.get('DPX_API_HINT', '1') != '0'


def attach_hints(data: dict, **kw) -> dict:
    """挂 data['hints'] 命名空间——值全空不挂（hints 缺省语义）。"""
    h = {k: v for k, v in kw.items() if v}
    if h:
        data['hints'] = h
    return data


def _host_of(url) -> str:
    try:
        return (urlsplit(url or '').hostname or '').lower()
    except Exception:
        return ''


def _row_ts(row):
    """netlog 行的请求时刻（unix 秒）。

    wts（requestWillBeSent.wallTime 挂钟时戳）优先；无 wts 的老行
    回退 ts——ts 是 CDP monotonic 时戳与 wall 窗口量纲不同，天然
    不命中不引入误报（monotonic ≪ unix epoch）。
    """
    v = row.get('wts')
    if v is None:
        v = row.get('ts')
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def touched_apis(session: str, t0: float, t1: float = None):
    """[t0-0.5s, t1+2s] 窗口内的 XHR netlog 行 → [{method,url,status?}]。

    因果窗口语义："这动作触发了哪个接口"——消灭 agent 翻时间线猜。
    netlog 未挂载（read_entries None）→ None 零成本短路；
    窗口无行 → None（hints 缺省，不空数组不误报）。
    """
    if not hints_enabled():
        return None
    if t1 is None:
        t1 = time.time()
    from dp_cli.netlog import read_entries
    try:
        entries = read_entries(session)
    except Exception:
        return None
    if entries is None:
        return None                        # netlog 未挂载
    if _GRACE_S > 0:
        time.sleep(_GRACE_S)               # 跨进程落盘宽限
        try:
            entries = read_entries(session) or entries
        except Exception:
            pass
    try:
        from dp_cli.commands.network import _netlog_rows
        rows = _netlog_rows(entries)
    except Exception:
        return None
    lo, hi = t0 - _WIN_BEFORE, t1 + _WIN_AFTER
    out = []
    for r in rows:
        if (r.get('type') or '').lower() not in _API_TYPES:
            continue
        ts = _row_ts(r)
        if ts is None or ts < lo or ts > hi:
            continue
        item = {'method': (r.get('method') or 'GET').upper(),
                'url': r.get('url')}
        if r.get('status') is not None:
            item['status'] = r['status']
        if r.get('failed'):
            item['failed'] = r['failed']
        out.append(item)
    return out or None


_GOAL_TOKEN_RE = re.compile(r'[a-z0-9_]+|[一-鿿]+')


def adapter_hint(url, goal: str = None):
    """当前页 URL host 命中 adapter url_template host →
    {name, method, params_keys}，无匹配 → None。

    host 相等或互为父域后缀（zhuanlan.zhihu.com 页命中 zhihu.com
    录制模板）；多命中时 goal token 命中 adapter 名的加权优先，
    否则注册表序（_list_adapters 文件名排序）首个。
    epoch 守卫天然成立——url 取当次 state/open 的当前 tab URL，
    页面变了 host 不同自然不提示陈旧 adapter。
    """
    if not hints_enabled():
        return None
    host = _host_of(url)
    if not host:
        return None
    try:
        from dp_cli.commands.network import _list_adapters
        ads = _list_adapters()
    except Exception:
        return None
    gtoks = set(_GOAL_TOKEN_RE.findall((goal or '').lower()))
    best = None
    best_score = 0.0
    for a in ads:
        ah = _host_of(a.get('url_template'))
        if not ah or not (ah == host or host.endswith('.' + ah)
                          or ah.endswith('.' + host)):
            continue
        score = 1.0
        name = a.get('name') or ''
        if gtoks and any(t in name.lower() for t in gtoks):
            score += 0.5                     # goal 相似第二加权
        if score > best_score:
            best_score = score
            best = {'name': name,
                    'method': (a.get('method') or 'GET').upper(),
                    'params_keys': a.get('params_keys') or []}
    return best
