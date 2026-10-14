# -*- coding:utf-8 -*-
"""JSRPC 桥命令: rpc-register / rpc-call / rpc-list / rpc-unregister

把浏览器当作"算法解析器"：将 JS 函数（页面内的加密/签名函数，
或自定义包装）暴露为命名 RPC，本地 Python/脚本远程调用。

- rpc-register：函数挂到 window.__dp_rpc[name]；--persist 时写入会话
  并经 Page.addScriptToEvaluateOnNewDocument 持久化（导航后仍可用，
  每条命令经 _get_page 自动重放）
- rpc-call：按名调用；__dp_rpc[name] 优先，否则按 window 点路径解析
  原生函数（如 JSON.stringify / btoa）；参数走 JSON，awaitPromise +
  returnByValue 结构化返回
- rpc-list / rpc-unregister：注册表管理与移除
"""
import json
import re

import click

from dp_cli.output import ok, error, debug
from dp_cli.session import load_session, save_session
from dp_cli.commands._utils import session_option, _get_page
from dp_cli.commands.browser import _cdp_eval

# 合法 JS 点路径（用于原生函数回落调用，如 JSON.stringify）
_JS_PATH = re.compile(r'^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$')


def _wrap_body(body: str) -> str:
    """把用户输入包装为可赋值函数表达式。

    已是函数形态（(a)=>/a=>/function/async function/async()=>）原样加括号；
    裸语句则包为 function(...__args){ <body> }，调用方可用 __args 取参。
    """
    b = body.strip()
    if re.match(r'^(async\s+function\b|function\b|\([^)]*\)\s*=>'
                r'|async\s*\([^)]*\)\s*=>|async\s+[\w$]+\s*=>|[\w$]+\s*=>)', b):
        return f'({b})'
    return f'(function(...__args){{\n{b}\n}})'


def _register_js(name: str, body: str) -> str:
    """生成注册脚本：window.__dp_rpc[name] = <fn>。"""
    return (f'window.__dp_rpc=window.__dp_rpc||{{}};'
            f'window.__dp_rpc[{json.dumps(name)}]={_wrap_body(body)};'
            f'true')


def _call_js(name: str, args_json: str) -> str:
    """生成调用表达式：__dp_rpc[name] 优先，否则按点路径解析原生函数。"""
    ref = f'(window.__dp_rpc||{{}})[{json.dumps(name)}]'
    if _JS_PATH.match(name):
        ref = f'({ref}||{name})'   # 注册函数优先，原生点路径回落
    return (f'(()=>{{const __f={ref};'
            f'if(typeof __f!=="function")return {{"__dp_rpc_nf":1}};'
            f'return __f(...{args_json});}})()')


def _rpc_call(page, name: str, args) -> object:
    """执行 RPC 调用；函数不存在时返回哨兵 dict。"""
    return _cdp_eval(page, _call_js(name, json.dumps(args)))


