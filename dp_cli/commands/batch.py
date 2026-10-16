# -*- coding:utf-8 -*-
"""dpx batch / dpx exec —— 单进程内顺序执行多条 dpx 子命令。

价值：
- 消除每条命令独立 Python 进程 + CDP attach 的开销（get_browser 进程内缓存复用）
- 输出 JSONL：每行 {"seq","line","cmd","exit","result"}，AI/脚本易解析
- 默认 fail-fast（首条失败即停），--keep-going 继续执行完

输入：文件路径或 '-'（stdin，缺省）。每行一条 dpx 子命令（'dpx ' 前缀可省），
      '# ' 开头为注释，空行跳过。禁止嵌套 batch/exec。
"""
import io
import json
import shlex
import sys
from contextlib import redirect_stdout

import click

from dp_cli.output import error

# 不允许在批处理内递归调用自身/serve（stdin 会读空/语义混乱）
_FORBIDDEN = {'batch', 'exec', 'serve', 'sh'}


def _read_lines(src: str) -> list:
    """读取批处理命令行，返回 [(lineno, text)]。src='-' 时读 stdin。"""
    if src == '-':
        if sys.stdin.isatty():
            error('批处理需要文件参数或管道输入（dpx batch cmds.txt / cat cmds.txt | dpx batch）',
                  code='INVALID_ARGS')
            return []
        raw = sys.stdin.read()
    else:
        try:
            with open(src, encoding='utf-8') as f:
                raw = f.read()
        except OSError as e:
            error(f'无法读取批处理文件: {src}', code='FILE_NOT_FOUND', detail=str(e))
            return []

    lines = []
    for i, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if s and not s.startswith('#'):
            lines.append((i, s))
    return lines


def _invoke(argv: list) -> tuple:
    """进程内执行一条 dpx 子命令，返回 (exit_code, result_dict)。

    捕获子命令的 stdout 并解析为 JSON 嵌入 result；非 JSON 输出（如
    --version/--help/click 用法错误）包装为 {'status','output'}。
    """
    from dp_cli.main import cli

    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf):
        try:
            cli.main(args=argv, prog_name='dpx', standalone_mode=False)
        except click.exceptions.Exit as e:
            code = e.exit_code or 0          # --help / ctx.exit() 正常路径
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except click.ClickException as e:
            code = e.exit_code               # UsageError/BadParameter → 2
            buf.write(e.format_message() + '\n')
        except click.exceptions.Abort:
            code = 1
            buf.write('Aborted.\n')
        except Exception as e:
            code = 1
            buf.write(json.dumps({'status': 'error', 'code': 'ERROR',
                                  'message': str(e)}, ensure_ascii=False))

    text = buf.getvalue().strip()
    try:
        return code, json.loads(text)
    except json.JSONDecodeError:
        result = {'status': 'ok' if code == 0 else 'error'}
        if text:
            result['output'] = text
        return code, result


def _invalid(msg: str) -> tuple:
    return 2, {'status': 'error', 'code': 'INVALID_ARGS', 'message': msg}


@click.command('batch')
@click.argument('file', required=False, default='-',
                metavar='[FILE|-]')
@click.option('-k', '--keep-going', is_flag=True,
              help='某条命令失败后继续执行后续命令（默认首条失败即停）')
@click.option('-v', '--verbose', is_flag=True,
              help='向子命令透传 debug 日志（等价于子命令加 -v）')
@click.option('--no-summary', is_flag=True,
              help='不在末尾输出统计行')
def batch(file, keep_going, verbose, no_summary):
    """批量顺序执行 dpx 子命令（FILE 或 stdin，每行一条）。

    同一进程内执行，浏览器 attach 结果在各命令间复用，远快于逐条
    调用 dp。输入每行为一条子命令（可省略 'dpx ' 前缀），'# ' 开头
    为注释。每条结果输出一行 JSON：

    \b
      {"seq":N,"line":M,"cmd":"...","exit":N,"result":{...}}

    示例：

    \b
      cat > cmds.txt <<'EOF'
      # 打开并截图
      open https://example.com
      snapshot --brief
      click "text:更多"
      EOF
      dpx batch cmds.txt            # 文件输入
      cat cmds.txt | dpx batch      # stdin
      dpx batch -k cmds.txt         # 失败继续
    """
    lines = _read_lines(file)
    if not lines:
        error('批处理输入为空（无有效命令行）', code='INVALID_ARGS')
        return

    n_ok = n_fail = 0
    first_fail = None
    executed = 0
    for seq, (lineno, text) in enumerate(lines, 1):
        try:
            argv = shlex.split(text)
        except ValueError as e:
            code, result = _invalid(f'第 {lineno} 行命令解析失败: {e}')
            argv = None
        else:
            if argv and argv[0] in ('dpx', 'dp', 'dp-cli'):
                argv = argv[1:]
            # 跳过全局 flag 定位真实命令名，防止 "-v batch f.txt" 绕过嵌套防护
            cmd_name = next(
                (a for a in argv
                 if a not in ('-v', '--verbose', '--compact')), None)
            if cmd_name is None:
                code, result = _invalid(f'第 {lineno} 行为空命令')
            elif cmd_name in _FORBIDDEN:
                code, result = _invalid(f'批处理内不允许嵌套 {cmd_name}')
            else:
                code, result = _invoke((['-v'] if verbose else []) + argv)

        print(json.dumps({'seq': seq, 'line': lineno, 'cmd': text,
                          'exit': code, 'result': result},
                         ensure_ascii=False))
        executed = seq
        if code == 0:
            n_ok += 1
        else:
            n_fail += 1
            if first_fail is None:
                first_fail = code
            if not keep_going:
                break

    if not no_summary:
        print(json.dumps({
            'summary': {'total': len(lines), 'executed': executed,
                        'ok': n_ok, 'failed': n_fail,
                        'stopped_early': first_fail is not None and not keep_going}
        }, ensure_ascii=False))

    if first_fail is not None:
        sys.exit(first_fail)


def register(cli):
    cli.add_command(batch)          # dpx batch
    cli.add_command(batch, 'exec')  # dpx exec（别名）
