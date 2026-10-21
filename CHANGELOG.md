# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.15.5] - 2026-10-21

### 修复

- **Windows pid 探活根治**（用户实测反馈）：`OpenProcess` 对死 PID 也返回非零句柄→`daemon-status` 误判活、stale meta 永不清理、每命令 6s TCP 超时。改 `OpenProcess+GetExitCodeProcess` 校验 `STILL_ACTIVE(259)`+`tasklist` 兜底；spawn 就绪链经 `_daemon_ready` 判死清理+自动重启。
- **Windows daemon 性能**：`_daemon_ready` 探活 2s TTL 缓存（POSIX 不变），避免每命令 meta+tcp+pid 三次探测。

## [0.15.4] - 2026-10-21

### 修复

- **Windows daemon 管理命令**（用户实测反馈）：`daemon-status` 活报 `running:false`（只查 AF_UNIX socket 文件，Windows 是 TCP meta 端口）→`_daemon_ready` 加 `_tcp_alive`/`_meta_pid_alive` 双平台探活；`daemon-stop` 因误报 false 停不掉→按 pid/TCP 真实探活杀；daemon 异常死亡 meta 残留致每条命令先吃 TCP 超时→`_clean_stale` 判死自动清理。
- **macOS `kill-all`**：`/proc/cmdline` 扫描在 darwin 静默失效（win 守卫漏 darwin）→`ps` 分流+非 POSIX 显式跳过。

### 规则

- `AGENTS.md` 升级「跨平台开发纪律（原子级）」——不止扫 API 名，用任何方法前先问三平台假设是否成立（`sock.exists()=alive` 语义级教训登记）。

## [0.15.3] - 2026-10-20

### 新增

- daemon netlog 取证链打通：`requests`/`request`/`listen`/`listen-stop` 在 `DPX_DAEMON=1` 下返回真实网络数据（文件级 ring buffer+socket 查询），NO_NETLOG 消除。
- daemon 版本探测：`daemon-status` 透 `version` 字段；client 首连版本不匹配 stderr 警告（不拒绝）。
- `record` 备用定位器三级兜底：css/xpath 失效自动 `text:` 降级（id 漂移场景真机实证命中）。

### 修复

- `_skipped` 路由：`mcp`/`record-*` 缺席时 DPX_DAEMON=1 会误转发挂起——改精确枚举 `_RECORD_CMDS`。
- netlog 跨进程竞态：`_maybe_trim`/`_append`/`read_entries` 同文件锁临界区（fcntl/O_EXCL 双平台）+tmp+rename 原子重写。
- `assert-text` flake：`innerText` 优先+空白归一化根治。
- `--by-js` hidden 拦截错误含 `--force` 可行动提示；`daemon-stop` 签名修复。
- daemon 协议兜底：未知 argv/非 JSON 回结构化错误非挂起。

## [0.15.2] - 2026-10-20

### 新增

- `click --by-mouse`：坐标级真鼠标序列点击（`click.at()`），覆盖 mousedown-only 型控件。
- 发版门禁固化：`make test-all`（单元+真机冒烟）、`Makefile`、`test_flag_matrix.py` 旗标一致性矩阵、`test_dp_contract.py` DrissionPage API 存在性契约、`scripts/smoke_hostile.py` 事件断言冒烟（`tests/fixtures/hostile.html`，9 用例）、CI integration job 挂 Hostile smoke step。
- AGENTS.md：跨平台边界表 + 验证铁律（新 flag/命令合并前必须真机跑）。

### 修复

- `click --by-js` 抛 `clicker object has no attribute js`——改 `run_js('this.click()')`。
- `serve`/`sh`/`mcp`/`daemon-*` 裸 `--session` 缺 `-S` 短旗——统一 `session_option` 双通路。
- `daemon` spawn 锁 `_flock` 异常不 close fd；O_EXCL 锁补 stale 检测（kill-9 残留自动回收）。
- `decide` margin 单边 probs 静默降级——`margin_source` 三分支显式标注。
- smoke `hits()` KeyError 兜底 + 结尾 `close` 防会话残留。

## [0.15.1] - 2026-10-20

### 修复（Windows 跨平台批）

