# bench/ — decide 引擎评测与训练管线（实验产物）

## 文件

- `eval_cases.py` — **隔离 eval 集**（n=38，原 holdout 8+新造 30；永不入 train）
- `eval_decide.py` — 评测入口，`python bench/eval_decide.py [model]`，分桶 elem/meta
- `train_reranker.py` — CrossEncoder 微调入口；**含泄漏守卫**（train query 命中 eval goal 即拒训）
- `collect_sites.py` — 静态采集器（fixture HTML→样本 jsonl）
- `bench_*.py`/`results.json` — 各引擎实测数据
- `DATA_SCHEMA.md` — 三形态数据契约（verified_real / hard_case /
  teacher），落盘开关与信任边界详见该文件
- `review_hard_cases.py` — hard_cases.jsonl 复核清单 + teacher
  待标注模板（`--emit-teacher`）+ 转正确认门（`--promote-teacher`）

## 数据纪律

- eval 与 train 来源级隔离；`check_leakage()` 在训练入口强制
- 默认只接收 `src='verified_real'` 且满足 `bench/verified_data.py`
  所定义证据的行：前快照 ref 合法、动作执行成功、前后断言证明
  目标状态发生期望变化；仅文件声明不替代人工审核目标/断言语义。
  `src='pilot_local'` 只演示不训练；历史 `seed_train_v13.json`/
  drive_d3 等保留不修改，实验性绕过须显式 `merge_train --allow-unverified`
  （`--allow-bad-src` 为旧版兼容别名）和
  `train_reranker --allow-unverified`——两个 override 都放行
  **全部无证据来源**（含 pilot_local/decide/meta/synthetic），
  只能作历史实验，eval goal 撞车仍被拒，
  标注不会作为 clean 数据声明
- **hard_case 被动采集默认开启**（`DPX_HARD_CASES` 缺省 `'1'`）：
  decide 低置信候选（conf<0.5 或 top2 margin<0.1）写
  `~/.dp_cli/hard_cases.jsonl`，`DPX_HARD_CASES=0` 关闭。
  性质=本地调试/训练采集池，不外发；与 **opt-in** 的
  `DPX_RECORD_DECIDE`（缺省关，写 `decide_train.jsonl`）是两份
  职责——一个默认开攒待标注原料、一个默认关录交互训练对。
  hard_case 永不当标签：须经 `--emit-teacher` 出模板 → LLM 回填
  → `--promote-teacher` 门检（chosen_ref∈candidate_refs、
  negatives 非空、pos∉negs）转正 `prov='teacher'` 才可走
  `--allow-unverified` 入训；手动改 prov 不绕过门检。
- `collected_train.jsonl` 首采 4 行=fixture 小集跑通验证，**非产量**；
  放量需真实 URL 集或多 fixture
- 历史口径修正：旧 n=8 holdout ±12.5pp/case 噪声不可信；
  bge zero-shot 新口径基线 **73.7%**（elem 77.4%/meta 57.1%）

## 门禁

`eval_decide.py` 输出 ADVISORY/AUTONOMOUS（85% 线）。当前全部模型 ADVISORY。

## 口径勘误（t3）

- 上轮「采集增益 meta +28.6pp」口径修正：meta 桶 n=7，±14.3pp/case，
  +28.6pp 仅为 2 个 case 翻转——方向可信幅度不可信，需 meta 扩桶复核。
- 净化复核：v1/v2/v3 过 `check_leakage` 均 0 命中（query 级）；v4 由
  `merge_train.py` 守卫化 merge 重建（剔除 1 条 goal 撞车行）——
  数据从源头到 merge 全链路有守卫。

## 阴性结果沉淀（数据纪律）

- **合成改写增广无效**：1420 对变体 vs 355 对——holdout 75% 纹丝不动；
  同分布重复曝光不扩泛化。
- **meta 伪样本毒化**：v6 混入 44% meta pair→elem 桶被稀释回退
  （64.5→61.3）；v7 剔净后 **elem 回升 61.3→71.0%（最高）**。
- **判定**：训练集只收真实 elem 样本；meta 类决策永远归 `_meta_decide`
  规则层（85.7%），不喂排序模型。collect_sites `_meta_samples` 已停用。

## 收口归档（2026-10，decide 线休眠）

### 范式上限（实证）

- **demo 站银标封顶**：56 条 verified_real（d2-8+d3-33+d4-15）+
  24 条 deep_semantic fixture（d3）——同分布扩量收益已零，
  error-driven 定向采集也未救 miss（v16 elem 64.5% 持平）。
- **准确率上限 ≈bge zero-shot 73.7%**——自训 MiniLM（22.7M）六路捷径
  （蒸馏/直训 bge/合成增广/公开轨迹/meta 伪样本/模板 goal）全部实证无效；
  `267eee5` 已切 bge-reranker 为默认 advisory 引擎，自训模型降级轻量备选。
- **meta 桶不可采集**：6/17 miss 是元决策（done/blocked/wait/back），
  属 `_meta_decide` 规则层而非元素排序——词表扩充 61.1→88.9%（t1 实证），
  是确定性收益而非数据问题。

### 管线基建（可复用资产）

- `verified_data.eligible()`：verified_real 契约=执行前 refs+实际动作+执行后断言闭合；
  v12/v13 未核验数据被默认拒收，`--allow-unverified` 显式放行仅作历史实验。
- `drive_verified_{d2,d3,d4}.py`：三套采集器可增量补跑；
  `gen_fixture_deep.py` localhost 批量 fixture 通路已证。
- `eval_cases.py` 扩至 68 条六桶覆盖（meta/deep_semantic/long_list/
  distractor/select/否定），统计力 ±1.9pp/case。
- `miss_targets.json`：17 miss 五桶分类+采集靶标映射——下次重启的数据地图。

### 遗留（休眠期等触发）

- 真实流量录制 `DPX_RECORD_DECIDE=1`（`~/.dp_cli/sessions/decide_train.jsonl`
  当前 ~18 条）——攒到 300+ 条真实自然 goal 再回来重训。
- bge conf/margin 分布完全重叠（`cc1a275` 实测），tier act 封顶 confirm——
  若有校准数据回归可解锁 act 档。
- deep_semantic 桶 5/17 miss 需 30+ 条真实语料才见信号（fixture 24 条已起步）。