def _parse_args(raw: str | None) -> list:
    """--args JSON → 位置参数列表。非数组 JSON 视为单参数。"""
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        error(f'--args 不是合法 JSON: {e}', code='INVALID_ARGS')
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def register(cli):

    @cli.command('rpc-register')
    @click.argument('name')
    @click.argument('js')
    @session_option
    @click.option('--persist', is_flag=True,
                  help='经 addInitScript 持久化：导航后仍可用，且写入会话'
                       '由后续 dp 命令自动重放')
    def rpc_register(name, js, session, persist):
        """注册命名 JS 函数为 RPC（挂到 window.__dp_rpc[name]）。

        JS 支持函数表达式（(a,b)=>... / function(a,b){...}）或裸语句
        （自动包为 function(...__args){...}）。

        \b
        示例:
          dp rpc-register sign 'function(p){return CryptoJS.MD5(p).toString()}'
          dp rpc-register add '(a,b)=>a+b' --persist
          dp rpc-register wrap 'const m=window.__sign;return m(__args[0])'
        """
        page = _get_page(session)
        script = _register_js(name, js)
        try:
            if persist:
                js_id = page.add_init_js(script)
                # init-js 对之后导航生效；当前页立即注册一次保证立即可用
                page.run_js(script)
            else:
                js_id = None
                page.run_js(script)
        except Exception as e:
            error(f'RPC 函数注册失败（JS 语法/注入错误）', code='RPC_FAILED',
                  detail=str(e))
            return

        if persist:
            sess = load_session(session) or {}
            rpc_cfg = dict(sess.get('rpc') or {})
            rpc_cfg[name] = {'body': js, 'js_id': js_id}
            sess['rpc'] = rpc_cfg
            save_session(session, sess)

        ok({'name': name, 'persist': persist, 'js_id': js_id},
           msg='RPC 已注册' + ('（持久化，导航后自动恢复）' if persist else ''))

    @cli.command('rpc-call')
    @click.argument('name')
    @click.argument('args', required=False)
    @session_option
    def rpc_call(name, args, session):
        """调用已注册的 RPC 函数（或页内原生函数，如 JSON.stringify）。

        ARGS 为 JSON：数组展开为位置参数，其它 JSON 值视为单参数。

        \b
        示例:
          dp rpc-call sign '["payload",123]'
          dp rpc-call add '[1,2]'
          dp rpc-call JSON.stringify '[{"a":1}]'
          dp rpc-call btoa '["hello"]'
        """
        argv = _parse_args(args)
        page = _get_page(session)
        try:
            result = _rpc_call(page, name, argv)
        except Exception as e:
            error(f'RPC 调用失败', code='RPC_CALL_FAILED', detail=str(e))
            return
        if isinstance(result, dict) and result.get('__dp_rpc_nf'):
            error(f'函数未注册且不是可调用的页内函数: {name}',
                  code='RPC_NOT_FOUND')
            return
        ok({'name': name, 'args': argv, 'result': result})

    @cli.command('rpc-list')
    @session_option
    def rpc_list(session):
        """列出当前页已注册的 RPC 函数（含持久化标记）。"""
        page = _get_page(session)
        try:
            names = _cdp_eval(
                page, 'Object.keys(window.__dp_rpc||{})') or []
        except Exception as e:
            error(f'RPC 列表获取失败', code='RPC_FAILED', detail=str(e))
            return
        sess = load_session(session) or {}
        persisted = set((sess.get('rpc') or {}).keys())
        items = [{'name': n, 'persist': n in persisted} for n in names]
        # 持久化但当前页缺失（注册后发生异常导航等）也列出便于诊断
        for n in sorted(persisted - set(names)):
            items.append({'name': n, 'persist': True, 'missing': True})
        ok({'rpc': items, 'count': len(items)})

    @cli.command('rpc-unregister')
    @click.argument('name')
    @session_option
    def rpc_unregister(name, session):
        """移除 RPC 注册（当前页 delete + 持久化 init-script 移除）。"""
        page = _get_page(session)
        sess = load_session(session) or {}
        rpc_cfg = dict(sess.get('rpc') or {})
        spec = rpc_cfg.pop(name, None)
        try:
            # 当前页移除
            _cdp_eval(
                page,
                f'delete (window.__dp_rpc||{{}})[{json.dumps(name)}]')
            # 持久化 init-script 移除（js_id 跨 attach 可能失效，best-effort）
            if spec and spec.get('js_id'):
                try:
                    page.remove_init_js(spec['js_id'])
                except Exception as e:
                    debug(f'remove_init_js(js_id={spec["js_id"]}) 失败', e)
            if spec:
                if rpc_cfg:
                    sess['rpc'] = rpc_cfg
                else:
                    sess.pop('rpc', None)
                save_session(session, sess)
            ok({'name': name, 'was_persisted': bool(spec)},
               msg='RPC 已移除' if spec else 'RPC 已从当前页移除（无持久化记录）')
        except Exception as e:
            error(f'RPC 移除失败', code='RPC_FAILED', detail=str(e))
