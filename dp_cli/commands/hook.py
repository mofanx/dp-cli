# -*- coding:utf-8 -*-
"""JS Hook 逆向工具: hook-set / hook-list / hook-clear / hook-dump

在页内注入 defineProperty/函数包装/XHR hook，捕获敏感行为
（cookie 写入、XHR/fetch 请求、函数调用入参、属性读写），
事件缓冲到 window.__dp_hook.buf，供结构化导出分析——逆向
定位加密入参、cookie 生成、签名请求链路的核心手段。

- hook-set：按 --type 注入对应 hook；匹配规则（cookie 名/URL/
  method/属性 get-set）可配；--persist 经 addInitScript 持久化
  （导航后仍生效，写入会话由 _get_page 自动重放）
- hook-list：当前页已装 hook + 持久化记录
- hook-clear：恢复原始实现并移除记录（--id 单个 / --all 全部）
- hook-dump：导出捕获事件 JSON（--output 落盘 / --clear 清空缓冲）
"""
import json
import re

import click

from dp_cli.output import ok, error, debug
from dp_cli.session import load_session, save_session
from dp_cli.commands._utils import (session_option, _get_page,
                                    _remove_init_js, _replay_persist_scripts,
                                    trim_options, shape_records)
from dp_cli.commands.browser import _cdp_eval

_HOOK_EVENT_FIELDS = {'seq', 'ts', 'hook', 'kind', 'data'}

# 合法 JS 点路径（func/prop hook 的目标路径，防注入）
_JS_PATH = re.compile(r'^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$')
_BUF_MAX = 5000

# __dp_hook 命名空间 + _emit 初始化（所有 hook 脚本共享的前导）
_NS_JS = (
    'window.__dp_hook=window.__dp_hook||{buf:[],seq:0,hooks:{}};'
    'var H=window.__dp_hook;'
    'if(!H._emit)H._emit=function(id,kind,data){'
    'H.buf.push({seq:H.seq++,ts:Date.now(),hook:id,kind:kind,data:data});'
    f'if(H.buf.length>{_BUF_MAX})H.buf.splice(0,H.buf.length-{_BUF_MAX});}};'
    # 参数序列化：JSON 往返容错循环引用/不可序列化值
    'if(!H._ser)H._ser=function(v){try{return JSON.parse(JSON.stringify(v))}'
    'catch(e){return String(v)}};'
)


def _regex_js(pattern: str | None) -> str:
    """pattern → new RegExp(...) 构造调用（JSON 转义，无字面量 / 转义问题）。"""
    return f'new RegExp({json.dumps(pattern)})' if pattern else 'null'


def _cookie_js(hid: str, name_pat: str | None) -> str:
    """document.cookie setter hook（Document.prototype desc 原型链查找）。"""
    return (
        f'(function(){{{_NS_JS}'
        f'if(H.hooks[{json.dumps(hid)}])return "exists";'
        'var desc=Object.getOwnPropertyDescriptor(Document.prototype,"cookie")'
        '||Object.getOwnPropertyDescriptor(HTMLDocument.prototype,"cookie");'
        'if(!desc||!desc.set)return "no-desc";'
        f'var RE={_regex_js(name_pat)};'
        'var nd={configurable:true,enumerable:desc.enumerable,'
        'get:function(){return desc.get?desc.get.call(this):""},'
        'set:function(v){try{'
        'var nm=String(v).split("=")[0].trim();'
        'if(!RE||RE.test(nm))H._emit(' + json.dumps(hid) + ',"set",{name:nm,'
        'value:String(v).slice(0,500),stack:(new Error()).stack});'
        '}catch(e){}return desc.set.call(this,v)}};'
        'try{Object.defineProperty(document,"cookie",nd)}catch(e){'
        'return "define-fail:"+e.message};'
        f'H.hooks[{json.dumps(hid)}]={{type:"cookie",desc:desc}};'
        'return "ok";})()')


