# embedding 召回层实机验证（2025-10-29）

## 页面

`bench/recall_bench.html`（file:// 本地 fixture，自建电商长列表页）：
51 个可交互元素 → `dpx agent-state --top-k 60` 实测 **54 个 refs**（>30 ✓，
a11y 快照把 select 的 option 也计为独立交互项）。目标元素 `提交订单` = **ref 49**，
语义干扰项 `查看历史订单` = ref 46、`我的订单` = ref 4。

命令：
```
dpx open file://.../bench/recall_bench.html --headless -S recallbench
dpx agent-state -S recallbench --top-k 60
```

## 召回触发证据（临时 DPX_RECALL_DEBUG stderr 仪表，已回退）

`bench/decide_A2.debug.log`：
```
[recall] TRIGGERED: 54 opts > threshold 30, topk=8
[recall] embed_model ready=True load=4.99s        # BAAI/bge-small-zh-v1.5
[recall] encode+rank 54 texts 0.078s kept_idx=[3,39,44,45,48,50,52,53]
[rerank] candidates after recall: 8
[rerank] CE scored 8 pairs in 0.104s
```
kept_idx(0-based)+1 → refs {4,40,45,46,49,51,53,54}，目标 ref 49 在召回集内 ✓
`bench/decide_A2.json` 的 probs.target 只有 **8 个键**（对照全量 54 键）——
非仪表化的行为证据。

## 延迟对照（同页同 goal "提交订单"，--engine reranker --top-k 60）

| 运行 | 模式 | wall | reranker 载入 | embed 载入 | encode | CE 打分 | 选中 |
|---|---|---|---|---|---|---|---|
| A2 | 两级(召回→top8) | 14.50s | 6.56s | 4.99s | 0.078s | 8×0.104s | 49 ✓ |
| A3 | 两级 | 14.27s | 6.64s | 3.53s | 0.075s | 8×0.081s | 同(确定性) |
| B  | 全量(threshold=100) | 8.45s | 6.61s | — | — | 54×0.441s | 49 ✓ |
| B2 | 全量 | 8.45s | 6.58s | — | — | 54×0.456s | 同 |

纯打分段：两级 0.18s vs 全量 0.44s（省 ~0.26s）；
**端到端 CLI 每次进程重载模型，embed 首载 ~4-5s 使两级反而慢 ~6s**。
54 候选规模下收益为负——召回层的盈亏平衡需要更大候选数或常驻进程
（serve/daemon/REPL 内 _EMBED 单例复用后边际成本仅 0.08s/次）。

embedding 模型首载：snapshot_download ~29s（~100MB，HF 直连无 token）+
进程内 load 3.5-5.0s。reranker（bge CrossEncoder ST，/media/yan/Data/bge_v19_tuned）6.6s。

## 正确性

两次模式均选中 **ref 49「提交订单」** ✓——语义正确，且召回子集内含
目标（idx 48）与强干扰（ref 46 查看历史订单、ref 4 我的订单）。
但 conf 仅 0.214 / margin 0.0002 → AGENT_UNSURE（tier=refuse）：
CE 给 ref 49 与 ref 4 打分几乎打平（0.214 vs 0.214），绝对置信无区分度。
全量模式下 49 也是 top1（0.0453 vs ref4 0.0453）——recall 未改变 top1，
但把 softmax 分母从 54 缩到 8，conf 数值被放大约 4.7×，margin 依然贴零。

## 遗留风险

1. **静默降级无信号**：`_embed_model()` 失败仅置 `_EMBED_MISS`，
   CLI 输出无任何提示——本机 `~/.cache/huggingface/hub` 是指向
   `/media/yan/mofanx/...`（root 所有）的死符号链接，PermissionError
   直接吞掉 → 用户视角召回"存在但永远不生效"且无从察觉（A1 运行实证）。
2. **每进程重载成本**：CLI 单发场景 recall 层净亏 ~6s（embed 载入 ≫ CE 节省）。
3. **首次运行另有 HF 下载 ~29s**，失败路径见上。
4. margin≈0 说明 CE 对"提交订单/我的订单"这类近义干扰区分力弱，
   召回层只缩规模不提精度（两模式 top1 相同）。
