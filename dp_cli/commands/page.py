# -*- coding:utf-8 -*-
"""页面操作命令: screenshot / pdf / eval / add-init-js / dialog-accept / dialog-dismiss / wait (idle/loaded/url/title/text/downloads)"""
import json
from collections import deque
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.output import ok, error, debug, format_page_info
from dp_cli.commands._utils import (session_option, _get_page,
                                    wait_network_idle, trim_options,
                                    shape_records, CdpWaitGate)

_CONSOLE_FIELDS = {'level', 'text', 'source', 'timestamp'}


def _annotate_refs(page, session):
    """给快照 ref 元素画编号 overlay：返回清除 overlay 的 JS 表达式。

    流程：refs 中 backendNodeId → DOM.resolveNode 定位 → getBoundingClientRect
    → 注入绝对定位 badge。截图后调用方跑返回的 JS 清除。
    """
    from dp_cli.session import load_refs
    from dp_cli.commands._utils import _mark_element_by_backend_id
    refs = load_refs(session) or {}
    if not refs:
        return None
    markers = []
    for rid, rd in sorted(refs.items(),
                          key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        bid = rd.get('backendNodeId')
        if not bid:
            continue
        marker = _mark_element_by_backend_id(page, bid)
        if marker:
            markers.append((rid, marker))
    if not markers:
        return None
    marks_js = json.dumps([{ 'id': rid, 'm': m} for rid, m in markers])
    inject = f"""
(function(){{
  if(document.getElementById('__dp_ann'))return;
  const layer=document.createElement('div');layer.id='__dp_ann';
  layer.style.cssText='position:absolute;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  for(const it of {marks_js}){{
    const el=document.querySelector('[data-dp-ref="'+it.m+'"]');
    if(!el)continue;
    const r=el.getBoundingClientRect();
    if(!r.width&&!r.height)continue;
    const b=document.createElement('div');
    b.textContent='ref:'+it.id;
    b.style.cssText='position:absolute;left:'+(r.x+scrollX)+'px;top:'+(r.y+scrollY)+'px;'
      +'background:#e33;color:#fff;font:bold 12px monospace;padding:1px 4px;'
      +'border-radius:3px;outline:1px solid #fff';
    layer.appendChild(b);
    el.style.outline='2px solid #e33';
  }}
  document.documentElement.appendChild(layer);
}})()
"""
    cleanup = """
(function(){const l=document.getElementById('__dp_ann');
  if(l)l.remove();
  document.querySelectorAll('[data-dp-ref]').forEach(function(e){
    e.style.outline='';e.removeAttribute('data-dp-ref')});})()
"""
    try:
        page.run_js(inject, as_expr=True)
    except Exception as e:
        debug('annotate 注入失败', e)
        return None
    return cleanup


def _shot_hash_path(session) -> Path:
    p = Path.home() / '.dp_cli' / 'shots'
    p.mkdir(parents=True, exist_ok=True)
    return p / f'{session}.sha1'


def _last_shot_hash(session) -> str:
    try:
        return _shot_hash_path(session).read_text().strip()
    except OSError:
        return ''


def _save_shot_hash(session, digest: str) -> None:
    try:
        _shot_hash_path(session).write_text(digest)
    except OSError:
        pass


def register(cli):

    @cli.command('screenshot')
    @session_option
    @click.option('--locator', default=None, help='截图特定元素')
    @click.option('--filename', default=None, help='保存路径')
    @click.option('--full-page', is_flag=True, help='截取完整页面（包括视口外）')
    @click.option('--format', 'fmt', type=click.Choice(['png', 'jpg', 'jpeg']),
                  default='png', show_default=True)
    @click.option('--annotate', is_flag=True,
                  help='在快照 ref 元素位置画编号 overlay（需先 dpx snapshot）')
    @click.option('--if-changed', is_flag=True,
                  help='与上次截图像素未变时返回 unchanged（省 token，不写文件）')
    def cmd_screenshot(session, locator, filename, full_page, fmt,
                       annotate, if_changed):
        """截图。DrissionPage 支持完整页面截图（含视口外内容）。

        \b
        示例:
          dpx screenshot
          dpx screenshot --filename page.png
          dpx screenshot --full-page
          dpx screenshot --locator "#header"
          dpx screenshot --annotate          # ref:N 编号标到图上
          dpx screenshot --if-changed        # 画面未变跳过写盘
        """
        page = _get_page(session)
        try:
            if not filename:
                from datetime import datetime
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f'screenshot_{ts}.{fmt}'
            overlay = None
            if annotate:
                overlay = _annotate_refs(page, session)
            if locator:
                ele = page.ele(locator)
                if not ele or ele.__class__.__name__ == 'NoneElement':
                    error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                    return
                ele.get_screenshot(path=filename)
            else:
                page.get_screenshot(path=filename, full_page=full_page)
            if overlay:
                try:
                    page.run_js(overlay, as_expr=True)
                except Exception:
                    pass
            result = {'filename': str(Path(filename).absolute())}
            if if_changed:
                import hashlib
                digest = hashlib.sha1(
                    Path(filename).read_bytes()).hexdigest()
                prev = _last_shot_hash(session)
                if digest == prev:
                    try:
                        Path(filename).unlink()
                    except OSError:
                        pass
                    result['unchanged'] = True
                    ok(result, msg='截图与上次一致（已跳过写盘）')
                    return
                _save_shot_hash(session, digest)
            ok(result, msg='截图已保存')
        except Exception as e:
            error(f'截图失败', code='SCREENSHOT_FAILED', detail=str(e))

    @cli.command('pdf')
    @session_option
    @click.option('--filename', default=None, help='保存路径')
    def cmd_pdf(session, filename):
        """将当前页面保存为 PDF。

        \b
        示例:
          dpx pdf
          dpx pdf --filename output.pdf
        """
        page = _get_page(session)
        try:
            if not filename:
                from datetime import datetime
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f'page_{ts}.pdf'
            result = page.run_cdp('Page.printToPDF', transferMode='ReturnAsBase64')
            import base64
            Path(filename).write_bytes(base64.b64decode(result['data']))
            ok({'filename': str(Path(filename).absolute())}, msg='PDF 已保存')
        except Exception as e:
            error(f'保存 PDF 失败', code='PDF_FAILED', detail=str(e))

    @cli.command('eval')
    @click.argument('script')
    @session_option
    @click.option('--locator', default=None, help='在特定元素上执行（this 指向该元素）')
    @click.option('--timeout', default=30, help='执行超时秒数', show_default=True)
    def cmd_eval(script, session, locator, timeout):
        """执行 JavaScript 并返回结果。

        \b
        示例:
          dpx eval "document.title"
          dpx eval "window.innerWidth"
          dpx eval "el => el.textContent" --locator "#header"
          dpx eval "return Array.from(document.links).map(l=>l.href)"
        """
        page = _get_page(session)
        try:
            if locator:
                ele = page.ele(locator, timeout=timeout)
                if not ele or ele.__class__.__name__ == 'NoneElement':
                    error(f'未找到元素: {locator}', code='ELEMENT_NOT_FOUND')
                    return
                if script.strip().startswith(('el =>', 'el=>', 'function')):
                    result = ele.run_js(f'return ({script})(this)', timeout=timeout)
                else:
                    result = ele.run_js(script, as_expr=True, timeout=timeout)
            else:
                if script.strip().startswith(('return ', 'function')):
                    result = page.run_js(script, timeout=timeout)
                else:
                    result = page.run_js(script, as_expr=True, timeout=timeout)
            ok({'result': result})
        except Exception as e:
            error(f'JavaScript 执行失败', code='JS_FAILED', detail=str(e))

    @cli.command('add-init-js')
    @click.argument('script')
    @session_option
    def add_init_js(script, session):
        """添加在每个新页面加载前执行的 JS 脚本（反检测/环境修改等）。

        \b
        示例:
          dpx add-init-js "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
          dpx add-init-js "window.__dp_cli = true"
        """
        page = _get_page(session)
        try:
            js_id = page.add_init_js(script)
            ok({'id': js_id, 'script': script[:100]}, msg='初始化脚本已添加')
        except Exception as e:
            error(f'添加初始化脚本失败', code='INIT_JS_FAILED', detail=str(e))

    # ── Console / Errors ──────────────────────────

    @cli.command('console-listen')
    @session_option
    def console_listen(session):
        """开始捕获页面 console 输出与未捕获异常。

        \b
        捕获来源：Runtime.consoleAPICalled（console.*）、
                  Runtime.exceptionThrown（未捕获异常）、Log.entryAdded。
        缓冲上限 1000 条（超出丢弃最旧记录）。

        \b
        示例:
          dpx console-listen
          dpx console-dump
          dpx console-dump --keep    # dump 后继续保持监听
        """
        page = _get_page(session)
        try:
            page.run_cdp('Runtime.enable')
            page.run_cdp('Log.enable')
            msgs = deque(maxlen=1000)
            setattr(page, '_dp_console_msgs', msgs)
            driver = page.driver

            def _on_console(**kw):
                parts = []
                for a in kw.get('args') or []:
                    if isinstance(a, dict):
                        # value 可能是 0/False/'' 等 falsy 值，需按键存在判断
                        v = a['value'] if 'value' in a else a.get('description', '')
                        parts.append(str(v))
                    else:
                        parts.append(str(a))
                msgs.append({'level': kw.get('type', 'log'),
                             'text': ' '.join(parts).strip(),
                             'source': 'console',
                             'timestamp': kw.get('timestamp')})

            def _on_exception(**kw):
                det = kw.get('exceptionDetails') or {}
                exc = det.get('exception') or {}
                text = exc.get('description') or det.get('text') or ''
                msgs.append({'level': 'error', 'text': text,
                             'source': 'exception',
                             'timestamp': kw.get('timestamp')})

            def _on_log(**kw):
                entry = kw.get('entry') or {}
                msgs.append({'level': entry.get('level', 'info'),
                             'text': entry.get('text', ''),
                             'source': entry.get('source', 'log'),
                             'timestamp': entry.get('timestamp')})

            driver.set_callback('Runtime.consoleAPICalled', _on_console)
            driver.set_callback('Runtime.exceptionThrown', _on_exception)
            driver.set_callback('Log.entryAdded', _on_log)
            ok(msg='已开始监听 console/error（上限 1000 条）')
        except Exception as e:
            error(f'启动 console 监听失败', code='CONSOLE_FAILED', detail=str(e))

    @cli.command('console-dump')
    @session_option
    @click.option('--keep', is_flag=True, help='dump 后继续保持监听')
    @click.option('--clear', 'clear_buf', is_flag=True, help='dump 后清空缓冲区')
    @trim_options
    def console_dump(session, keep, clear_buf, fields, head):
        """读取已捕获的 console/error 记录。

        默认 dump 后停止监听并清空缓冲区；--keep 保持监听，
        --clear 仅在 --keep 时有意义（继续监听但清空已读记录）。
        """
        page = _get_page(session)
        try:
            msgs = getattr(page, '_dp_console_msgs', None)
            if msgs is None:
                ok({'messages': [], 'count': 0}, msg='当前无 console 监听记录')
                return
            results = list(msgs)
            if clear_buf or not keep:
                msgs.clear()
            if not keep:
                try:
                    driver = page.driver
                    driver.set_callback('Runtime.consoleAPICalled', None)
                    driver.set_callback('Runtime.exceptionThrown', None)
                    driver.set_callback('Log.entryAdded', None)
                except Exception as e:
                    debug('console 回调清理失败', e)
            shaped, total = shape_records(results, fields, head,
                                          _CONSOLE_FIELDS)
            data = {'messages': shaped, 'count': len(shaped)}
            if head is not None:
                data['total'] = total
            ok(data)
        except Exception as e:
            error(f'读取 console 记录失败', code='CONSOLE_FAILED', detail=str(e))

    @cli.command('dialog-accept')
    @click.argument('text', required=False)
    @session_option
    def dialog_accept(text, session):
        """接受（确认）对话框。可选传入 prompt 输入内容。

        \b
        示例:
          dpx dialog-accept
          dpx dialog-accept "确认文本"
        """
        page = _get_page(session)
        try:
            page.handle_alert(accept=True, send=text)
            ok(msg='对话框已接受')
        except Exception as e:
            error(f'处理对话框失败', code='DIALOG_FAILED', detail=str(e))

    @cli.command('dialog-dismiss')
    @session_option
    def dialog_dismiss(session):
        """取消对话框。"""
        page = _get_page(session)
        try:
            page.handle_alert(accept=False)
            ok(msg='对话框已取消')
        except Exception as e:
            error(f'处理对话框失败', code='DIALOG_FAILED', detail=str(e))

    @cli.command('dialog-policy')
    @click.option('--accept', 'action', flag_value='accept',
                  help='自动接受（confirm/alert 点确定，prompt 用默认值）')
    @click.option('--dismiss', 'action', flag_value='dismiss',
                  help='自动取消/关闭')
    @click.option('--types', 'types_', default=None,
                  help='生效的对话框类型，逗号分隔：alert,confirm,prompt,beforeunload；缺省 all')
    @click.option('--clear', is_flag=True, help='清除已设策略')
    @session_option
    def dialog_policy(action, types_, clear, session):
        """设置会话级 JS 对话框自动处理策略（写入 session，后续命令生效）。

        未设策略时弹窗会阻塞后续命令（dpx dialog-accept/dismiss 手动处理）；
        设策略后 attach 自动应用，agent 无人值守场景防挂死。

        \b
        示例:
          dpx dialog-policy --accept                       # 全部自动接受
          dpx dialog-policy --dismiss --types alert,confirm
          dpx dialog-policy --clear
        """
        from dp_cli.session import load_session, save_session
        sess = load_session(session)
        if not sess:
            error(f'会话不存在: {session}', code='SESSION_NOT_FOUND')
            return
        if clear:
            sess.pop('dialog_policy', None)
            save_session(session, sess)
            ok(msg='dialog 策略已清除')
            return
        if not action:
            error('请指定 --accept / --dismiss / --clear', code='INVALID_ARGS')
            return
        types = ([t.strip() for t in types_.split(',') if t.strip()]
                 if types_ else [])
        sess['dialog_policy'] = {'action': action, 'types': types}
        save_session(session, sess)
        ok({'action': action, 'types': types or ['all']}, msg='dialog 策略已设置')

    @cli.command('sleep')
    @click.argument('seconds', type=float)
    def cmd_sleep(seconds):
        """无条件定时等待（脚本节奏控制）。

        \b
        示例:
          dpx sleep 2        # 等 2 秒
          dpx sleep 0.5      # 半秒节流
        """
        if seconds < 0:
            error('秒数不能为负', code='INVALID_ARGS')
            return
        _sleep(seconds)
        ok({'slept': seconds}, msg=f'已等待 {seconds}s')

    @cli.command('wait')
    @session_option
    @click.option('--url', default=None, help='等待 URL 包含此文本')
    @click.option('--title', default=None, help='等待标题包含此文本')
    @click.option('--locator', default=None, help='等待元素出现')
    @click.option('--locator-gone', default=None, help='等待元素消失')
    @click.option('--text', default=None, help='等待页面包含指定文本')
    @click.option('--loaded', is_flag=True, help='等待页面 DOM 加载完成')
    @click.option('--idle', type=float, is_flag=False, flag_value=2.0, default=None,
                  help='等待网络空闲（无请求持续 N 秒，默认 2），适合懒加载')
    @click.option('--downloads-done', is_flag=True, help='等待所有下载完成')
    @click.option('--download', 'downloads_done_alias', is_flag=True,
                  flag_value=True, help='--downloads-done 别名')
    @click.option('--popup', is_flag=True,
                  help='等待新 tab 弹出（点击触发后），返回新 tab id')
    @click.option('--js', 'js_expr', default=None,
                  help='等待 JS 表达式结果为真（轮询），如 "document.querySelector(\'#x\')!==null"')
    @click.option('--response', 'resp_pattern', default=None,
                  help='等待 URL 含此文本的网络响应出现（一步式，等价 listen + listen-stop）')
    @click.option('--stable', 'stable_loc', default=None,
                  help='等待元素 DOM 静默（outerHTML 连续 --for 秒不变）')
    @click.option('--for', 'stable_for', type=float, default=0.5,
                  show_default=True, help='--stable 的静默窗口秒数')
    @click.option('--timeout', default=30, help='超时秒数', show_default=True)
    @click.option('--poll', '--no-events', 'force_poll', is_flag=True,
                  help='禁用事件驱动（--text/--js），强制纯轮询')
    def wait(session, url, title, locator, locator_gone, text, loaded, idle,
             downloads_done, downloads_done_alias, popup, js_expr, resp_pattern,
             stable_loc, stable_for, timeout, force_poll):
        """等待条件满足。

        \b
        示例:
          dpx wait --loaded                          # DOM 加载完成
          dpx wait --idle                            # 网络空闲 2 秒（默认）
          dpx wait --idle 3                          # 自定义空闲时长
          dpx wait --locator "#result"               # 元素出现
          dpx wait --locator-gone ".loading"          # 加载指示器消失
          dpx wait --url "success"                   # URL 变化
          dpx wait --title "搜索结果"                 # 标题变化
          dpx wait --text "操作成功"                  # 页面含文本
          dpx wait --downloads-done                  # 下载完成
          dpx wait --js "window.appReady === true"   # JS 表达式为真
          dpx wait --response "api/login"            # 捕获到 URL 含 api/login 的响应

        \b
        懒加载典型工作流:
          dpx scroll --bottom && dpx wait --idle
          dpx count ".item"
        """
        page = _get_page(session)
        try:
            if idle is not None:
                wait_network_idle(page, idle_time=idle, timeout=timeout)
                ok(format_page_info(page), msg=f'网络已空闲 {idle}s')
            elif loaded:
                page.wait.doc_loaded(timeout=timeout)
                ok(format_page_info(page), msg='页面已加载')
            elif url:
                page.wait.url_change(url, timeout=timeout)
                ok(format_page_info(page), msg='URL 已变化')
            elif title:
                page.wait.title_change(title, timeout=timeout)
                ok(format_page_info(page), msg='标题已变化')
            elif locator:
                ele = page.wait.ele_displayed(locator, timeout=timeout)
                ok({'locator': locator, 'found': bool(ele)}, msg='元素已出现')
            elif stable_loc:
                # DOM 静默等待：outerHTML 采样连续 --for 秒不变即过。
                from dp_cli.commands._utils import resolve_locator as _rl
                real = _rl(stable_loc, session)
                end = perf_counter() + timeout
                last_html = object()
                stable_since = None
                done = False
                while perf_counter() < end:
                    try:
                        ele = page.ele(real, timeout=0.5)
                        cur = (getattr(ele, 'raw_html', None)
                               or getattr(ele, 'html', None) or '')
                    except Exception:
                        cur = ''
                    now = perf_counter()
                    if cur == last_html:
                        if stable_since is None:
                            stable_since = now
                        if now - stable_since >= stable_for:
                            done = True
                            break
                    else:
                        last_html = cur
                        stable_since = now
                    _sleep(0.1)
                if done:
                    ok({'locator': real, 'stable_for': stable_for},
                       msg=f'DOM 已静默 {stable_for}s')
                else:
                    error(f'等待超时：{timeout}s 内元素 DOM 未达到 '
                          f'{stable_for}s 静默', code='WAIT_TIMEOUT')
            elif locator_gone:
                page.wait.ele_hidden(locator_gone, timeout=timeout)
                ok({'locator': locator_gone}, msg='元素已消失')
            elif text:
                gate = CdpWaitGate(page)
                armed = gate.arm(
                    'document.body&&document.body.innerText.includes('
                    + json.dumps(text) + ')',
                    force_poll=force_poll)
                try:
                    end = perf_counter() + timeout
                    found = False
                    while perf_counter() < end:
                        if text in page.html:
                            found = True
                            break
                        if armed:
                            gate.wait(min(0.3, end - perf_counter()))
                        else:
                            _sleep(0.3)
                finally:
                    gate.disarm()
                if found:
                    ok({'text': text}, msg='文本已出现')
                else:
                    error(f'等待超时：文本未出现 "{text}"', code='WAIT_TIMEOUT')
            elif downloads_done or downloads_done_alias:
                # download_dir 是显式配置标记（download --dir 写入）；
                # page.download_path 是 DP 默认值（恒 truthy，不可作判定）
                # ——取回封装见 session._dl_path()
                from dp_cli.session import _dl_path as _dlp
                _dl_dir = _dlp(session)
                if not _dl_dir:
                    error('下载目录未设置，无法等待下载完成——'
                          '先运行 `dpx download --dir <path>` 设置',
                          code='DOWNLOAD_FAILED')
                    return
                # 跨进程断链修复：本进程重新武装 _dl_mgr
                # （download --dir 所在进程的 downloadProgress 回调随进程
                #   退出已失效，missions 在新进程里永远空）
                mgr = None
                try:
                    page.set.download_path(_dl_dir)
                    mgr = getattr(getattr(page, 'browser', None),
                                  '_dl_mgr', None)
                except Exception as e:
                    debug('download_path 重武装失败', e)
                if mgr is None:
                    error('下载管理器不可用（_dl_mgr 缺失），无法跟踪下载',
                          code='DOWNLOAD_FAILED')
                    return
                end = perf_counter() + timeout
                missions = []
                while perf_counter() < end:
                    missions = list(
                        getattr(mgr, 'missions', {}).values())
                    if missions and all(
                            getattr(m, 'is_done', True) for m in missions):
                        break
                    _sleep(0.2)
                done = [{'name': getattr(m, 'name', None),
                         'path': getattr(m, 'final_path', None)
                                 or str(getattr(m, 'folder', '') or ''),
                         'state': getattr(m, 'state', None)}
                        for m in missions]
                if missions and all(
                        getattr(m, 'is_done', True) for m in missions):
                    ok({'missions': done,
                        'download_dir': _dl_dir}, msg='下载已完成')
                elif not missions:
                    # 事后语义回退：本进程未观测到事件（跨进程断链），
                    # 但前进程已完成的下载文件应可追溯——扫目录兜底
                    from pathlib import Path as _P
                    _skip_sfx = ('.crdownload', '.part', '.partial', '.tmp')
                    try:
                        files = [f for f in _P(_dl_dir).iterdir()
                                 if f.is_file()
                                 and f.suffix.lower() not in _skip_sfx]
                    except OSError:
                        files = []
                    if files:
                        files.sort(key=lambda f: f.stat().st_mtime,
                                   reverse=True)
                        ok({'missions': [{
                            'name': f.name,
                            'path': str(f),
                            'state': 'completed（目录回扫）',
                            'size': f.stat().st_size,
                        } for f in files[:20]],
                            'download_dir': _dl_dir,
                            'note': '本进程未观测到下载事件，'
                                    '结果为目录回扫（跨进程断链）'},
                           msg='下载已完成（目录回扫）')
                    else:
                        error(f'{timeout}s 内无下载完成', code='WAIT_TIMEOUT',
                              detail='未检测到下载任务，目录无已完成文件'
                                     '（跨进程已完成下载不可追溯）')
                else:
                    error(f'{timeout}s 内无下载完成', code='WAIT_TIMEOUT',
                          detail=json.dumps(done, ensure_ascii=False))
            elif popup:
                # 新 tab 等待：tab_ids 在浏览器级对象上（_get_page 返回的
                # MixTab/ChromiumTab 没有该属性，需走 .browser 兜底）
                _tab_host = getattr(page, 'browser', None) or page
                _tab_ids = getattr(_tab_host, 'tab_ids', None)
                if _tab_ids is None:
                    error('当前页面对象不支持 tab 枚举（非 Chromium 会话）',
                          code='POPUP_FAILED')
                    return
                baseline = set(_tab_ids)
                new_id = None
                end = perf_counter() + timeout
                while perf_counter() < end:
                    try:
                        cur = set(_tab_host.tab_ids)
                    except Exception:
                        cur = baseline
                    diff = cur - baseline
                    if diff:
                        new_id = sorted(diff)[0]
                        break
                    _sleep(0.2)
                if new_id:
                    ok({'tab_id': new_id}, msg='新 tab 已弹出')
                else:
                    error(f'等待超时：{timeout}s 内无新 tab',
                          code='WAIT_TIMEOUT')
            elif js_expr:
                gate = CdpWaitGate(page)
                armed = gate.arm(f'({js_expr})', force_poll=force_poll)
                try:
                    end = perf_counter() + timeout
                    value = None
                    while perf_counter() < end:
                        try:
                            value = page.run_js(js_expr, as_expr=True)
                        except Exception:
                            value = None
                        if value:
                            break
                        if armed:
                            gate.wait(min(0.3, end - perf_counter()))
                        else:
                            _sleep(0.3)
                finally:
                    gate.disarm()
                if value:
                    ok({'expression': js_expr, 'result': value}, msg='JS 条件已满足')
                else:
                    error(f'等待超时：JS 表达式未返回真值 "{js_expr[:80]}"',
                          code='WAIT_TIMEOUT')
            elif resp_pattern:
                # 一步式响应等待：start→wait→stop；若已有监听器在跑则只等待不干扰
                started = False
                try:
                    page.listen.start(resp_pattern)
                    started = True
                except Exception as e:
                    debug('listen.start 失败，wait 仍会兜底探测', e)
                pkt = None
                try:
                    pkt = page.listen.wait(1, timeout=timeout, fit_count=False)
                finally:
                    if started:
                        try:
                            page.listen.stop()
                        except Exception as e:
                            debug('listen.stop 失败', e)
                pkts = pkt if isinstance(pkt, list) else ([pkt] if pkt else [])
                if pkts:
                    p = pkts[0]
                    ok({'url': p.url, 'method': p.method,
                        'status': p.response.status if p.response else None},
                       msg='已捕获匹配响应')
                else:
                    error(f'等待超时：未捕获 URL 含 "{resp_pattern}" 的响应',
                          code='WAIT_TIMEOUT')
            else:
                error('请至少指定一个等待条件', code='INVALID_ARGS')
        except Exception as e:
            error(f'等待失败', code='WAIT_FAILED', detail=str(e))


