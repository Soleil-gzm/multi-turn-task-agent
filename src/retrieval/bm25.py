"""BM25 检索器（MVP 轻量实现，jieba 分词，无额外依赖）。

语料：data/knowledge/<scenario>/corpus.yaml，片段级检索。
接口协议化：后续可换向量 + RRF，调用方只依赖 search() 签名。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import jieba
import yaml

# jieba 词典缓存默认写到系统临时目录（可能在其他盘），改为随项目走。
# jieba 0.42.1 的缓存位置由 Tokenizer 实例（jieba.dt）的 cache_file / tmp_dir 控制，
# 无 set_cache_file 顶层 API，直接设置属性即可，须在首次分词前生效。
_CACHE_DIR = Path(__file__).resolve().parents[2] / "output" / "jieba_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
jieba.dt.tmp_dir = str(_CACHE_DIR)
jieba.dt.cache_file = str(_CACHE_DIR / "jieba.cache")

K1 = 1.5
B = 0.75


def _tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


class BM25Retriever:
    """对 YAML 语料做片段级 BM25 检索。"""

    def __init__(self, corpus_path: str | Path) -> None:
        path = Path(corpus_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.fragments: list[dict[str, Any]] = payload["fragments"]
        self._docs = [
            _tokenize(f"{f['title']} {f['text']}") for f in self.fragments
        ]
        self._doc_len = [len(d) for d in self._docs]
        n = len(self._docs)
        self._avgdl = sum(self._doc_len) / n if n else 0.0

        df: dict[str, int] = {}
        for doc in self._docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        self._idf = {
            t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()
        }

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """返回按分数降序的片段列表，含 score 字段。"""
        terms = _tokenize(query)
        if not terms or not self._docs:
            return []
        scored: list[tuple[float, int]] = []
        for i, doc in enumerate(self._docs):
            tf: dict[str, int] = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            score = 0.0
            dl = self._doc_len[i] or 1
            for term in terms:
                if term not in tf:
                    continue
                idf = self._idf.get(term, 0.0)
                freq = tf[term]
                score += idf * (freq * (K1 + 1)) / (freq + K1 * (1 - B + B * dl / self._avgdl))
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [
            {**self.fragments[i], "score": round(score, 4)}
            for score, i in scored[:top_k]
        ]
