# -*- coding: utf-8 -*-
"""
src/financial_comparator.py

재무제표 단순 비교 + 룰기반 분석 생성기.

핵심 전제:
- gemini_financial_tables_all.csv 파일 하나 안에 이미 당기/전기 금액 컬럼이 존재한다.
- 따라서 t 파일과 t_minus_1 파일을 merge하지 않는다.
- 한 CSV 내부의 당기 금액 vs 전기 금액을 비교한다.

입력 후보:
outputs/jobs/{job_id}/t/gemini_financial_tables_all.csv
outputs/jobs/{job_id}/t/financial/gemini_financial_tables_all.csv
outputs/jobs/{job_id}/gemini_financial_tables_all.csv

출력:
outputs/jobs/{job_id}/financial_comparison/
  - financial_comparison_table.csv
  - financial_comparison_rules.csv
  - financial_comparison_viewer.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from src.financial_rule_engine import (
    apply_financial_rules,
    build_cross_variable_rules,
)
from src.highlight_builder import build_financial_row_highlights

from src.financial_rag_builder import build_financial_rag_context, build_financial_rag_items
from src.financial_llm_builder import build_financial_llm_payload
# =========================================================
# 1. CSV 안전 로드
# =========================================================

def read_csv_safely(path: Path) -> pd.DataFrame:
    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            df = pd.read_csv(path, encoding=enc)
            df.columns = df.columns.astype(str).str.strip()
            return df.fillna("")
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"[CSV READ ERROR] {path} / {e}")
            return pd.DataFrame()

    return pd.DataFrame()


# =========================================================
# 2. 금액 정제
# =========================================================

def clean_financial_value(series: pd.Series) -> pd.Series:
    """
    재무제표 금액 문자열을 숫자로 변환.
    예:
    '1,234' -> 1234
    '(1,234)' -> -1234
    '-' -> 0
    '￦ 1,234' -> 1234
    """
    s = series.astype(str).str.strip()

    has_brackets = s.str.startswith("(") & s.str.endswith(")")

    s = s.str.replace(r"[\(\)]", "", regex=True)
    s = s.str.replace(",", "", regex=False)
    s = s.str.replace("￦", "", regex=False)
    s = s.str.replace("원", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.replace("-", "0")
    s = s.replace("", "0")

    numeric = pd.to_numeric(s, errors="coerce").fillna(0.0)
    numeric = np.where(has_brackets, -numeric, numeric)

    return pd.Series(numeric)


def format_growth_rate(val: float) -> str:
    if pd.isna(val) or val == 0:
        return "0.0%"
    if val > 0:
        return f"+{val:.1f}%"
    return f"{val:.1f}%"


# =========================================================
# 3. 컬럼 탐색
# =========================================================

def find_subject_col(df: pd.DataFrame) -> str:
    candidates = ["과목", "계정과목", "항목", "account_name", "account", "line_item", "item_name"]

    for col in candidates:
        if col in df.columns:
            return col

    raise ValueError(f"과목/계정과목 컬럼을 찾지 못했습니다. columns={list(df.columns)}")


def find_table_name_col(df: pd.DataFrame) -> str | None:
    candidates = ["table_name", "statement_type", "표명", "재무제표명", "sheet_name"]

    for col in candidates:
        if col in df.columns:
            return col

    return None

def extract_period_number(col_name: str) -> int | None:
    col_name = str(col_name)

    m = re.search(r"제\s*(\d+)", col_name)

    if not m:
        return None

    return int(m.group(1))

def find_current_previous_cols(df: pd.DataFrame) -> tuple[str, str]:
    """
    재무제표 컬럼 중 당기/전기 컬럼을 찾는다.

    핵심:
    - 과목 컬럼 제외
    - col_ 계열 제외
    - 숫자 데이터가 많이 들어있는 제N기 컬럼만 후보
    - 가장 최근 기수 2개를 당기/전기로 사용
    """

    exclude_cols = {"과목", "계정과목", "항목", "table_name", "statement_type"}

    candidate_cols = []

    for col in df.columns:
        col_str = str(col).strip()

        if col_str in exclude_cols:
            continue

        if col_str.startswith("col_"):
            continue

        # 제11기, 제10기, 제9기, 제 35(당) 기 등
        if not re.search(r"제\s*\d+", col_str):
            continue

        s = df[col].astype(str).str.strip()

        numeric_like_ratio = (
            s.str.replace(",", "", regex=False)
             .str.replace("-", "0", regex=False)
             .str.replace("￦", "", regex=False)
             .str.match(r"^\(?\d+(\.\d+)?\)?$")
             .mean()
        )

        candidate_cols.append({
            "col": col,
            "numeric_like_ratio": numeric_like_ratio,
            "period_num": extract_period_number(col_str),
        })

    candidate_cols = [
        x for x in candidate_cols
        if x["numeric_like_ratio"] >= 0.3 and x["period_num"] is not None
    ]

    if len(candidate_cols) < 2:
        raise ValueError(f"당기/전기 후보 컬럼이 부족합니다. columns={list(df.columns)}")

    # 기수가 큰 순서: 제11기, 제10기, 제9기
    candidate_cols = sorted(
        candidate_cols,
        key=lambda x: x["period_num"],
        reverse=True
    )

    current_col = candidate_cols[0]["col"]
    previous_col = candidate_cols[1]["col"]

    return current_col, previous_col


# =========================================================
# 4. 입력 파일 탐색
# =========================================================

def find_financial_csv(job_dir: str | Path) -> Path:
    job_dir = Path(job_dir)

    # 무조건 t 폴더 안에서만 찾는다.
    t_dir = job_dir / "t"

    candidates = [
        t_dir / "gemini_financial_tables_all.csv",
        t_dir / "financial_gemini_structured" / "gemini_financial_tables_all.csv",
        t_dir / "financial" / "gemini_financial_tables_all.csv",
        t_dir / "financial_structured" / "gemini_financial_tables_all.csv",
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path

    matches = list(t_dir.rglob("gemini_financial_tables_all.csv"))
    matches = [p for p in matches if p.exists() and p.stat().st_size > 0]

    if matches:
        return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    raise FileNotFoundError(f"t 폴더에서 gemini_financial_tables_all.csv를 찾지 못했습니다: {t_dir}")
# =========================================================
# 5. 계정 정제/분류
# =========================================================

def is_noise_account(account: str) -> bool:
    account = str(account or "").strip()

    if not account:
        return True

    noise_patterns = [
        r"^nan$",
        r"회사명",
        r"희석주당",
        r"기본주당",
        r"주당",
        r"주석",
        r"^\(?주\d+\)?$",
        r"^[IVXLC]+\.$",
        r"^\d+\.$",
        r"^\(?단위",
        r"감사의견",
        r"회계감사인",
    ]

    return any(re.search(p, account, flags=re.I) for p in noise_patterns)


def classify_account_category(account: str) -> str:
    account = str(account)

    rules = [
        ("자산", ["자산", "현금", "매출채권", "미수금", "재고", "유형자산", "무형자산", "투자자산"]),
        ("부채", ["부채", "차입금", "사채", "매입채무", "충당부채", "미지급", "리스부채"]),
        ("자본", ["자본", "자본금", "이익잉여금", "자본잉여금", "기타자본"]),
        ("매출", ["매출", "수익", "영업수익"]),
        ("비용", ["비용", "원가", "판매비", "관리비", "감가상각", "손상차손"]),
        ("손익", ["이익", "손실", "당기순", "영업이익", "법인세"]),
        ("현금흐름", ["현금흐름", "영업활동", "투자활동", "재무활동"]),
    ]

    for category, keywords in rules:
        if any(k in account for k in keywords):
            return category

    return "기타"


# =========================================================
# 6. fallback 룰 분석
# financial_rule_engine.py가 없을 때만 사용
# =========================================================

def assign_fallback_rule_analysis(row: pd.Series) -> Dict[str, object]:
    account = str(row["계정과목"])
    category = row["계정분류"]
    change = float(row["증감액_원본"])
    rate = float(row["증감률_원본"])

    abs_rate = abs(rate)

    risk_category = "일반 계정 변동"
    risk_tags = []
    priority_score = 0
    analysis_method = []
    check_points = []
    benchmark_hint = "필요 시 동일업종 평균 재무비율과 비교"
    interpretation = []

    if abs_rate >= 150:
        risk_category = "급격한 계정 변동"
        risk_tags.append("증감률 150% 이상")
        priority_score += 4
        analysis_method.append("전기 대비 급격한 변동 원인 확인")
    elif abs_rate >= 100:
        risk_category = "큰 폭의 계정 변동"
        risk_tags.append("증감률 100% 이상")
        priority_score += 3
        analysis_method.append("전기 대비 큰 폭의 변동 원인 확인")
    elif abs_rate >= 50:
        risk_category = "유의미한 계정 변동"
        risk_tags.append("증감률 50% 이상")
        priority_score += 2
        analysis_method.append("전기 대비 유의미한 변동 원인 확인")
    elif abs_rate >= 20:
        risk_tags.append("증감률 20% 이상")
        priority_score += 1
        analysis_method.append("전기 대비 변동 원인 확인")

    if category == "자산" and change < 0:
        risk_tags.append("자산 감소")
        analysis_method.append("자산 감소가 처분, 손상, 현금 유출과 관련되는지 확인")
        check_points.extend(["자산 처분 여부", "손상차손 여부", "현금흐름 변화"])

    if category == "부채" and change > 0:
        risk_category = "부채 확대 리스크"
        risk_tags.append("부채 증가")
        priority_score += 2
        analysis_method.append("부채 증가가 차입금 증가인지 영업채무 증가인지 확인")
        check_points.extend(["차입금 증가 여부", "이자비용 증가 여부", "만기구조"])

    if any(k in account for k in ["매출채권", "미수금"]) and change > 0:
        risk_category = "채권 회수 리스크"
        risk_tags.append("채권 증가")
        priority_score += 2
        analysis_method.append("매출채권 증가율과 매출 증가율 비교")
        check_points.extend(["대손충당금", "장기 미회수채권", "특수관계자 채권"])

    if "재고" in account and change > 0:
        risk_category = "재고자산 평가 리스크"
        risk_tags.append("재고 증가")
        priority_score += 1
        analysis_method.append("재고자산 증가율과 매출/매출원가 증가율 비교")
        check_points.extend(["재고평가충당금", "장기체화재고", "매출원가율"])

    if any(k in account for k in ["차입금", "사채", "리스부채"]) and change > 0:
        risk_category = "재무부담 증가 리스크"
        risk_tags.append("차입부채 증가")
        priority_score += 3
        analysis_method.append("차입금 증가 원인, 이자비용, 만기구조 확인")
        check_points.extend(["단기차입금", "장기차입금", "이자비용", "담보제공"])

    if any(k in account for k in ["손상", "감액", "평가손실"]):
        risk_category = "자산손상 리스크"
        risk_tags.append("손상/감액")
        priority_score += 4
        analysis_method.append("손상차손 발생 자산과 회수가능액 산정 근거 확인")
        check_points.extend(["손상 대상 자산", "회수가능액", "평가방법"])

    if priority_score >= 7:
        priority_grade = "상"
    elif priority_score >= 4:
        priority_grade = "중"
    else:
        priority_grade = "하"

    if not risk_tags:
        risk_tags.append("일반 변동")

    if not analysis_method:
        analysis_method.append("전기 대비 금액 및 증감률 단순 비교")

    if not check_points:
        check_points.extend(["관련 주석 확인", "일회성 거래 여부", "전기 대비 변동 원인"])

    direction = "증가" if change > 0 else "감소" if change < 0 else "변동 없음"

    interpretation.append(
        f"{account} 계정은 전기 대비 {direction}했습니다. "
        f"증감률은 {rate:.1f}%이며, {risk_category} 관점에서 검토할 수 있습니다."
    )

    return {
        "risk_category": risk_category,
        "risk_tags": ", ".join(dict.fromkeys(risk_tags)),
        "priority_score": priority_score,
        "priority_grade": priority_grade,
        "analysis_method": " / ".join(dict.fromkeys(analysis_method)),
        "check_points": " / ".join(dict.fromkeys(check_points)),
        "benchmark_hint": benchmark_hint,
        "interpretation": " ".join(interpretation),
    }


# =========================================================
# 7. LLM/RAG 프롬프트 블록
# =========================================================

def build_financial_llm_prompt_blocks(rule_df: pd.DataFrame, top_n: int = 10) -> List[Dict[str, object]]:
    blocks = []

    target_df = rule_df.head(top_n).copy()

    for _, row in target_df.iterrows():
        blocks.append({
            "source": "financial_statement_comparison",
            "table_name": row.get("table_name", ""),
            "category": row.get("계정분류", ""),
            "account_name": row.get("계정과목", ""),
            "priority_score": int(row.get("priority_score", 0)),
            "priority_grade": row.get("priority_grade", "하"),
            "analysis_method": row.get("analysis_method", ""),
            "check_points": row.get("check_points", ""),
            "benchmark_hint": row.get("benchmark_hint", ""),
            "result": (
                f"{row.get('계정과목', '')} 계정은 전기 {row.get('전기 계정', 0):,}백만원에서 "
                f"당기 {row.get('당기 계정', 0):,}백만원으로 변동했습니다. "
                f"증감액은 {row.get('증감액', 0):,}백만원이며, 증감률은 {row.get('증감률', '0.0%')}입니다."
            ),
            "llm_instruction": (
                "너는 재무제표 분석 담당자다. "
                "아래 계정과목의 전기 대비 당기 변동을 바탕으로 "
                "재무적 리스크, 주석에서 확인해야 할 사항, 추가 검토 포인트를 설명하라."
            ),
            "risk_category": row.get("risk_category", ""),
            "risk_tags": row.get("risk_tags", ""),
            "interpretation": row.get("interpretation", ""),
        })

    return blocks


# =========================================================
# 8. 단일 CSV 기반 비교 생성
# =========================================================

def generate_financial_comparison_from_single_csv(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    unit_divisor: int = 1,
) -> Dict[str, object]:

    input_path = Path(input_path)
    df = read_csv_safely(input_path)

    if df.empty:
        raise ValueError(f"재무제표 CSV가 비어있습니다: {input_path}")

    subject_col = find_subject_col(df)
    table_col = find_table_name_col(df)
    current_col, previous_col = find_current_previous_cols(df)

    print("[FINANCIAL COMPARATOR] input:", input_path)
    print("[FINANCIAL COMPARATOR] subject_col:", subject_col)
    print("[FINANCIAL COMPARATOR] table_col:", table_col)
    print("[FINANCIAL COMPARATOR] current_col:", current_col)
    print("[FINANCIAL COMPARATOR] previous_col:", previous_col)

    use_cols = [subject_col, current_col, previous_col]
    if table_col:
        use_cols = [table_col] + use_cols

    work = df[use_cols].copy()

    if table_col:
        work = work.rename(columns={table_col: "table_name"})
    else:
        work["table_name"] = "재무제표"

    work = work.rename(columns={
        subject_col: "계정과목",
        current_col: "당기_원본",
        previous_col: "전기_원본",
    })

    work["table_name"] = work["table_name"].astype(str).str.strip()
    work["계정과목"] = work["계정과목"].astype(str).str.strip()

    # 노이즈 제거
    work = work[~work["계정과목"].apply(is_noise_account)].copy()

    work["당기_원본"] = pd.to_numeric(
        work["당기_원본"].astype(str).str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0)

    work["전기_원본"] = pd.to_numeric(
        work["전기_원본"].astype(str).str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0)

    # 둘 다 0이면 제거
    work = work[
        ~((work["당기_원본"] == 0) & (work["전기_원본"] == 0))
    ].copy()

    work["증감액_원본"] = work["당기_원본"] - work["전기_원본"]

    work["증감률_원본"] = np.where(
        work["전기_원본"] != 0,
        work["증감액_원본"] / work["전기_원본"] * 100,
        np.where(work["당기_원본"] != 0, 100.0, 0.0)
    )

    work["계정분류"] = work["계정과목"].apply(classify_account_category)

    work["전기_원본"] = pd.to_numeric(work["전기_원본"], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0)
    work["당기_원본"] = pd.to_numeric(work["당기_원본"], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0)
    work["증감액_원본"] = pd.to_numeric(work["증감액_원본"], errors="coerce").replace([np.inf, -np.inf], 0).fillna(0)

    work["전기 계정"] = (work["전기_원본"] / unit_divisor).round(0).fillna(0).astype(int)
    work["당기 계정"] = (work["당기_원본"] / unit_divisor).round(0).fillna(0).astype(int)
    work["증감액"] = (work["증감액_원본"] / unit_divisor).round(0).fillna(0).astype(int)
    work["증감률_원본"] = pd.Series(work["증감률_원본"]).replace([np.inf, -np.inf], 0).fillna(0)
    work["증감률"] = work["증감률_원본"].apply(format_growth_rate)

    print("[DEBUG WORK TARGET ROWS BEFORE RULE]")
    print(
        work[
            work["계정과목"].astype(str).isin([
                "대손상각비",
                "유가증권평가손실",
                "재고자산평가손실",
            ])
        ][[
            "table_name",
            "계정과목",
            "전기_원본",
            "당기_원본",
            "전기 계정",
            "당기 계정",
            "증감률",
        ]].to_string(index=False)
    )

    # # RAG/LLM 검토 후보는 재무상태표/손익계산서 중심으로 제한
    # # 현금흐름표는 간접법 조정항목이 많아 동일 계정명이 중복/전파될 수 있으므로 제외
    # work = work[
    #     ~work["table_name"].astype(str).str.contains("현금흐름", na=False)
    # ].copy()

    # 룰엔진 적용
    try:
        from src.financial_rule_engine import (
            apply_financial_rules,
            build_cross_variable_rules,
        )

        rule_results = work.apply(apply_financial_rules, axis=1, result_type="expand")
        work = pd.concat([work, rule_results], axis=1)
        cross_variable_analysis = build_cross_variable_rules(work)

    except Exception as e:
        print("[WARN] financial_rule_engine 적용 실패, fallback 사용:", e)
        rule_results = work.apply(assign_fallback_rule_analysis, axis=1, result_type="expand")
        work = pd.concat([work, rule_results], axis=1)
        cross_variable_analysis = []

    table_df = work[[
        "table_name",
        "계정과목",
        "전기 계정",
        "당기 계정",
        "증감률",
    ]].copy()

    # 룰엔진 버전에 따라 새 컬럼이 없을 수 있으므로 안전하게 보정
    optional_rule_cols = {
        "account_category": "",
        "formula_name": "",
        "formula": "",
        "bank_data_required": "",
        "formula_result": "",
    }
    for col, default_value in optional_rule_cols.items():
        if col not in work.columns:
            work[col] = default_value

    rule_df = work[[
        "table_name",
        "계정과목",
        "계정분류",
        "전기 계정",
        "당기 계정",
        "증감액",
        "증감률",
        "risk_category",
        "risk_tags",
        "priority_score",
        "priority_grade",
        "account_category",
        "analysis_method",
        "formula_name",
        "formula",
        "bank_data_required",
        "formula_result",
        "check_points",
        "benchmark_hint",
        "interpretation",
    ]].copy()

    rule_df = rule_df.sort_values(
        by=["priority_score", "증감액"],
        ascending=[False, False]
    ).reset_index(drop=True)



    # =========================================================
    # OUTPUT DIR
    # =========================================================

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================
    # RAG / LLM PATH
    # =========================================================

    rag_output_path = (
        output_dir /
        "financial_rag_context.json"
    )

    financial_rag_items_path = (
        output_dir /
        "financial_rag_items.json"
    )

    llm_payload_path = (
        output_dir /
        "financial_llm_payload.json"
    )

    # =========================================================
    # RAG 생성
    # =========================================================

    build_financial_rag_context(
        rule_df=rule_df,
        cross_rules=cross_variable_analysis,
        output_path=rag_output_path,
    )

    financial_rag_items = build_financial_rag_items(
        rule_df=rule_df,
        output_path=financial_rag_items_path,
        max_items=5,
    )

    # =========================================================
    # LLM Payload 생성
    # =========================================================

    build_financial_llm_payload(
        rag_context_path=rag_output_path,
        output_path=llm_payload_path,
    )

    # =========================================================
    # Viewer Data
    # =========================================================

    viewer_data = {

        "unit": "백만원",

        "source_file": str(input_path),

        "subject_col": subject_col,
        "table_col": table_col,

        "current_col": current_col,
        "previous_col": previous_col,

        "summary": {
            "total_accounts": int(len(rule_df)),
            "high_priority_count": int((rule_df["priority_grade"] == "상").sum()),
            "middle_priority_count": int((rule_df["priority_grade"] == "중").sum()),
            "low_priority_count": int((rule_df["priority_grade"] == "하").sum()),
        },

        "comparison_table":
            table_df.to_dict(orient="records"),

        "rule_analysis":
            rule_df.to_dict(orient="records"),

        "cross_variable_analysis":
            cross_variable_analysis,

        "highlight_items":
            build_financial_row_highlights(rule_df),

        "llm_prompt_blocks":
            build_financial_llm_prompt_blocks(rule_df),

        "rag_context_file":
            str(rag_output_path),

        "financial_rag_items_file":
            str(financial_rag_items_path),

        "financial_rag_items":
            financial_rag_items,

        "llm_payload_file":
            str(llm_payload_path),
    }

    # =========================================================
    # 저장
    # =========================================================

    table_df.to_csv(
        output_dir / "financial_comparison_table.csv",
        index=False,
        encoding="utf-8-sig",
    )

    rule_df.to_csv(
        output_dir / "financial_comparison_rules.csv",
        index=False,
        encoding="utf-8-sig",
    )

    with open(output_dir / "financial_comparison_viewer.json", "w", encoding="utf-8") as f:
        json.dump(viewer_data, f, ensure_ascii=False, indent=2)

    print(f"[FINANCIAL COMPARISON] saved -> {output_dir}")

    return {
        "table_df": table_df,
        "rule_df": rule_df,
        "viewer_data": viewer_data,
    }


# =========================================================
# 9. app.py에서 호출하는 진입점
# =========================================================

def run_financial_comparator(job_dir: str | Path, unit_divisor: int = 1) -> Dict[str, object]:
    job_dir = Path(job_dir)

    input_file = find_financial_csv(job_dir)
    output_dir = job_dir / "financial_comparison"

    result = generate_financial_comparison_from_single_csv(
        input_path=input_file,
        output_dir=output_dir,
        unit_divisor=unit_divisor,
    )

    return {
        "status": "success",
        "rows": len(result["rule_df"]),
        "input_file": str(input_file),
        "output_dir": str(output_dir),
        "summary": result["viewer_data"]["summary"],
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent

    TEST_JOB_DIR = (
        BASE_DIR
        / "outputs"
        / "jobs"
        / "job_da46b3fc"
    )

    result = run_financial_comparator(TEST_JOB_DIR)
    print(result)
