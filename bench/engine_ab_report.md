# decide 引擎 A/B 实机对照：reranker(bge_v19_tuned) vs external(DOM 序 top1)

日期：2026-02（dp-cli 0.15.10，本机 Ubuntu 26.04，无头 Chromium）
数据：`bench/engine_ab_report.json`；驱动：`bench/engine_ab.py`（可复跑）

## 方法

- 浏览器：`dpx open --headless` 起无头 Chromium，`dpx daemon-start` 起常驻
  daemon——reranker 模型驻留 daemon 进程，热载延迟不被每次 ~7s 模型
  加载污染。每场景 `dpx goto <page>` 同页面同 goal 跑两引擎，跑完
  `daemon-stop` + `close` 清场。
- 页面：本地 fixture `bench/fixtures/*.html`（含新造 5 个歧义/meta 页）
  + 真实站 github.com（97 个可交互候选，触发 reranker embed 召回两级路径）。
- **external 基线协议**：无模型——恒定答 `target=1`（DOM 序首候选），
  action 由 ref1 的 role 经生产 `_goal_action(goal, role)` 映射（与
  reranker 选中后的 action 推导同一函数，口径一致）。这是"纯 DOM 序"
  稻草人基线，不代表真实 LLM 外部引擎。
- reranker：`/media/yan/Data/bge_v19_tuned`（ST CrossEncoder，bge 家族）。
- 采样：external×2、reranker×3（热载）取中位数；模型首载单列不混入。
- 判定：元素场景比对 top1 ref 与期望 ref（期望 = agent-state 候选表中
  名称/role 首个命中的元素）；meta 场景比对 action。UNSURE 时 detail
  里的 top1 仍计入对错判定。
- 延迟 = `dpx decide` CLI 端到端 wall ms（含 snapshot+IPC+引擎），
  meta 短路路径的 ~143ms 即"无引擎"基线参照。

## 汇总表

| 场景 | 类目 | goal | 候选数 | ext top1 | rer top1 | ext 对 | rer 对 | ext ms(中位) | rer ms(中位) | meta短路 | UNSURE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| c1 | click | 点击登录按钮 | 6 | 1 click | 1 click | ✗ | ✗(zh↔en miss) | 143 | 221 | — | ✓refuse |
| c2 | click | 点击导出按钮 | 10 | 1 click | 5 click | ✗ | ✓ | 157 | 238 | — | ✓refuse |
| c3 | click | 取消这次删除操作 | 3 | 1 click | 1 click | ✓运气 | ✓ | 134 | 193 | — | — |
| f1 | type | 填写用户名 | 6 | 1 type | 1 type | ✓运气 | ✓ | 153 | 228 | — | — |
| f2 | type | 填写邮箱地址 | 6 | 1 click | 2 type | ✗ | ✓ | 148 | 238 | — | — |
| f3 | type | 搜索本站内容 | 7 | 1 click | 7 type | ✗ | ✓ | 153 | 220 | — | ✓refuse |
| a1 | 歧义 | 注册新账号 | 8 | 1 click | 7 click | ✗ | ✓ | 145 | 236 | — | — |
| a2 | 歧义 | 点登录 | 8 | 1 click | 6 click | ✗ | ✓(button vs 同名 link) | 148 | 240 | — | ✓refuse |
| a3 | 歧义 | 删除这条记录 | 8 | 1 click | 3 click | ✗ | ✓(vs 归档) | 143 | 239 | — | ✓refuse |
| a4 | 歧义 | 归档这条记录 | 8 | 1 click | 2 click | ✗ | ✓ | 132 | 240 | — | ✓refuse |
| a5 | 歧义 | 查看耳机的详情 | 6 | 1 click | 3 click | ✗ | ✓(3 个同名"详情"靠 aria 区分) | 147 | 247 | — | ✓refuse |
| a6 | 歧义 | 立即购买下单 | 7 | 1 select | 6 click | ✗ | ✓(vs Add to cart) | 147 | 263 | — | — |
| a7 | 歧义 | 开启深色模式 | 5 | 1 click | 2 click | ✗ | ✓ | 143 | 208 | — | — |
| m1 | meta | 返回上一页 | 10 | back | back | ✓ | ✓ | 130 | 152 | ✓返回意图 | — |
| m2 | meta | 向下滚动查看更多内容 | 10 | scroll_down | scroll_down | ✓ | ✓ | 161 | 144 | ✓滚动意图 | — |
| m3 | meta | 确认提交已成功 | 1 | done | done | ✓ | ✓ | 140 | 134 | ✓完成意图+完成态 | — |
| m4 | meta | 继续下一步操作 | 1 | blocked | blocked | ✓ | ✓ | 112 | 140 | ✓人机特征 | — |
| m5 | meta | 返回购物车 | 6 | back | back | ✓规则层 | ✓规则层 | 157 | 148 | ✓返回意图* | — |
| g1 | 歧义·真站 | 点击 Sign in 登录 | 97(截20/池97) | 1 click | 4 click | ✗ | ✓ | 860 | 3445† | — | ✓refuse |
| g2 | 歧义·真站 | 注册 GitHub 账号 | 97(截20/池97) | 1 click | 47 click | ✗ | ✗(选 'GitHub' link 非 Sign up) | 861 | 3271 | — | ✓refuse |