- `daemon` 传输层 Windows 不可用——socket.AF_UNIX 不存在；Windows 分流 TCP 回环（127.0.0.1+内核分配端口写 meta），POSIX AF_UNIX 语义不变。
- `_flock` 异常路径 fd 泄漏——finally 防护；O_EXCL 锁加 stale pid 检测+自动回收（kill-9 残留场景）。
- `decide` margin 单边 probs 静默降级——改 `margin_source` 三分支显式标注（real/conf_fallback/none）。
- `kill-all` Windows 显式跳过守卫。
- 新增 `AGENTS.md` 跨平台兼容清单（Unix-only API 降级表+开发铁律）。

## [0.15.0] - 2026-10-20

### 新增

- `agent-state`：Jev 格式控件表输出（state+options[]+epoch 版本戳，top-K ≤20）——决策模型统一输入。
- `decide`【experimental】：单步决策原语——扇出三问（action/target/done/stuck），三引擎（external/julia/jev）；`--cascade` top-3 粗筛级联、`--min-margin` 歧义门控。波次 0 实测 Julia-1 准确率未达立项阈值，标 experimental 不宣传。
- `agent-setup`：决策引擎注册入 session。
- `batch` 支持 `a || b || c` 行内回退链——首选失败自动降级备选。
- `record-stop -f dp` 导出的回放脚本自动挂 `||` 备用定位器链（element.locators css/xpath），且输出去 `set -e` 改 batch-clean 头——`dpx batch` 直接重放。

### 修复

- `daemon` Windows 跨平台故障——fcntl Unix-only 防护（O_EXCL 降级锁）+`_spawn_kwargs` 平台分派+chmod/USERNAME 防护，Unix flock 语义不变。
- `daemon-start/status/stop` 统一 `-S/--session` 双通路（对齐 `session_option` 全局契约）。

## [0.14.1] - 2026-10-19

### 修复（实测打磨批）

- `drag` 签名漏收 `no_wait` 致 TypeError 全失效——修复并接 `_wait_ele` actionability（实机拖拽实证）。
- `listen-stop` 无监听时裸 NoneType 异常——listening 前置检查+`LISTEN_FAILED` 可行动指引。
- `dblclick` 用错 API（`click(count=2)` 不存在）——改 `click.multi(times=2)`。
- `cookies-import` 头串嗅探把 `Path`/`Domain`/`HttpOnly`/`expires`/`SameSite`/`Partitioned` 当伪 cookie 扩散——`_COOKIE_ATTRS` 过滤修复。
- `bridge list` 死桥堆积——存活校验+stale 清理。
- MCP/daemon 会话 `netlog` arm 前置——首条导航命令流量不再丢缓冲。
- `--unsafe-eval` -h 权限说明补全；损坏 session 边界文档注明。

## [0.14.0] - 2026-10-19

### 新增

- **`dpx mcp`**：MCP stdio server——34 个白名单工具（导航/观察/交互/会话/取证），
  schema 手写映射 + 现有 JSON 输出契约透传；`eval` 高危工具默认关闭（`--unsafe-eval` 显式开），
  `--allow-domain` 透传域名白名单。Claude Desktop/Windsurf 等 MCP client 直接接入。
- **断言扩展**：`dpx assert-value <loc> <expect>`（元素 value 断言，property 优先）；
  `assert-count` 支持比较运算符（`>=3`/`<=5`/`!=2`/`>1`/`<9`/`==`）；全部 assert-* 命令支持 `--not` 反向断言。
- **`dpx wait --stable <loc> [--for N]`**：元素 outerHTML 连续静默 N 秒判定 DOM 稳定。
- **netlog 并入 daemon**：`dpx daemon-start` 后被动网络日志由 daemon 进程托管，
  `dpx requests`/`request` 不再依赖 `dpx serve`（NO_NETLOG 语义更新）。
- **fill-form 输入校验**：pair 模式 `@name=x` 属性定位器给出改用 `--data` 的可行动提示。

### 修复

- `assert-value`：读取元素 `value` property 优先（JS 赋值不落 attribute），attribute 兜底。
- `fill-form`：字段定位器解析失败收敛进 per-field 明细（不再中断整批/污染 stdout 契约）。
- MCP 工具表实名修正（tab-select/cookie-list/localstorage-get 等），`goto` 语义对齐。

## [0.13.0] - 2026-10-19

### 新增

