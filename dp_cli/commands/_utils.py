# -*- coding:utf-8 -*-
"""所有命令模块共享的工具函数和装饰器"""
import io
import csv
import re
import threading
from time import perf_counter, sleep as _sleep

import click

from dp_cli.session import get_browser, load_refs, load_session, save_session
from dp_cli.output import error, debug


def normalize_url(url: str) -> str:
    """补全 URL scheme，支持省略 http:// / https://"""
    if not url:
        return url
    if not url.startswith(('http://', 'https://', 'file://')):
        return 'https://' + url
    return url


def session_option(f):
    return click.option('-S', '--session', default='default',
                        help='会话名称，默认 default', show_default=True)(f)


def trim_options(f):
    """--fields/--head 输出裁剪选项组（记录列表类命令共用）。"""
    f = click.option('--head', type=int, default=None,
                     help='只输出前 N 条记录（附 total 字段说明截断）')(f)
    f = click.option('--fields', default=None,
                     help='字段白名单，逗号分隔；非法字段名报错')(f)
    return f


def shape_records(records, fields, head, valid_fields):
    """应用 --fields/--head 裁剪，返回 (shaped, total)；参数非法经 error() 退出。"""
    total = len(records)
    if fields is not None:
        keys = [k.strip() for k in fields.split(',') if k.strip()]
        bad = [k for k in keys if k not in valid_fields]
        if not keys or bad:
            error(f'--fields 非法字段: {", ".join(bad) or "（空）"}'
                  f'（可用: {", ".join(sorted(valid_fields))}）',
                  code='INVALID_ARGS')
        records = [{k: (r.get(k) if isinstance(r, dict) else None)
                    for k in keys} for r in records]
    if head is not None:
        if head < 0:
            error('--head 需为非负整数', code='INVALID_ARGS')
        records = records[:head]
    return records, total


def frame_option(f):
    return click.option('--frame', default=None,
                        help='限定在 iframe 内查找元素（值为 iframe 的定位器，'
                             '支持 css:/xpath:/pw:/ref:），如 --frame "css:iframe"')(f)


def frame_ctx(page, frame_loc: str = None, session: str = 'default'):
    """frame_loc 为 None 时返回 page 本身，否则返回该 iframe 的 frame 上下文。

    返回对象（ChromiumFrame）同样提供 ele()/eles()，可直接替换 page 使用。
    找不到 iframe 时 error(ELEMENT_NOT_FOUND) 退出。
    """
    if not frame_loc:
        return page
    loc = resolve_locator(frame_loc, session, page)
    fr = page.get_frame(loc, timeout=3)
    # 按类名判定：真实 NoneElement.__getattr__ 对任意属性抛
    # ElementNotFoundError（非 AttributeError），getattr 默认值挡不住
    if fr is None or fr.__class__.__name__ == 'NoneElement':
        error(f'iframe 未找到: {frame_loc}', code='ELEMENT_NOT_FOUND')
        return page
    return fr


