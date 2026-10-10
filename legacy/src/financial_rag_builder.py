# =========================================================
# src/financial_rag_builder.py
# =========================================================

# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from src.bank_financial_formula_engine import add_bank_derived_metrics

def build_financial_rag_context(
    rule_df: pd.DataFrame,
    cross_rules: list,
    output_path,
):

    results = []

    # =====================================================
    # 단일 변수 룰
    # =====================================================

    if rule_df is not None and not rule_df.empty:

        for _, row in rule_df.iterrows():

            results.append({

                "source_type":
                    "financial_statement",

                "analysis_type":
                    "single_variable",

                "account_name":
                    row.get("계정과목", ""),

                "table_name":
                    row.get("table_name", ""),

                "previous_value":
                    row.get("전기 계정", 0),

                "current_value":
                    row.get("당기 계정", 0),

                "change_amount":
                    row.get("증감액", 0),

                "growth_rate":
                    row.get("증감률", ""),

                "risk_category":
                    row.get("risk_category", ""),

                "risk_tags":
                    row.get("risk_tags", ""),

                "priority_grade":
                    row.get("priority_grade", ""),

                "priority_score":
                    row.get("priority_score", 0),

                "analysis_method":
                    row.get("analysis_method", ""),

                "formula":
                    row.get("formula", ""),

                "check_points":
                    row.get("check_points", ""),

                "benchmark_hint":
                    row.get("benchmark_hint", ""),

                "interpretation":
                    row.get("interpretation", ""),

                "llm_context":
                    row.get("llm_context", ""),
            })

    # =====================================================
    # 교차 변수 룰
    # =====================================================

    for item in cross_rules:

        results.append({

            "source_type":
                "financial_statement",

            "analysis_type":
                "cross_variable",

            "risk_category":
                item.get("risk_category", ""),

            "priority_grade":
                item.get("priority_grade", ""),

            "priority_score":
                item.get("priority_score", 0),

            "analysis_method":
                item.get("analysis_method", ""),

            "formula":
                item.get("formula", ""),

            "result":
                item.get("result", ""),

            "check_points":
                item.get("check_points", []),

            "llm_context":
                item.get("llm_context", ""),
        })

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            results,
            f,
            ensure_ascii=False,
            indent=2
        )

    return results

def _safe_number(x, default=0):
    try:
        if x is None:
            return default
        if hasattr(x, "item"):
            x = x.item()
        return float(x)
    except Exception:
        return default



# =====================================================
# OVERRIDE: item/value 중심 Financial RAG item 생성기
# =====================================================
def _fmt_num_for_rag(x):
    try:
        if x is None:
            return ""
        if hasattr(x, "item"):
            x = x.item()
        return f"{float(x):,.0f}"
    except Exception:
        return str(x or "")


def _to_float_for_rag(x, default=0.0):
    try:
        if x is None:
            return default
        if hasattr(x, "item"):
            x = x.item()
        return float(str(x).replace(",", "").replace("%", ""))
    except Exception:
        return default


def _direction_from_change(change):
    v = _to_float_for_rag(change, 0.0)
    if v > 0:
        return "증가"
    if v < 0:
        return "감소"
    return "변동 없음"


