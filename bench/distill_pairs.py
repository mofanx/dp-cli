#!/usr/bin/env python3
"""蒸馏对生成——bge-reranker 教师对 (goal,候选) 打软分，
输出 MarginMSELoss 格式：{query, positive, negative:[top5难负], label:[分数]}。
硬负例选择=教师分最高的 top-5 错例（天然 hard negative mining）。"""
import json, sys
from pathlib import Path

def main(src='bench/seed_train_v8.json', out='bench/distill_v8.json', topk=5):
    rows = json.load(open(src))
    from sentence_transformers import CrossEncoder
    teacher = CrossEncoder('BAAI/bge-reranker-base', max_length=512)
    pairs = []
    for r in rows:
        cands = [r['positive']] + r['negatives']
        scores = teacher.predict([[r['query'], c] for c in cands])
        s_pos, s_negs = float(scores[0]), [float(s) for s in scores[1:]]
        # hard-neg top-k——教师分最高的错例信息量最大
        order = sorted(range(len(s_negs)), key=lambda i: -s_negs[i])[:topk]
        negs = [r['negatives'][i] for i in order]
        labs = [s_pos] + [s_negs[i] for i in order]
        pairs.append({'query': r['query'], 'positive': r['positive'],
                      'negative': negs, 'label': labs})
    json.dump(pairs, open(out, 'w'), ensure_ascii=False)
    print(f'{len(pairs)} rows×{topk}neg → {out}')

if __name__ == '__main__':
    main(*sys.argv[1:])