def _get_page(session: str, raw: bool = False, inject_recording: bool = True):
    """获取页面对象，失败则 error 退出。

    :param raw: True 时始终返回 ChromiumPage（用于浏览器级操作如标签页管理）。
                False 时返回绑定的标签页 ChromiumTab（如有），否则返回当前激活标签页。
    :param inject_recording: session 录制中时是否自动注入录制器。
    """
    try:
        page = get_browser(session)
    except Exception as e:
        # 分流：会话文件存在但浏览器不可达（断连）→ SESSION_DISCONNECTED；
        # 会话本就不存在 → SESSION_NOT_FOUND（提示先 dpx open）
        code = ('SESSION_DISCONNECTED' if load_session(session)
                else 'SESSION_NOT_FOUND')
        error(f'无法连接浏览器会话 [{session}]，请先执行 dpx open',
              code=code, detail=str(e))
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
            try:
                target = page.latest_tab or page
            except Exception:
                target = page

    # 自动重新应用 stealth：CDP init_js 绑定到 CDP session，每个 dpx 命令是独立
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
        except Exception as e:
            # 不能让 stealth 失败阻塞常规命令
            debug('stealth 重注入失败', e)

    if inject_recording and sess.get('recording'):
        try:
            from dp_cli.recorder import inject_recorder
            inject_recorder(target)
        except Exception as e:
            debug('录制器重注入失败', e)

    # 会话级 CDP override 重放（emulate/intercept 每条命令重新应用）
    from dp_cli.commands.emulate import _apply_emulate, _apply_intercept
    emulate_cfg = sess.get('emulate')
    if emulate_cfg:
        try:
            _apply_emulate(target, emulate_cfg)
        except Exception as e:
            debug('emulate 重放失败', e)
    intercept_cfg = sess.get('intercept')
    if intercept_cfg:
        try:
            _apply_intercept(target, intercept_cfg)
        except Exception as e:
            debug('intercept 重放失败', e)
    browser_cdp_cfg = sess.get('browser_cdp')
    if browser_cdp_cfg:
        try:
            from dp_cli.commands.browser import _apply_browser_cdp
            _apply_browser_cdp(target, browser_cdp_cfg)
        except Exception as e:
            debug('browser_cdp 重放失败', e)
    _replay_persist_scripts(target, session, sess)

    # dialog 预设策略重放：accept/dismiss 存 session，每条命令 attach 时应用
    dpol = sess.get('dialog_policy')
    if dpol:
        try:
            accept = dpol.get('action', 'accept') == 'accept'
            types = set(dpol.get('types') or [])
            if not types or 'all' in types:
                target._alert.auto = accept
            else:
                _orig_open = target._on_alert_open

                def _typed_open(_orig=_orig_open, _t=types,
                                _a=accept, _tgt=target, **kw):
                    _orig(**kw)
                    if kw.get('type') in _t:
                        try:
                            _tgt._handle_alert(_a)
                        except Exception:
                            pass

                target.driver.set_callback('Page.javascriptDialogOpening',
                                           _typed_open, immediate=True)
        except Exception as e:
            debug('dialog_policy 应用失败', e)

    # 挂起 dialog 检测：无策略且有弹窗时快速告警（否则后续命令挂死等待）
    if not dpol and getattr(target, '_has_alert', False):
        try:
            import sys as _sys
            print(f'[dp-cli] 警告：存在挂起的 JS 对话框'
                  f'（dpx dialog-accept/dialog-dismiss 处理，'
                  f'或 dpx dialog-policy 预设策略）', file=_sys.stderr)
        except Exception:
            pass

    return target


def _replay_persist_scripts(target, session, sess, only_ids=None):
    """重放会话持久化 init-js：rpc/hooks 的 N 个脚本合并为单个 bundle
    一次 addScriptToEvaluateOnNewDocument 注册，省 N-1 个 CDP RTT。

    每段 try/catch 包裹隔离运行时异常（原独立注册语义）；各段均为
    生成器模板 + json.dumps 插值，语法安全由生成器保证。
    js_id 共享 bundle id 并落盘：跨进程 unregister/clear 拿到当前
    生效 id 才能移除整包（否则只见注册时单 id，bundle 残留无法清除）。

    :param only_ids: 只重放 js_id 命中该集合的 spec——用于 unregister/
                     clear 移除整包后精准补回其中剩余段；其它 js_id 的
                     脚本仍在 target 上存活，不得重复注册。None 表示
                     全量重放（_get_page 路径）。
    """
    def _wanted(spec):
        return only_ids is None or spec.get('js_id') in only_ids

    bundle_parts = []

    rpc_cfg = sess.get('rpc')
    if rpc_cfg:
        try:
            from dp_cli.commands.rpc import _register_js
            for rpc_name, spec in rpc_cfg.items():
                if not _wanted(spec):
                    continue
                try:
                    bundle_parts.append(_register_js(rpc_name, spec['body']))
                except Exception as e:
                    debug(f'rpc 重放失败 name={rpc_name}', e)
        except Exception as e:
            debug('rpc 重放失败', e)
    hooks_cfg = sess.get('hooks')
    if hooks_cfg:
        try:
            from dp_cli.commands.hook import _hook_js
            for hid, spec in hooks_cfg.items():
                if not _wanted(spec):
                    continue
                try:
                    bundle_parts.append(_hook_js(hid, spec['type'], spec['spec']))
                except Exception as e:
                    debug(f'hook 重放失败 id={hid}', e)
        except Exception as e:
            debug('hook 重放失败', e)

    if not bundle_parts:
        return
    try:
        bundle_js = ';'.join(
            f'try{{{s}}}catch(e){{}}' for s in bundle_parts)
        bundle_id = target.add_init_js(bundle_js)
        for spec in (rpc_cfg or {}).values():
            if _wanted(spec):
                spec['js_id'] = bundle_id
        for spec in (hooks_cfg or {}).values():
            if _wanted(spec):
                spec['js_id'] = bundle_id
        try:
            save_session(session, sess)
        except Exception as e:
            debug('bundle js_id 回写失败', e)
    except Exception as e:
        debug('persist 脚本打包注册失败', e)


