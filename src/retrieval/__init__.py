"""检索层：知识库片段检索入口。

`get_retriever()` 返回进程内单例（语料为静态 YAML，加载一次即可）。
"""

from __future__ import annotations

from pathlib import Path

from src.retrieval.bm25 import BM25Retriever

ROOT = Path(__file__).resolve().parent.parent.parent

_retrievers: dict[str, BM25Retriever] = {}


def get_retriever(
    scenario: str | None = None,
    corpus_path: str | Path | None = None,
) -> BM25Retriever:
    """按场景获取检索器单例。

    优先级：显式 corpus_path > scenario 名 > 自动发现 `data/knowledge/*/corpus.yaml`。
    自动发现使框架侧不必写死任何场景名。
    """
    if corpus_path:
        path = Path(corpus_path)
    elif scenario:
        path = ROOT / "data" / "knowledge" / scenario / "corpus.yaml"
    else:
        found = sorted((ROOT / "data" / "knowledge").glob("*/corpus.yaml"))
        if not found:
            raise FileNotFoundError("data/knowledge 下没有任何 corpus.yaml")
        path = found[0]

    key = str(path)
    if key not in _retrievers:
        _retrievers[key] = BM25Retriever(path)
    return _retrievers[key]