- **`dpx daemon-start/status/stop` + socket 化**（opt-in）：命令经 unix socket 路由到常驻进程，ENOENT 自动 spawn+retry、超时分层、idle 自退、flock 锁并发 spawn=1；`DPX_DAEMON=1` 启用，默认不变现有行为。
- **被动网络日志**：`netlog` ring buffer（serve 常驻托管）+ `dpx requests`/`dpx request <id>` 滚动查询（NO_NETLOG 降级指引）。
- **域名白名单护栏**：`intercept --allow-domain` 导航+全资源拦截（fail-closed，畸形 URL/未知 scheme 拒绝，`--allow-file` 逃生位）+ `FORBIDDEN_DOMAIN`。
- **内容边界**：`read/extract/query --boundary` nonce 包裹防 prompt injection。
- **`dpx fill-form`**：批量表单填充（--data JSON/@file 或 locator=value 对），逐字段 actionability，per-field 明细+FILL_FAILED。
- **`dpx sleep`**、`wait --popup`（新 tab 捕获）、`wait --download`（三态：可行动报错→missions 轮询→目录回扫兜底）。
- **`screenshot --annotate`**（refs overlay）+`--if-changed`（未变不落盘省 token）。
- **`cookies-import`**：curl dump/JSON/Cookie header 三格式自动嗅探批量导入。
- **`read <url>` 直抓**：无浏览器 curl_cffi+trafilatura+llms.txt 探测。
- `select` 失败枚举可用 option；`state-save` 多 origin localStorage；`state-load` 兼容 playwright storageState（0.12 已含导出侧）。
- `README_zh.md` 中文文档与英文版互链。

### 修复

- wait --popup 对 MixTab 无 tab_ids 被 except 吞恒超时（复核链抓到）；download --dir 持久化 sess 标记（page.download_path 恒 truthy 死代码）。
- `_host_allowed` fail-open → fail-closed（复核链抓到：畸形 URL 可绕白名单）。
- daemon spawn 竞态→flock；daemon-stop 死代码→判定前移。

## [0.12.0] - 2026-10-19

### 新增

- **actionability 自动等待**：`click`/`fill`/`hover`/`check`/`upload`/`clear`/`select`/`fill-form` 执行前自动等待元素可见+可用+位置稳定（两帧）+未被遮挡；被遮挡时报 `CLICK_FAILED` 并返回遮挡元素明细；`--no-wait`/`--force` 可跳过。
- **click 参数面**：`--button`（right/middle）/`--modifiers`/`--position`/`--click-count`/`--force`，CDP `Input.dispatchMouseEvent` 路径。
- **精细键鼠原语**：`keydown`/`keyup`/`mousemove`/`mousedown`/`mouseup`/`mousewheel`（真实 CDP 输入事件，光标位置会话持久）。
- **`dpx kill-all`**：关全部会话+停全部 bridge+强杀 dp-cli 自管浏览器（不误伤用户浏览器）。
- **`dpx dialog-policy`**：会话级弹窗预设策略（auto-accept/dismiss/按类型过滤），弹窗挂起时其他命令快速失败并告警——无人值守必备。
- **`batch --screenshot-on-fail DIR`**：任一命令失败自动截图留证后再中断。
- `assert-*` 四断言统一轮询到 `--timeout`（事件驱动，非单次判断）。
- `state-load` 兼容 playwright `storage_state` JSON（cookie 全字段恢复+origins.localStorage）。
- `dpx -V/--version` 与 `-h` 路径覆盖 skill 漂移告警（升级后第一处可见）+ `--check` 指引行。
- `README_zh.md` 中文文档，与英文版顶部互链。

### 修复

- `_ACTIONABLE_JS` IIFE 在 run_js 下静默失效致遮挡检测从未生效（复核链抓到）。
- `state-load` cookie 丢失 domain/path/secure/httpOnly/sameSite/expires 字段。

## [0.11.0] - 2026-10-17

### 新增

- **`dpx bridge list`/`dpx bridge stop`**：跨 session bridge 注册表——同 profile 多 session 共享授权 bridge（flock 锁并发 spawn=1、四道闸 adopt），`open -a` 二次及多 session 场景免重复点 Allow。
- `dpx close --stop-bridge`：显式清理授权 bridge；`DPX_CLOSE_STOPS_BRIDGE=1` 回退旧语义。
- Edge 渠道补齐：`--channel edge-beta/edge-dev/edge-canary`；`auto-connect-gnome.sh --browser edge` 参数化；`--port` 报错补 `microsoft-edge`/`msedge.exe` 启动示例。

### 变更（行为）

- **`dpx close` 对 user_connected 会话默认保留授权 bridge**（bridge 上游断自行退出，无孤儿风险）——`open→close→open -a` 循环不再每轮重弹授权框；注意存活 bridge 是本机免 Allow 的控制通道，用 `dpx bridge stop` 或 `--stop-bridge` 显式清理。

