# -*- coding:utf-8 -*-
"""所有命令模块共享的工具函数和装饰器"""
import io
import csv
import re
from time import perf_counter, sleep as _sleep

import click

from dp_cli.session import get_browser, load_refs, load_session, save_session
from dp_cli.output import error


def normalize_url(url: str) -> str:
    """补全 URL scheme，支持省略 http:// / https://"""
    if not url:
        return url
    if not url.startswith(('http://', 'https://', 'file://')):
        return 'https://' + url
    return url


def session_option(f):
    return click.option('-s', '--session', default='default',
                        help='会话名称，默认 default', show_default=True)(f)


def _get_page(session: str, raw: bool = False):
    """获取页面对象，失败则 error 退出。

    :param raw: True 时始终返回 ChromiumPage（用于浏览器级操作如标签页管理）。
                False 时返回绑定的标签页 ChromiumTab（如有），否则返回 ChromiumPage。
    """
    try:
        page = get_browser(session)
    except Exception as e:
        error(f'无法连接浏览器会话 [{session}]，请先执行 dp open',
              code='SESSION_NOT_FOUND', detail=str(e))
        return

    sess = load_session(session) or {}

    if raw:
        target = page
    else:
        # 检查是否有绑定的标签页
        tab_id = sess.get('active_tab')
        if tab_id:
            try:
                target = page.get_tab(tab_id)
            except Exception:
                sess.pop('active_tab', None)
                save_session(session, sess)
                target = page
        else:
            target = page

    # 自动重新应用 stealth：CDP init_js 绑定到 CDP session，每个 dp 命令是独立
    # Python 进程/独立 session，必须重新注册才能让下一次 navigation 生效。
    stealth_cfg = sess.get('stealth')
    if stealth_cfg:
        try:
            from dp_cli.stealth import apply_stealth, PRESETS
            preset = stealth_cfg.get('preset', 'full')
            features = set(stealth_cfg.get('features') or PRESETS.get(preset, PRESETS['full']))
            apply_stealth(target, features=features,
                          ua=stealth_cfg.get('ua'),
                          langs=stealth_cfg.get('langs'),
                          webgl_vendor=stealth_cfg.get('webgl_vendor'),
                          webgl_renderer=stealth_cfg.get('webgl_renderer'))
        except Exception:
            pass  # 不能让 stealth 失败阻塞常规命令

    return target


_KNOWN_PREFIX = re.compile(
    r'^(css[:=]|xpath[:=]|text[:=^$]|tag[:=^$]|@@?[\w]|ref:)', re.IGNORECASE)
_CSS_ID_CLASS = re.compile(r'^[#.][\w-]')           # #id  .class
_CSS_TAG_SEL = re.compile(r'^[\w-]+[.#\[][\w-]')   # div.class  a[href]  h1#title
_CSS_COMBINATOR = re.compile(r'[\[>+~]|::|:(?:nth|first|last|not|has)')  # [attr] > + ~ ::pseudo :nth-child
_XPATH_START = re.compile(r'^\(?/')                 # //div  /html  (//a)


def normalize_locator(loc: str) -> str:
    """智能补全定位器前缀，允许省略 css:/xpath:。

    规则（按优先级）：
    1. 已有已知前缀 → 原样返回
    2. 以 / 或 (/ 开头 → xpath
    3. 以 # . 开头 → css
    4. tag.class / tag#id / tag[attr] → css
    5. 含 CSS 组合符 ([ > + ~ :: :nth 等) → css
    6. 其它 → 原样返回（DrissionPage 默认当文本模糊搜索）
    """
    if _KNOWN_PREFIX.match(loc):
        return loc
    if _XPATH_START.match(loc):
        return f'xpath:{loc}'
    if _CSS_ID_CLASS.match(loc):
        return f'css:{loc}'
    if _CSS_TAG_SEL.match(loc):
        return f'css:{loc}'
    if _CSS_COMBINATOR.search(loc):
        return f'css:{loc}'
    return loc


