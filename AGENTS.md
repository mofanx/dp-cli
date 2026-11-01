# dp-cli 项目规则

## 验证纪律

- 全量测试：`python -m pytest -m 'not integration' --tb=short -q`（提交前必跑）
- `commands.md` 为生成文件——改命令面后必跑 `python -m dp_cli.catalog --md > skills/dp-cli/references/commands.md`，否则 `test_docs` 挂
- JSON 是唯一输出契约：`status`/`code`/`message`/`detail`，禁止裸 traceback 泄漏

## 跨平台兼容清单（实测教训固化）

**禁用裸 Unix-only API**——以下调用必须有 `try/except ImportError` 防护+降级路径+mock 测试：

| API | 降级方案 | 高危场景 |
|---|---|---|
| `fcntl.flock` | `os.open(O_CREAT\|O_EXCL)` 原子建文件+轮询 | 文件锁（见 `daemon._acquire_spawn_lock`） |
| `socket.AF_UNIX` | TCP 回环 `127.0.0.1:0`+内核分配端口+meta 文件记端口 | daemon/serve socket |
| `os.getuid`/`os.geteuid` | `os.name`/`getpass.getuser` | 文件权限/属主检查 |
| `os.kill(pid, 0)` 存活探针 | `psutil` 或平台分派 | 进程存活检测 |
| `SIGKILL`/`os.killpg`/`setsid` | `os.name=='posix'` 守卫+`CREATE_NEW_PROCESS_GROUP` | 进程组管理（见 `_spawn_kwargs`） |
| `os.chmod` 权限位 | Windows 上跳过或 ACL 替代 | socket/lock 文件权限 |
| `$USER` 环境变量 | `getpass.getuser()`（Windows 无 `$USER`） | 归属判断 |

**四条铁律**：

1. 平台相关代码必须 `os.name`/`sys.platform` 分派或 try-ImportError 防护——禁止顶层 `import fcntl`
2. 降级路径必须有 mock ImportError 的单测（`monkeypatch.setitem(sys.modules, 'fcntl', None)`）
3. Unix 路径零回归——Windows 降级不得改变 POSIX 语义
4. 新增 daemon/socket/文件锁/进程管理代码时对照上表自查

## 实测教训登记

