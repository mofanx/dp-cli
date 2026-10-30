# 真机训练数据 Schema——三形态契约

三条数据通路的字段/判据/入库条件统一约定。核心分层：**标签**（可直接入训）
< **弱标注**（降权或显式放行才入训）< **采集池**（待标注原料，永不当标签）。

## 对照总表

| 形态 | 来源 | 字段 | 判据 | 入库条件 | prov | 证据级别 |
|---|---|---|---|---|---|---|
| verified_real | drive_verified_*.jsonl 采集器 / decide_train.jsonl 经 `promote_recorded` 提升 | `query` `ref`(→evidence.chosen_ref) `action.argv` `evidence:{url,before_refs,chosen_ref,action:{argv,exit,result},before:{expr,result},after:{expr,expected,result}}` `positive` `negatives` `post_state`（录制行） | `eligible()` = `src=='verified_real'` + `page` 是 http/https + `has_outcome_evidence()`（before/after 同 expr、exit=0、三段 status=ok、old≠expected 且 new==expected、positive/negatives 与 before_refs 渲染一致、argv 指向 chosen_ref） | 真实执行 + 断言证据：采集器现场断言，或录制行 pre/post 快照 **url diff** 推导断言（`prov='record_derived'`，merge 须 `--allow-derived` 显式放行） | `verified_real`（现场断言）/ `record_derived`（快照推导） | **标签级**——执行后证据背书，唯一默认可入训形态 |
| hard_case | `decide_train._maybe_log_hard_case` 被动落盘 ~/.dp_cli/hard_cases.jsonl（**默认开启**——`DPX_HARD_CASES` 缺省 `'1'`，`=0` 显式关） | `ts` `session` `goal` `url` `engine` `action` `engine_ref` `conf` `margin` `margin_source` `tier` `unsure` `triggers` `schema_v` `options_total` `candidates[{ref,name,role,score?}]`——**schema_v=2 起 candidates 录全池**（`state['options']` 完整集，cap `_HARD_POOL_CAP=300` 对齐 `_EMBED_POOL_MAX`；计分集候选带 `score`、池内未计分 `score` 缺省）；**无 `schema_v` 字段的旧行=v1**（probs top3 截断），下游解析按 v 分流 | `conf<0.5`（low_conf）或 top2 `margin<0.1`（low_margin）或 `conf≥0.8` 且录制行 `post_delta=[]`（high_conf_no_effect——`_record_decide_row` 双快照齐才挂字段，字段缺失不触发；补 conf=1.0 自信错选采集盲区） | **本身不是标签是待标注池**——须经 `review_hard_cases.py --emit-teacher` 出模板（v2 行候选经 `_pick_teacher_cands` 干扰度取样至 `_TEACHER_CAND_MAX=32`：计分候选+同名/同 role 高干扰优先而非纯前 N）、LLM 填 chosen_ref/negatives、**`--promote-teacher` 显式确认门**校验转正 `prov='teacher'`、再过泄漏三闸才可入训；`export_pairs` 不读此文件 | `hard_case`（落盘行无 prov 字段；转模板后 `teacher_pending`） | **采集池级**——低置信信号，仅作主动学习原料 |
| teacher | gen_teacher_*.py 生成 teacher_hardneg*.jsonl / hard_case 模板经 LLM 回填 + `--promote-teacher` 转正 | `query`(`goal`) `positive` `negatives` `candidates` `chosen_ref`（hard_case 转正模板特有，标注位） `page` `teacher_note` | LLM/教师标注 + 三道质量闸：闸1 query×EVAL_CASES 全 goal exact+子串泄漏检测；闸2 miss case 候选串零复用（pos+neg 双查）；闸3 方向对称闸（方向族 pos/neg 双向成对，单极性无镜像断言失败）+ query 去重 + pos∉negs；**hard_case 转正先过 `promote_teacher` 门**：chosen_ref 已填且 ∈ candidate_refs、negatives 非空、query 非空、pos∉negs 四条件齐备才写 `prov='teacher'` | `src='teacher_hardneg'` 过不了 `eligible()`——默认拒收，须 `--allow-unverified`（merge_train/train_reranker）显式放行；未回填/未转正模板行 `prov='teacher_pending'` 同样拒收；**手动改 prov 不绕过门检**——缺 chosen_ref/negatives 的伪标注过不了 `--promote-teacher`，也不该进任何 merge 源 | `teacher`（hard_case 转正）`teacher_hardneg` `teacher_hardneg_r2` …（`teacher*` 家族）；待标注态 `teacher_pending` | **弱标注级**——人工/教师标注但无执行证据，显式旗标放行才入训 |