def _func_js(hid: str, path: str) -> str:
    """函数调用 hook：包装原函数记录入参/调用栈。"""
    return (
        f'(function(){{{_NS_JS}'
        f'if(H.hooks[{json.dumps(hid)}])return "exists";'
        f'var parts={json.dumps(path)}.split(".");'
        'var nm=parts.pop();'
        'var obj=parts.reduce(function(o,k){return o&&o[k]},window);'
        'if(!obj)return "no-obj";'
        'var orig=obj[nm];'
        'if(typeof orig!=="function")return "not-fn";'
        'var wrapped=function(){'
        'try{H._emit(' + json.dumps(hid) + ',"call",{'
        'args:H._ser(Array.prototype.slice.call(arguments)),'
        'stack:(new Error()).stack})}catch(e){}'
        'return orig.apply(this,arguments)};'
        'wrapped.__dp_orig=orig;'
        'try{Object.defineProperty(wrapped,"name",{value:nm})}catch(e){}'
        'try{Object.defineProperty(wrapped,"toString",{value:function(){'
        'return orig.toString()}})}catch(e){}'
        'try{obj[nm]=wrapped;'
        # 非严格模式对 non-writable 属性赋值不抛错——必须回读验证
        'if(obj[nm]!==wrapped)return "wrap-fail:non-writable";'
        '}catch(e){return "wrap-fail:"+e.message};'
        f'H.hooks[{json.dumps(hid)}]={{type:"func",'
        f'path:{json.dumps(path)},orig:orig}};'
        'return "ok";})()')


def _prop_js(hid: str, path: str, when: str) -> str:
    """属性读写 hook：原型链查 desc，get/set 按 when 过滤捕获。"""
    return (
        f'(function(){{{_NS_JS}'
        f'if(H.hooks[{json.dumps(hid)}])return "exists";'
        f'var parts={json.dumps(path)}.split(".");'
        'var nm=parts.pop();'
        'var obj=parts.reduce(function(o,k){return o&&o[k]},window);'
        'if(!obj)return "no-obj";'
        # 原型链找 desc（如 navigator.webdriver 在 Navigator.prototype 上）
        'var owner=obj,desc=null;'
        'while(owner&&!desc){desc=Object.getOwnPropertyDescriptor(owner,nm);'
        'if(!desc)owner=Object.getPrototypeOf(owner)}'
        'if(!desc)return "no-desc";'
        f'var W_GET={"true" if when in ("get", "both") else "false"},'
        f'W_SET={"true" if when in ("set", "both") else "false"};'
        'var nd={configurable:true,enumerable:desc.enumerable};'
        'if("value" in desc){var v=desc.value;'
        'nd.get=function(){if(W_GET)try{H._emit(' + json.dumps(hid) + ','
        '"get",{value:H._ser(v)})}catch(e){}return v};'
        'nd.set=function(nv){if(W_SET)try{H._emit(' + json.dumps(hid) + ','
        '"set",{old:H._ser(v),new:H._ser(nv)})}catch(e){}v=nv};'
        '}else{var g=desc.get,s=desc.set;'
        'nd.get=function(){if(W_GET)try{H._emit(' + json.dumps(hid) + ','
        '"get",{})}catch(e){}return g?g.call(this):undefined};'
        'nd.set=function(nv){if(W_SET)try{H._emit(' + json.dumps(hid) + ','
        '"set",{new:H._ser(nv)})}catch(e){}'
        'return s&&s.call(this,nv)}};'
        # hook 到 desc 所在层（原型链上层）——直接 define obj 会遮蔽原型 desc
        'try{Object.defineProperty(owner,nm,nd)}catch(e){'
        'return "define-fail:"+e.message};'
        f'H.hooks[{json.dumps(hid)}]={{type:"prop",'
        f'path:{json.dumps(path)},desc:desc,owner:owner,prop:nm}};'
        'return "ok";})()')