def _build_financial_formula_result(row):
    try:
        from pathlib import Path
        from src.bank_financial_formula_engine import (
            read_csv_safely,
            load_financial_analysis_rules,
            load_industry_mapping,
            normalize_bank_industry,
            run_bank_analysis_for_item,
            BANK_DATA_PATH,
        )

        BASE_DIR = Path(__file__).resolve().parent.parent

        FINANCIAL_ANALYSIS_RULES_PATH = BASE_DIR / "data" / "financial_analysis_rules.csv"
        INDUSTRY_MAPPING_PATH = None

        bank_df = read_csv_safely(BANK_DATA_PATH)
        from src.bank_financial_formula_engine import add_bank_derived_metrics
        bank_df = add_bank_derived_metrics(bank_df)
        rules_df = load_financial_analysis_rules(FINANCIAL_ANALYSIS_RULES_PATH)
        industry_mapping = load_industry_mapping(INDUSTRY_MAPPING_PATH)

        bank_df = normalize_bank_industry(bank_df, industry_mapping)

        mapped_industry = str(
            row.get("mapped_industry")
            or row.get("industry")
            or row.get("업종")
            or "제조업"
        ).strip()

        item = {
            "account_name": str(row.get("계정과목", "")),
            "account_category": str(row.get("account_category", "")),
            "risk_category": str(row.get("risk_category", "")),
            "current_value": row.get("당기 계정", 0),
            "previous_value": row.get("전기 계정", 0),
        }
        bank_df = add_bank_derived_metrics(bank_df)
        return run_bank_analysis_for_item(
            item=item,
            bank_df=bank_df,
            rules_df=rules_df,
            mapped_industry=mapped_industry,
        )

    except Exception as e:
        return {
            "status": "formula_engine_unavailable",
            "error": str(e),
            "formula_name": row.get("formula_name", ""),
            "formula": row.get("formula", ""),
            "company_current_value": row.get("당기 계정", 0),
            "company_previous_value": row.get("전기 계정", 0),
        }


