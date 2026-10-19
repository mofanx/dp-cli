# -*- coding:utf-8 -*-
"""dpx mcp —— MCP server（stdio transport）。

复用 _invoke 分发（daemon/serve 同路径），工具 schema 手写映射（不自动
生成——click nargs/multiple/flag 规则各异，白名单可控）。eval 类高危
工具默认不暴露，`--unsafe-eval` 显式开。mcp SDK 走可选依赖。
"""
import json

# 工具表：name -> (argv 模板前缀, {参数名: (flag|None, required)})
# flag=None → 位置参数；flag='--x' → '--x v'；flag='--x!' → bool flag
_TOOLS = {
    'navigate':        (['goto'], {'url': (None, True)}),
    'back':            (['back'], {}),
    'forward':         (['forward'], {}),
    'reload':          (['reload'], {}),
    'close':           (['close'], {}),
    'new_tab':         (['open', '--new'], {'url': (None, True)}),
    'snapshot':        (['snapshot'], {'interactive': ('--interactive!', False),
                                       'compact': ('--compact!', False)}),
    'screenshot':      (['screenshot'], {'path': ('--filename', False),
                                         'annotate': ('--annotate!', False)}),
    'eval':            (['eval'], {'js': (None, True)}),
    'read':            (['read'], {'url': (None, False)}),
    'extract':         (['extract'], {'selector': (None, True)}),
    'query':           (['query'], {'selector': (None, True)}),
    'click':           (['click'], {'locator': (None, True),
                                    'force': ('--force!', False)}),
    'fill':            (['fill'], {'locator': (None, True),
                                   'value': (None, True)}),
    'fill_form':       (['fill-form'], {'data': ('--data', True)}),
    'select':          (['select'], {'locator': (None, True),
                                     'value': (None, True)}),
    'press':           (['press'], {'key': (None, True)}),
    'hover':           (['hover'], {'locator': (None, True)}),
    'scroll':          (['scroll'], {'direction': (None, False)}),
    'wait':            (['wait'], {'locator': (None, False),
                                   'timeout': ('--timeout', False)}),
    'sleep':           (['sleep'], {'seconds': (None, True)}),
    'tab_list':        (['tab-list'], {}),
    'tab_switch':      (['tab-select'], {'index': (None, True)}),
    'cookies_get':     (['cookie-list'], {}),
    'cookies_import':  (['cookies-import'], {'path': (None, True)}),
    'storage_get':     (['localstorage-get'], {'key': (None, True)}),
    'dialog_policy':   (['dialog-policy'], {'policy': (None, True)}),
    'net_requests':    (['requests'], {}),
    'net_request':     (['request'], {'id': (None, True)}),
    'state_save':      (['state-save'], {'path': (None, True),
                                         'format': ('--format', False)}),
    'download':        (['download'], {'dir': ('--dir', False),
                                       'timeout': ('--timeout', False)}),
    'assert_title':    (['assert-title'], {'expect': (None, True)}),
    'assert_text':     (['assert-text'], {'locator': (None, True),
                                          'expect': (None, True)}),
    'assert_url':      (['assert-url'], {'expect': (None, True)}),
    'assert_visible':  (['assert-visible'], {'locator': (None, True)}),
}

_UNSAFE = {'eval'}   # --unsafe-eval 才暴露
_schema_cache = {}


def _schema(name, spec):
    if name in _schema_cache:
        return _schema_cache[name]
    _, params = spec
    props, req = {}, []
    for k, (flag, required) in params.items():
        t = 'boolean' if flag and flag.endswith('!') else 'string'
        if k in ('index',):
            t = 'integer'
        if k == 'seconds':
            t = 'number'
        props[k] = {'type': t}
        if required:
            req.append(k)
    s = {'type': 'object', 'properties': props}
    if req:
        s['required'] = req
    _schema_cache[name] = s
    return s


def _to_argv(name, spec, args):
    base, params = spec
    argv = list(base)
    for k, (flag, required) in params.items():
        if k not in args:
            continue
        v = args[k]
        if flag is None:
            argv.append(str(v))
        elif flag.endswith('!'):
            if v:
                argv.append(flag[:-1])
        else:
            argv.extend([flag, str(v)])
    return argv


def run_mcp(session: str, unsafe_eval: bool, allowed_domains):
    """启动 stdio MCP server。"""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
    except ImportError:
        import sys
        sys.stderr.write('mcp SDK 未安装：pip install "dp-cli[mcp]"\n')
        return 1
    import anyio

    tools = {k: v for k, v in _TOOLS.items()
             if unsafe_eval or k not in _UNSAFE}
    server = Server('dp-cli')

    @server.list_tools()
    async def list_tools():
        return [Tool(name=n, description=f'dpx {spec[0][0]}',
                     inputSchema=_schema(n, spec))
                for n, spec in tools.items()]

    @server.call_tool()
    async def call_tool(name, arguments):
        spec = tools.get(name)
        if spec is None:
            return [TextContent(type='text',
                                text=f'{{"error":"unknown tool {name}"}}')]
        argv = _to_argv(name, spec, dict(arguments or {}))
        argv += ['--session', session]
        # netlog 前置武装：本条 tool 导航的流量也要进缓冲
        try:
            from dp_cli.commands.serve import _maybe_arm_netlog
            _maybe_arm_netlog(session)
        except Exception:
            pass
        from dp_cli.commands.batch import _invoke
        code, result = _invoke(argv)
        return [TextContent(type='text',
                            text=json.dumps(result, ensure_ascii=False))]

    async def _main():
        async with stdio_server() as (rs, ws):
            await server.run(rs, ws, server.create_initialization_options())

    anyio.run(_main)
    return 0


def register(cli):
    import click
    from dp_cli.output import ok, error

    @cli.command('mcp')
    @click.option('--session', default='default', show_default=True)
    @click.option('--unsafe-eval', is_flag=True,
                  help='额外暴露 eval 工具——允许 MCP client 在页面'
                       '执行任意 JavaScript（默认不暴露；'
                       '仅供可信 client，等同 --eval 权限面）')
    @click.option('--allow-domain', 'domains', multiple=True,
                  help='域名白名单（透传会话拦截配置，可多次）')
    def mcp_cmd(session, unsafe_eval, domains):
        """以 MCP stdio server 运行——供 Claude Desktop/agent 客户端接入。

        白名单 ~36 工具：导航/观察/交互/会话/取证。eval 默认关。
        """
        if domains:
            from dp_cli.commands.batch import _invoke
            a = ['intercept']
            for d in domains:
                a += ['--allow-domain', d]
            _invoke(a + ['--session', session])
        code = run_mcp(session, unsafe_eval, domains)
        if code:
            error('mcp server 启动失败', code='MCP_FAILED')
