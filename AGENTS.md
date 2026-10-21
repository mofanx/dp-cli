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
