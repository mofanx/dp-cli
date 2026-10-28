#!/usr/bin/env python3
"""reranker 微调管线——CrossEncoder.fit，输入 decide_train.jsonl 导出的 pos/neg 对。

用法：python bench/train_reranker.py [train.jsonl] [--smoke]
无数据/空数据安全退出；--smoke 用 42 用例构造伪训练集验证管线能跑。
"""
import json, sys, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from verified_data import eligible

OUT = Path.home() / '.dp_cli' / 'decide_model'
BASE = 'cross-encoder/ms-marco-MiniLM-L6-v2'


def load_pairs(path, allow_unverified=False):
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    invalid = [i for i, row in enumerate(rows, 1) if not eligible(row)]
    if invalid and not allow_unverified:
        raise ValueError(f'{len(invalid)} 行缺执行后证据，行号 {invalid[:5]}；'
                         '旧实验须显式 --allow-unverified')
    pairs = []
    for r in rows:
        q, pos = r['query'], r['positive']
        if r.get('src') == 'unsure' or not q.strip() or not pos:
            continue
        pairs.append((q, pos, 1.0))
        for neg in r.get('negatives', []):
            if neg and neg != pos:
                pairs.append((q, neg, 0.0))
    return pairs


def smoke_pairs():
    """42 用例构造伪训练集：goal→expect 元素为正，其他为负。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from bench_needle import CASES
    from bench_engines import candidates
    pairs = []
    for c in CASES:
        ea, er = c['expect']['action'], c['expect']['ref']
        for a, r, t in candidates(c):
            label = 1.0 if (r == er and er is not None) else 0.0
            # 动作伪选项：expected action 无 ref 时标记对应动作文本为正
            if er is None and a == ea:
                label = 1.0
            pairs.append((c['goal'], t, label))
    return pairs


def check_leakage(pairs):
    """训练 query 与 eval 集 goal 做精确+子串重叠检测——命中即拒绝。

    纪律：eval_cases.EVAL_CASES 是隔离集，train 数据不得含其 query
    （同字面或互为子串）。返回 (clean, hits)。
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from eval_cases import EVAL_CASES
    eval_goals = {c['goal'].strip() for c in EVAL_CASES}
    hits = []
    for q, _, _ in pairs:
        qs = q.strip()
        for g in eval_goals:
            if qs == g or (len(qs) > 4 and (qs in g or g in qs)):
                hits.append((qs, g))
                break
    return hits


