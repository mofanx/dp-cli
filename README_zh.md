# dp-cli

[English](README.md) | **中文**

基于 [DrissionPage](https://github.com/g1879/DrissionPage) 的强大 CLI——浏览器自动化、结构化数据提取、网络监听等。

## 特性

- **默认反检测**——不基于 webdriver，`navigator.webdriver` 为 `false`
- **复用你自己的浏览器**——`--auto-connect`（Chrome/Edge 144+，无需启动参数）或 `--port`
- **结构化输出**——全部命令输出 JSON，带语义化错误码与退出码，agent/脚本管道友好
- **持久会话**——跨命令复用浏览器会话（`~/.dp_cli/sessions/`）
- **常驻模式**——`dpx serve`/`dpx sh`/`dpx batch` 摊销进程与 attach 开销，循环内命令 ~10ms 级
- **可操作自动等待**——click/fill 执行前自动等待元素可见+稳定+可用+无遮挡，被遮挡时报出遮挡元素；`dialog-policy` 无人值守弹窗策略；`batch --screenshot-on-fail` 失败自动截图留证
- **精细键鼠**——`keydown/keyup` + 真实 `mousemove/mousedown/mouseup/mousewheel` CDP 事件；`click --button/--modifiers/--click-count/--force`

## 安装

```bash
pip install dp-cli
dpx --help
```

> **入口冲突提示**：DrissionPage 4.1.1.4+ 也注册了 `dp` 命令（浏览器路径配置工具）。若 `dp` 被抢占（装包/升级顺序所致），用主入口 **`dpx`**（独占命名）或 `python -m dp_cli.main`；`pip install --force-reinstall dp-cli` 可夺回 `dp`。归属检测：`dpx install --skills --check` 输出的 `entrypoint` 字段。

### Agent skill（可选，推荐）

为 AI agent 安装完整使用指南（全部命令参考 + 工作流 playbook）：

```bash
dpx install --skills                        # 装到 agent 的 skill 目录
npx skills add mofanx/dp-cli               # 或走 skills CLI 渠道
claude plugin marketplace add mofanx/dp-cli  # 或 Claude Code 插件市场
```

未安装也能用——skill 只是文档层；agent 可直接读 `dpx --help` 或 `python -m dp_cli.catalog` 自举。

> **升级后请刷新 skill**：`pip install -U dp-cli` 后已装 skill 版本戳落后于 CLI——每条命令 stderr 会提示 `运行 'dpx install --skills' 更新`（不污染 stdout JSON）。执行 `dpx install --skills` 或 `dpx install --skills --check` 校验即可对齐。版本戳相同但内容被篡改/过期时，`--check` 同样检出差异文件清单。

## Quick Start

```bash
dpx open https://example.com        # 打开页面（持久会话）
dpx snapshot                        # 可访问性快照（元素 ref 编号）
dpx click "text:登录"                # 语义定位点击
dpx eval "document.title"           # 执行 JS
dpx screenshot /tmp/p.png           # 截图
dpx close                           # 断开（用户浏览器保留运行）
```

## 连接你自己的浏览器（`--auto-connect`）

无需启动参数，正常打开 Chrome 或 Edge 即可（需 144+ 版本）：

1. 地址栏打开 `chrome://inspect/#remote-debugging`（Edge 用 `edge://inspect/#devices`），点击 **"Allow remote debugging for this browser instance"**
2. `dpx open --auto-connect`（或 `dpx open -a`）

工作机制：

1. 从 user-data-dir 读取 `DevToolsActivePort` → 真实 CDP 端口
2. 探测端口——`/json/version` 无响应则判定为 inspect 限流模式
3. 启动本地 bridge（`python -m dp_cli.bridge`）：
   - 由 CDP 调用合成缺失的 HTTP 端点
   - 经 `Target.attachToTarget(flatten=True)` 在单条浏览器级 WebSocket 上多路复用页面级 CDP 流量
4. 将 DrissionPage 指向 bridge——后续 `dpx` 命令复用同一 bridge

bridge 子进程登记在共享注册表（`~/.dp_cli/bridges/`）中——同一 profile 的所有 session 复用一条已授权连接。`dpx close` **默认保留** bridge（仅摘除本 session）；`dpx close --stop-bridge` 或 `DPX_CLOSE_STOPS_BRIDGE=1` 回退旧语义；`dpx bridge list`/`dpx bridge stop` 可直接查看/终止注册的 bridge。`dpx close` 永远不会 quit 你的浏览器（浏览器是你的，不是 dp 的）。

### 注意事项

- Chrome/Edge 对每条新的 WebSocket 客户端都会弹 **"Allow remote debugging"** 授权框——bridge 只持一条连接、全部 `dpx` 命令共享，所以每次 `dpx open --auto-connect` 最多点一次。
- 使用浏览器实际在用的 profile——同样的 cookies、登录态、历史记录。
- 经典 `--remote-debugging-port=9222` 模式仍可用：`dpx open --port 9222`。

## 常用命令

```bash
dpx open <url>          # 打开 URL
dpx snapshot            # a11y 快照（interactive 元素带 ref:N）
dpx snapshot --since last   # 与上次基线的增量 diff（↻ 换血去噪）
dpx click <locator>     # 点击（text:/css:/pw:role=...）
dpx fill <locator> <v>  # 填表
dpx eval <js>           # 执行 JS
dpx screenshot <file>   # 截图
dpx download <url>      # 下载文件
dpx record start/stop/export  # 录制动作并导出 Playwright 脚本
dpx serve               # stdin/stdout JSONL 常驻服务（agent 推荐）
dpx close               # 断开连接（浏览器/bridge 默认保留）
dpx close --stop-bridge # 断开并停 bridge
dpx bridge list|stop    # 查看/终止注册的 bridge
dpx install --skills    # 安装/更新 agent skill
dpx catalog             # 实时命令 schema（无 skill 时自救）
```

定位器优先 `pw:role=...`/`text:` 语义形式，css 兜底。全部输出为 JSON——错误带 `code`（如 `SESSION_NOT_FOUND`/`SESSION_DISCONNECTED`/`SKILL_DRIFT`）与退出码。


## 未发布（Unreleased）

- **录制→回放闭环**：`dpx record-start`/`record-stop -f dp` 产出可直接 `dpx batch` 重放的脚本——每行自动挂 `||` 备用定位器回退链（css/xpath 备选），首选失效自动降级。
- **batch 行内回退**：`a || b || c` 依次尝试直到一条成功，回报 `{fallback:{tried,used}}` 审计字段。
- **agent 决策原语（experimental）**：`dpx agent-state` 输出 Jev 格式控件表（state+options+epoch 版本戳）；`dpx decide <目标>` 单步决策——三引擎（external/julia/jev）+ `--cascade` top-3 粗筛 + `--min-margin` 歧义门控；`dpx agent-setup` 引擎注册。波次 0 实测 Julia-1 准确率未达立项阈值，标 experimental。

## 0.14 新特性

- **`dpx mcp`** — MCP stdio server：34 个白名单工具透传 JSON 契约；`eval` 需 `--unsafe-eval` 显式开启。Claude Desktop：`{"mcpServers":{"dpx":{"command":"dpx","args":["mcp"]}}}`
- **断言扩展** — `dpx assert-value`、`assert-count` 运算符（`>=3`/`!=2`…）、全 assert-* 支持 `--not`、`dpx wait --stable <loc> --for N`（DOM 静默）
- **netlog 并入 daemon** — `dpx daemon-start` 同样托管被动日志，`dpx requests` 不再依赖 `dpx serve`
- **fill-form 提示** — pair 模式 `@name=x` 定位器提示改用 `--data`（`=` 会误拆）

## 0.13 新特性

- **批量表单填充** — `dpx fill-form "#u=admin" "css:#p=pw"` 或 `--data '{"#u":"x"}'`；每字段 ok/fail 明细
- **Cookie 批量导入** — `dpx cookies-import cookies.txt|-` 自动识别 Netscape dump / JSON / `Cookie:` 头串
- **域名白名单** — `dpx intercept --allow-domain ex.com` 拦截白名单外全部请求（`--allow-file` 放行 file://）
- **无浏览器抓取** — `dpx read <url>` curl_cffi 直取（`Accept: text/markdown` + llms.txt 祖先探测），不起浏览器
- **被动网络日志** — `dpx serve` 托管 ~500 条 ring buffer → `dpx requests` / `dpx request <id>`
- **session daemon** — `dpx daemon-start` 后命令走 unix socket 免启动税（`DPX_DAEMON=1` 强制路由）
- **定时等待** — `dpx sleep N`、`dpx wait --popup`（新 tab id）、`dpx wait --download`（目录回扫兜底）
- **select 诊断** — `dpx select` 失败时枚举可选 option 的 value(text)

## 项目结构

```
dp_cli/
├── main.py              # CLI 入口（dpx/dp 入口点）
├── session.py           # 浏览器会话管理 + auto-connect bridge 接线
├── bridge.py            # chrome://inspect 模式 CDP bridge
├── bridge_manager.py    # bridge 子进程生命周期 + inspect 模式探测
├── bridge_registry.py   # 跨 session bridge 注册表（flock 锁）
├── commands/            # 全部子命令实现
├── snapshot/            # a11y 快照与增量 diff
└── skill/               # wheel 内嵌 agent skill 副本
```

## 许可

MIT
