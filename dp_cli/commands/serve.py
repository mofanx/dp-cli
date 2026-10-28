# -*- coding:utf-8 -*-
"""dpx serve / dpx sh —— 常驻会话模式：stdin/stdout JSONL 协议。

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
import threading
from pathlib import Path

import click

from dp_cli.output import error, _write_out, _write_err
from dp_cli.commands.batch import _invoke, _invalid

_FORBIDDEN = {'batch', 'exec', 'serve', 'sh'}
_MAX_REQ_LINE = 1_048_576   # 单行 JSONL 请求上限 1MB——超限不整行
                            # 读入内存（超大 payload 撑爆内存的 DoS 面）


class _TimeoutInterrupt(BaseException):
    """setitimer 超时中断——必须 BaseException：命令/调用链里裸
    except Exception 会把 timeout 吞掉（吞了=超时命令照跑完+
    应答计时错乱）。外层 except _TimeoutInterrupt 显式捕获不变，
    KeyboardInterrupt/SystemExit 同层语义。"""
    pass


def _install_timeout_handler():
    try:
        import signal as _sig
        _sig.signal(_sig.SIGALRM,
                    lambda *a: (_ for _ in ()).throw(_TimeoutInterrupt()))
    except Exception:
        pass
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

    if argv and argv[0] in ('dpx', 'dp', 'dp-cli'):
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


_IN_SNAP = False            # _snap_state 内部 _invoke 防递归录制


def _snap_state(session: str) -> dict:
    """执行后状态快照——url/页面签名，供 post_state 证据字段。

    仅在 DPX_RECORD_DECIDE 开启时取快照（in-process _invoke 调 eval
    location.href）——失败返回空 dict 不阻塞主链路；_IN_SNAP 守卫
    避免嵌套 _invoke 自身再被录制。
    """
    from dp_cli.decide_train import enabled
    if not enabled():
        return {}
    global _IN_SNAP
    _IN_SNAP = True
    try:
        code, r = _invoke(['eval', 'location.href', '--session', session])
        if code == 0 and isinstance(r, dict):
            url = (r.get('data') or {}).get('result')
            if isinstance(url, str):
                return {'url': url}
    except Exception:
        pass
    finally:
        _IN_SNAP = False
    return {}


def _record_session(argv: list, default: str = None) -> str:
    for i, arg in enumerate(argv or []):
        if arg in ('--session', '-S') and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith('--session='):
            return arg.partition('=')[2]
        if arg.startswith('-S') and len(arg) > 2:
            return arg[2:]
    return default or 'default'


def _has_session_flag(argv: list) -> bool:
    """argv 是否已显式带 session 旗——-Sfoo/-S x/--session x/
    --session=x 四形态全认（与 _record_session 同判据），
    供默认会话注入判定；'-Sfoo' 连写不再漏判。"""
    return any(a in ('--session', '-S')
               or a.startswith('--session=')
               or (a.startswith('-S') and len(a) > 2)
               for a in (argv or ()))


def _emit(seq: int, req_id, code: int, result: dict,
          elapsed_ms: int = None) -> None:
    out = {'seq': seq, 'exit': code, 'result': result}
    if req_id is not None:
        out['id'] = req_id
    if elapsed_ms is not None:
        out['elapsed_ms'] = elapsed_ms
    _write_out(json.dumps(out, ensure_ascii=False))


def _ping() -> tuple:
    from dp_cli.skill_dist import cli_version
    return 0, {'status': 'ok', 'pong': True, 'version': cli_version()}


def _repl_cmds() -> list:
    """TAB 补全候选：已注册命令名。"""
    try:
        from dp_cli.main import cli as _cli
        return sorted(_cli.commands)
    except Exception:
        return []


_REF_CACHE = {}          # snapshot 产出的 ref→{role,name} 表
# tab-* 切换=页面切换：tab-list 只读不算导航
_NAV_CMDS = {'open', 'goto', 'go-back', 'go-forward', 'back',
             'forward', 'reload', 'close', 'new', 'switch',
             'tab-new', 'tab-select', 'tab-close'}
_REF_SRC = {'snapshot', 'agent-state'}


def _refresh_refs(cmd: str, session: str) -> None:
    """补全缓存失效契约（A+：编号+范围，meta 仅提示不担保）：
    snapshot/agent-state 覆盖；导航类清空；其余不动。"""
    global _REF_CACHE
    if cmd in _NAV_CMDS:
        _REF_CACHE = {}
        return
    if cmd in _REF_SRC:
        try:
            from dp_cli.session import load_refs
            _REF_CACHE = load_refs(session or 'default') or {}
        except Exception:
            _REF_CACHE = {}


def _hist_path():
    return Path.home() / '.dp_cli' / 'sh_history'


def _exec_line(line: str, session: str) -> None:
    """REPL 单行执行（三层共用）。"""
    import shlex as _sh
    if not line:
        return
    if line in ('exit', 'quit'):
        raise EOFError
    if line == 'help':
        _write_err('可用命令: ' + ' '.join(_repl_cmds()[:20])
                         + ' ...（dpx catalog --md 查全表）\n')
        return
    try:
        argv = _sh.split(line)
    except ValueError as e:
        _write_err(f'解析失败: {e}\n')
        return
    if argv and argv[0] in ('dpx', 'dp', 'dp-cli'):
        argv = argv[1:]
    if not argv:
        return
    cmd = argv[0]
    if cmd == 'ping':
        _, r = _ping()
        _write_out(json.dumps(r, ensure_ascii=False))
        return
    if cmd in _FORBIDDEN:
        _write_err(f'REPL 内不允许 {cmd}\n')
        return
    if (session and not _has_session_flag(argv)
            and _accepts_session(cmd)):
        argv = argv + ['--session', session]
    try:
        record_session = _record_session(argv, session)
        _pre = _snap_state(record_session)   # 未开录制时 {} 零开销
        code, result = _invoke(argv)
        _write_out(json.dumps(result, ensure_ascii=False))
        # 公共录制入口（decide-train）——判据/字段与 JSONL、
        # daemon 渠道一致；异常内部自吞不炸 REPL
        from dp_cli.decide_train import _record_decide_row
        _record_decide_row(record_session, argv, result,
                           code=code, pre_state=_pre)
        _refresh_refs(cmd, record_session)
    except KeyboardInterrupt:
        _write_err('（已中断）\n')
    except Exception as e:
        _write_err(f'错误: {e}\n')


def _repl_ptk(session: str) -> int:
    """L1：prompt_toolkit——跨平台最优体验（惰性 import）。"""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import (Completer, Completion,
                                           WordCompleter)
    from prompt_toolkit.history import FileHistory

    class _DpCompleter(Completer):
        _words = WordCompleter(
            _repl_cmds() + ['help', 'exit', 'quit', 'ping'],
            ignore_case=True)

        def get_completions(self, doc, ce):
            # @e 前缀 → ref 候选+meta（纯内存 O(N)，零 IO 零执行）
            import re as _re
            m = _re.search(r'@e(\d*)$', doc.text_before_cursor)
            if m:
                for rid, info in (_REF_CACHE or {}).items():
                    if rid.startswith(m.group(1)):
                        meta = (f'{info.get("role","")} '
                                f'{info.get("name","")}').strip()
                        yield Completion(
                            f'@e{rid}',
                            start_position=-len(m.group(0)),
                            display_meta=meta[:60])
                return
            yield from self._words.get_completions(doc, ce)

    sess = PromptSession(
        history=FileHistory(str(_hist_path())),
        completer=_DpCompleter())
    # 外部 snapshot 产出的 refs 也要能补——启动先载入（失败静默为空）
    _refresh_refs('snapshot', session)
    _write_err(f'dp-cli REPL（session={session or "default"}，'
                     'help 查命令、exit 退出）\n')
    while True:
        try:
            line = sess.prompt('dp> ').strip()
        except (EOFError, KeyboardInterrupt):
            _write_err('\n')
            return 0
        try:
            _exec_line(line, session)
        except EOFError:
            return 0


def _repl_readline(session: str) -> int:
    """L2：readline——Unix 兜底（无 prompt_toolkit 时）。"""
    import readline, atexit
    hist = _hist_path()
    try:
        hist.parent.mkdir(parents=True, exist_ok=True)
        readline.read_history_file(str(hist))
    except Exception:
        pass
    atexit.register(lambda: _safe_hist(hist))
    _cmds = _repl_cmds() + ['help', 'exit', 'quit', 'ping']
    readline.set_completer(
        lambda t, st: ([c for c in _cmds if c.startswith(t)]
                       + [None])[st])
    readline.parse_and_bind('tab: complete')
    return _repl_bare(session)


def _repl_bare(session: str) -> int:
    """L3：裸 input()——零依赖，Windows 保底。"""
    _write_err(f'dp-cli REPL（session={session or "default"}，'
                     'help 查命令、exit 退出）\n')
    _write_err('[dp] tip: pip install "dp-cli[repl]" '
                     'for TAB completion & history\n')
    while True:
        try:
            line = input('dp> ').strip()
        except (EOFError, KeyboardInterrupt):
            _write_err('\n')
            return 0
        try:
            _exec_line(line, session)
        except EOFError:
            return 0


def _repl(session: str) -> int:
    """TTY REPL 三层降级：prompt_toolkit→readline→裸 input()。"""
    try:
        import prompt_toolkit  # noqa
        return _repl_ptk(session)
    except ImportError:
        pass
    try:
        import readline  # noqa
        return _repl_readline(session)
    except ImportError:
        pass
    return _repl_bare(session)


def _safe_hist(hist):
    try:
        import readline
        readline.set_history_length(500)
        readline.write_history_file(str(hist))
    except Exception:
        pass


def _serve(session: str = None, tty: bool = False) -> int:
    """主循环：TTY→REPL 裸命令；非 TTY→JSONL 解析 → _invoke → 应答。"""
    if tty:
        return _repl(session)
    seq = 0
    while True:
        if tty:
            _write_err('dp> ')
            sys.stderr.flush()
        try:
            # readline(size) 限定读取探行长——超长行只取回前
            # _MAX_REQ_LINE+1 字符的截断段（无换行结尾即超限标记），
            # 不把整行读进内存
            raw = sys.stdin.readline(_MAX_REQ_LINE + 1)
        except KeyboardInterrupt:
            return 0                       # Ctrl-C 于读入时 → 优雅退出
        if raw == '':                      # EOF → 退出
            return 0
        if len(raw) > _MAX_REQ_LINE and not raw.endswith('\n'):
            # 超长行：按块丢弃残行（仍不整体入内存）后回结构化
            # 错误——serve 不崩，后续请求继续服务
            while True:
                rest = sys.stdin.readline(_MAX_REQ_LINE + 1)
                if not rest or rest.endswith('\n'):
                    break
            seq += 1
            _emit(seq, None, 2, {
                'status': 'error', 'code': 'PAYLOAD_TOO_LARGE',
                'message': '请求体超限',
                'detail': f'单行上限 {_MAX_REQ_LINE} 字节'})
            continue
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line in ('quit', 'exit', '.quit'):
            return 0
        seq += 1
        import time as _t
        _t0 = _t.monotonic()

        _pre = {}   # 执行前快照占位——err/ping/forbidden/畸形
                    # timeout 路径无执行时机，保持 {} 不录
        argv, req_id, err = _parse_request(line)
        if err:
            code, result = _invalid(err)
        else:
            cmd_name = next(
                (a for a in argv if a not in _GLOBAL_FLAGS), None)
            if cmd_name == 'ping':
                code, result = _ping()
            elif cmd_name is None:
                code, result = _invalid('空命令')
            elif cmd_name in _FORBIDDEN:
                code, result = _invalid(f'serve 内不允许嵌套 {cmd_name}')
            else:
                # 默认会话注入：子命令未带 --session/-S 且支持该选项时补；
                # 无 session 选项的命令（config-set/list/http-* 等）不注入
                if (session and not _has_session_flag(argv)
                        and _accepts_session(cmd_name)):
                    argv = argv + ['--session', session]
                _tm = None
                try:
                    _tm = json.loads(line).get('timeout_ms')
                except Exception:
                    pass
                if _tm is not None and not (
                        isinstance(_tm, (int, float))
                        and not isinstance(_tm, bool)
                        and _tm > 0):
                    # 畸形 timeout_ms（str/bool/非正数）——float(_tm)
                    # 会炸主循环，按参数错误应答，serve 继续服务
                    code, result = _invalid(
                        f'timeout_ms 须为正数毫秒: {_tm!r}')
                else:
                    _armed_alarm = False
                    _sig = None
                    _pre = _snap_state(_record_session(argv, session))
                    # 执行前快照——enabled() 未开时 {} 零开销
                    try:
                        if _tm and hasattr(__import__('signal'),
                                           'setitimer'):
                            import signal as _sig
                            _sig.setitimer(_sig.ITIMER_REAL,
                                           float(_tm) / 1000.0)
                            _armed_alarm = True
                    except Exception:
                        _armed_alarm = False   # arm 失败按无超时跑
                    try:
                        try:
                            code, result = _invoke(argv)
                        except _TimeoutInterrupt:
                            code, result = 1, {
                                'status': 'error', 'code': 'TIMEOUT',
                                'message': f'命令超时 {_tm}ms',
                                'detail': ''}
                    except KeyboardInterrupt:
                        code, result = 1, {
                            'status': 'error', 'code': 'INTERRUPTED',
                            'message': '命令被中断', 'detail': ''}
                    except Exception as e:
                        code, result = 1, {
                            'status': 'error', 'code': 'ERROR',
                            'message': f'serve 执行异常: {e}',
                            'detail': repr(e)}
                    finally:
                        # alarm 必须复位——残留 SIGALRM 会被下一条
                        # 命令吃到（_TimeoutInterrupt 是 BaseException）
                        if _armed_alarm and _sig is not None:
                            try:
                                _sig.setitimer(_sig.ITIMER_REAL, 0)
                            except Exception:
                                pass
        _emit(seq, req_id, code, result,
              int((_t.monotonic() - _t0) * 1000))
        record_session = _record_session(argv, session)
        # _pre 复用 _invoke 前（上方 else 分支）已采的快照——
        # 不再后置清零，JSONL 渠道录制行携带 pre_state
        from dp_cli.decide_train import _record_decide_row
        _record_decide_row(record_session, argv, result,
                           code=code, pre_state=_pre)
        _maybe_arm_netlog()


def _maybe_arm_netlog() -> None:
    """命令执行后尝试挂载被动网络日志（本进程已 attach 的会话）。

    只遍历 _PAGE_CACHE——未缓存的会话走 get_browser 会触发全量重连，
    stale 会话（浏览器已关/auto-connect 等授权）可阻塞主链路数秒，
    违背"绝不阻塞主链路"契约。挂载后请求/响应事件持续写入
    ~/.dp_cli/netlog-{session}.jsonl ring buffer，供 dpx requests 离线读。
    失败静默——被动日志是增强项。
    """
    try:
        from dp_cli.session import _PAGE_CACHE
        from dp_cli.netlog import arm_netlog
        # arm_netlog 内部比对 session→tab_id——latest_tab 换 tab
        # （旧 driver WebSocket 已死）时自动重挂，无需在此预判
        for sess, page in list(_PAGE_CACHE.items()):
            arm_netlog(page.latest_tab or page, sess)
    except Exception:
        pass


def _prewarm():
    """预 import DrissionPage（~260ms）——serve 启动空窗摊销，
    让第一条浏览器命令零等待。失败静默（真用到时再兜底报错）。"""
    try:
        import DrissionPage  # noqa: F401
    except Exception:
        pass


def _warmup_attach(session: str) -> None:
    """后台 attach 默认 session 浏览器——serve 启动空窗摊销，
    让首条浏览器命令零 attach 税。失败静默（浏览器可能还没起）。"""
    try:
        from dp_cli.session import get_browser
        get_browser(session)
    except Exception:
        pass


def _warmup_imports():
    """daemon 线程后台预热——不阻塞 serve 主循环启动。"""
    threading.Thread(target=_prewarm, daemon=True).start()


@click.command('serve')
@click.option('-S', '--session', default=None,
              help='绑定默认会话名（子命令未指定 --session 时注入）')
@click.option('--warmup', is_flag=True,
              help='启动时后台 attach 默认 session（首命令零等待；'
                   '失败不阻塞启动）')
def serve(session, warmup):
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
      dpx serve                          # stdin/stdout JSONL 长驻
      echo '{"cmd":"snapshot"}' | dpx serve
      dpx serve --session my             # 默认会话注入
      dpx sh                             # 交互别名（TTY 下提示符）
    """
    _warmup_imports()
    _install_timeout_handler()
    if warmup:
        threading.Thread(target=_warmup_attach,
                         args=(session or 'default',),
                         daemon=True).start()
    return _serve(session=session, tty=sys.stdin.isatty())


def register(cli):
    cli.add_command(serve)               # dpx serve
    cli.add_command(serve, 'sh')         # dpx sh（交互别名）
