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

## decide margin 契约

`result.margin_source` ∈ `probs`（双侧真 margin）/ `single_side`（单边 probs，非真 margin）/ `conf_fallback`（无 probs 静默降级）——上层据此知 margin 可信度，勿当统一语义用。
