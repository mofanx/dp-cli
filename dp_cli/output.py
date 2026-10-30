# -*- coding:utf-8 -*-
"""
dp-cli 输出格式化模块
统一的 JSON 输出格式，便于 AI 工具解析。

错误码与退出码契约见 ERROR_CODES.md。
"""
import json
import sys
from typing import Any, Optional
from dp_cli.logs import debug  # re-export，调用方只需 from dp_cli.output import ok, error, debug
from dp_cli.snapshot.utils import suggest_locator

# ── 退出码分级 ──────────────────────────────────────────────────────────────
#   1 runtime   通用运行时失败（默认，向后兼容）
#   2 usage     参数/用法错误（与 click 用法错误退出码一致）
#   3 connect   连接/网络类失败
#   4 not_found 元素/资源未找到、断言类失败
#   5 timeout   超时
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONNECT = 3
EXIT_NOT_FOUND = 4
EXIT_TIMEOUT = 5

# 错误码注册表：code -> (exit_code, 说明)。新增错误码请同步登记到
# ERROR_CODES.md。未注册的 code 按 EXIT_RUNTIME 处理（向后兼容）。
ERROR_CODES: dict[str, tuple[int, str]] = {
    # ── usage（退出码 2）──
    'INVALID_ARGS':         (EXIT_USAGE, '参数组合/取值无效'),
    'INVALID_CONFIDENCE':   (EXIT_USAGE, '置信度参数越界'),
    'INVALID_JSON':         (EXIT_USAGE, 'JSON 参数解析失败'),
    'CONFLICTING_OPTIONS':  (EXIT_USAGE, '互斥选项同时使用'),
    'PW_SYNTAX':            (EXIT_USAGE, 'pw 定位器语法错误'),
    'FILE_NOT_FOUND':       (EXIT_USAGE, '指定文件不存在'),
    'PAYLOAD_TOO_LARGE':    (EXIT_USAGE, '请求体超限（serve JSONL 单行上限）'),

    # ── connect（退出码 3）──
    'SESSION_NOT_FOUND':    (EXIT_CONNECT, '无法连接浏览器会话'),
    'SESSION_DISCONNECTED': (EXIT_CONNECT, '会话存在但浏览器连接不可达'),
    'BROWSER_START_FAILED': (EXIT_CONNECT, '浏览器启动/连接失败'),
    'AUTOCONNECT_FAILED':   (EXIT_CONNECT, 'auto-connect 自动发现失败'),
    'NAVIGATE_FAILED':      (EXIT_CONNECT, '页面导航失败'),
    'HTTP_FAILED':          (EXIT_CONNECT, 'HTTP 请求失败'),

    # ── not_found（退出码 4）──
    'ELEMENT_NOT_FOUND':    (EXIT_NOT_FOUND, '元素未找到'),
    'TAB_NOT_FOUND':        (EXIT_NOT_FOUND, '标签页不存在'),
    'REF_NOT_FOUND':        (EXIT_NOT_FOUND, 'ref 编号不在映射中'),
    'REF_UNRESOLVABLE':     (EXIT_NOT_FOUND, 'ref 无法解析为有效定位器'),
    'COOKIE_NOT_FOUND':     (EXIT_NOT_FOUND, 'Cookie 不存在'),
    'PW_NOT_FOUND':         (EXIT_NOT_FOUND, 'pw 定位器未匹配到元素'),
    'PROFILE_NOT_FOUND':    (EXIT_NOT_FOUND, '指定 profile 不存在'),
    'NO_REFS':              (EXIT_NOT_FOUND, '无可用的 ref 映射（需先 snapshot）'),
    'ASSERT_FAILED':        (EXIT_NOT_FOUND, '断言失败（页面状态不符合预期）'),
    'RPC_NOT_FOUND':        (EXIT_NOT_FOUND, 'RPC 函数未注册且非页内可调用函数'),

    # ── timeout（退出码 5）──
    'WAIT_TIMEOUT':         (EXIT_TIMEOUT, '等待超时'),

    # ── runtime（退出码 1）──
    'CLICK_FAILED':         (EXIT_RUNTIME, '点击失败'),
    'FILL_FAILED':          (EXIT_RUNTIME, '输入失败'),
    'TYPE_FAILED':          (EXIT_RUNTIME, '按键输入失败'),
    'KEY_FAILED':           (EXIT_RUNTIME, '按键失败'),
    'MOUSE_FAILED':         (EXIT_RUNTIME, '鼠标操作失败'),
    'POPUP_FAILED':         (EXIT_RUNTIME, '弹窗等待失败'),
    'FORBIDDEN_DOMAIN':     (EXIT_RUNTIME, '目标域名不在 --allowed-domains 白名单'),
    'NO_NETLOG':            (EXIT_RUNTIME, '无被动网络日志缓冲（需 dpx serve/daemon）'),
    'REQUEST_NOT_FOUND':    (EXIT_RUNTIME, '请求 id 不在网络日志 ring buffer'),
    'SCROLL_FAILED':        (EXIT_RUNTIME, '滚动失败'),
    'AUTOSCROLL_FAILED':    (EXIT_RUNTIME, '自动滚动失败'),
    'HOVER_FAILED':         (EXIT_RUNTIME, '悬停失败'),
    'CHECK_FAILED':         (EXIT_RUNTIME, '勾选失败'),
    'SELECT_FAILED':        (EXIT_RUNTIME, '下拉选择失败'),
    'DRAG_FAILED':          (EXIT_RUNTIME, '拖拽失败'),
    'CLEAR_FAILED':         (EXIT_RUNTIME, '清空失败'),
    'UPLOAD_FAILED':        (EXIT_RUNTIME, '文件上传失败'),
    'COUNT_FAILED':         (EXIT_RUNTIME, '计数失败'),
    'CLOSE_FAILED':         (EXIT_RUNTIME, '关闭失败'),
    'RELOAD_FAILED':        (EXIT_RUNTIME, '刷新失败'),
    'STEALTH_FAILED':       (EXIT_RUNTIME, 'stealth 应用失败'),
    'TAB_FAILED':           (EXIT_RUNTIME, '标签页操作失败'),
    'LISTEN_FAILED':        (EXIT_RUNTIME, '网络监听数据获取失败'),
    'WAIT_FAILED':          (EXIT_RUNTIME, '等待操作失败'),
    'CONSOLE_FAILED':       (EXIT_RUNTIME, 'console 记录读取失败'),
    'JS_FAILED':            (EXIT_RUNTIME, 'JS 执行失败'),
    'INIT_JS_FAILED':       (EXIT_RUNTIME, 'init_js 注入失败'),
    'DIALOG_FAILED':        (EXIT_RUNTIME, '对话框操作失败'),
    'SCREENSHOT_FAILED':    (EXIT_RUNTIME, '截图失败'),
    'PDF_FAILED':           (EXIT_RUNTIME, 'PDF 导出失败'),
    'RESIZE_FAILED':        (EXIT_RUNTIME, '窗口尺寸调整失败'),
    'WINDOW_FAILED':        (EXIT_RUNTIME, '窗口操作失败'),
    'STATE_FAILED':         (EXIT_RUNTIME, '状态获取失败'),
    'INSPECT_FAILED':       (EXIT_RUNTIME, '元素探测失败'),
    'SNAPSHOT_FAILED':      (EXIT_RUNTIME, '快照获取失败'),
    'EXPECT_GEN_FAILED':    (EXIT_RUNTIME, 'expect 断言生成失败'),
    'SKILL_NOT_FOUND':      (EXIT_RUNTIME, 'bundled skill 缺失'),
    'SKILL_DRIFT':          (EXIT_RUNTIME, '已装 skill 与包内副本不一致'),
    'SCAN_FAILED':          (EXIT_RUNTIME, 'clickable 扫描失败'),
    'EXTRACT_FAILED':       (EXIT_RUNTIME, '数据提取失败'),
    'DEPENDENCY_MISSING':   (EXIT_RUNTIME, '可选依赖缺失'),
    'FIND_FAILED':          (EXIT_RUNTIME, '元素查找失败'),
    'QUERY_FAILED':         (EXIT_RUNTIME, '查询失败'),
    'DOM_FAILED':           (EXIT_RUNTIME, 'DOM 操作失败'),
    'COOKIE_FAILED':        (EXIT_RUNTIME, 'Cookie 操作失败'),
    'STORAGE_FAILED':       (EXIT_RUNTIME, 'Storage 操作失败'),
    'PW_EVAL_FAILED':       (EXIT_RUNTIME, 'pw 定位器求值失败'),
    'RECORD_START_FAILED':  (EXIT_RUNTIME, '录制启动失败'),
    'RECORD_STOP_FAILED':   (EXIT_RUNTIME, '录制停止失败'),
    'RECORD_STATUS_FAILED': (EXIT_RUNTIME, '录制状态获取失败'),
    'RECORD_SHOW_FAILED':   (EXIT_RUNTIME, '录制记录展示失败'),
    'RECORD_EXPORT_FAILED': (EXIT_RUNTIME, '录制导出失败'),
    'RECORD_CLEAR_FAILED':  (EXIT_RUNTIME, '录制清空失败'),
    'EMULATE_FAILED':       (EXIT_RUNTIME, 'emulate 环境模拟设置失败'),
    'DOWNLOAD_FAILED':      (EXIT_RUNTIME, '下载设置/等待失败'),
    'INTERCEPT_FAILED':     (EXIT_RUNTIME, '请求拦截设置失败'),
    'PERF_FAILED':          (EXIT_RUNTIME, '性能指标获取失败'),
    'RPC_FAILED':           (EXIT_RUNTIME, 'RPC 注册/列表/移除失败'),
    'RPC_CALL_FAILED':      (EXIT_RUNTIME, 'RPC 调用执行失败'),
    'HOOK_FAILED':          (EXIT_RUNTIME, 'hook 注入/移除/导出失败'),
    'HOOK_NOT_FOUND':       (EXIT_NOT_FOUND, 'hook 未安装'),
    'TRACE_FAILED':         (EXIT_RUNTIME, 'trace 采集/写盘失败'),
    'SCREENCAST_FAILED':    (EXIT_RUNTIME, 'screencast 帧录制失败'),
    'STEALTH_CHECK_FAILED': (EXIT_RUNTIME, 'stealth 指纹自检失败'),
    'PERMISSIONS_FAILED':   (EXIT_RUNTIME, '浏览器权限操作失败'),
    'CLIPBOARD_FAILED':     (EXIT_RUNTIME, '剪贴板读写失败'),
    'MEDIA_FAILED':         (EXIT_RUNTIME, '媒体模拟设置失败'),
    'BYPASS_CSP_FAILED':    (EXIT_RUNTIME, 'CSP 绕过设置失败'),
    'THROTTLE_FAILED':      (EXIT_RUNTIME, '网络/CPU 节流设置失败'),
    'FILE_CHOOSER_FAILED':  (EXIT_RUNTIME, '文件选择对话框处理失败'),
    'BUSY':                 (EXIT_RUNTIME, 'daemon 并发连接超限'),
    'MODEL_DOWNLOAD_FAILED': (EXIT_CONNECT, '模型资产下载失败'),
    'MODEL_SHA_MISMATCH':   (EXIT_RUNTIME, '模型资产 sha256 校验不一致'),
    'MODEL_INSTALL_FAILED': (EXIT_RUNTIME, '模型解压/部署失败'),
}


