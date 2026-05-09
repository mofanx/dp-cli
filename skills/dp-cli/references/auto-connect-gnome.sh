#!/bin/bash
# auto-connect-gnome.sh
#
# Ubuntu Gnome (Wayland) 环境下自动完成 dp open --auto-connect 授权流程的辅助脚本。
#
# 背景：
#   dp open --auto-connect 首次（或 Chrome 重启后）会在 Chrome 中弹出
#   "Allow remote debugging for this browser instance" 对话框，需用户手动切窗口点击。
#   本脚本通过 gauto + ydotool 自动激活 Chrome 窗口并模拟 Tab+Enter 确认，
#   免去手动操作。
#
# 依赖：
#   - dp CLI（必须）
#   - gauto（可选，用于检测/启动/激活 Chrome 窗口）
#   - ydotool（可选，用于模拟键盘；需运行 ydotoold 守护进程）
#
# 用法：
#   ./auto-connect-gnome.sh           # 自动模式（需 gauto + ydotool）
#   ./auto-connect-gnome.sh --full    # 先启动 Chrome，再连接
#   ./auto-connect-gnome.sh --simple  # 手动模式（无 gauto/ydotool 也可用）
#
# 注意：
#   - Allow 弹窗通常只在 Chrome 重启后首次连接时出现，后续命令自动复用连接无需重复
#   - ydotool 在 Wayland 下需要 ydotoold 守护进程：sudo ydotoold &
#   - 若自动确认失败，请手动切到 Chrome 窗口点击 Allow 按钮

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_debug() { echo -e "${BLUE}[DEBUG]${NC} $1"; }

check_deps() {
    if ! command -v dp &> /dev/null; then
        log_error "dp CLI 未安装"
        exit 1
    fi
    command -v gauto   &> /dev/null || log_warn "gauto 未安装，无法自动激活窗口"
    command -v ydotool &> /dev/null || log_warn "ydotool 未安装，无法自动确认授权"
}

is_chrome_running() {
    command -v gauto &> /dev/null || return 1
    gauto list 2>/dev/null | tr '[:upper:]' '[:lower:]' | grep -qE "chrome|google-chrome"
}

launch_chrome() {
    log_info "正在启动 Chrome..."
    if command -v gauto &> /dev/null; then
        gauto launch chrome
        sleep 3
    else
        log_warn "请手动启动 Chrome"
        read -r -p "按回车键继续..."
    fi
}

# 激活 Chrome 窗口并发送 Tab+Enter 确认 Allow 对话框
perform_auto_confirm() {
    log_info "尝试自动确认授权对话框..."

    if command -v gauto &> /dev/null; then
        log_debug "激活 Chrome 窗口..."
        gauto activate-class google-chrome 2>/dev/null || true
        sleep 0.5
    fi

    if command -v ydotool &> /dev/null; then
        log_debug "发送 Tab + Enter 按键..."
        # 106 = Tab, 28 = Enter (Linux 输入事件码)
        if ydotool key 106:1 106:0 28:1 28:0 2>/dev/null; then
            log_info "已发送授权确认按键"
        else
            log_warn "ydotool 执行失败（确认 ydotoold 守护进程是否运行），请手动点击 Allow"
        fi
    else
        log_warn "ydotool 不可用，请手动切到 Chrome 窗口点击 Allow"
    fi
}

# 执行 dp open --auto-connect，实时监控输出并在检测到授权提示时自动确认
run_dp_connect_auto() {
    log_info "执行 dp open --auto-connect..."
    log_info "若 Chrome 弹出授权对话框，脚本将自动确认；也可手动切到 Chrome 点击 Allow"

    local auth_confirmed=false
    local tmp
    tmp=$(mktemp)

    # 用命名管道避免 pipe 子 shell 导致变量不共享
    local fifo
    fifo=$(mktemp -u)
    mkfifo "$fifo"

    dp open --auto-connect 2>&1 | tee "$tmp" > "$fifo" &
    local dp_pid=$!

    while IFS= read -r line <"$fifo"; do
        echo "$line"
        # bridge_manager.py 输出的授权提示关键词
        if [[ "$auth_confirmed" == false ]] && \
           [[ "$line" == *"Allow remote debugging"* || "$line" == *"请切到 Chrome"* ]]; then
            auth_confirmed=true
            perform_auto_confirm &
        fi
    done

    wait "$dp_pid"
    local exit_code=$?

    rm -f "$tmp" "$fifo"

    if [ $exit_code -eq 0 ]; then
        log_info "连接成功"
    else
        log_error "dp open --auto-connect 失败 (exit=$exit_code)"
        log_warn "若未点 Allow：请手动切到 Chrome 窗口点击 Allow 后重试"
    fi
    return $exit_code
}

run_dp_connect_simple() {
    log_info "手动模式：执行 dp open --auto-connect"
    log_warn "请留意 Chrome 是否弹出 Allow 对话框，需手动点击 Allow"
    dp open --auto-connect
}

main() {
    local full_mode=false
    local auto_mode=true

    for arg in "$@"; do
        case $arg in
            --full|-f)   full_mode=true ;;
            --simple|-s) auto_mode=false ;;
            --help|-h)
                echo "用法: $0 [--full] [--simple]"
                echo "  --full    完整流程：先启动 Chrome，再连接"
                echo "  --simple  手动模式：不自动确认，需手动点击 Allow"
                exit 0
                ;;
        esac
    done

    check_deps

    if [ "$full_mode" = true ]; then
        is_chrome_running || launch_chrome
    else
        is_chrome_running || { log_warn "Chrome 未检测到，尝试启动..."; launch_chrome; }
    fi

    if [ "$auto_mode" = true ]; then
        run_dp_connect_auto
    else
        run_dp_connect_simple
    fi
}

main "$@"