## 证据层级与"何时进训练"

```
标签级   verified_real   → merge_train/load_pairs 默认接收
         └ record_derived → 同 eligible() 但 merge 须 --allow-derived
弱标注级 teacher*        → 须 --allow-unverified 显式放行（训练侧自行降权）
采集池级 hard_case       → 永不直接入训；--emit-teacher → LLM 回填
                          → --promote-teacher 门检转正 → teacher
         decide(src)     → export_pairs 产弱标注对（训练侧降权/过滤）
         unsure/meta     → 核验原料/伪候选，load_pairs 显式跳过
```

- **何时进训练**：仅 `verified_real`（含 `record_derived` 旗标路径）；
  teacher 需 `--allow-unverified` + 三闸全过。
- **何时只是采集池**：hard_case 全生命周期（转正前）、unsure 行、
  `post_delta=[]` 的录制行（快照无变化=动作疑似未生效，保留作
  失败形态分析原料）、缺 pre_state/post_state 的录制行（无基线
  可判，不冒充 verified）。

## 落盘开关与信任边界

- **hard_case 落盘默认开启**：`DPX_HARD_CASES` 环境变量缺省即 `'1'`
  （`decide_train.hard_cases_enabled()` 判定 `!= '0'` 即开），写路径
  `~/.dp_cli/hard_cases.jsonl`（跨进程文件锁
  `~/.dp_cli/hard_cases.lock`）；显式关闭：`DPX_HARD_CASES=0`。
- **性质=本地调试/训练采集池**：低置信 decide 候选的被动攒盘，
  只写本地 home 目录、不外发；行内容是"待标注原料"不是训练承诺。
- **与 `DPX_RECORD_DECIDE` 是两份职责**：录制训练对是 **opt-in**
  （缺省关，`DPX_RECORD_DECIDE=1` 才写
  `~/.dp_cli/sessions/decide_train.jsonl`）；hard_case 采集池是
  **默认开**——一个主动录制交互命令、一个被动攒低置信候选，
  两文件并行互不替代。
- **teacher_pending→teacher 显式确认门**：`--emit-teacher` 产出的
  模板行 `prov='teacher_pending'` 是待标注态。LLM 回填后必须经
  `review_hard_cases.py --promote-teacher IN --promote-out OUT`
  门检（chosen_ref 已填 ∈ candidate_refs、negatives 非空、
  query 非空、pos∉negs，positive 留空自动按 chosen 候选回填）
  才写 `prov='teacher'`；chosen_ref 空/非法一律拒收计数。
  手动把 prov 改成 'teacher' 不能当作转正——缺字段的伪标注
  过不了本门；merge/train 侧 `eligible()` 对非 verified_real
  默认拒收是第二道保险（teacher 入训仍须 `--allow-unverified`）。

## 录制行附加字段（decide_train.jsonl）

| 字段 | 含义 |
|---|---|
| `pre_state` / `post_state` | 执行前/后快照 `{url, refs_epoch?}`——refs_epoch 为 session refs 签名（控件集变化重签） |
| `post_delta` | pre/post 快照 diff 字段清单（`state_delta()`：`url`/`hash`/`refs_epoch`/`sig`/`dom_sig` 任一变化）；`[]`=零变化空快照，`promote_recorded` 短路拒提升；缺 pre_state 不写此字段（无基线不判） |

`promote_recorded` 证据门（顺序 fail-closed）：unsure→None →
缺 goal/refs/pre/post/ref/argv→None → **`post_delta=[]` 标记或
就地重算零 diff→None** → url diff 才推导导航断言（sig 等包装
字段不提升）→ argv ref 与 chosen ref 比对 → 产出行再过
`eligible()` 终审。