_COMPACT = False


def set_compact(enabled: bool) -> None:
    """全局紧凑输出开关（--compact / DP_CLI_COMPACT=1）。"""
    global _COMPACT
    _COMPACT = enabled


def _dump(obj: Any) -> str:
    """统一 JSON 序列化：compact 单行无空格 / 默认 indent=2。"""
    if _COMPACT:
        return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _write_err(s: str) -> None:
    """stderr 镜像 _write_out：非 TTY 走 UTF-8 字节写防 locale 炸。"""
    buf = getattr(sys.stderr, 'buffer', None)
    if buf is not None and not sys.stderr.isatty():
        buf.write(s.encode('utf-8'))   # 不自动加 \n——prompt 类输出兼容
        buf.flush()
    else:
        sys.stderr.write(s)
        sys.stderr.flush()


def _write_out(s: str) -> None:
    """JSON 输出走 UTF-8 字节写——pipe/重定向在非 UTF-8 locale
    （如 Windows GBK）下 stdout 编码是 locale，含 ⚡/⚠/💡 等字符的
    JSON 会触发 UnicodeEncodeError 静默吞掉响应（daemon/serve
    长流程静默失败根因）。TTY 路径仍走 print（Windows 控制台经
    WriteConsoleW 无编码面）。"""
    buf = getattr(sys.stdout, 'buffer', None)
    if buf is not None and not sys.stdout.isatty():
        buf.write(s.encode('utf-8') + b'\n')
        buf.flush()
    else:
        print(s)