def build_financial_rag_items(
    rule_df: pd.DataFrame,
    output_path,
    max_items: int | None = 5,
):
    """
    financial_comparison_rules.csv 성격의 rule_df를
    '카테고리'가 아니라 '계정과목 + 전기/당기 수치 + 증감률 + 분석방법/수식' 단위로 재구성한다.

    핵심:
    - 같은 risk_category라도 account_name/current_value/previous_value/growth_rate가 다르면 item이 달라진다.
    - LLM에는 이 item 단위 근거가 들어가므로 유가증권평가손실/재고자산평가손실이 같은 문장으로 뭉개지지 않는다.
    - 은행데이터는 아직 없으므로 formula_result.status='pending_bank_data' 자리만 남긴다.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if rule_df is None or rule_df.empty:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []

    work = rule_df.copy().fillna("")

    for col in ["priority_score", "증감액", "전기 계정", "당기 계정"]:
        if col not in work.columns:
            work[col] = 0

    work["_priority_sort"] = pd.to_numeric(work["priority_score"], errors="coerce").fillna(0)
    work["_abs_change_sort"] = pd.to_numeric(work["증감액"], errors="coerce").fillna(0).abs()

    # 계정과목/표 중복은 유지하되, 완전히 같은 row는 제거
    dedup_cols = [c for c in ["table_name", "계정과목", "전기 계정", "당기 계정", "증감률"] if c in work.columns]
    if dedup_cols:
        work = work.drop_duplicates(subset=dedup_cols, keep="first")

    work = work.sort_values(
        by=["_priority_sort", "_abs_change_sort"],
        ascending=[False, False],
    )

    if max_items is not None:
        work = work.head(max_items)

    items = []

    for rank, (_, row) in enumerate(work.iterrows(), start=1):
        table_name = str(row.get("table_name", "")).strip()
        account_name = str(row.get("계정과목", "")).strip()
        if not account_name:
            continue

        prev_val = row.get("전기 계정", 0)
        curr_val = row.get("당기 계정", 0)
        change_val = row.get("증감액", 0)
        growth_rate = str(row.get("증감률", "")).strip()
        direction = _direction_from_change(change_val)

        risk_category = str(row.get("risk_category", "")).strip()
        account_category = str(row.get("account_category", row.get("계정분류", ""))).strip()
        analysis_method = str(row.get("analysis_method", "")).strip()
        formula_name = str(row.get("formula_name", "")).strip()
        formula = str(row.get("formula", "")).strip()
        check_points = str(row.get("check_points", "")).strip()
        benchmark_hint = str(row.get("benchmark_hint", "")).strip()
        interpretation = str(row.get("interpretation", "")).strip()

        formula_result = _build_financial_formula_result(row)
        external_analysis = formula_result
        headline_seed = (
            f"{account_name}은 전기 {_fmt_num_for_rag(prev_val)}백만원에서 "
            f"당기 {_fmt_num_for_rag(curr_val)}백만원으로 {direction}했으며, "
            f"증감액은 {_fmt_num_for_rag(change_val)}백만원, 증감률은 {growth_rate}입니다."
        )

        rag_context = "\n".join([
            "[Financial RAG ITEM]",
            f"- 표: {table_name}",
            f"- 계정과목: {account_name}",
            f"- 계정 카테고리: {account_category}",
            f"- 리스크 카테고리: {risk_category}",
            f"- 전기 금액: {_fmt_num_for_rag(prev_val)}백만원",
            f"- 당기 금액: {_fmt_num_for_rag(curr_val)}백만원",
            f"- 증감액: {_fmt_num_for_rag(change_val)}백만원",
            f"- 증감률: {growth_rate}",
            f"- 변동방향: {direction}",
            f"- 분석방법: {analysis_method}",
            f"- 적용 수식: {formula_name}: {formula}",
            f"- 은행데이터 수식 결과: {json.dumps(formula_result, ensure_ascii=False)}",
            f"- 검토 포인트: {check_points}",
            f"- 벤치마크 힌트: {benchmark_hint}",
            f"- 기존 해석: {interpretation}",
        ])

        item = {
            "item_id": f"financial_{table_name}_{account_name}_{rank}",
            "summary_type": "financial",
            "title": account_name,
            "table_name": table_name,
            "account_name": account_name,
            "account_category": account_category,
            "risk_category": risk_category,
            "risk_level": row.get("priority_grade", ""),
            "priority_score": _to_float_for_rag(row.get("priority_score", 0)),
            "priority_level": row.get("priority_grade", ""),
            "financial_evidence": {
                "account_name": account_name,
                "table_name": table_name,
                "previous_value": prev_val,
                "current_value": curr_val,
                "change_amount": change_val,
                "growth_rate": growth_rate,
                "previous_value_display": _fmt_num_for_rag(prev_val),
                "current_value_display": _fmt_num_for_rag(curr_val),
                "change_amount_display": _fmt_num_for_rag(change_val),
                "growth_rate_display": growth_rate,
                "direction": direction,
            },
            "previous_value": prev_val,
            "current_value": curr_val,
            "change_amount": change_val,
            "growth_rate": growth_rate,
            "analysis_method": analysis_method,
            "formula_name": formula_name,
            "formula": formula,
            "bank_data_required": row.get("bank_data_required", ""),
            "formula_result": formula_result,
            "external_analysis": external_analysis,
            "check_points": check_points,
            "benchmark_hint": benchmark_hint,
            "interpretation": interpretation,
            "headline_seed": headline_seed,
            "rag_context": rag_context,
            "highlight_targets": [
                account_name,
                _fmt_num_for_rag(curr_val),
                _fmt_num_for_rag(prev_val),
                _fmt_num_for_rag(change_val),
                growth_rate,
            ],
            "_source_row": row.to_dict(),
        }
        items.append(item)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2, default=str)

    # CSV도 같이 저장
    try:
        csv_path = output_path.with_suffix(".csv")
        flat = []
        for item in items:
            fe = item.get("financial_evidence", {})
            flat.append({
                "item_id": item.get("item_id", ""),
                "table_name": item.get("table_name", ""),
                "account_name": item.get("account_name", ""),
                "risk_category": item.get("risk_category", ""),
                "priority_score": item.get("priority_score", 0),
                "priority_level": item.get("priority_level", ""),
                "previous_value": fe.get("previous_value", ""),
                "current_value": fe.get("current_value", ""),
                "change_amount": fe.get("change_amount", ""),
                "growth_rate": fe.get("growth_rate", ""),
                "analysis_method": item.get("analysis_method", ""),
                "formula_name": item.get("formula_name", ""),
                "formula": item.get("formula", ""),
                "formula_result_status": (item.get("formula_result", {}) or {}).get("status", ""),
            })
        pd.DataFrame(flat).to_csv(csv_path, index=False, encoding="utf-8-sig")
    except Exception:
        pass

    return items
