# UI↔API 联动设计：提示式接口化（advisory hint 四层）

状态：**设计冻结，可实施**（P0 起按节落地）
版本：v1.1（v0.15.29 基线；v1.1 补 L0.5 缓存契约+serve hints 命名空间）

## 一、定位

dp-cli 已完成"一切皆接口"基础设施：netlog 被动抓包、`api-list` 端点泛化、
`requests --export` curl/python 导出、`dpx api` 页内 fetch 原语、
`api-record`/`dpx api <name>` 录制式 adapter（fail-closed `_run_ui_fallback`）、
serve `action=api` 中转。实测 api 直调 0.344s vs 纯 UI 1.76s（~5x）。

缺口在**衔接层**：零件之间全靠 agent 手动串——不知道"这动作触发了哪个接口"，
不知道"这站录过 adapter"。本文档定义四层提示机制补上衔接，**全加性、
advisory、零破坏**。

## 二、三层红线（不可违反）

1. **全加性字段，零破坏**：只加响应字段/命令 flag/新 action——旧 consumer
   忽略未知字段照常工作。daemon 序列化同 dict 透传，不改传输层。
   不改任何默认行为路径：UI 还是 UI、api 还是 api。
2. **advisory，绝不自动换路径**：所有提示是建议非指令——agent 可无视走 UI。
   L3"默认走 api（自动劫持 UI→API）"已否决；`DPX_API_PREFER` opt-in 推迟到 P4，
   等 P0-P2 数据证明价值再议。
3. **读写策略分层**：
   - 读（列表/详情/搜索/分页）：API 优先——读不改状态、接口稳定、返回结构化
   - 写（发帖/评论/删除/提交）：UI 优先——CSRF/签名/反爬/审计边界
   - 幂等写（draft PATCH/设置 PATCH）：可录 adapter，但须显式录制
   - 导航/登录：UI 唯一，无接口可替代

## 三、四层提示机制

### L0 adapter_hint（前置路由提示）——P1

`decide`/`open` 响应新增 `data.adapter_hint`：

```json
{"adapter_hint": {"name": "zhihu-draft", "domain": "zhuanlan.zhihu.com",
                  "action": "PATCH", "params": ["title", "content"]}}
```

- 匹配：当前 URL 的 host + adapter 的 url_template host 命中（+可选 action/goal 相似度）
- **epoch 守卫**：refs_epoch 变（页面变了）不提示陈旧 adapter——提示仅对"当前页面形态"有效
- 语义："此页有录过的 adapter，可考虑 `dpx api zhihu-draft` 而非走 UI"
- 否决注脚：拒答（UNSURE）路径也带 hint——拒答恰是接口化最值钱的场景

### L0.5 hint 字段的缓存契约（现算派生，禁缓存）

`adapter_hint`/`touched_apis` 属**现算派生字段**——与 `margin`/`tier`/
`confidence_tier` 同级：decide 缓存（`_DECIDE_CACHE`）命中时**不得从缓存体
读出 hint**，必须命中后同 margin/tier 一样**当次重算**。

理由：hint 依赖两类易变外部状态——
- adapter 注册表可变（agent/用户随时 `api-record`/`--delete`）——缓存的
  `adapter_hint` 会指向已删 adapter（陈旧误指）；
- netlog 持续追加——`touched_apis` 的 ±2s 窗口只对当次动作有意义，
  缓存命中=新动作旧窗口，直接错义。

落法：缓存 `put` 前的 resp 对象中剥离 `hints` 子树（或 hint 在 put 后注入），
`get` 命中后由响应组装层按当前注册表/netlog 重算注入——与 margin 现算
同一代码层。实现成本≈0（hint 本来就要当次算），删除一处"把 hint 放
进缓存体"的诱惑即可。

### L1 touched_apis（因果捕获）——P0

`click`/`fill`/`submit`/`type` 成功响应新增 `data.touched_apis`：

```json
{"touched_apis": [{"method": "PATCH", "url": "https://zhuanlan.zhihu.com/api/articles/20998.../draft",
                   "status": 200, "latency_ms": 342}]}
```

- 实现：动作执行前后 ±2s 时间窗内，过滤已在写的 netlog 行（内存筛选非热路径）
- netlog 未挂载/netlog 该窗口无 XHR → 字段缺省（不空数组不误报）
- 语义："这动作触发的是 PATCH /draft"——消灭 agent 翻时间线猜接口

### L2 api-record --auto/--last（显式固化降摩擦）——P2

`dpx api-record <name> --last`：自动取最近窗口内最后一条 XHR，无需 `--index`
`dpx api-record --auto`：自动域名命名（`zhihu-draft`→`zhuanlan.zhihu.com-draft`）+取最后 XHR

- **仍显式调用**——自动化的正确粒度是"自动看到，手动决策"：自动录会
  产生垃圾 adapter（埋点/探活端点）、静默隐私面（URL+body 落盘）、注册表污染
- 参数边界人来划（`--param kw=query`）——机器无法判哪参数可变