def _remove_init_js(page, js_id):
    """移除 init-js，绕开 DrissionPage remove_init_js 的 _init_jss 门控。

    DP 的 remove_init_js 只在 script_id in self._init_jss（本进程
    实例属性，跨进程为空）时执行 CDP 调用——跨进程 unregister 静默
    失效。直接调 Page.removeScriptToEvaluateOnNewDocument 绕过；
    identifier 失效（session 已死/从未注册）吞异常返回 False。
    """
    if not js_id:
        return False
    try:
        page.run_cdp('Page.removeScriptToEvaluateOnNewDocument',
                     identifier=js_id)
        # 同步清理 DP 内部注册表（若本进程注册过）
        jss = getattr(page, '_init_jss', None)
        if isinstance(jss, list) and js_id in jss:
            jss.remove(js_id)
        return True
    except Exception:
        return False


_KNOWN_PREFIX = re.compile(
    r'^(css[:=]|xpath[:=]|text[:=^$]|tag[:=^$]|@@?[\w]|ref:|pw:)', re.IGNORECASE)
_CSS_ID_CLASS = re.compile(r'^[#.][\w-]')           # #id  .class
_CSS_TAG_SEL = re.compile(r'^[\w-]+[.#\[][\w-]')   # div.class  a[href]  h1#title
_CSS_COMBINATOR = re.compile(r'[\[>+~]|::|:(?:nth|first|last|not|has)')  # [attr] > + ~ ::pseudo :nth-child
_XPATH_START = re.compile(r'^\(?/')                 # //div  /html  (//a)


def wrap_boundary(content: str) -> tuple:
    """内容边界包裹：防不可信页面文本 prompt-injection 混入 agent 上下文。

    返回 (wrapped_text, nonce)。调用方把 nonce 放进 data.boundary，
    文本两头加 UNTRUSTED-CONTENT 标记行，agent 侧可识别并隔离。
    """
    import secrets
    nonce = secrets.token_hex(4)
    return (f'<<<UNTRUSTED-CONTENT nonce="{nonce}">>>\n'
            f'{content}\n<<<END-UNTRUSTED-CONTENT nonce="{nonce}">>>',
            nonce)


def boundary_option(f):
    return click.option(
        '--boundary', is_flag=True, default=False,
        help='页面文本输出加不可信内容边界（防 prompt-injection；'
             'nonce 见 data.boundary）')(f)


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


def _resolve_pw(expr: str, session: str, page) -> str:
    """解析 pw: 表达式 → 在页面上打标 → 返回 @data-dp-ref=<marker>。

    失败（语法错 / 未匹配 / 浏览器不可用）会调用 error 并退出。
    """
    from dp_cli.locators import parse_pw, build_pw_js, PwParseError
    try:
        matchers = parse_pw(expr)
    except PwParseError as e:
        error(f'pw 定位器语法错误: {e}', code='PW_SYNTAX')
        raise SystemExit(1)

    if page is None:
        try:
            page = _get_page(session)
        except SystemExit:
            raise
        except Exception as e:
            code = ('SESSION_DISCONNECTED' if load_session(session)
                    else 'SESSION_NOT_FOUND')
            error('无法连接浏览器会话', code=code, detail=str(e))
            raise SystemExit(1)

    js = build_pw_js(matchers)
    try:
        marker = page.run_js(js)
    except Exception as e:
        error(f'pw 定位器求值失败', code='PW_EVAL_FAILED', detail=str(e))
        raise SystemExit(1)

    if not marker:
        error(f'pw 定位器未匹配到元素: pw:{expr}', code='PW_NOT_FOUND')
        raise SystemExit(1)

    return f'@data-dp-ref={marker}'