def _mark_element_by_backend_id(page, backend_node_id: int) -> str:
    """通过 CDP 给指定 backendNodeId 的元素打一个临时 data-dp-ref 属性，
    返回 marker 字符串，调用方可用 @data-dp-ref=<marker> 精确定位。

    这是 ref:N → Element 的最鲁棒通路：
      - 绕开 CSS Modules / React 动态 class 命名
      - 绕开 xpath 结构变化
      - 只要 DOM 节点还在，backendNodeId 稳定

    失败（节点已不存在、CDP 异常）返回 None。
    """
    import uuid
    marker = 'dp' + uuid.uuid4().hex[:12]
    try:
        res = page.run_cdp('DOM.resolveNode', backendNodeId=int(backend_node_id))
    except Exception:
        return None
    if not isinstance(res, dict):
        return None
    obj_id = (res.get('object') or {}).get('objectId')
    if not obj_id:
        return None
    try:
        page.run_cdp(
            'Runtime.callFunctionOn',
            objectId=obj_id,
            functionDeclaration=(
                'function(m){'
                'try{this.setAttribute("data-dp-ref", m);}catch(e){}'
                '}'
            ),
            arguments=[{'value': marker}],
            returnByValue=True,
        )
    except Exception:
        return None
    return marker


def resolve_locator(locator: str, session: str = 'default', page=None) -> str:
    """解析定位器：ref:N 展开 + 智能前缀补全。

    如果 locator 以 'ref:' 开头，从 session 的 refs 映射中查找。
      - 有 backendNodeId 时：通过 CDP 现场打临时属性，返回 @data-dp-ref=<marker>
        （最鲁棒，绕开 CSS Modules / 动态 class / xpath 变化）
      - 无 backendNodeId 或打标失败时：回落到保存的 locator 字符串
      - 再失败，用 name 作 text 定位器

    :param page: 可选，传入避免内部再调用 _get_page；为 None 时按需懒加载。
    """
    if not locator.startswith('ref:'):
        return normalize_locator(locator)

    ref_id = locator[4:]
    refs = load_refs(session)
    if not refs:
        error(f'没有可用的 ref 映射，请先执行 dp snapshot',
              code='NO_REFS')
        raise SystemExit(1)

    ref_data = refs.get(ref_id)
    if not ref_data:
        available = sorted(refs.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        hint = f"可用范围: ref:1 ~ ref:{available[-1]}" if available else ""
        error(f'ref:{ref_id} 不存在。{hint}',
              code='REF_NOT_FOUND')
        raise SystemExit(1)

    # 1. 首选：backendNodeId 打标
    bid = ref_data.get('backendNodeId')
    if bid:
        if page is None:
            try:
                page = _get_page(session)
            except SystemExit:
                page = None
            except Exception:
                page = None
        if page is not None:
            marker = _mark_element_by_backend_id(page, bid)
            if marker:
                return f'@data-dp-ref={marker}'

    # 2. 回退：原保存的 locator 字符串
    real_loc = ref_data.get('locator')
    if real_loc and not real_loc.startswith('t:'):
        return real_loc

    # 3. 再回退：用 name 作文字定位
    name = ref_data.get('name', '')
    if name and len(name) <= 50:
        return f'text:{name}'

    error(f'ref:{ref_id} 无法解析为有效定位器 (role={ref_data.get("role")})',
          code='REF_UNRESOLVABLE')
    raise SystemExit(1)


def wait_network_idle(page, idle_time: float = 2.0, timeout: float = 30) -> bool:
    """等待网络空闲：通过 CDP 监听，直到无活跃请求持续 idle_time 秒。

    :raises TimeoutError: 若 timeout 秒内未达成空闲条件
    """
    pending = set()
    last_activity = perf_counter()

    def _on_request(**kwargs):
        nonlocal last_activity
        pending.add(kwargs.get('requestId', ''))
        last_activity = perf_counter()

    def _on_response(**kwargs):
        nonlocal last_activity
        pending.discard(kwargs.get('requestId', ''))
        last_activity = perf_counter()

    page.run_cdp('Network.enable')
    driver = page.driver
    driver.set_callback('Network.requestWillBeSent', _on_request)
    driver.set_callback('Network.loadingFinished', _on_response)
    driver.set_callback('Network.loadingFailed', _on_response)

    try:
        end = perf_counter() + timeout
        while perf_counter() < end:
            if not pending and (perf_counter() - last_activity) >= idle_time:
                return True
            _sleep(0.1)
        raise TimeoutError(f'网络未在 {timeout}s 内空闲 {idle_time}s')
    finally:
        driver.set_callback('Network.requestWillBeSent', None)
        driver.set_callback('Network.loadingFinished', None)
        driver.set_callback('Network.loadingFailed', None)


def records_to_csv(records: list) -> str:
    """将记录列表转为 CSV 字符串（含 BOM，Excel 直接打开不乱码）"""
    if not records:
        return ''
    fields = list(records[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore',
                            lineterminator='\n')
    writer.writeheader()
    for row in records:
        clean = {k: ('|'.join(str(i) for i in v) if isinstance(v, list) else v)
                 for k, v in row.items()}
        writer.writerow(clean)
    return buf.getvalue()