\* m5：goal 语义指向 `Back to cart` 链接（ref6），meta 规则按"返回"
误短路为浏览器 back——两引擎同受影响（meta 在引擎调用前执行），
规则层判对、语义层实为误命中。
† g1 首跑 12727ms 含 embed 召回模型冷载，已从热载中位数剔除。

## 统计

| 指标 | external | reranker | Δ |
|---|---|---|---|
| 元素场景准确率（n=15） | 2/15 = 13.3% | 13/15 = 86.7% | **+73.3pp** |
| 歧义子集（n=9，权重放大） | 0/9 = 0% | 8/9 = 88.9% | **+88.9pp** |
| meta 场景（n=5） | 5/5（走 meta-rules） | 5/5（同） | 0pp |
| 本地 fixture 延迟中位数 | 144ms | 233ms | +89ms |
| meta 路径延迟中位数 | — | 143ms | 引擎未调用的基线 |
| github.com 延迟中位数 | 861ms | 3375ms | +2514ms（97 候选+embed 召回+大 snapshot） |
| UNSURE 触发率 | 0/20 | 9/20 = 45%（元素场景 9/15 = 60%） | — |
| 模型冷载 | — | bge CE 首载 6954ms；embed 召回模型首载 ≈9300ms（g1 首跑 12727 vs 热 ~3400） | 进程级一次性 |

## 结论

- **歧义场增益实锤**：9 条歧义场景 external 全错（恒定选 ref1），
  reranker 对 8 条（+88.9pp）——同名 link/button 区分（a2）、
  删除 vs 归档（a3/a4）、多同名按钮靠 aria-label 上下文（a5）、
  Add-to-cart vs Buy-now（a6）、真站 Sign in（g1）全对。
- **两败点如实记录**：c1 中文 goal"登录"匹配不上英文 'Sign In'
  （跨语近义 miss，且分数全塌→flat probs→UNSURE 正确拒答）；
  g2 goal 里的 "GitHub" 字面词劫持打分，top1 选了 'GitHub' link
  （ref47），正确项 'Sign up for GitHub'（ref7/ref44 两处）在 top2。
- **UNSURE 不误伤**：9 次 UNSURE 里 7 次 top1 其实对——margin 门槛
  保守（refuse 档仍给出可用 top1），c1 那次 refuse 恰好拦下真错答。
  tier/UNSURE 口径略不一致：a6 margin 0.196 过线未 UNSURE 但
  conf 0.31 仍 refuse 档。
- **延迟代价**：本地页 +89ms 中位（snapshot 本身 ~143ms 占大头，
  引擎增量 <100ms）；候选近百的页面热载 ~3.4s（embed 召回+CE 97 对）。
  进程级冷载 bge ≈7s、embed ≈9s，daemon/serve 常驻可摊薄为零。

## 复现

```
dpx open --headless && dpx daemon-start
python bench/engine_ab.py      # → bench/engine_ab_report.json
```