- 裸 `import fcntl` 在 Windows 直接 ImportError——daemon 整个模块装不上（0.15.0 修复）
- `page.listen` 跨进程不持久——`listen-stop` 需 `listening` 前置检查
- `page.tab_ids` 在 `MixTab` 上不存在——popup 检测要走 `page.browser.tab_ids` 回退
- `download_path` 默认是 cwd 而非"未配置"——不能当条件判据
- adapter 导航模式：URL 直参可能因登录态/会话重定向打回首页——manmanbuy `historyLowest.aspx?url=` 未登录直跳首页，需先 open 工具页再填表单二次导航（B8 归因，勿把直参 URL 当稳定入口）
- refs 池落盘默认脱敏：`name`/`text`/`innerText`/`text:` locator/`pw_locator`/`options` 均含页面明文（绩效页 98 条实测全泄漏）——`session._scrub_ref_entry` 存 fnv32 `*_hash`，resolve 走 `_mark_element_by_name_hash` 页内哈希比对不回放原文；`DPX_REFS_PRIVATE=0` 恢复明文；`@attr=` 属性定位器保留（重解析必需，attrs 是脱敏边界）；`dpx refs-clear` 手动清场
- `open --auto-connect <url>` 曾走 `ChromiumPage.get`=tab_ids[0] 劫持用户首个 tab（跑批归因）——`browser._open_navigate` 分流：无 active_tab 绑定→new_tab+绑，有绑定→绑定 tab 导航；`--port` 未分流（如需同款行为另行评估）
- netlog 仅 serve/daemon 长驻进程 arm（设计边界）——CDP Network 事件按 WebSocket 连接投递，逐条 `dpx <cmd>` 短进程退出即断链，arm 只能捕存活期残段且污染 `requests` 的"未挂载"降级语义；CLI 路径 `touched_apis` 恒空属预期，被动日志走 `dpx serve`/daemon 或 `listen`
- `wait --idle` 埋点/心跳白名单（`_IDLE_IGNORE_RE`）——遥测 URL 不计入 pending/activity；站点私有心跳 endpoint 漏网时用 `--loaded`/`--text` 替代
- cookie HttpOnly 不可见→登录态探针走 DOM meta——GitHub `logged_in`/`z_c0`/`BDUSS` 类会话 cookie 设 HttpOnly，`document.cookie` 探不到；登录态断言改 `meta[name="user-login"]:not([content=""])` 或登录/注册链接 DOM（GitHub 新头像 class `prc-Avatar-*`，`img.avatar` 已废）；cookie 探针只能测非 HttpOnly 的 `_octo`/`_device_id`（站点事实非代码 bug，B/N2 归因）
- daemon build_id 漂移警告（stderr "daemon 代码漂移 build vX vs vY-dirty"）是机制实证非 bug——开发期代码改动后 `daemon-stop` 重开消警告，发版后 build_id 自然对齐，无需改码（N4 归因）
- `dpx download`/`wait --downloads-done` 的 `missions` 恒空（B18 已修）：DrissionPage `DownloadManager.set_done` 完成即从 `_missions` 弹出 → 裸读 missions 恒只含在途，`missions and all(is_done)` 恒不可达烧满 timeout；stale 'running' mission（外部触发/断链）无过期会堵死目录回扫分支。修法落地于 `_utils._missions_seen_update`：轮询累积 seen 快照（存 mission 对象引用——弹出后 is_done/final_path/state 原地可读）、stale 按 `received_bytes` 停滞 TTL（`DPX_DL_STALE_TTL` 默认 30s，<=0 关）剔除并记 `dropped` 防原地复活（running 未完成的不会被 DP 弹出，无 dropped 集合下轮又收编重计时）、download/wait 共用 `_dl_dir_files` 目录增量回扫（窗口内新落盘文件即 done；wait 保留 post-hoc 全目录回扫兜底跨进程断链）。教训：**stale 剔除必须配 dropped 持久集**——被剔对象仍在源集合里是这类"弹出式"管理器的通病
- GitHub release Assets 是 `include-fragment loading="lazy"`——不入视口不发请求，`wait --locator` 裸等恒超时；需先 `scroll-to` include-fragment 再等元素（B11 归因，同 lazy 形态教训）
- JD 登录态 cookie 名已换代：`pt_pin`/`pt_key` 废弃，现为 `pin`/`thor`/`pinId`——cookie-get 探针用旧名恒 COOKIE_NOT_FOUND（DOM 昵称才是真已登录信号）
- `wait --idle` blob:/data: scheme 残留——CDP 对本地资源请求发 requestWillBeSent 但永不 loadingFinished/Failed（知乎 /hot blob: Script 7×/15s、netlog-zhhot 80×/31min 实锤：prefetch/worker 反复重生，TTL 老化刚剔又入列+每发刷 activity→idle 永假）——`_long_lived_request` 加 scheme 判据豁免，与 WS/SSE 同通道
- 跑批/service 类入口 daemon 漂移防陈旧码：build_id 警告**只告警不拦截**——`ExecStartPre=-python3.13 -m dp_cli.main daemon-stop -S <session>` 物理停（`-` 前缀容错，daemon 不在也继续；`dpx-taskpool.service` 已配）；bench 脚本/新 service 接入前同款前置，勿依赖 client 侧警告兜底
- 站点风控页 title 可含站点名——京东"京东验证"滑块页 title 也含"京东"，`document.title contains 站点名` 断言假阳性；详情页断言改用 URL+非验证标题+商品 DOM 标记（`.specification-item-sku`/`.cross_platform_card_btn_cart`/`#comment-root`，`#choose-attr-1` 已废）复合判定（N3 归因）

## 发版门禁

发版前必跑 `make test-all`（单元 + `scripts/smoke_hostile.py` 真机冒烟）。
smoke 用 `tests/fixtures/hostile.html`（file:// 无外网依赖）对 click 各变体
做事件断言——`--by-js`/`--by-mouse`/disabled/遮挡/`--force`/dblclick/动态渲染
逐条验证真实触发。新 flag/交互命令合并前必须过冒烟，CLI 单元 mock 测不出
"API 实际长这样"类缺陷（clicker.js 误名就是这么漏的）。

## 跨平台边界（Windows）

| API | POSIX | Windows | 处理位置 |
|---|---|---|---|
| `fcntl.flock` | spawn/bridge 锁 | `O_CREAT\|O_EXCL` 原子文件锁+pid stale 回收 | `daemon._acquire_spawn_lock` |
| `socket.AF_UNIX` | daemon 传输 | `AF_INET` 127.0.0.1+meta 记 port | `daemon._connect_endpoint` |
| `os.kill(pid,0)` | 存活探测 | `OpenProcess(0x1000)` ctypes | `daemon._pid_alive` |
| `SIGTERM/SIGKILL/killpg` | bridge 停进程 | `CTRL_BREAK_EVENT`→`taskkill /F /T` | `bridge_manager`（已有分支） |
| `pgrep`+`/proc` cmdline | kill-all 扫描 | 显式跳过 `sys.platform.startswith('win')` | `browser.py` |
| `os.chmod 0600` | socket 权限 | try/except 跳过 | `daemon.run_daemon` |
| `$USER` | owner 记录 | `USERNAME` 兜底 | `daemon.run_daemon` |
| `start_new_session` | spawn 脱离 | `DETACHED_PROCESS\|CREATE_NEW_PROCESS_GROUP` | `daemon._spawn_kwargs` |

铁律：平台分支只放调用点一处（`_HAS_UNIX_SOCK`/`os.name`/`sys.platform` 判定），散落的 `import fcntl` 一律进 try/except 或 `_lazydeps` 式守卫。

## 跨平台开发纪律（原子级）

