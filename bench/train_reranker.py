#!/usr/bin/env python3
"""reranker 微调管线——CrossEncoder.fit，输入 decide_train.jsonl 导出的 pos/neg 对。

用法：python bench/train_reranker.py [train.jsonl] [--smoke]
无数据/空数据安全退出；--smoke 用 42 用例构造伪训练集验证管线能跑。
"""
import json, sys, random
from pathlib import Path

OUT = Path.home() / '.dp_cli' / 'decide_model'
BASE = 'cross-encoder/ms-marco-MiniLM-L6-v2'


def load_pairs(path):
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    pairs = []
    for r in rows:
        q, pos = r['query'], r['positive']
        pairs.append((q, pos, 1.0))
        for neg in r.get('negatives', []):
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


def main():
    smoke = '--smoke' in sys.argv
    src = next((a for a in sys.argv[1:] if not a.startswith('--')), None)
    if smoke:
        pairs = smoke_pairs()
    elif src and Path(src).exists():
        pairs = load_pairs(src)
    else:
        f = Path.home() / '.dp_cli/sessions/decide_train.jsonl'
        if f.exists():
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from dp_cli.decide_train import export_pairs
            pairs = load_pairs('/dev/stdin') if False else [
                (r['query'], r['positive'], 1.0)
                for r in export_pairs(f)]
        else:
            print('无训练数据——先 DPX_RECORD_DECIDE=1 录制或 --smoke')
            return
    if len(pairs) < 10:
        print(f'数据不足（{len(pairs)}<10 对）——继续录制后再训')
        return
    from sentence_transformers import CrossEncoder
    from sentence_transformers.cross_encoder import CrossEncoderTrainer, CrossEncoderTrainingArguments
    from datasets import Dataset
    random.seed(0); random.shuffle(pairs)
    ds = Dataset.from_list([{'sentence1': q, 'sentence2': c, 'label': l}
                            for q, c, l in pairs])
    m = CrossEncoder(BASE, num_labels=1)
    args = CrossEncoderTrainingArguments(
        output_dir=str(OUT), num_train_epochs=2,
        per_device_train_batch_size=8, learning_rate=2e-5,
        logging_steps=20, save_strategy='no')
    tr = CrossEncoderTrainer(model=m, args=args, train_dataset=ds)
    tr.train()
    m.save_pretrained(str(OUT))
    print(f'trained {len(pairs)} pairs → {OUT}')


if __name__ == '__main__':
    main()
