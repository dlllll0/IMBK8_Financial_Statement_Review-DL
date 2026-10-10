# -*- coding: utf-8 -*-
"""
financial_structurer_fixed.py

financial_value_data.csv를 비교 가능한 재무제표 구조로 변환한다.

핵심 수정점
- account_name이 빈 행 제거
- 표구분/항목/단위/섹션명 행 제거
- raw_text에서 CSV 구조를 안전하게 파싱
- value_json이 ["1,2,3"]처럼 깨진 경우 raw_text 우선 사용
- 재무상태표/손익계산서/포괄손익계산서 우선 구조화
- 자본변동표/현금흐름표는 기본 제외

출력
- financial_statement_structured.csv
- financial_statement_compare.csv
- financial_highlight_items.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ALLOWED_TABLES = {
    "재무상태표",
    "손익계산서",
    "포괄손익계산서",
    "현금흐름표",
}

SECTION_WORDS = {
    "자산", "부채", "자본", "부채및자본", "부채와자본",
    "유동자산", "비유동자산", "유동부채", "비유동부채",
    "자본금", "자본잉여금", "이익잉여금", "기타자본항목",
    "매출", "비용", "손익", "수익",
}

DROP_EXACT = {
    "", "표구분", "항목", "단위", "단위원", "단위백만원", "단위천원",
    "재무상태표", "손익계산서", "포괄손익계산서", "현금흐름표", "자본변동표",
}

ACCOUNT_CATEGORY_RULES = [
    ("매출채권", "자산/채권"),
    ("기타채권", "자산/채권"),
    ("재고자산", "자산/재고"),
    ("현금", "유동성"),
    ("단기금융", "유동성"),
    ("차입금", "부채/차입"),
    ("사채", "부채/차입"),
    ("충당부채", "부채/충당"),
    ("매출액", "수익성"),
    ("영업이익", "수익성"),
    ("당기순이익", "수익성"),
    ("손실", "손익위험"),
    ("공정가치", "평가위험"),
]

RISK_DIRECTION = {
    "매출채권": "increase_bad",
    "기타채권": "increase_bad",
    "재고자산": "increase_bad",
    "차입금": "increase_bad",
    "부채": "increase_bad",
    "충당부채": "increase_bad",
    "현금": "decrease_bad",
    "영업이익": "decrease_bad",
    "당기순이익": "decrease_bad",
    "매출액": "decrease_bad",
}


def read_csv_safe(path: Path) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def clean_text(x: Any) -> str:
    if x is None or pd.isna(x):
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_key(x: Any) -> str:
    s = clean_text(x)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\(\)\[\]\{\}\-_/·.,:;]", "", s)
    return s


def parse_number(x: Any) -> Optional[float]:
    s = clean_text(x)
    if not s or s.lower() in {"nan", "none", "null", "[]", "-"}:
        return None

    neg = False
    # (123,456) 음수 처리
    if re.fullmatch(r"\([\d,\.]+\)", s):
        neg = True
        s = s[1:-1]

    # 숫자 안 콤마 제거
    s = s.replace(",", "")
    s = re.sub(r"[^0-9\.\-]", "", s)
    if not s or s in {"-", "."}:
        return None

    # 하이픈이 중간에 있으면 깨진 값으로 판단
    if s.count("-") > 1 or ("-" in s and not s.startswith("-")):
        return None

    try:
        v = float(s)
        return -v if neg and v > 0 else v
    except Exception:
        return None


def split_raw_csv_line(raw_text: Any) -> List[str]:
    raw = clean_text(raw_text)
    if not raw:
        return []
    try:
        return next(csv.reader(StringIO(raw)))
    except Exception:
        return [p.strip() for p in raw.split(",")]


def extract_account_and_values(row: pd.Series) -> tuple[str, List[Optional[float]]]:
    """
    raw_text를 우선 사용한다.
    예: 매출채권및기타채권,610473445710,929245558667,556468921794
    """
    raw_parts = split_raw_csv_line(row.get("raw_text", ""))

    if raw_parts:
        account = clean_text(raw_parts[0])
        value_parts = raw_parts[1:]
        values = [parse_number(v) for v in value_parts]
        values = [v for v in values if v is not None]
        return account, values

    account = clean_text(row.get("account_candidate", ""))
    raw = clean_text(row.get("value_json", ""))
    nums = re.findall(r"\(?-?\d[\d,]*\)?(?:\.\d+)?", raw)
    values = [parse_number(n) for n in nums]
    values = [v for v in values if v is not None]
    return account, values


def is_probably_account(account: str, values: List[Optional[float]], table_name: str) -> bool:
    key = normalize_key(account)
    if not key:
        return False
    if key in DROP_EXACT:
        return False
    if key.startswith("항목제") or re.fullmatch(r"제\d+기.*", key):
        return False
    if "단위" in key or "표구분" in key:
        return False
    if len(values) < 2:
        return False

    # 너무 일반적인 섹션 행은 제거. 단, 유동자산 같은 중간합계는 분석용으로 살릴 수 있으나 일단 RAG 하이라이트 제외 목적이면 제거.
    if key in {normalize_key(w) for w in SECTION_WORDS}:
        return False

    # 계정명이 숫자/기수 헤더에 가까우면 제거
    if re.fullmatch(r"[0-9제기]+", key):
        return False

    return True


def infer_category(account: str) -> str:
    key = normalize_key(account)
    for pat, cat in ACCOUNT_CATEGORY_RULES:
        if normalize_key(pat) in key:
            return cat
    return "기타"


def infer_direction_rule(account: str) -> str:
    key = normalize_key(account)
    for pat, direction in RISK_DIRECTION.items():
        if normalize_key(pat) in key:
            return direction
    return "large_change"


def calc_growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    # 전기값이 0이면 변화율 폭주. 별도 플래그로 처리.
    if abs(previous) < 1:
        return None
    return (current - previous) / abs(previous)


def calc_risk(account: str, current: Optional[float], previous: Optional[float], growth: Optional[float]) -> str:
    if current is None or previous is None:
        return "low"

    abs_g = abs(growth) if growth is not None else None
    direction = infer_direction_rule(account)

    bad_direction = False
    if direction == "increase_bad" and current > previous:
        bad_direction = True
    elif direction == "decrease_bad" and current < previous:
        bad_direction = True
    elif direction == "large_change":
        bad_direction = True

    if abs_g is None:
        # 0 -> 큰 수, 큰 수 -> 0 같은 경우
        if abs(current - previous) >= 100_000_000:
            return "mid"
        return "low"

    if abs_g >= 0.5 and bad_direction:
        return "high"
    if abs_g >= 0.2:
        return "mid"
    return "low"


def format_amount(x: Any) -> str:
    v = parse_number(x)
    if v is None:
        return ""
    return f"{v:,.0f}"


def format_pct(x: Any) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x) * 100:.1f}%"


def make_item_id(account: str) -> str:
    base = normalize_key(account)
    base = re.sub(r"[^가-힣A-Za-z0-9]+", "_", base).strip("_")
    return base[:80] or "item"


def structure_financial_df(df: pd.DataFrame, allowed_tables: Optional[set[str]] = None) -> pd.DataFrame:
    if allowed_tables is None:
        allowed_tables = ALLOWED_TABLES

    rows: List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        table_name = clean_text(row.get("table_name", ""))
        if table_name not in allowed_tables:
            continue

        account, values = extract_account_and_values(row)
        if not is_probably_account(account, values, table_name):
            continue

        out = {
            "job_id": row.get("job_id", ""),
            "period_type": row.get("period_type", ""),
            "file_name": row.get("file_name", ""),
            "industry": row.get("industry", ""),
            "data_type": row.get("data_type", ""),
            "report_type": row.get("report_type", ""),
            "table_name": table_name,
            "row_group": row.get("row_group", ""),
            "account_name": account,
            "account_key": normalize_key(account),
            "raw_text": row.get("raw_text", ""),
            "value_current": values[0] if len(values) > 0 else None,
            "value_previous": values[1] if len(values) > 1 else None,
            "value_two_years_ago": values[2] if len(values) > 2 else None,
            "value_count_parsed": len(values),
        }
        rows.append(out)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result = result.drop_duplicates(
        subset=["job_id", "period_type", "table_name", "account_key"],
        keep="first",
    ).reset_index(drop=True)
    return result


def compare_financial_df(structured_df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for _, row in structured_df.iterrows():
        account = clean_text(row.get("account_name", ""))
        current = parse_number(row.get("value_current"))
        previous = parse_number(row.get("value_previous"))
        two_years = parse_number(row.get("value_two_years_ago"))

        if not account or current is None or previous is None:
            continue

        diff = current - previous
        growth = calc_growth(current, previous)
        diff_prev = previous - two_years if two_years is not None else None
        growth_prev = calc_growth(previous, two_years) if two_years is not None else None
        risk = calc_risk(account, current, previous, growth)

        out = row.to_dict()
        out.update({
            "category": infer_category(account),
            "account_direction_rule": infer_direction_rule(account),
            "current_value": current,
            "previous_value": previous,
            "two_years_ago_value": two_years,
            "diff_current_previous": diff,
            "growth_rate_current_previous": growth,
            "abs_growth_rate_current_previous": abs(growth) if growth is not None else None,
            "diff_previous_two_years_ago": diff_prev,
            "growth_rate_previous_two_years_ago": growth_prev,
            "risk_level": risk,
            "item_id": make_item_id(account),
            "current_value_fmt": format_amount(current),
            "previous_value_fmt": format_amount(previous),
            "diff_fmt": format_amount(diff),
            "growth_rate_fmt": format_pct(growth),
        })
        rows.append(out)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    # 비교 불가/폭주 행 제거: account_name 비어있거나 값이 비정상적으로 큰 행 방어
    result = result[result["account_name"].astype(str).str.strip() != ""]
    result = result[result["current_value"].abs() < 10**18]
    result = result[result["previous_value"].abs() < 10**18]

    risk_order = {"high": 0, "mid": 1, "low": 2}
    result["_risk_sort"] = result["risk_level"].map(risk_order).fillna(9)
    result["_abs_sort"] = result["abs_growth_rate_current_previous"].fillna(0)
    result = result.sort_values(["_risk_sort", "_abs_sort"], ascending=[True, False])
    return result.drop(columns=["_risk_sort", "_abs_sort"]).reset_index(drop=True)


def build_highlight_items(compare_df: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    if compare_df.empty:
        return pd.DataFrame()

    target = compare_df[compare_df["risk_level"].isin(["high", "mid"])].copy()
    if target.empty:
        target = compare_df.copy()
    target = target.head(top_n)

    rows: List[Dict[str, Any]] = []
    for _, row in target.iterrows():
        account = clean_text(row.get("account_name", ""))
        if not account:
            continue
        current = row.get("current_value")
        previous = row.get("previous_value")
        growth = row.get("growth_rate_current_previous")
        direction_word = "증가" if (growth is not None and not pd.isna(growth) and growth > 0) else "감소"
        sentence = (
            f"{account} 항목은 전기 {format_amount(previous)}원에서 "
            f"당기 {format_amount(current)}원으로 {format_pct(abs(growth)) if growth is not None and not pd.isna(growth) else '변동'} {direction_word}했습니다."
        )
        rows.append({
            "job_id": row.get("job_id", ""),
            "period_type": row.get("period_type", ""),
            "file_name": row.get("file_name", ""),
            "industry": row.get("industry", ""),
            "item_id": row.get("item_id", make_item_id(account)),
            "item_type": "financial",
            "title": account,
            "category": row.get("category", "기타"),
            "risk_level": row.get("risk_level", "low"),
            "table_name": row.get("table_name", ""),
            "highlight_account": account,
            "highlight_value": format_amount(current),
            "highlight_text_candidates": json.dumps([account, format_amount(current), format_amount(previous)], ensure_ascii=False),
            "current_value": current,
            "previous_value": previous,
            "diff_current_previous": row.get("diff_current_previous"),
            "growth_rate_current_previous": growth,
            "current_value_fmt": format_amount(current),
            "previous_value_fmt": format_amount(previous),
            "diff_fmt": format_amount(row.get("diff_current_previous")),
            "growth_rate_fmt": format_pct(growth),
            "change_sentence": sentence,
        })
    return pd.DataFrame(rows)


def run_financial_structurer(input_path: Path, output_dir: Path, top_n: int = 30) -> Dict[str, pd.DataFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_df = read_csv_safe(input_path)
    print(f"[LOAD] {input_path} {raw_df.shape}")

    structured_df = structure_financial_df(raw_df)
    compare_df = compare_financial_df(structured_df)
    highlight_df = build_highlight_items(compare_df, top_n=top_n)

    structured_path = output_dir / "financial_statement_structured.csv"
    compare_path = output_dir / "financial_statement_compare.csv"
    highlight_path = output_dir / "financial_highlight_items.csv"

    structured_df.to_csv(structured_path, index=False, encoding="utf-8-sig")
    compare_df.to_csv(compare_path, index=False, encoding="utf-8-sig")
    highlight_df.to_csv(highlight_path, index=False, encoding="utf-8-sig")

    print("=" * 80)
    print("[FINANCIAL STRUCTURER DONE]")
    print("structured:", structured_df.shape, structured_path)
    print("compare:", compare_df.shape, compare_path)
    print("highlight:", highlight_df.shape, highlight_path)
    if not compare_df.empty:
        print(compare_df[["table_name", "account_name", "current_value_fmt", "previous_value_fmt", "growth_rate_fmt", "risk_level"]].head(10).to_string(index=False))
    print("=" * 80)

    return {"structured": structured_df, "compare": compare_df, "highlight": highlight_df}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()

    run_financial_structurer(Path(args.input), Path(args.output_dir), top_n=args.top_n)


# =========================================================
# app.py 연동용 wrapper
# =========================================================

def structure_financial_csv(
    input_csv,
    output_dir,
    save_output=True,
):
    return run_financial_structurer(
        input_path=Path(input_csv),
        output_dir=Path(output_dir),
    )
    
if __name__ == "__main__":
    main()
