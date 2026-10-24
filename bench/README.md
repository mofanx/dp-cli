# bench/ — decide 引擎评测与训练管线（实验产物）

## 文件

- `eval_cases.py` — **隔离 eval 集**（n=38，原 holdout 8+新造 30；永不入 train）
- `eval_decide.py` — 评测入口，`python bench/eval_decide.py [model]`，分桶 elem/meta
- `train_reranker.py` — CrossEncoder 微调入口；**含泄漏守卫**（train query 命中 eval goal 即拒训）
- `collect_sites.py` — 静态采集器（fixture HTML→样本 jsonl）
- `bench_*.py`/`results.json` — 各引擎实测数据

## 数据纪律

- eval 与 train 来源级隔离；`check_leakage()` 在训练入口强制
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
