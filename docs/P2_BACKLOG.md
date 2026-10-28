# P2 Backlog——四波生产审查登记台账

> 来源：session/bridge_manager（11）+ daemon/serve（15）+ decide_train/动作层（14）+ tab/cookie/storage/listen/netlog（12）四波扫描。
> 分级口径：P0=数据丢/安全洞/崩溃；P1=用户可感知错状态/误杀/契约破；P2=边界/竞态/韧性/语义瑕疵——当前可查不阻塞。
> 0.15.15 已修全部 21 P1；本表为剩余 P2 登记，按主题合并同类项。

## 一、stale-marker / 状态残留一族（最密桶，~10 条）

| 文件 | 缺陷 | 建议修法 | 严重度理由 |
|---|---|---|---|
| `daemon.py` meta 文件族（:207/:790 回收点） | daemon 异常退出后 `.meta.json`/`.sock` 残留，下次 spawn 需 stale 回收路径；部分分支只清 sock 不清 meta | 启动侧统一 `_cleanup_stale()`：meta pid `_pid_alive` 双源核验后再判定，跨平台一致 | P2——已有回收主路径，残留只在异常 kill 场景 ✅已修(commit待) |
| `daemon.py:215` `_READY_CACHE` | session 就绪缓存按名存——session 文件被外部改/删后缓存脏读 | 就绪缓存加 mtime/inode 指纹，变更即失效 | P2——概率低，改会话文件需显式操作 ✅已修(commit待) |
| `serve.py` 会话路由 | `record_session` 变量在分支间传递，错误会话名路径下有执行残留态可能 | 请求入口统一会话校验前置，非法名 fast-fail | P1 已修主线；残留为边角分支 |
| `netlog.py:18` `_armed` | session→tab_id 跟随已修；残留：session 删除后 `_armed` 键泄漏（长驻进程内存） | `_armed` 定期按活跃 session 集收编 | P2——内存微漏，重启即清 |
| `bridge_manager.py` registry | bridge registry json 已原子写；残留：stale bridge 条目无 TTL 清理 | registry 读侧过滤 `_pid_alive==False` 条目惰性清 | P2——文件会涨但不影响正确性 ✅已修(commit待) |

## 二、竞态/并发（~8 条）

| 文件 | 缺陷 | 建议修法 | 严重度理由 |
|---|---|---|---|
| `tab` 命令族 | tab select/close 与 navigate 并发时 refs 缓存失效时序窗口 | 导航类操作已清缓存（契约内）；窗口期并发双命令属使用侧风险 | P2——CLI 串行使用模型下几乎不触发 |
| `netlog._append` | 跨进程文件锁已修；残留：同 session 多 serve 实例写竞争边界 | 锁粒度已够；append+trim 同临界区已闭环 | 记录项——主路径已修 |
| `daemon._acquire_spawn_lock`（daemon.py:352-361 LOCK_NB 已修，残留为 stale 锁属主） | flock→LOCK_NB 轮询已修；残留：Windows O_EXCL 路径 stale 锁回收依赖 `_pid_alive`，pid 复用边缘 | cmdline 校验同款套到锁文件属主判定 | P2——pid 复用低概率 ✅已修(commit待) |
| `session.py` refs 缓存（设计边界已文档化） | DOM 内变更不清缓存是设计边界；残留：eval 侧手动 DOM 改动（JS remove）不触发失效 | 文档化边界；或 eval 后可选 sig 校验 | P2——设计取舍，文档已述 |
| `storage.py` cookie 读写 | cookie 文件读改写非原子（多进程并发写丢行） | 同 session 原子写模式套 cookie 文件 | P2——CLI 并发写同 cookie 文件场景少见 |

## 三、契约/语义残缺（~9 条）