### 修复

- `open` 连接死端口 30s→0.22s：`_port_open` TCP 预探活 + `_probe_cdp_http` 分层探测（含 page-target 等价判定），inspect 端口正确分流 `-a` 指引；existing_only attach `browser_connect_timeout` 收窄 30s→8s。
- 僵尸 bridge：上游 WS 断开 bridge 自行退出 + `/healthz` 预检；挂起请求 `-32000` 应答防泄漏。
- 授权等待感知：`notify-send` 桌面通知（GNOME 不受窗口遮挡）；`ready_timeout` 与 ws `open_timeout` 单一事实源联动，消除 90-119s 点击误杀窗口。

## [0.10.0] - 2026-10-16

### 变更（契约）

- **入口统一改名 `dpx`**：`dpc`/`dpcli` 候选名评估后定 `dpx` 为规范入口——DrissionPage 4.1.1.4 起自带 `dp` console script（浏览器配置工具），后装者静默抢占；`dp` 入口保留兼容（未被抢时可用），`check_dp_entry()` 双入口归属检测并在 `install --check` 输出抢占预警；所有文档/skill 改用 `dpx`。

### 修复

- `install --skills --check` 漂移检测补内容双检：版本戳相同但内容过期的场景（改名批实证漏检）现在报 `SKILL_DRIFT` 并定位差异文件；`read_text` 加 `errors=replace` 二进制容错。
- `snapshot --since` churn 配对改位置邻近确定性算法，多对多场景计数守恒、role 隔离不误并。
- 测试环境根治 flake：conftest autouse 三维隔离（session 目录/`_PAGE_CACHE`/env），session 残留不再污染用例。

## [0.9.1] - 2026-10-16

### 修复

- `_lazydeps` 兼容 DrissionPage 4.1.1.3+：上游将 `DataRecorder` 包改名为 `DrissionRecord`，桩按候选名集合（`DrissionRecord`/`DataRecorder`）双覆盖——DownloadKit 2.0.7 仍依赖老包名，DP 核心用新名，两者共存时分别装桩；引用点钉住测试改为包名无关（上游改名/结构调整时变红不绿炸）。

## [0.9.0] - 2026-10-16

### 新增

- `dp snapshot --since ts|last`：元素级增量 diff（backendNodeId/hash 键控、近 5 基线、churn 维度归类 innerHTML 换血）。
- 新错误码 `SESSION_DISCONNECTED`（EXIT_CONNECT=3）：会话文件存在但连接不可达时与新会话不存在的 `SESSION_NOT_FOUND` 分流，附恢复指引；契约新增非破坏。

### 性能

- `_lazydeps` 自愈式 DataRecorder 懒代理：`import DrissionPage` -46%（324→176ms），单发浏览器命令冷启动 -38%；`DP_CLI_NO_STUB=1` 逃生，运行期 heal 回载真模块。
- `serve`/`sh` 启动后台 daemon 预 import DrissionPage：首条浏览器命令 0.396→0.033s（-92%）。
- session headless 状态缓存：非 user_connected 会话跳过重复探测。
- `test_import_budget.py`：非浏览器命令零 DrissionPage 加载守卫。

### 修复

- `watch` 命中后 JS 自清理（clearInterval+disconnect）+ `disarm` 页内清理注入，修复 done 后空跑。
- `hook` 自动 id 计数法改键集选空位（页内+会话双命名空间），修复 remove 后误撞与持久化覆盖。
- `human` min/max 语义澄清为停顿时长。

## [0.8.1] - 2026-10-15

### 修复

- CI/容器环境 Chrome 启动补 `--no-sandbox`/`--disable-dev-shm-usage`（root 或 `CI` 环境变量时自动附加，修复 runner 上 `BROWSER_START_FAILED`）。
- 测试兼容性：`CliRunner(mix_stderr=)` 在 click>=8.2 已移除，改兼容垫片；版本戳断言改动态 `cli_version()`；`sync-skill.sh` 管道 SIGPIPE（rc=141）修复；CLI invoke 测试补 HOME 隔离。

## [0.8.0] - 2026-10-15

### 新增

