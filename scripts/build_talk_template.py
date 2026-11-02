"""由 talk_templates.yaml 生成 Excel 话术模板（config 中 synthesis.talk_template 指向的产物）。

YAML 是唯一真源（可 diff、可评审）；xlsx 为生成物，不入库（.gitignore）。
用法：python scripts/build_talk_template.py  或  make templates
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "schemas" / "receivables" / "talk_templates.yaml"
DST = ROOT / "data" / "schemas" / "receivables" / "话术模板.xlsx"

COLUMNS = [
    "id", "module", "speaker", "persona", "content",
    "option", "requires", "next", "weight", "max_repeat", "set",
]


def main() -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        print("缺少 openpyxl：python -m pip install openpyxl")
        sys.exit(1)

    payload = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    rows = payload["templates"]

    wb = Workbook()
    ws = wb.active
    ws.title = "话术模板"
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row.get(col) for col in COLUMNS])
    for col_idx, col in enumerate(COLUMNS, start=1):
        width = max(len(str(col)) + 2, 14)
        if col == "content":
            width = 60
        ws.column_dimensions[chr(64 + col_idx)].width = width

    DST.parent.mkdir(parents=True, exist_ok=True)
    wb.save(DST)
    print(f"已生成 {DST}（{len(rows)} 条话术）")


if __name__ == "__main__":
    main()
