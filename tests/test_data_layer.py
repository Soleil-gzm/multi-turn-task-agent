"""W2 数据层测试：话术表与 policy 知识库。

覆盖三件事：
  1. 话术模板 schema 完整性与占位符三方一致（模板 ↔ case 字段 ↔ 工具/policy 字段）
  2. 知识库语料完整性（唯一 id、必填字段、内部政策标注虚构）
  3. 检索冒烟（BM25 命中预期片段）与 query_policy 检索接线
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.retrieval import get_retriever
from src.tools.receivables.tools import query_policy

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_PATH = ROOT / "data" / "schemas" / "receivables" / "talk_templates.yaml"
CORPUS_PATH = ROOT / "data" / "knowledge" / "receivables" / "corpus.yaml"

TEMPLATE_FIELDS = {
    "id", "module", "speaker", "persona", "content",
    "option", "requires", "next", "weight", "max_repeat", "set",
}
MODULES = {
    "opening", "identify", "inform", "clarify", "negotiate",
    "confirm", "escalate", "close", "insert",
}
PERSONAS = {"cooperative", "delay", "tough", "misleading", "policy_probe"}

# 占位符三方一致白名单：账款实体字段（↔ case init_state ↔ 工具返回）
# + 承诺字段 + policy 派生字段
ALLOWED_PLACEHOLDERS = {
    "contract_no", "invoice_no", "due_date", "amount", "overdue_days", "status",
    "pay_date",
    "max_installments", "max_extension_days", "max_waiver_percent",
}
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

TOPICS = {
    "internal_policy", "payment_terms", "collection_conduct",
    "contract_law", "reconciliation",
}


@pytest.fixture(scope="module")
def templates() -> list[dict]:
    return yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]


@pytest.fixture(scope="module")
def fragments() -> list[dict]:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))["fragments"]


# ---------------------------------------------------------------------------
# 话术模板
# ---------------------------------------------------------------------------

class TestTalkTemplates:
    def test_fields_complete(self, templates):
        for row in templates:
            assert set(row.keys()) == TEMPLATE_FIELDS, f"{row.get('id')} 字段不齐"

    def test_unique_ids(self, templates):
        ids = [r["id"] for r in templates]
        assert len(ids) == len(set(ids)), "话术 id 重复"

    def test_module_and_speaker_valid(self, templates):
        for row in templates:
            assert row["module"] in MODULES, row["id"]
            assert row["speaker"] in ("agent", "customer"), row["id"]

    def test_persona_only_for_customer(self, templates):
        for row in templates:
            if row["speaker"] == "customer":
                assert row["persona"] in PERSONAS, row["id"]
            else:
                assert row["persona"] is None, row["id"]

    def test_placeholders_in_whitelist(self, templates):
        """占位符三方一致：模板中出现的占位符必须落在白名单内。"""
        for row in templates:
            found = set(_PLACEHOLDER_RE.findall(row["content"]))
            unknown = found - ALLOWED_PLACEHOLDERS
            assert not unknown, f"{row['id']} 出现未登记占位符: {unknown}"

    def test_placeholders_fillable_from_case_and_policy(self, templates):
        """白名单字段确实能由 case init_state / policy / 承诺字段填充（三方一致的另一半）。"""
        import json

        case = json.loads(
            (ROOT / "eval" / "datasets" / "receivables" / "cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        case_fields = set(case["init_state"].keys())
        policy_fields = {"max_installments", "max_extension_days", "max_waiver_ratio"}
        derived = {"pay_date", "max_waiver_percent"}  # 承诺字段 + policy 展示派生
        fillable = case_fields | policy_fields | derived
        assert ALLOWED_PLACEHOLDERS <= fillable, (
            f"白名单中有无法填充的占位符: {ALLOWED_PLACEHOLDERS - fillable}"
        )

    def test_option_groups_have_variants(self, templates):
        groups: dict[str, int] = {}
        for row in templates:
            if row["option"]:
                groups[row["option"]] = groups.get(row["option"], 0) + 1
        for group, count in groups.items():
            assert count >= 2, f"option 组 {group} 只有 {count} 条，起不到多选项作用"

    def test_module_coverage(self, templates):
        """8 个业务模块都至少有 agent 话术。"""
        covered = {r["module"] for r in templates if r["speaker"] == "agent"}
        assert covered == MODULES, f"缺模块: {MODULES - covered}"


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------

class TestKnowledgeCorpus:
    def test_unique_ids_and_required_fields(self, fragments):
        ids = set()
        for f in fragments:
            for key in ("id", "topic", "source", "title", "text"):
                assert key in f and f[key], f"片段缺字段 {key}: {f.get('id')}"
            assert f["id"] not in ids, f"片段 id 重复: {f['id']}"
            ids.add(f["id"])

    def test_topic_valid(self, fragments):
        for f in fragments:
            assert f["topic"] in TOPICS, f["id"]

    def test_internal_policy_marked_fictional(self, fragments):
        for f in fragments:
            if f["topic"] == "internal_policy":
                assert "虚构" in f["source"], f["id"]

    def test_minimum_size(self, fragments):
        assert len(fragments) >= 20, f"语料只有 {len(fragments)} 条，不足以支撑检索演示"


# ---------------------------------------------------------------------------
# 检索与 query_policy 接线
# ---------------------------------------------------------------------------

class TestRetrieval:
    def test_installment_query_hits_internal_policy(self):
        hits = get_retriever().search("分期最多可以分几期", top_k=3)
        assert hits, "无命中"
        assert hits[0]["id"] == "POL-INT-001", f"首命中为 {hits[0]['id']}"

    def test_threat_query_hits_collection_conduct(self):
        hits = get_retriever().search("能不能威胁客户说要起诉他", top_k=3)
        topics = {h["topic"] for h in hits}
        assert "collection_conduct" in topics, f"命中主题: {topics}"

    def test_reconciliation_query(self):
        hits = get_retriever().search("账面金额和客户说的不一致怎么办", top_k=3)
        assert any(h["topic"] == "reconciliation" for h in hits)

    def test_empty_query(self):
        assert get_retriever().search("", top_k=3) == []


class TestQueryPolicyWithKnowledge:
    def test_no_query_backward_compatible(self):
        result = query_policy()
        assert result["ok"] is True
        assert result["data"]["max_installments"] == 3
        assert "fragments" not in result["data"]

    def test_with_query_returns_fragments(self):
        result = query_policy(query="延期最多多少天")
        assert result["ok"] is True
        fragments = result["data"]["fragments"]
        assert fragments, "带 query 应返回知识库片段"
        assert result["meta"]["source"] == "policy_kb"
        assert any(f["id"] == "POL-INT-002" for f in fragments)

    def test_fragments_pass_through_sanitizer(self):
        """知识库片段同样走工具返回隔离（间接注入防护）。"""
        from src.guardrails.guardrails import sanitize_tool_output

        result = query_policy(query="减免上限是多少")
        sanitized = sanitize_tool_output(result["data"])
        assert isinstance(sanitized, str)
        assert "[tool_data]" in sanitized or "tool_data" in sanitized
