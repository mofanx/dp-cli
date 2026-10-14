# -*- coding:utf-8 -*-
"""dp serve / dp sh —— 常驻会话模式：stdin/stdout JSONL 协议。

与 batch 的差异：serve 是长驻进程、逐行增量应答（非一次性读全部），
浏览器 attach / page 对象在进程内跨命令复用（_PAGE_CACHE），
摊掉每条命令的 Python 进程启动 + CDP attach 开销——脚本/Agent
高频调用场景的提速路径。

协议：
  请求（stdin，每行一个 JSON）：
    {"cmd": "snapshot --brief"}              # 字符串命令（shlex 解析）
    {"argv": ["snapshot", "--brief"]}        # 数组命令（免转义）
    {"cmd": "...", "id": 7}                  # 可选 id，原样透传响应
  响应（stdout，每行一个 JSON）：
    {"id": 7, "seq": 1, "exit": 0, "result": {...}}

EOF（stdin 关闭）→ 优雅退出 0；Ctrl-C 中断当前命令不崩 daemon；
'quit'/'exit' 行内命令关闭。serve/sh 不可被 batch/serve 嵌套。
"""
import json
import shlex
import sys

import click

from dp_cli.output import error
from dp_cli.commands.batch import _invoke, _invalid

_FORBIDDEN = {'batch', 'exec', 'serve', 'sh'}
_GLOBAL_FLAGS = ('-v', '--verbose', '--compact')


def _parse_request(raw: str) -> tuple:
    """解析一行 JSON 请求 → (argv, req_id, err)。

    合法形态：{"cmd": str} / {"argv": [str,...]}；附可选 "id" 透传。
    返回 (argv, id, None) 或 (None, None, error_msg)。
    """
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, None, f'请求不是合法 JSON: {e}'
    if not isinstance(req, dict):
        return None, None, '请求须为 JSON 对象'

    req_id = req.get('id')
    if isinstance(req.get('argv'), list):
        argv = [str(a) for a in req['argv']]
    elif isinstance(req.get('cmd'), str):
        try:
            argv = shlex.split(req['cmd'])
        except ValueError as e:
            return None, req_id, f'cmd 解析失败: {e}'
    else:
        return None, req_id, '请求缺 "cmd"（字符串）或 "argv"（数组）'

    if argv and argv[0] in ('dp', 'dp-cli'):
        argv = argv[1:]
    return argv, req_id, None


def _accepts_session(cmd_name: str) -> bool:
    """该子命令是否定义了 --session 选项（决定是否可注入默认会话）。"""
    try:
        from dp_cli.main import cli as _cli
        sub = _cli.commands.get(cmd_name)
        if sub is None:
            return False
        return any('--session' in (getattr(p, 'opts', None) or [])
                   for p in getattr(sub, 'params', []))
    except Exception:
        return False


def _emit(seq: int, req_id, code: int, result: dict) -> None:
    out = {'seq': seq, 'exit': code, 'result': result}
    if req_id is not None:
        out['id'] = req_id
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def _serve(session: str = None, tty: bool = False) -> int:
    """主循环：readline → 解析 → _invoke → 应答。"""
    seq = 0
    while True:
        if tty:
            sys.stderr.write('dp> ')
            sys.stderr.flush()
        try:
            raw = sys.stdin.readline()
        except KeyboardInterrupt:
            return 0                       # Ctrl-C 于读入时 → 优雅退出
        if raw == '':                      # EOF → 退出
            return 0
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line in ('quit', 'exit', '.quit'):
            return 0
        seq += 1

        argv, req_id, err = _parse_request(line)
        if err:
            code, result = _invalid(err)
        else:
            cmd_name = next(
                (a for a in argv if a not in _GLOBAL_FLAGS), None)
            if cmd_name is None:
                code, result = _invalid('空命令')
            elif cmd_name in _FORBIDDEN:
                code, result = _invalid(f'serve 内不允许嵌套 {cmd_name}')
            else:
                # 默认会话注入：子命令未带 --session/-S 且支持该选项时补；
                # 无 session 选项的命令（config-set/list/http-* 等）不注入
                if (session and '--session' not in argv
                        and '-S' not in argv
                        and _accepts_session(cmd_name)):
                    argv = argv + ['--session', session]
                try:
                    code, result = _invoke(argv)
                except KeyboardInterrupt:
                    code, result = 1, {
                        'status': 'error', 'code': 'INTERRUPTED',
                        'message': '命令被中断'}
                except Exception as e:
                    code, result = 1, {
                        'status': 'error', 'code': 'ERROR',
                        'message': f'serve 执行异常: {e}'}
        _emit(seq, req_id, code, result)


@click.command('serve')
@click.option('--session', default=None,
              help='绑定默认会话名（子命令未指定 --session 时注入）')
def serve(session):
    """常驻 JSONL 服务：stdin 每行一个命令请求，stdout 每行一个结果。

    进程长驻，浏览器 attach 跨命令复用——高频调用场景比逐条起
    `dp` 进程快一个数量级。Agent/脚本驱动浏览器的推荐入口。

    \b
    请求形态（stdin 每行一个 JSON）：
      {"cmd": "open https://example.com"}
      {"cmd": "snapshot --brief", "id": 1}
      {"argv": ["click", "text:登录"], "id": 2}
    响应（stdout 每行一个 JSON）：
      {"id": 1, "seq": 1, "exit": 0, "result": {"status": "ok", ...}}

    \b
    示例:
      dp serve                          # stdin/stdout JSONL 长驻
      echo '{"cmd":"snapshot"}' | dp serve
      dp serve --session my             # 默认会话注入
      dp sh                             # 交互别名（TTY 下提示符）
    """
    return _serve(session=session, tty=sys.stdin.isatty())


def register(cli):
    cli.add_command(serve)               # dp serve
    cli.add_command(serve, 'sh')         # dp sh（交互别名）