### L3 api-list --suggest（闲置资产盘点）——P3

`dpx api-list --suggest`：netlog group by url_template 计数，列出
"同 endpoint ≥2 次且未录 adapter"的端点——**重复低效操作自动冒头**：

```json
{"suggest": [{"url_template": ".../draft", "hits": 4, "suggest_record": "api-record zhihu-draft --last"}]}
```

### serve `action=api` 的 hint 命名空间（定为聚合式）

决定：`hints:{}` **聚合命名空间**，不平铺顶层。

```json
{"status": "ok", "data": {
  "http_status": 200, "via": "api", "body": "...",
  "hints": {"adapter_hint": {...}, "touched_apis": [...]}
}}
```

取舍理由：
- serve 层契约已被 `{"action":...}` 请求形态约束——响应 `data` 是
  动作结果域，hint 是**旁路信息**，聚合后消费端一处 `data.hints` 判存在
  即可，不用逐字段探测；
- 平铺会让 `data` 顶层随 L2-L4 扩展越塞越杂（adapter_hint/touched_apis/
  suggest/未来的 plan 建议），命名空间给扩展留位置不挤结果域；
- CLI `dpx api/decide/click` 直发响应**同规则**——hint 一律进
  `data.hints`，子键与本文档各层字段同名（adapter_hint/touched_apis），
  serve 透传不映射。

兼容：`hints` 整体缺省=无提示（非空对象）；消费者判 `data.get("hints")`
存在性，永不 KeyError。

## 四、P4（推迟）：DPX_API_PREFER

`DPX_API_PREFER=1` 时 `dpx decide` 先查注册表，goal 匹配 adapter 直接
`via=api` 直调跳过 UI——**默认走 api 对用户意外性大，等 P0-P2 使用数据
证明 adapter 覆盖率足够高再做**。opt-in 反转式设计：不开 env 的用户
拿到的响应体和今天完全一样。

## 五、开销预算

| 件 | 实现 | 成本 |
|---|---|---|
| touched_apis | ±2s 窗口过滤已在写的 netlog 行 | ms 级内存筛选，非热路径 |
| adapter_hint | readdir adapters/ + host 匹配 | <1ms（几 KB 目录） |
| --suggest | netlog 单趟 group by 计数 | ms 级离线命令 |
| 响应体膨胀 | `data.hints` 命名空间下几十字节 | 可忽略；`DPX_API_HINT=0` 可关 |

净成本：**每字段几十字节+ms 级内存筛选**——无新增网络/磁盘热路径。

## 六、破坏性审计

| 改动 | 风险 | 缓解 |
|---|---|---|
| 响应加 `adapter_hint`/`touched_apis`/`hints` | 零——字段加性，旧 consumer 忽略 | `data.hints` 命名空间隔离 |
| netlog action marker 行 | 低——`_netlog_rows` 解析遇无 URL 行会崩 | marker 加 `"type":"action_marker"`，解析 filter by type |
| adapter schema 加 `steps` | 零——老 adapter 无字段走原 goal 路径 | `.get('steps')` 宽容 |
| `api <name>` vs URL 歧义 | 零——name 正则 `[A-Za-z0-9][\w-]{0,63}` 不匹配 URL | 已存在 |
| decide 感知注册表 | 中——默认开会暗中改路由 | `DPX_API_PREFER` opt-in 推迟 P4 |
| daemon/serve 传输 | 零——hint 走既有响应管道 | — |

**默认行为一字不变**：不开 env 的用户拿到的响应体和今天完全一样。

## 七、落地优先级

| 序 | 件 | 理由 |
|---|---|---|
| P0 | netlog action marker + touched_apis | 治"翻时间线猜"——因果窗口最小改动最大收益 |
| P1 | adapter_hint 前置 | 治"录了却忘了用"——路由决策前移 |
| P2 | api-record --auto/--last | 固化摩擦再降一档仍显式 |
| P3 | api-list --suggest + 分页参数检测 | 重复低效自动冒头+遍历场景 10-100x |
| P4 | DPX_API_PREFER | 等 P0-P2 数据验证覆盖率再做 |

前置必修（采集链地基）：netlog arm 挂 `latest_tab` 忽略 `active_tab` 的
multi-tab 失效 bug；`dpx listen` 零捕获待复现。

## 八、协同而非替代

- **decide**：从"每步必跑"变"首次探索引擎"——职责更纯
- **netlog**：从事后排查工具升级成因果图谱
- **ui_fallback**：补 `steps` 序列回放（录确定性动作链非靠 decide 重猜）——对 conf=1.0 误选站点更稳
- **hard_cases 飞轮**：api 碎→UI fallback→再碎→自动进 hard_cases——接口路径失败也有数据产出

心智模型：**"第一次用 UI 摸清怎么干（netlog 自动记录+hint 提示固化）→
adapter 沉淀为技能 → 同类任务直调接口 → 接口碎了自动回 UI"**——UI 探索，
API 复用，失败闭环。