def _xhr_js(hid: str, url_pat: str | None, m_pat: str | None) -> str:
    """XHR.open/send + fetch hook：捕获 method/url/body/调用栈。"""
    return (
        f'(function(){{{_NS_JS}'
        f'if(H.hooks[{json.dumps(hid)}])return "exists";'
        f'var URL_RE={_regex_js(url_pat)},M_RE={_regex_js(m_pat)};'
        'var oo=XMLHttpRequest.prototype.open,'
        'os=XMLHttpRequest.prototype.send;'
        'XMLHttpRequest.prototype.open=function(m,u){'
        'this.__dp_m=m;this.__dp_u=String(u);'
        'return oo.apply(this,arguments)};'
        'XMLHttpRequest.prototype.send=function(body){try{'
        'var m=String(this.__dp_m||""),u=String(this.__dp_u||"");'
        'if((!URL_RE||URL_RE.test(u))&&(!M_RE||M_RE.test(m)))'
        'H._emit(' + json.dumps(hid) + ',"xhr",{method:m,url:u,'
        'body:String(body||"").slice(0,1000),stack:(new Error()).stack});'
        '}catch(e){}return os.apply(this,arguments)};'
        'var of=window.fetch;'
        'if(of){window.fetch=function(){try{'
        'var a=arguments,r=a[0];'
        'var u=String(r&&r.url||r),m=String((a[1]&&a[1].method)||'
        '(r&&r.method)||"GET");'
        'if((!URL_RE||URL_RE.test(u))&&(!M_RE||M_RE.test(m)))'
        'H._emit(' + json.dumps(hid) + ',"fetch",{method:m,url:u,'
        'body:String((a[1]&&a[1].body)||"").slice(0,1000),'
        'stack:(new Error()).stack});'
        '}catch(e){}return of.apply(this,arguments)}};'
        f'H.hooks[{json.dumps(hid)}]={{type:"xhr",'
        'orig:{open:oo,send:os,fetch:of}};'
        'return "ok";})()')


def _hook_js(hid: str, htype: str, spec: dict) -> str:
    """按类型生成注入脚本（session 持久化重放共用）。"""
    if htype == 'cookie':
        return _cookie_js(hid, spec.get('name'))
    if htype == 'func':
        return _func_js(hid, spec['path'])
    if htype == 'prop':
        return _prop_js(hid, spec['path'], spec.get('when') or 'both')
    if htype == 'xhr':
        return _xhr_js(hid, spec.get('url'), spec.get('method'))
    raise ValueError(f'未知 hook 类型: {htype}')


def _clear_js(hid: str) -> str:
    """恢复原始实现 + 移除记录。"""
    return (
        f'(function(){{var H=window.__dp_hook||{{}};'
        f'var spec=(H.hooks||{{}})[{json.dumps(hid)}];'
        'if(!spec)return "nf";'
        'try{'
        'if(spec.type==="cookie")'
        # 优先 delete 自有 accessor 让原型 desc 恢复（保真），失败再回写 desc
        '{if(!delete document.cookie)'
        'Object.defineProperty(document,"cookie",spec.desc);}'
        'else if(spec.type==="prop"){var o=spec.owner;'
        'if(!o)return "owner-gone";'
        'Object.defineProperty(o,spec.prop,spec.desc);}'
        'else if(spec.type==="func"){'
        f'var parts=spec.path.split(".");'
        'var nm=parts.pop();'
        'var obj=parts.reduce(function(o,k){return o&&o[k]},window);'
        'if(obj)obj[nm]=spec.orig;}'
        'else if(spec.type==="xhr"){'
        'XMLHttpRequest.prototype.open=spec.orig.open;'
        'XMLHttpRequest.prototype.send=spec.orig.send;'
        'if(spec.orig.fetch)window.fetch=spec.orig.fetch;}'
        '}catch(e){return "restore-fail:"+e.message};'
        f'delete H.hooks[{json.dumps(hid)}];'
        'return "ok";})()')


def _list_js() -> str:
    return (
        '(function(){var H=window.__dp_hook||{};'
        'return Object.keys(H.hooks||{}).map(function(id){'
        'var s=H.hooks[id];'
        'return {id:id,type:s.type,path:s.path||null,'
        'buf:H.buf.length}})})()')