- 批量执行：`batch`/`exec` 进程内串行执行命令 + JSONL 输出，省逐命令启动开销。
- 常驻会话：`serve`/`sh` JSONL 协议（命令/argv 双形态、请求 ID 透传、可选 `--session` 注入、EOF/quit/Ctrl-C 优雅退出）。
- JSRPC 桥：`rpc-register`/`rpc-call`/`rpc-list`/`rpc-unregister`；`hook` 工具链与 DOM snapshot；`read` 正文提取；`cf-wait` CF 挑战等待。
- 拟人输入：`click`/`dblclick`/`fill`/`type`/`press` 拟人化 flag。
- 网络/存储增强：`listen-stop` 捕获字段与落盘、Netscape cookie 导出（域名过滤）、可选 `curl_cffi` 指纹模拟。
- 设备与 CDP：`stealth` 检查、设备预设、权限/剪贴板/媒体/CSP 绕过/网络节流/文件选择器。
- `wait`/`cf-wait` 事件化：`CdpWaitGate` 事件驱动取代纯轮询（保留轮询回退）。
- Playwright 用例固化链路：`record-export --format playwright(-async)` 语义定位器（`get_by_role`/`get_by_text`/`get_by_test_id`）+ 导航同步点；新增 `playwright-ts`/`playwright-ts-async` TypeScript 导出与 `expect-gen --lang ts`；`expect-gen` 断言物化；`state-save --format playwright` 输出标准 storageState；`snapshot --pw-locators` 交互节点附语义定位器。
- skill 随包分发：`dp install --skills [--dir/--all/--check]` 覆盖主流 agent harness（Claude/Devin/Windsurf/Cursor/OpenCode/Codex），commands.md 安装时现场生成；多目标版本戳漂移检测（stderr 告警、stdout 零污染）；`dp catalog [--md|--json]`；`dp -V/--version`。
- 输出层：`--compact`/`--fields`/`--head` 裁剪；`.claude-plugin/` marketplace 分发渠道；`scripts/sync-skill.sh` 开发期同步。

### 修复

- DrissionPage `remove_init_js` 跨进程 `_init_jss` 门控静默失效：新增 `_remove_init_js` 直发 CDP 绕门控，js_id 经 bundle_id 落盘使跨进程 unregister/clear 生效；移整包后立即精准重注册幸存 persist 段（修复延迟恢复缺口）。
- 会话生命周期：`user_connected`/`probe_dir` 复用失败改 raise ConnectionError（杜绝静默新浏览器顶替丢登录态）；`close --force` 死代码修复；quit 前补 `_detect_headless` 修 headless 会话 close 假成功。
- `serve --session` 注入仅限支持该选项的命令（修复 `No such option: --session`）。
- `cf-wait` `detect_js` 顶层 return 表达式语法修复；导航中断误报修正。
- `read --format html` 返回提取后 HTML（原误返回原始 HTML）；trafilatura 非 ImportError 失败回退 readability。
- `emulate` 移动端→桌面端切换残留 touch 模拟修复；`batch` 嵌套命令保护修复全局 flag 绕过；`har-dump` 版本号动态读取。
- `localstorage-list` 顶层 return 致真机必败修复；`record-export` `_final` 伪动作泄漏/count 虚报/id 元字符转义/`data-*` attr 降级修复。

### 其他

- 文档守卫体系：commands.md freshness 逐行比对、SKILL.md 引用扫描、ERROR_CODES 注册表↔文档同步、frontmatter schema 与版本同步守卫（`test_docs.py` 8 项）。
- 测试 297 → 641；命令面 86 → 112。

## [0.7.0] - 2026-10-14

### 新增

- 断言命令：`assert-text` / `assert-url` / `assert-visible` / `assert-count`（`--min`/`--max` 范围），断言失败以 `ASSERT_FAILED`（退出码 4）输出 JSON，可直接用于 `&&` 串联与 CI 检查。
- `wait` 新增 `--js`（JS 表达式轮询）与 `--response`（一步式网络响应等待，自动清理监听器）。
- console/error 捕获：`console-listen` 注册 CDP `Runtime.consoleAPICalled`/`Runtime.exceptionThrown`/`Log.entryAdded` 回调（缓冲上限 1000），`console-dump` 读取记录（`--keep` 保持监听、`--clear` 清缓冲）。
- 对话框应答：`dialog-accept [text]` / `dialog-dismiss`。
- 环境模拟：`emulate`（`--viewport`/`--ua`/`--locale`/`--timezone`/`--geolocation`/`--mobile`/`--reset`，经 CDP Emulation domain，会话持久化重放）。
- 下载与拦截：`download`（下载完成等待 + `--timeout`）、`intercept`（`--block` 资源屏蔽、`--header` 自定义请求头、`--mock`/`--mock-status`/`--mock-body`/`--mock-header` 请求 mock、`--mock-clear`/`--off`）。
- 观测能力：`perf`（Performance.getMetrics + Navigation Timing）、`trace`（Tracing 事件流落盘为 traceEvents JSON）、`screencast`（帧截图序列）。
- `har-dump`：Network 事件聚合导出 HAR 1.2（`--body`/`--output`/`--filter`）。
- `--frame` 作用域：`click`/`fill`/`assert-*` 等 13 个命令支持在指定 iframe 内定位元素。
- `cookie-list` 输出补充 `secure`/`httpOnly`/`sameSite`/`expires` 字段。
- 全局 debug 日志通道：`dp -v/--verbose` 或 `DP_DEBUG=1`，输出至 stderr 不污染 stdout JSON。
- 错误码分级退出：`ERROR_CODES` 注册表与 `ERROR_CODES.md` 契约文档（usage=2 / connect=3 / not_found=4 / timeout=5 / runtime=1），未注册码向后兼容回落 1。
- 新增 `LICENSE`（BSD-3-Clause）、`CHANGELOG.md`、`.github/workflows/release.yml`（tag `v*` → 版本一致性校验 → build → twine upload）。