def _exit_code_for(code: str) -> int:
    """错误码 → 退出码；未注册码按通用运行时失败处理（向后兼容）。"""
    return ERROR_CODES.get(code, (EXIT_RUNTIME, ''))[0]


def ok(data: Any = None, msg: str = None) -> None:
    """成功输出"""
    result = {'status': 'ok'}
    if msg:
        result['message'] = msg
    if data is not None:
        result['data'] = data
    _write_out(_dump(result))


def error(msg: str, code: str = 'ERROR', detail: str = None) -> None:
    """错误输出并按错误码分级退出（见 ERROR_CODES.md）。

    向后兼容：JSON 契约（status/code/message/detail）不变；
    未注册/未知 code 仍退出 1。
    """
    result = {'status': 'error', 'code': code, 'message': msg}
    if detail:
        result['detail'] = detail
    _write_out(_dump(result))
    sys.exit(_exit_code_for(code))


def format_element(ele, include_rect: bool = False) -> dict:
    """格式化单个元素信息"""
    try:
        attrs = ele.attrs
    except Exception:
        attrs = {}

    info = {
        'tag': ele.tag,
        'text': (ele.raw_text or '').strip()[:200],
        'attrs': attrs,
        'loc': suggest_locator(ele.tag, attrs, (ele.raw_text or '').strip()[:50]),
    }

    if include_rect:
        try:
            info['rect'] = {
                'location': list(ele.rect.location),
                'size': list(ele.rect.size),
                'midpoint': list(ele.rect.midpoint),
            }
        except Exception as e:
            debug('获取元素 rect 失败', e)

    return info


def format_page_info(page) -> dict:
    """格式化页面基本信息"""
    return {
        'url': page.url,
        'title': page.title,
        'ready_state': page.states.ready_state,
    }