**不止扫 API 名——用任何方法前先问：这个假设在三平台（Linux/macOS/Windows）上都成立吗？**

- **语义级假设同样致命**：`os.path.exists(sock)` 是合法跨平台 API，但"socket 文件存在=daemon 活着"只在 AF_UNIX 下成立——Windows 是 TCP 端口。改传输层/资源形态时，**所有判定/清理/状态命令必须同步分流**（本次 daemon-status/stop 教训）。
- **macOS ≠ Linux**：`pgrep -f` 参数差异、`/proc` 不存在（用 `ps`/sysctl）、`getattr(socket,'AF_UNIX')` 存在但 launchd/ulimit 语义不同；`os.fork` 在 macOS 受 ObjC runtime 限制。
- **禁单平台特有方法**：进程/文件/网络原语一律走双/三平台分流（`_HAS_UNIX_SOCK`/`os.name` 判定一处），不得裸写 `if linux else pass`。
- **验证**：新平台分支必须 mock 该平台常量跑分支（`_HAS_UNIX_SOCK=False` 模拟）——不能只靠开发机原生环境。

## decide margin 契约

`result.margin_source` ∈ `probs`（双侧真 margin）/ `single_side`（单边 probs，非真 margin）/ `conf_fallback`（无 probs 静默降级）——上层据此知 margin 可信度，勿当统一语义用。

## 验证铁律（补充）

- **新 flag/新命令路径：合并前必须真机跑一遍**。mock 通过不算验证——
  API 名字错（clicker.js/click(count=2)）mock 永远抓不到，
  只有 hostile.html + 真机冒烟能暴露。
- **调用 DrissionPage API 前先查 `test_dp_contract.py`**——
  每个新方法调用都要在契约表里登记，防"以为有但没有"。
- **公共旗一致性由 `test_flag_matrix.py` 断言**——新命令注册
  session/timeout 等公共旗必须用 `session_option`/`trim_options`
  装饰器，不得裸写 `@click.option`。
- **刁难控件冒烟清单**：`scripts/smoke_hostile.py`（9 用例，
  事件计数断言非返回码断言）——发版前跑一遍。

## netlog 文件锁（P3 已修）

`netlog._append` 用进程内 `threading.Lock` + 跨进程文件锁（`~/.dp_cli/netlog-{session}.lock`，复用 `daemon._acquire_spawn_lock` 的 POSIX flock/Win O_EXCL+_pid_alive 双平台基建）——serve/daemon 并存写同 jsonl 不丢行不撕裂，append+trim 同临界区。

## daemon 路由边界

- `daemon._skipped()` 前缀匹配自动捕获未来命令：`record-*` 前缀族一律不转发 daemon（进程内监听断链教训同 listen-stop）。新增 `record-*`/stdio-server 类命令前先确认是否会被前缀误拦或漏拦。
- `open` 自身初始导航不入 netlog（设计边界）——`open` 的 goto 发生在 arm 之前，`requests` 从第二条导航起才有数据。

## daemon 版本探测

`daemon-status` 响应含 `version`；`DPX_DAEMON=1` client 首次连接探测 meta 版本，不匹配仅 stderr 警告不拒绝（向后兼容，重连提示 `daemon-stop` 后重开）。

## kill-all 平台分流（f1 闭环）

`browser.py kill-all` 三分支：win→bridge taskkill（已有）/linux→`pgrep`+`/proc/pid/cmdline`/darwin→`pgrep`+`ps -p -o command=`；其余平台显式 `procs_skipped=platform`。**语义级教训**：合法 API `open('/proc/...')` 在 macOS 抛异常被 `except` 吞→杀 0 进程无提示——平台守卫必须先于 per-pid 循环内的文件读。

## Windows pid 探活教训

`OpenProcess` 对死 PID 可返非零句柄——必须再 `GetExitCodeProcess` 校验 `code==259(STILL_ACTIVE)`，否则死 daemon 被误判活。`_pid_alive` Windows 分支：OpenProcess(0x0400)+GetExitCodeProcess，ctypes 失败回退 `tasklist /FI "PID eq N"`。

## 终端交互层 Unix-only

`readline`/`termios`/`tty`/`curses` 全 Unix-only——`sh` REPL 三层降级：prompt_toolkit（`[repl]` 可选依赖）→readline→裸 `input()`。新交互功能选型默认 prompt_toolkit，不再加 readline-only 依赖。

**可选依赖测试纪律**：optional extras 的功能测试必须 `pytest.importorskip("模块")` 守卫——本地装了≠CI 装了（0.15.8 CI 红教训：`test_e_completer` 裸依赖 prompt_toolkit，CI 只装 `.[test]`）。对应 CI extra 要同步加进 workflow install。

## @e 句柄补全失效契约

ref→元素是 locator 映射非元素句柄——DOM 内变更（click/fill/eval）不清缓存（locator 重解析仍可能命中不同元素=meta 滞后的设计边界，执行端走 actionability 检查兜底）；导航类（open/goto/go-*/tab-new|select|close/reload/close）清缓存；`tab-list` 只读不清。
