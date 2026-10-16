# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