def resolve_locator(locator: str, session: str = 'default', page=None) -> str:
    """解析定位器：ref:N 展开 + pw: 表达式求值 + 智能前缀补全。

    - pw:<expr>：Playwright 风格（role/text/label/placeholder/alt/title/
      testid/css/xpath/nth/has-text/visible），支持 >> 链式。通过 JS 求值
      + 打标，返回 @data-dp-ref=<marker>。
    - ref:N：从 session 的 refs 映射中查找。
        · 有 backendNodeId 时：通过 CDP 现场打临时属性，返回 @data-dp-ref=
          （最鲁棒，绕开 CSS Modules / 动态 class / xpath 变化）
        · 无 backendNodeId 或打标失败时：回落到保存的 locator 字符串
        · 再失败，用 name 作 text 定位器
    - 其它：智能补全 css:/xpath: 前缀。

    :param page: 可选，传入避免内部再调用 _get_page；为 None 时按需懒加载。
    """
    if locator.startswith('pw:'):
        return _resolve_pw(locator[3:], session, page)
    if not locator.startswith('ref:'):
        return normalize_locator(locator)

    ref_id = locator[4:]
    refs = load_refs(session)
    if not refs:
        error(f'没有可用的 ref 映射，请先执行 dpx snapshot',
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


class CdpWaitGate:
    """CDP 事件驱动的等待唤醒门：事件到达即唤醒重检，无事件按 poll 节拍回落。

    事件源：
    - Runtime.bindingCalled：页内 watcher（MutationObserver + 低频 interval）
      检测到条件变真后经 __dp_wait binding 上报——JS 侧探测零 CDP 轮询流量
    - Page.frameNavigated/domContentEventFired/loadEventFired：导航唤醒
      重检，并在新文档重注入 watcher（导航后 JS 上下文销毁）

    arm() 任一步失败（旧内核不支持 addBinding / driver 不可用）或调用方
    强制轮询 → armed=False，调用方走纯 _sleep 路径，语义完全等价。
    """
    BIND_NAME = '__dp_wait'
    _WATCHER = (
        '(function(){if(window.__dp_wait_armed)return"armed";'
        'window.__dp_wait_armed=1;var done=false;'
        'function fin(){done=true;'
        'try{clearInterval(window.__dp_wait_iv)}catch(e){}'
        'try{if(window.__dp_wait_ob)window.__dp_wait_ob.disconnect()}'
        'catch(e){}}'
        'function chk(){if(done)return;var v=false;'
        'try{v=!!(__COND__)}catch(e){}'
        'if(v){fin();try{window.__dp_wait("hit")}catch(e){}}}'
        'try{window.__dp_wait_ob=new MutationObserver(chk);'
        'window.__dp_wait_ob.observe('
        'document.documentElement||document,'
        '{subtree:true,childList:true,attributes:true,characterData:true})}'
        'catch(e){}'
        'window.__dp_wait_iv=setInterval(chk,250);chk();return "ok"})()')
    # disarm 时页内清理：撤掉 interval/observer/armed 标记（等待超时/放弃
    # 场景下 watcher 否则会空转到导航销毁）
    _WATCHER_CLEAN = (
        '(function(){try{clearInterval(window.__dp_wait_iv)}catch(e){}'
        'try{if(window.__dp_wait_ob)window.__dp_wait_ob.disconnect()}'
        'catch(e){}window.__dp_wait_armed=0;return"clean"})()')
    _PAGE_EVENTS = ('Page.frameNavigated', 'Page.domContentEventFired',
                    'Page.loadEventFired')
    _EVENTS = ('Runtime.bindingCalled',) + _PAGE_EVENTS

    def __init__(self, page):
        self.page = page
        self.wake = threading.Event()
        self.nav = threading.Event()
        self.armed = False
        self._driver = None
        self._cond = None

    def arm(self, cond_js=None, force_poll=False) -> bool:
        """注册 binding + Page 事件 + 注入页内 watcher。返回是否成功武装。"""
        if force_poll:
            return False
        self._cond = cond_js
        try:
            self._driver = self.page.driver
            self.page.run_cdp('Runtime.enable')
            self.page.run_cdp('Runtime.addBinding', name=self.BIND_NAME)
            self.page.run_cdp('Page.enable')
            self._driver.set_callback('Runtime.bindingCalled', self._on_binding)
            for ev in self._PAGE_EVENTS:
                self._driver.set_callback(ev, self._on_nav)
            self.armed = True
            if cond_js:
                self.reinject()
        except Exception as e:
            debug('事件驱动等待不可用，回落轮询', e)
            self.disarm()
        return self.armed

    def reinject(self):
        """（导航后的新文档）重新注入页内条件 watcher。"""
        if not (self.armed and self._cond):
            return
        try:
            self.page.run_js(
                self._WATCHER.replace('__COND__', f'({self._cond})'))
        except Exception as e:
            # 导航瞬断期注入失败无碍：后续 Page 事件/轮询节拍会再触发重试
            debug('watcher 注入失败', e)

    def wait(self, seconds: float) -> bool:
        """阻塞至事件唤醒或超时；返回 True=被事件唤醒。自动处理导航重注入。"""
        fired = self.wake.wait(max(0.0, seconds))
        self.wake.clear()
        if self.nav.is_set():
            self.nav.clear()
            self.reinject()
        return fired

    def disarm(self):
        """摘除全部回调 + 页内 watcher 清理（binding 留会话内无副作用）。"""
        if self._driver is not None:
            for ev in self._EVENTS:
                try:
                    self._driver.set_callback(ev, None)
                except Exception:
                    pass
            self._driver = None
        if self.armed and self._cond:
            try:
                self.page.run_js(self._WATCHER_CLEAN)
            except Exception as e:
                # 页面已死/导航瞬断时清理失败无碍——watcher 随文档销毁
                debug('watcher 页内清理失败', e)
        self.armed = False

    def _on_binding(self, **kw):
        if kw.get('name') == self.BIND_NAME:
            self.wake.set()

    def _on_nav(self, **kw):
        self.nav.set()
        self.wake.set()


_ACTIONABLE_JS = """
const el=this;
if(!el)return{ok:false,reason:'not_found'};
  const cs=getComputedStyle(el);
  const r=el.getBoundingClientRect();
  const visible=r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none';
  if(!visible)return{ok:false,reason:'hidden',rect:[r.x,r.y,r.width,r.height]};
  if(el.disabled||el.getAttribute('aria-disabled')==='true')
    return{ok:false,reason:'disabled'};
  const cx=r.x+r.width/2,cy=r.y+r.height/2;
  const hit=document.elementFromPoint(cx,cy);
  let occluder=null;
  if(hit&&hit!==el&&!el.contains(hit)&&!hit.contains(el))
    occluder={tag:hit.tagName.toLowerCase(),id:hit.id||null,
              cls:(hit.className||'').toString().slice(0,80)||null};
  return{ok:!occluder,reason:occluder?'occluded':'ok',
         rect:[r.x,r.y,r.width,r.height],center:[cx,cy],occluder};
"""


def wait_actionable(ctx, locator, index=1, timeout=10.0, settle=0.15,
                    hit_check=True):
    """PW 风格 actionability 等待：存在→可见→enabled→rect 两帧稳定→命中检测。

    :param ctx: frame_ctx(page, frame, session) 返回值（有 .ele/.eles/.run_js）
    :param hit_check: False 跳过 elementFromPoint 遮挡检查
    :returns: (ele, None) 成功；(ele_or_None, {'reason':..., 'occluder':...}) 失败
    """
    deadline = perf_counter() + timeout
    last_rect = None
    stable_from = None
    while True:
        try:
            ele = ctx.ele(locator, index=index, timeout=0.3)
        except Exception:
            ele = None
        missing = (ele is None or ele.__class__.__name__ == 'NoneElement')
        if missing:
            info = {'reason': 'not_found'}
        else:
            try:
                r = ele.run_js(_ACTIONABLE_JS)
                if not isinstance(r, dict):
                    r = {'ok': True, 'reason': 'ok'}
            except Exception:
                r = {'ok': True, 'reason': 'ok'}  # 求值失败不阻塞（保守放行）
            info = r
            if not hit_check and info.get('reason') == 'occluded':
                info = dict(info); info['ok'] = True; info['reason'] = 'ok'
        if info.get('ok'):
            rect = tuple(info.get('rect') or ())
            if rect and rect == last_rect:
                if stable_from is None:
                    stable_from = perf_counter()
                if perf_counter() - stable_from >= settle:
                    return ele, None
            else:
                last_rect = rect
                stable_from = None
            if info.get('reason') == 'ok' and not rect:
                return ele, None
        else:
            last_rect = None
            stable_from = None
        if perf_counter() >= deadline:
            info.setdefault('reason', 'timeout')
            return ele, info
        _sleep(0.08)


def _wait_ele(ctx, locator, index=1, timeout=10.0, no_wait=False,
              hit_check=True, code='ELEMENT_FAILED', label='操作'):
    """actionability 等待的统一入口：命中失败即 error 输出并返回 None。

    :returns: 元素对象，或 None（错误已输出，调用方直接 return）
    """
    def _missing(e):
        return e is None or e.__class__.__name__ == 'NoneElement'

    if no_wait:
        try:
            ele = ctx.ele(locator, index=index, timeout=0)
        except Exception:
            ele = None
        if _missing(ele):
            error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
            return None
        return ele

    ele, fail = wait_actionable(ctx, locator, index=index, timeout=timeout,
                                hit_check=hit_check)
    if fail is None:
        return ele
    reason = fail.get('reason', 'timeout')
    if reason == 'not_found':
        error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
        return None
    if reason == 'occluded':
        occ = fail.get('occluder') or {}
        detail = (f"遮挡者: <{occ.get('tag')}> id={occ.get('id')} "
                  f"class={occ.get('cls')}")
        error(f'{label}失败: 元素被遮挡 {locator}', code=code, detail=detail)
        return None
    error(f'{label}失败: 元素不可交互({reason}) {locator}', code=code)
    return None


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