def main():
    global BASE, OUT
    smoke = '--smoke' in sys.argv
    distill = '--distill' in sys.argv
    base = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--model=')), None)
    out = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--out=')), None)
    if base: BASE = base
    if out: OUT = Path(out)
    src = next((a for a in sys.argv[1:]
                if not a.startswith('--')), None)
    allow_unverified = '--allow-unverified' in sys.argv
    if allow_unverified:
        print('WARNING: --allow-unverified admits legacy/unverified data; '
              'results are not clean training data')
    if distill:
        if not allow_unverified:
            print('蒸馏历史数据缺执行后证据；仅旧实验可显式 --allow-unverified')
            sys.exit(2)
        # 蒸馏路径——query/positive/negative[]/label[软分] 格式，
        # MarginMSELoss 学教师 margin 非 0/1 硬标签
        rows = json.loads(Path(src).read_text(encoding='utf-8'))
        from datasets import Dataset
        # 三元组(query,pos,neg)+教师 margin 差分标签——MarginMSELoss 支持格式
        flat = [{'sentence1': r['query'], 'sentence2': r['positive'],
                 'sentence3': n, 'label': r['label'][0] - r['label'][i + 1]}
                for r in rows for i, n in enumerate(r['negative'])]
        ds = Dataset.from_list(flat)
        from sentence_transformers import CrossEncoder
        from sentence_transformers.cross_encoder import (
            CrossEncoderTrainer, CrossEncoderTrainingArguments)
        from sentence_transformers.cross_encoder.losses import MarginMSELoss
        m = CrossEncoder(BASE, num_labels=1)
        args = CrossEncoderTrainingArguments(
            output_dir=str(OUT), num_train_epochs=int(__import__('os').environ.get('TRAIN_EPOCHS','2')),
            per_device_train_batch_size=8, learning_rate=2e-5,
            logging_steps=20, save_strategy='no')
        tr = CrossEncoderTrainer(model=m, args=args, train_dataset=ds,
                                 loss=MarginMSELoss(m))
        tr.train()
        m.save_pretrained(str(OUT))
        print(f'distill trained {len(rows)} rows → {OUT}')
        return
    if smoke:
        pairs = smoke_pairs()
    elif src and Path(src).exists():
        try:
            pairs = load_pairs(src, allow_unverified=allow_unverified)
        except ValueError as e:
            print(f'训练数据拒收: {e}')
            sys.exit(2)
    else:
        f = Path.home() / '.dp_cli/sessions/decide_train.jsonl'
        if f.exists():
            if not allow_unverified:
                print('原始录制缺执行后证据；仅使用经核验的训练文件。旧实验须显式 --allow-unverified')
                return
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from dp_cli.decide_train import export_pairs
            pairs = [(r['query'], r['positive'], 1.0)
                     for r in export_pairs(f)
                     if r.get('src') not in ('meta', 'synthetic', 'unsure')
                     and r.get('positive')]
        else:
            print('无训练数据——先 DPX_RECORD_DECIDE=1 录制或 --smoke')
            return
    pairwise = '--pairwise' in sys.argv
    hits = check_leakage(pairs)
    if hits:
        print(f'泄漏拒绝：{len(hits)} 条训练 query 命中 eval 集——'
              f'{hits[:3]}。eval 集数据永不入训练。')
        sys.exit(2)
    if len(pairs) < 10:
        print(f'数据不足（{len(pairs)}<10 对）——继续录制后再训')
        return
    from sentence_transformers import CrossEncoder
    from sentence_transformers.cross_encoder import CrossEncoderTrainer, CrossEncoderTrainingArguments
    from datasets import Dataset
    random.seed(0); random.shuffle(pairs)
    if pairwise:
        # pairwise margin：chosen vs runner-up 差分目标，而非 0/1 硬标签。
        # 复用录制行负例列表做 (q,pos,neg) 三元组，margin 标签 +1——
        # MarginMSELoss 学 score(pos)-score(neg)→1 的差分而非分类。
        rows = json.loads(Path(src).read_text(encoding='utf-8')) if src else []
        flat = []
        for r in rows:
            q, pos = r.get('query', ''), r.get('positive', '')
            if r.get('src') == 'unsure' or not q.strip() or not pos:
                continue
            for n in r.get('negatives', [])[:3]:
                if n and n != pos:
                    flat.append({'sentence1': q, 'sentence2': pos,
                                 'sentence3': n, 'label': 1.0})
        if len(flat) < 10:
            print(f'pairwise 三元组不足（{len(flat)}<10）——换 --pointwise')
            return
        ds = Dataset.from_list(flat)
        from sentence_transformers.cross_encoder.losses import MarginMSELoss
        loss_fn = MarginMSELoss(m_model := CrossEncoder(BASE, num_labels=1))
        m = m_model
        pairs = [(f'{len(flat)} triplets', '', 0)]
    else:
        ds = Dataset.from_list([{'sentence1': q, 'sentence2': c, 'label': l}
                                for q, c, l in pairs])
        loss_fn = None
        m = CrossEncoder(BASE, num_labels=1)
    args = CrossEncoderTrainingArguments(
        output_dir=str(OUT), num_train_epochs=int(__import__('os').environ.get('TRAIN_EPOCHS','2')),
        per_device_train_batch_size=8, learning_rate=2e-5,
        logging_steps=20, save_strategy='no')
    tr = CrossEncoderTrainer(model=m, args=args, train_dataset=ds,
                             loss=loss_fn) if loss_fn else \
        CrossEncoderTrainer(model=m, args=args, train_dataset=ds)
    tr.train()
    m.save_pretrained(str(OUT))
    print(f'trained {len(ds)} {"triplets" if pairwise else "pairs"} → {OUT}')


if __name__ == '__main__':
    main()