### 修复

- `frame_ctx` 对真实 `NoneElement` 用 `getattr('_type')` 判定会抛 `ElementNotFoundError`，`--frame` 未命中误以 exit1 退出；改为类名判定，正确走 `ELEMENT_NOT_FOUND`（exit4）。
- `cookie-list --domain` 裸 `localhost`/IPv4/IPv6 曾被误判为顶级后缀拒绝，现加白名单放行。
- `emulate` viewport 不再硬编码 `mobile=True`，支持 `--mobile/--no-mobile`。
- `console` 回调对 `value=0/False/''` 的 falsy 参数改为按键存在判断，不再丢失。
- `download` 依赖的 `_dl_mgr` 私有 API 增加防御性回退；`--timeout 0` 边界修复（循环前先读一次 missions）。
- `assert-count` 位置参数与 `--min/--max` 并用时按声明互斥返回 `INVALID_ARGS`（exit2），不再静默取位置参数。
- 34 处静默 `except: pass` 改为 `debug()` 记录，便于 `DP_DEBUG=1` 排障。

### 测试

- 非集成测试由 333 增至 366；新增 `tests/conftest.py` 共享 fixture（FakePage 工厂、isolated session dir、http server）。
- CI 覆盖率门禁 `--cov-fail-under=58`（实测水位 ~59%，后续逐步上调）。

## [0.6.3] - 2026-10-14

### 新增

- `sessionstorage-delete` 命令，补齐 localStorage/sessionStorage CRUD 对称性。
- `config-set` 无参数时提示使用 `--help`。

### 修复

- `snapshot --brief` 长文本节点不截断（`_CONTENT_ROLES` early return 跳过截断逻辑）。
- iframe 节点定位器生成 `UnboundLocalError` 回退为 text 定位；iframe 分支复用 `describeNode` 结果消除一次冗余 CDP 调用。
- `_build_dom_bid_map` 缺少 `pierce=True` 导致 shadow DOM 元素无法进入映射；`_build_tree` 多根节点（如 iframe）丢失其余子树。
- a11y XPath/属性选择器引号未转义导致的 JS 注入风险（改用 `json.dumps`）。
- `http-get`/`http-post` 在响应为 None 时 `AttributeError`；`--output` 现在写入原始字节。
- `listen-stop` 在 `wait()` 抛异常时监听器残留（改 `try/finally` 保证 `stop`）。
- `cookie-list --url` 未剥离 userinfo；`--domain` 子串匹配误中 `notexample.com`，改为域边界匹配。
- `press` 特殊键名映射错误：`PageUp`→`PAGEUP` 未命中 `Keys.PAGE_UP`，退化为字面输入并在修饰键组合下误触浏览器快捷键；新增别名表与归一化。
- `record-start` 对 `url=None` 直接切片崩溃；active tab 查找改为 `get_tab(targetId)` 直查。
- `recorder` `findScrollable` 空值防御；`js_scripts` 回退快照穿透 open shadow DOM、过滤哈希类名、兼容 `SVGElement.className.baseVal`。

### 测试

- 新增 a11y 转义/iframe/storage/network/keyboard/record 等回归用例，非集成测试由 268 增至 297。
- `test_bridge_integration` 标记 `@pytest.mark.integration`；新增真实浏览器冒烟用例 `test_browser_smoke.py`；CI 启用 integration job。
