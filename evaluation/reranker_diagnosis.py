"""evaluation/reranker_diagnosis —— 细粒度 reranker 诊断（方案 5.11「不以回答流畅判断好坏」）。

回答两个问题：
1. reranker 到底有没有改变候选池排序？（改变次数 / 位置位移量）
2. 改变是否有利？（gold 排名前进=改善 / 后退=恶化 / 进出 top5）

并复用同一套 51 道定性 query 比较 Recall@5 / MRR / nDCG@5 / 延迟。

关键边界（须牢记）：reranker 只能在 BM25 候选池（top_n）内部重排，候选池之外的
gold 它永远碰不到。所以 reranker 的收益上限 =「把候选池内排后面的 gold 提到 top5」，
救不了「BM25 根本没召回进候选池」的 gold。

用法：
    python -m evaluation.reranker_diagnosis                 # 全量 51 题，候选池 top_n=20
    python -m evaluation.reranker_diagnosis --candidates 50  # 测不同候选池大小
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

from evaluation.eval_retrieval import (
    gold_chunk_ids,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evaluation.run_eval_retrieval import COMPANY_CFG, build_chunks
from retrieval.reranker import DashScopeReranker
from retrieval.retriever import Retriever, tokenize


@dataclass
class QueryDiagnosis:
    qid: str
    query: str
    company: str
    n_gold: int                 # 该 query 的 gold chunk 总数
    n_gold_in_pool: int         # 进入 BM25 候选池的 gold 数
    gold_ids: set[str] = field(repr=False)
    bm25_top5: list[str] = field(default_factory=list)
    rerank_top5: list[str] = field(default_factory=list)
    # 候选池内每个 gold 的 (chunk_id, bm25_rank, rerank_rank)，rank 从 1 起
    gold_ranks: list[tuple[str, int, int]] = field(default_factory=list)
    changed: bool = False        # reranker 是否改变了候选池顺序
    moved_positions: int = 0     # 候选池内所有 chunk 的排名位移总量 Σ|Δrank|
    latency_bm25_ms: float = 0.0
    latency_rerank_ms: float = 0.0


def diagnose_company(cfg: dict, reranker, top_n: int = 20, k: int = 5) -> list[QueryDiagnosis]:
    company = cfg["company"]
    chunks = build_chunks(cfg)
    retriever = Retriever(chunks, reranker=reranker)  # embedder=None → 纯 BM25 + reranker
    rows: list[QueryDiagnosis] = []

    for q in cfg["queries"]:
        gold_ids = gold_chunk_ids(chunks, q.gold_phrases)
        indices = retriever._filtered_indices(company, cfg["year"], None)

        t0 = time.perf_counter()
        bm25_ranked = retriever._bm25_ranked(tokenize(q.query), indices, top_n)
        latency_bm25 = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        reranked = retriever._rerank(q.query, bm25_ranked) if len(bm25_ranked) > 1 else None
        latency_rerank = (time.perf_counter() - t0) * 1000.0
        if reranked is None:
            reranked = bm25_ranked  # reranker 失败/无 key → 降级为 BM25 原顺序

        bm25_rank = {idx: i + 1 for i, idx in enumerate(bm25_ranked)}
        rerank_rank = {idx: i + 1 for i, idx in enumerate(reranked)}

        moved = sum(abs(rerank_rank[i] - bm25_rank[i]) for i in bm25_ranked)
        changed = reranked != bm25_ranked

        gold_ranks: list[tuple[str, int, int]] = []
        n_in_pool = 0
        for idx in bm25_ranked:
            cid = chunks[idx].chunk_id
            if cid in gold_ids:
                n_in_pool += 1
                gold_ranks.append((cid, bm25_rank[idx], rerank_rank[idx]))

        rows.append(
            QueryDiagnosis(
                qid=q.qid,
                query=q.query,
                company=company,
                n_gold=len(gold_ids),
                n_gold_in_pool=n_in_pool,
                gold_ids=gold_ids,
                bm25_top5=[chunks[i].chunk_id for i in bm25_ranked[:k]],
                rerank_top5=[chunks[i].chunk_id for i in reranked[:k]],
                gold_ranks=gold_ranks,
                changed=changed,
                moved_positions=moved,
                latency_bm25_ms=latency_bm25,
                latency_rerank_ms=latency_rerank,
            )
        )
    return rows


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(rows: list[QueryDiagnosis], k: int) -> dict:
    """跨 query 聚合：顺序改变 / 改善恶化 / 进出 top5 / 四指标对比。"""
    n = len(rows)
    n_changed = sum(1 for r in rows if r.changed)
    total_moved = sum(r.moved_positions for r in rows)

    # gold 排名前进=改善 / 后退=恶化（候选池内）
    improved = worsened = unchanged = 0
    # 进出 top5
    in_top5 = out_top5 = 0
    # gold 在候选池但排在 top_k 之后（reranker 有机会救的）
    pool_after_k = 0
    pool_after_k_rescued = 0  # 被 reranker 提进 top_k 的

    for r in rows:
        for cid, br, rr in r.gold_ranks:
            if rr < br:
                improved += 1
            elif rr > br:
                worsened += 1
            else:
                unchanged += 1
            if br <= k and rr > k:
                out_top5 += 1
            elif br > k and rr <= k:
                in_top5 += 1
            if br > k:
                pool_after_k += 1
                if rr <= k:
                    pool_after_k_rescued += 1

    # 四指标（BM25 vs BM25+reranker）
    recall_b = [_q_recall(r, k) for r in rows]
    recall_r = [_q_recall(r, k, use_rerank=True) for r in rows]

    return {
        "n": n,
        "n_changed": n_changed,
        "changed_ratio": n_changed / n if n else 0.0,
        "total_moved": total_moved,
        "avg_moved": total_moved / n if n else 0.0,
        "improved": improved,
        "worsened": worsened,
        "unchanged": unchanged,
        "in_top5": in_top5,
        "out_top5": out_top5,
        "pool_after_k": pool_after_k,
        "pool_after_k_rescued": pool_after_k_rescued,
        "recall_bm25": _avg([recall_at_k(r.bm25_top5, r.gold_ids, k) for r in rows]),
        "recall_rerank": _avg([recall_at_k(r.rerank_top5, r.gold_ids, k) for r in rows]),
        "precision_bm25": _avg([precision_at_k(r.bm25_top5, r.gold_ids, k) for r in rows]),
        "precision_rerank": _avg([precision_at_k(r.rerank_top5, r.gold_ids, k) for r in rows]),
        "mrr_bm25": _avg([mrr(r.bm25_top5, r.gold_ids) for r in rows]),
        "mrr_rerank": _avg([mrr(r.rerank_top5, r.gold_ids) for r in rows]),
        "ndcg_bm25": _avg([ndcg_at_k(r.bm25_top5, r.gold_ids, k) for r in rows]),
        "ndcg_rerank": _avg([ndcg_at_k(r.rerank_top5, r.gold_ids, k) for r in rows]),
        "latency_bm25": _avg([r.latency_bm25_ms for r in rows]),
        "latency_rerank": _avg([r.latency_rerank_ms for r in rows]),
    }


def _q_recall(r: QueryDiagnosis, k: int, use_rerank: bool = False) -> float:
    top = r.rerank_top5 if use_rerank else r.bm25_top5
    return recall_at_k(top, r.gold_ids, k)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", choices=["bull", "changxin", "all"], default="all")
    ap.add_argument("--candidates", type=int, default=20, help="BM25 候选池大小 top_n")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()

    companies = {
        "bull": ["公牛集团"],
        "changxin": ["长鑫科技"],
        "all": ["公牛集团", "长鑫科技"],
    }[args.company]

    reranker = DashScopeReranker()

    # 生效性探测：key 失效 / API 失败会让 rank 返回 None，导致全程静默降级为纯 BM25。
    # 先探测一次，避免把「reranker 没跑」误读成「reranker 零收益」。
    _probe = reranker.rank("经营", ["预收货款下降所致", "理财产品到期赎回较多"])
    if _probe is None:
        print("⚠️ reranker 未生效：rank 探测返回 None（DASHSCOPE_API_KEY 失效或 API 失败）。")
        print("   以下「+reranker」实为纯 BM25 降级结果，无法据此评估 reranker 真实效果；")
        print("   请先更换有效 key 后重跑本脚本。\n")

    all_rows: list[QueryDiagnosis] = []
    for name in companies:
        cfg = dict(COMPANY_CFG[name], company=name)
        rows = diagnose_company(cfg, reranker, top_n=args.candidates, k=args.k)
        all_rows.extend(rows)
        print(f"\n{'='*80}")
        print(f"【{name}】候选池 top_n={args.candidates}  k={args.k}  查询={len(rows)}")
        print(f"{'='*80}")
        print(f"{'qid':<4} {'gold':>4} {'池内':>4} {'改变':>4} {'位移':>4} "
              f"{'改善':>4} {'恶化':>4} | BM25排名→rerank排名（gold）")
        for r in rows:
            gr = " ".join(f"{br}→{rr}" for _, br, rr in r.gold_ranks) or "-"
            print(f"{r.qid:<4} {r.n_gold:>4} {r.n_gold_in_pool:>4} "
                  f"{'✓' if r.changed else '·':>4} {r.moved_positions:>4} "
                  f"{_improved(r):>4} {_worsened(r):>4} | {gr}")

    s = summarize(all_rows, args.k)
    k = args.k
    print(f"\n{'='*80}")
    print(f"汇总（{s['n']} 个查询，候选池 top_n={args.candidates}，k={args.k}）")
    print(f"{'='*80}")
    print(f"顺序改变：{s['n_changed']}/{s['n']} 个查询被 reranker 改变（{s['changed_ratio']:.0%}）")
    print(f"  位置位移总量={s['total_moved']}，平均每查询位移={s['avg_moved']:.1f}")
    print(f"gold 排名：改善={s['improved']}  恶化={s['worsened']}  不变={s['unchanged']}")
    print(f"进出 top5：进入={s['in_top5']}  掉出={s['out_top5']}")
    print(f"候选池内排在 top{k} 之后：{s['pool_after_k']} 个，被 reranker 救进 top{k}：{s['pool_after_k_rescued']} 个")
    print(f"\n{'指标':<16} {'纯 BM25':>10} {'+reranker':>10} {'差值':>10}")
    print(f"{'Recall@'+str(args.k):<16} {s['recall_bm25']:>10.3f} {s['recall_rerank']:>10.3f} {s['recall_rerank']-s['recall_bm25']:>+10.3f}")
    print(f"{'Precision@'+str(args.k):<16} {s['precision_bm25']:>10.3f} {s['precision_rerank']:>10.3f} {s['precision_rerank']-s['precision_bm25']:>+10.3f}")
    print(f"{'MRR':<16} {s['mrr_bm25']:>10.3f} {s['mrr_rerank']:>10.3f} {s['mrr_rerank']-s['mrr_bm25']:>+10.3f}")
    print(f"{'nDCG@'+str(args.k):<16} {s['ndcg_bm25']:>10.3f} {s['ndcg_rerank']:>10.3f} {s['ndcg_rerank']-s['ndcg_bm25']:>+10.3f}")
    print(f"{'延迟(ms)':<16} {s['latency_bm25']:>10.1f} {s['latency_bm25']+s['latency_rerank']:>10.1f} {s['latency_rerank']:>+10.1f}")

    # 候选池外漏召的 gold（reranker 救不了，须靠扩大候选池/改召回）
    outside = [r for r in all_rows if r.n_gold > r.n_gold_in_pool]
    if outside:
        print(f"\n⚠️ gold 完全不在候选池（reranker 永远救不了，共 {sum(r.n_gold - r.n_gold_in_pool for r in outside)} 个）：")
        for r in outside:
            print(f"  {r.qid}: {r.query}（gold={r.n_gold}，池内={r.n_gold_in_pool}）")


def _improved(r: QueryDiagnosis) -> int:
    return sum(1 for _, br, rr in r.gold_ranks if rr < br)


def _worsened(r: QueryDiagnosis) -> int:
    return sum(1 for _, br, rr in r.gold_ranks if rr > br)


if __name__ == "__main__":
    main()
