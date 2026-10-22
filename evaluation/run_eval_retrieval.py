"""run_eval_retrieval —— 定性检索评测 CLI（BM25 基线 + 可选 reranker）。

用法：
    python -m evaluation.run_eval_retrieval                    # 两家公司，纯 BM25
    python -m evaluation.run_eval_retrieval --company bull     # 只跑公牛
    python -m evaluation.run_eval_retrieval --reranker         # 接入 qwen3-rerank（需 DASHSCOPE_API_KEY）

输出：逐条打印 gold 锚点命中数（验证标注质量）+ Recall@5 / Precision@5 / MRR，
最后给出平均指标与「锚点未命中」清单（未命中 = 标注锚点与当前切块文本不一致，须修正）。
"""

from __future__ import annotations

import argparse
import time

from domain import StatementType
from evaluation.eval_retrieval import gold_chunk_ids, mrr, precision_at_k, recall_at_k
from evaluation.qualitative_queries import (
    ALL_QUALITATIVE,
    BULL_QUALITATIVE,
    CHANGXIN_QUALITATIVE,
    QualitativeQuery,
)
from ingestion.pdf_parser import parse_pdf
from retrieval.chunker import chunk_pages
from retrieval.embedding import BGEEmbedder
from retrieval.retriever import Retriever
from retrieval.reranker import DashScopeReranker


# 每个公司对应的 PDF 路径、解析参数、切块 report_id
COMPANY_CFG = {
    "公牛集团": {
        "pdf": "storage/公牛集团-2024年报.pdf",
        "year": 2024,
        "report_id": "bull-2024",
        "queries": BULL_QUALITATIVE,
    },
    "长鑫科技": {
        "pdf": "storage/长鑫科技-招股书.pdf",
        "year": 2025,
        "report_id": "changxin-2025",
        "queries": CHANGXIN_QUALITATIVE,
    },
}


def build_chunks(cfg: dict):
    """解析 PDF（取全文 pages）→ 结构切块。"""
    parsed = parse_pdf(cfg["pdf"], cfg["company"], cfg["year"], StatementType.BALANCE_SHEET)
    return chunk_pages(parsed.pages, cfg["company"], cfg["year"], cfg["report_id"])


def eval_company(cfg: dict, embedder, reranker, k: int = 5) -> dict:
    company = cfg["company"]
    chunks = build_chunks(cfg)
    retriever = Retriever(chunks, embedder=embedder, reranker=reranker)
    rows = []

    for q in cfg["queries"]:
        gold_ids = gold_chunk_ids(chunks, q.gold_phrases)
        t0 = time.perf_counter()
        ranked = [
            c.chunk_id
            for c in retriever.retrieve(
                q.query, company_name=company, fiscal_year=cfg["year"], top_k=k
            )
        ]
        latency = (time.perf_counter() - t0) * 1000.0
        rows.append(
            {
                "qid": q.qid,
                "query": q.query,
                "n_gold": len(gold_ids),
                "recall": recall_at_k(ranked, gold_ids, k),
                "precision": precision_at_k(ranked, gold_ids, k),
                "mrr": mrr(ranked, gold_ids),
                "latency_ms": latency,
            }
        )
    return {"company": company, "chunks": len(chunks), "rows": rows}


def _avg(rows: list[dict], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", choices=["bull", "changxin", "all"], default="all")
    ap.add_argument("--embedder", action="store_true", help="接入 BGE 语义召回（本地模型，默认纯 BM25）")
    ap.add_argument("--reranker", action="store_true", help="接入 qwen3-rerank（默认纯 BM25）")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()

    companies = {
        "bull": ["公牛集团"],
        "changxin": ["长鑫科技"],
        "all": ["公牛集团", "长鑫科技"],
    }[args.company]

    # 评测脚本显式构造 reranker（不走生产开关 build_reranker），--reranker 即强制启用
    reranker = DashScopeReranker() if args.reranker else None
    embedder = BGEEmbedder() if args.embedder else None

    all_rows: list[dict] = []
    for name in companies:
        cfg = dict(COMPANY_CFG[name], company=name)
        res = eval_company(cfg, embedder, reranker, k=args.k)
        rows = res["rows"]
        all_rows.extend(rows)
        print(f"\n{'='*72}")
        print(f"【{name}】chunk={res['chunks']}  查询={len(rows)}  k={args.k}"
              f"  reranker={'on' if reranker else 'off'}")
        print(f"{'='*72}")
        for r in rows:
            flag = "  ⚠️锚点未命中" if r["n_gold"] == 0 else ""
            print(f"{r['qid']} gold={r['n_gold']:>2}  R@k={r['recall']:.3f}  "
                  f"P@k={r['precision']:.3f}  MRR={r['mrr']:.3f}  {r['latency_ms']:6.1f}ms"
                  f"  | {r['query']}{flag}")

    # 汇总
    print(f"\n{'='*72}")
    print(f"汇总（{len(all_rows)} 个查询，k={args.k}，reranker={'on' if reranker else 'off'}）")
    print(f"{'='*72}")
    print(f"平均 Recall@{args.k}  = {_avg(all_rows, 'recall'):.3f}")
    print(f"平均 Precision@{args.k} = {_avg(all_rows, 'precision'):.3f}")
    print(f"平均 MRR              = {_avg(all_rows, 'mrr'):.3f}")
    print(f"平均 Latency          = {_avg(all_rows, 'latency_ms'):.1f} ms")

    miss = [r for r in all_rows if r["n_gold"] == 0]
    if miss:
        print(f"\n⚠️ 锚点未命中（{len(miss)} 个，须修正 gold_phrases）：")
        for r in miss:
            print(f"  {r['qid']}: {r['query']}")
    else:
        print("\n✅ 所有 gold 锚点均命中（标注质量 OK）")


if __name__ == "__main__":
    main()