def register(cli):

    @cli.command('hook-set')
    @click.option('--type', 'htype', required=True,
                  type=click.Choice(['cookie', 'func', 'prop', 'xhr']),
                  help='hook 类型')
    @click.option('--path', default=None,
                  help='func/prop 目标点路径，如 JSON.stringify / '
                       'Navigator.prototype.webdriver')
    @click.option('--name', default=None,
                  help='cookie 类型：cookie 名匹配正则（默认全部捕获）')
    @click.option('--url', default=None,
                  help='xhr 类型：URL 匹配正则（默认全部捕获）')
    @click.option('--method', default=None,
                  help='xhr 类型：HTTP method 匹配正则（默认全部）')
    @click.option('--when', default='both', show_default=True,
                  type=click.Choice(['get', 'set', 'both']),
                  help='prop 类型：捕获读/写/两者')
    @click.option('--id', 'hid', default=None,
                  help='hook 标识（默认自动生成 type-N）')
    @click.option('--persist', is_flag=True,
                  help='经 addInitScript 持久化：导航后仍生效，写入会话'
                       '由后续 dp 命令自动重放')
    @session_option
    def hook_set(htype, path, name, url, method, when, hid, persist, session):
        """注入 JS hook 捕获页内行为（逆向定位加密/cookie/签名链路）。

        \b
        类型与匹配:
          cookie  document.cookie 写入（--name 按 cookie 名正则过滤）
          func    --path 函数调用入参/调用栈（如 CryptoJS.MD5）
          prop    --path 属性读写（--when get|set|both，原型链自动定位）
          xhr     XHR.open/send + fetch（--url/--method 正则过滤）

        \b
        示例:
          dp hook-set --type cookie --name 'session|token' --persist
          dp hook-set --type func --path CryptoJS.MD5
          dp hook-set --type prop --path Navigator.prototype.webdriver
          dp hook-set --type xhr --url '/api/' --method POST
        """
        # 参数前置校验（不触发浏览器 attach）
        if htype in ('func', 'prop'):
            if not path or not _JS_PATH.match(path):
                error(f'{htype} 类型需要 --path 合法 JS 点路径'
                      '（如 JSON.stringify / Navigator.prototype.webdriver）',
                      code='INVALID_ARGS')
                return
        spec = {}
        if htype == 'cookie' and name:
            spec['name'] = name
        elif htype == 'func':
            spec['path'] = path
        elif htype == 'prop':
            spec['path'] = path
            spec['when'] = when
        elif htype == 'xhr':
            if url:
                spec['url'] = url
            if method:
                spec['method'] = method

        page = _get_page(session)
        if not hid:
            try:
                keys = _cdp_eval(
                    page,
                    'JSON.stringify(Object.keys('
                    '(window.__dp_hook||{}).hooks||{}))')
                # 页内现存 + 会话持久化记录均占号——防止 remove/导航后
                # 计数法撞存活 id 或覆盖会话里仍登记的持久化 hook
                used = set(json.loads(keys or '[]'))
                used |= set((load_session(session) or {}).get('hooks')
                            or {})
                i = 1
                while f'{htype}-{i}' in used:
                    i += 1
                hid = f'{htype}-{i}'
            except Exception:
                hid = f'{htype}-1'
        script = _hook_js(hid, htype, spec)
        try:
            res = _cdp_eval(page, script)
            if res == 'exists':
                error(f'hook 已存在: {hid}（先 hook-clear 或换 --id）',
                      code='INVALID_ARGS')
                return
            if res and res != 'ok':
                error(f'hook 注入失败: {res}', code='HOOK_FAILED')
                return
            js_id = None
            if persist:
                js_id = page.add_init_js(script)
                sess = load_session(session) or {}
                hooks = dict(sess.get('hooks') or {})
                hooks[hid] = {'type': htype, 'spec': spec, 'js_id': js_id}
                sess['hooks'] = hooks
                save_session(session, sess)
        except Exception as e:
            error('hook 注入失败', code='HOOK_FAILED', detail=str(e))
            return
        ok({'id': hid, 'type': htype, 'persist': persist, 'js_id': js_id},
           msg=f'hook 已注入（{htype}）'
               + ('，导航后自动恢复' if persist else ''))

    @cli.command('hook-list')
    @session_option
    def hook_list(session):
        """列出当前页已装 hook（含持久化标记与缓冲事件数）。"""
        page = _get_page(session)
        try:
            items = _cdp_eval(page, _list_js()) or []
        except Exception as e:
            error('hook 列表获取失败', code='HOOK_FAILED', detail=str(e))
            return
        sess = load_session(session) or {}
        persisted = sess.get('hooks') or {}
        out = [{'id': i['id'], 'type': i['type'], 'path': i.get('path'),
                'buf': i.get('buf', 0), 'persist': i['id'] in persisted}
               for i in items]
        for hid in sorted(set(persisted) - {i['id'] for i in items}):
            out.append({'id': hid, 'type': persisted[hid]['type'],
                        'persist': True, 'missing': True})
        ok({'hooks': out, 'count': len(out),
            'buf': (items[0]['buf'] if items else 0)})

    @cli.command('hook-clear')
    @click.option('--id', 'hid', default=None, help='清除指定 hook')
    @click.option('--all', 'all_', is_flag=True, help='清除全部 hook')
    @click.option('--buf', is_flag=True, help='同时清空捕获缓冲')
    @session_option
    def hook_clear(hid, all_, buf, session):
        """移除 hook 并恢复原始实现。"""
        if not hid and not all_:
            error('需要 --id 或 --all', code='INVALID_ARGS')
            return
        page = _get_page(session)
        sess = load_session(session) or {}
        orig_hooks = sess.get('hooks') or {}
        hooks_cfg = dict(orig_hooks)
        try:
            removed_ids = set()
            if all_:
                items = _cdp_eval(page, _list_js()) or []
                ids = [i['id'] for i in items] + list(hooks_cfg)
                done, failed = [], []
                for h in dict.fromkeys(ids):
                    r = _cdp_eval(page, _clear_js(h))
                    spec = hooks_cfg.pop(h, None)
                    if spec and spec.get('js_id'):
                        _remove_init_js(page, spec['js_id'])
                        removed_ids.add(spec['js_id'])
                    (done if r == 'ok' or r == 'nf' else failed).append(h)
            else:
                r = _cdp_eval(page, _clear_js(hid))
                spec = hooks_cfg.pop(hid, None)
                if r == 'nf' and spec is None:
                    error(f'hook 未安装: {hid}', code='HOOK_NOT_FOUND')
                    return
                if r and r != 'ok' and r != 'nf':
                    error(f'hook 移除失败: {r}', code='HOOK_FAILED')
                    return
                done, failed = [hid], []
                if spec and spec.get('js_id'):
                    _remove_init_js(page, spec['js_id'])
                    removed_ids.add(spec['js_id'])
            if buf:
                _cdp_eval(page,
                          '(window.__dp_hook||{}).buf=[];'
                          '(window.__dp_hook||{}).seq=0;true')
            if hooks_cfg != orig_hooks:
                if hooks_cfg:
                    sess['hooks'] = hooks_cfg
                else:
                    sess.pop('hooks', None)
                save_session(session, sess)
            if removed_ids:
                # 整包移除带走其中其余 persist 段（含 rpc），立即重注册，
                # 避免到下次 _get_page 重放前的导航丢失 init-js；
                # only_ids 限定同包 spec，不重复注册其它存活脚本。
                _replay_persist_scripts(page, session, sess,
                                        only_ids=removed_ids)
        except Exception as e:
            error('hook 移除失败', code='HOOK_FAILED', detail=str(e))
            return
        result = {'cleared': done}
        if failed:
            result['failed'] = failed
        ok(result, msg=f'已移除 {len(done)} 个 hook'
                       + ('（含缓冲清空）' if buf else ''))

    @cli.command('hook-dump')
    @click.option('--output', 'out', default=None,
                  help='写入文件（默认内联输出 data.events）')
    @click.option('--clear', is_flag=True, help='导出后清空缓冲')
    @trim_options
    @session_option
    def hook_dump(out, clear, fields, head, session):
        """导出 __dp_hook 捕获事件为结构化 JSON。"""
        page = _get_page(session)
        try:
            buf = _cdp_eval(
                page, '(window.__dp_hook||{}).buf||[]') or []
        except Exception as e:
            error('hook 缓冲导出失败', code='HOOK_FAILED', detail=str(e))
            return
        buf, total = shape_records(buf, fields, head, _HOOK_EVENT_FIELDS)
        result = {'count': len(buf)}
        if head is not None:
            result['total'] = total
        if out:
            from pathlib import Path
            Path(out).write_text(
                json.dumps(buf, ensure_ascii=False, indent=2),
                encoding='utf-8')
            result['saved_to'] = out
        else:
            result['events'] = buf
        if clear:
            try:
                _cdp_eval(page,
                          '(window.__dp_hook||{}).buf=[];'
                          '(window.__dp_hook||{}).seq=0;true')
            except Exception as e:
                debug('缓冲清空失败', e)
        ok(result)
