# -*- coding: utf-8 -*-
"""
src/financial_rag_item_builder.py

financial_comparison_rules.csv를 읽어 계정과목/수치/증감률/수식 중심의
Financial RAG item을 재생성하는 독립 실행용 wrapper.

보통은 financial_comparator.py 내부에서 자동 생성되지만,
디버깅이나 재생성이 필요할 때 이 파일을 직접 호출할 수 있다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from src.financial_rag_builder import build_financial_rag_items
from src.bank_financial_formula_engine import run_bank_analysis_for_item


def _read_csv(path: Path) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(path, encoding=enc).fillna("")
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path).fillna("")


def run_financial_rag_item_builder(
    job_dir: str | Path,
    max_items: int | None = 5,
) -> Dict[str, Any]:
    job_dir = Path(job_dir)
    input_path = job_dir / "financial_comparison" / "financial_comparison_rules.csv"
    output_path = job_dir / "financial_comparison" / "financial_rag_items.json"

    if not input_path.exists():
        raise FileNotFoundError(f"financial_comparison_rules.csv 없음: {input_path}")

    rule_df = _read_csv(input_path)

    items = build_financial_rag_items(
        rule_df=rule_df,
        output_path=output_path,
        max_items=max_items,
    )

    print(f"[FINANCIAL RAG ITEM BUILDER DONE] {output_path} count={len(items)}")

    return {
        "status": "success",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "count": len(items),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs"
    print("Use run_financial_rag_item_builder(job_dir)")