| 文件 | 缺陷 | 建议修法 | 严重度理由 |
|---|---|---|---|
| `serve.py` 错误响应 | 部分异常路径返回缺 `status/code` 字段的裸 dict | JSON 契约断言测试补全——每条错误路径走 `contract` 校验 | P2——主路径已合规，边角响应漏 ✅已修(commit待) |
| `decide_train.py` export | unsure 行 export 边界：`engine_ref` 有但 `chosen_ref` 无时正例为空已被 skip；残留边界为 engine_ref==chosen 同源污染 | export 加 `engine_ref != chosen_ref` 断言注释 | P2——已 skip 主路径 ✅已修(commit待) |
| `eval`/`frame` 坐标 | frame 内元素坐标系（page vs frame 相对）存疑——需实机验证 | 实机复现后再定 | P2 待验证项 |
| `tab` `tab-list` 只读 | `tab-list` 不清缓存正确；残留：tab detach 事件后 list 滞后 | CDP detached 事件监听惰性标记 | P2——滞后窗口小 |
| `storage.py` cookie HttpOnly | 已修导出 skipped_lines；残留：导入路径对部分 HttpOnly cookie 静默跳过（浏览器策略） | 导入结果同样报 skipped 数 | P1 已半修，残留为导入侧同报 |
| `hook.py` event_handlers | 事件回调异常吞掉无上报通道 | 回调 except 分支写 debug 计数到响应 detail | P2——调试可见性 ✅已修(commit待) |

## 四、DoS/资源面（~6 条）

| 文件 | 缺陷 | 建议修法 | 严重度理由 |
|---|---|---|---|
| `serve.py` 单请求 | 单请求 `daemon-stop`/`kill` 类命令无鉴权——本地 socket 默认可信但共享机风险 | serve 绑定 token 可选旗（`--token`），默认仅 localhost | P2——威胁模型为本地，远程绑定才有真实风险 |
| `serve.py` 请求体 | 单行 JSONL 无大小上限——超大 payload 内存撑爆 | `readline` 加 1MB 截断+413 契约错 | P2——本地工具 ✅已修(commit待) |
| `daemon.py` 连接数 | 无并发连接上限——慢请求堆积 fd 泄漏 | accept 循环加 max_conn 闸 | P2 ✅已修(commit待) |
| `bridge_manager.py` Popen PIPE | stdout/stderr PIPE 无人 drain——bridge 大量输出时管道满阻塞 | stderr→DEVNULL 或 drain 线程 | P2——bridge 输出量实际小 |

## 五、韧性/边界（~7 条）

| 文件 | 缺陷 | 建议修法 | 严重度理由 |
|---|---|---|---|
| `decide_train.py` jsonl | `decide_train.jsonl` 无限增长无 trim | 复用 netlog trim 模式按行数滚动 | P2 |
| `serve.py` idle 标记 | headless idle 1h 自退已实现；残留：attached 会话豁免判定只看连接态不看活动 | idle 判定加最后活动时间戳 | P2 |
| `browser.py` kill-all | darwin `ps -p -o command=` 已分派；残留：pgrep 匹配过宽可能误列（部分实现） | 匹配串加 daemon/dpx 特征收窄 | P2 |
| `network.py` har-dump | 已修回调回填；残留：dump 中途中断留半成品文件 | tmp+rename 原子产出 | P2 |
| `listen`/`record-*` | `_skipped()` 前缀匹配已闭环；残留：未来 `record-*` 新命令命名漂移会误拦/漏拦 | 注册表显式名单替代前缀匹配（或双保险） | P2——文档已警示 |
| `session.py` `download_path` | cwd 默认非"未配置"——已知教训，部分下游判定仍可能误用 | 统一 `_dl_path()` 语义封装 | P2 |
| `agent-state`/`snapshot` | refs epoch 签名只比 body sig——属性级变化（disabled/hidden）不重签 | sig 输入加交互属性子集哈希 | P2——影响 ref 缓存精度边缘 |

## 汇总

- **P2 合计 ~40 条**（去重后），五桶分布：状态残留 ~10 / 竞态 ~8 / 契约 ~9 / DoS ~6 / 韧性 ~7
- **共同修法模式**：原子写/`_pid_alive` 双源校验/cmdline 校验/结构化 skipped 上报/契约断言测试——21 P1 已建的工具函数全部可直接复用
- **建议处理序**：状态残留一族 > 契约残缺 > 竞态 > 韧性 > DoS（本地工具威胁模型下 DoS 最低优先）
- 每条修复成本预估 1-4h（复用既有基建）；全清约需 2-3 天工作量
