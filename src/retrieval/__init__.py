"""检索层：知识库片段检索入口。

`get_retriever()` 返回进程内单例（语料为静态 YAML，加载一次即可）。
"""

from __future__ import annotations

from pathlib import Path

from src.retrieval.bm25 import BM25Retriever

ROOT = Path(__file__).resolve().parent.parent.parent

_retrievers: dict[str, BM25Retriever] = {}


def get_retriever(scenario: str = "receivables", corpus_path: str | Path | None = None) -> BM25Retriever:
    """按场景获取检索器单例。"""
    path = Path(corpus_path) if corpus_path else ROOT / "data" / "knowledge" / scenario / "corpus.yaml"
    key = str(path)
    if key not in _retrievers:
        _retrievers[key] = BM25Retriever(path)
    return _retrievers[key]
