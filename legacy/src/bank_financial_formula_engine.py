# -*- coding: utf-8 -*-
"""
src/bank_financial_formula_engine.py

은행데이터와 재무제표 RAG item을 연결하는 외부 비교 엔진.

목표:
- 재무제표 계정과목(account_name)과 카테고리(category)를 기준으로
  사용할 은행데이터 변수(bank_column)를 찾는다.
- 업종_대분류 기준 동일업종 median/mean을 계산한다.
- 재무제표 current_value와 동일업종 median을 단순 비교한다.
- 결과를 financial_rag_items.json의 external_analysis에 넣을 수 있게 반환한다.

주의:
- 실제 은행데이터 파일 경로는 프로젝트 환경에 맞게 app.py 또는 rag_item_builder에서 전달.
- 회사 단위 식별자가 없으므로 현재는 "재무제표 값 vs 동일업종 은행데이터 분포" 비교만 수행.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import math
import pandas as pd

BANK_DATA_PATH = str(Path(__file__).resolve().parents[1] / "private-input" / "bank-data.csv")
FINANCIAL_UNIT_SCALE = 1  # 재무제표 값: 백만원 → 원
BANK_UNIT_SCALE = 1_000_000       # 은행데이터 값: 백만원 → 원
RATIO_DISPLAY_CAP = 1000

def normalize_money_value(value, scale: int = 1_000_000):
    v = safe_float(value)
    if v is None:
        return None
    return v * scale

# "업종별 표본 안정성을 고려하여 대분류 기준으로 단순화하였다."
DEFAULT_INDUSTRY_MAP = {
    "제조업": "제조업",
    "도매 및 소매업": "유통/소매",
    "숙박 및 음식점업": "숙박/서비스",
    "금융 및 보험업": "금융",
    "정보통신업": "IT/정보통신",
    "건설업": "건설",
}



def safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        s = str(x).replace(",", "").replace("%", "").strip()
        if s == "" or s.lower() == "nan":
            return None
        return float(s)
    except Exception:
        return None


def read_csv_safely(path: str | Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            df = pd.read_csv(path, encoding=enc)
            df.columns = df.columns.astype(str).str.strip()
            return df
        except UnicodeDecodeError:
            continue
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def load_industry_mapping(path: str | Path | None = None) -> Dict[str, str]:
    if path is None:
        return DEFAULT_INDUSTRY_MAP.copy()

    df = read_csv_safely(path)
    if df.empty or not {"bank_industry", "mapped_industry"}.issubset(df.columns):
        return DEFAULT_INDUSTRY_MAP.copy()

    return {
        str(r["bank_industry"]).strip(): str(r["mapped_industry"]).strip()
        for _, r in df.iterrows()
        if str(r.get("bank_industry", "")).strip()
    }


def normalize_bank_industry(df: pd.DataFrame, industry_mapping: Dict[str, str]) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    if "업종_대분류" not in df.columns:
        df["mapped_industry"] = "기타서비스"
        return df
    df["mapped_industry"] = (
        df["업종_대분류"].astype(str).str.strip().map(industry_mapping).fillna("기타서비스")
    )
    return df


def load_financial_analysis_rules(path: str | Path | None) -> pd.DataFrame:
    df = read_csv_safely(path)
    if df.empty:
        return pd.DataFrame(columns=[
            "account_keyword", "account_category", "bank_metric", "bank_column",
            "analysis_method", "formula_name", "formula", "compare_method"
        ])
    return df.fillna("")


def find_rule_for_account(account_name: str, category: str, rules_df: pd.DataFrame) -> Dict[str, Any]:
    if rules_df is None or rules_df.empty:
        return {}

    account_name = str(account_name or "").strip()
    category = str(category or "").strip()

    def normalize_account_name(x: str) -> str:
        x = str(x or "").strip()

        remove_words = [
            "의상환", "의차입", "의발행", "의처분",
            "의감소", "의증가",
            "(감소)", "(증가)",
            "감소", "증가",
            "상환", "차입", "발행", "처분",
            " ", "\n", "\t",
        ]

        for w in remove_words:
            x = x.replace(w, "")

        return x.strip()

    account_norm = normalize_account_name(account_name)

    # 1순위: account_keyword가 계정명에 포함되는 경우
    for _, row in rules_df.iterrows():
        kw = str(row.get("account_keyword", "")).strip()
        if not kw:
            continue

        kw_norm = normalize_account_name(kw)

        if (
            kw in account_name
            or kw_norm in account_norm
            or account_norm in kw_norm
        ):
            print("[RULE MATCH]", account_name, "->", kw)
            return row.to_dict()

    # 2순위: category가 맞는 경우
    for _, row in rules_df.iterrows():
        cat = str(row.get("account_category", "")).strip()
        if cat and (cat == category or cat in category or category in cat):
            print("[CATEGORY MATCH]", account_name, "->", cat)
            return row.to_dict()

    print("[NO RULE MATCH]", account_name, "/", category)
    return {}

def add_bank_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def sum_cols(cols):
        valid = [c for c in cols if c in df.columns]
        if not valid:
            return None
        return df[valid].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)

    derived = {
        "total_loan": ["여신_운전자금대출잔액", "여신_시설자금대출잔액"],
        "total_deposit": ["요구불예금잔액", "거치식예금잔액", "적립식예금잔액"],
        "investment_balance": ["수익증권잔액", "신탁잔액"],
        "sales_proxy": ["신용카드사용금액", "체크카드사용금액", "창구거래금액"],
        "revenue_proxy": ["요구불입금금액", "신용카드사용금액", "체크카드사용금액"],
        "transaction_amount": ["창구거래금액", "인터넷뱅킹거래금액", "스마트뱅킹거래금액", "ATM거래금액"],
        "fx_total": ["외환_수출실적금액", "외환_수입실적금액"],
        "deposit_in": ["요구불입금금액"],
        "deposit_out": ["요구불출금금액"],
        "demand_deposit": ["요구불예금잔액"],
        "time_deposit": ["거치식예금잔액"],
        "facility_loan": ["여신_시설자금대출잔액"],
        "working_loan": ["여신_운전자금대출잔액"],
        "retirement_pension": ["퇴직연금잔액"],
    }

    for metric, cols in derived.items():
        s = sum_cols(cols)
        if s is not None:
            df[metric] = s

    return df

def build_industry_stats(
    bank_df: pd.DataFrame,
    industry: str,
    bank_column: str,
) -> Dict[str, Any]:
    if bank_df.empty or not bank_column or bank_column not in bank_df.columns:
        return {
            "available": False,
            "reason": f"은행데이터에 {bank_column} 컬럼이 없습니다.",
        }

    if "mapped_industry" not in bank_df.columns:
        return {
            "available": False,
            "reason": "mapped_industry 컬럼이 없습니다.",
        }

    sub = bank_df[bank_df["mapped_industry"].astype(str).str.strip() == str(industry).strip()].copy()

    if sub.empty:
        return {
            "available": False,
            "reason": f"동일업종 데이터가 없습니다: {industry}",
        }

    vals = pd.to_numeric(sub[bank_column], errors="coerce").dropna()
    vals = vals[vals >= 0]

    # 은행데이터: 백만원 → 원 단위 normalize
    vals = vals * BANK_UNIT_SCALE

    if vals.empty:
        return {
            "available": False,
            "reason": f"{bank_column} 유효값이 없습니다.",
        }

    return {
        "available": True,
        "industry_group": industry,
        "bank_column": bank_column,
        "unit": "원",
        "raw_unit": "백만원",
        "scale": BANK_UNIT_SCALE,
        "count": int(vals.shape[0]),
        "mean": float(vals.mean()),
        "median": float(vals.median()),
        "q25": float(vals.quantile(0.25)),
        "q75": float(vals.quantile(0.75)),
        "max": float(vals.max()),
    }


def interpret_ratio(ratio: Optional[float]) -> str:
    if ratio is None or math.isnan(ratio):
        return "동일업종 비교 비율을 계산할 수 없습니다."
    if ratio >= 3:
        return f"동일업종 중앙값 대비 약 {ratio:.2f}배로 매우 높은 수준입니다."
    if ratio >= 1.5:
        return f"동일업종 중앙값 대비 약 {ratio:.2f}배로 높은 수준입니다."
    if ratio >= 0.5:
        return f"동일업종 중앙값과 유사한 범위입니다."
    if ratio < 0.01:
        return f"동일업종 중앙값 대비 약 {ratio:.4f}배로 낮은 수준입니다."


def run_bank_analysis_for_item(
    item: Dict[str, Any],
    bank_df: pd.DataFrame,
    rules_df: pd.DataFrame,
    mapped_industry: str,
) -> Dict[str, Any]:
    """
    financial_rag_item 하나에 대해 외부 은행데이터 비교 결과 생성.
    """
    account_name = item.get("account_name") or item.get("title") or ""
    category = item.get("account_category") or item.get("risk_category") or item.get("category") or ""
    company_value = safe_float(
        item.get("current_value")
        or item.get("current_amount")
        or item.get("당기 계정")
        or item.get("current")
    )
    company_value_raw = company_value

    # 재무제표 값: 백만원 → 원 단위 normalize
    company_value = normalize_money_value(
        abs(company_value_raw),
        FINANCIAL_UNIT_SCALE,
    )
    rule = find_rule_for_account(account_name, category, rules_df)

    if not rule:
        return {
            "available": False,
            "reason": "계정과목/카테고리에 매핑되는 은행데이터 분석 룰이 없습니다.",
            "account_name": account_name,
            "category": category,
        }

    bank_metric = str(rule.get("bank_metric", "")).strip()
    bank_column = str(rule.get("bank_column", "")).strip()

    if not bank_column:
        bank_column = bank_metric
    stats = build_industry_stats(bank_df, mapped_industry, bank_column)

    result = {
        "available": bool(stats.get("available")),
        "account_name": account_name,
        "category": category,
        "mapped_industry": mapped_industry,
        "bank_metric": rule.get("bank_metric", ""),
        "bank_column": bank_column,
        "analysis_method": rule.get("analysis_method", ""),
        "formula_name": rule.get("formula_name", ""),
        "formula": rule.get("formula", ""),
        "company_value": company_value,
        "company_value_raw": company_value_raw,
        "company_value_raw_unit": "원",
        "company_value_normalized_unit": "원",
        "industry_stats": stats,
    }

    if not stats.get("available") or company_value is None:
        result["ratio_to_median"] = None
        result["difference_from_median"] = None
        result["interpretation"] = stats.get("reason", "은행데이터 비교를 수행할 수 없습니다.")
        return result

    median = stats.get("median") or 0
    if median == 0:
        ratio = None
        diff = None
    else:
        ratio = company_value / median
        diff = company_value - median

    result["ratio_to_median"] = ratio
    result["difference_from_median"] = diff
    result["interpretation"] = interpret_ratio(ratio)

    return result


def enrich_financial_items_with_bank_data(
    items: list[Dict[str, Any]],
    bank_data_path: str | Path | None,
    rules_path: str | Path | None,
    industry_mapping_path: str | Path | None,
    mapped_industry: str,
) -> list[Dict[str, Any]]:
    """
    financial_rag_items 전체에 external_analysis를 붙인다.
    """
    if not items:
        return items

    bank_df = read_csv_safely(bank_data_path)
    rules_df = load_financial_analysis_rules(rules_path)
    industry_map = load_industry_mapping(industry_mapping_path)

    if bank_df.empty:
        for item in items:
            item["external_analysis"] = {
                "available": False,
                "reason": "은행데이터 파일이 없거나 비어 있습니다. bank_data_path를 확인하세요.",
            }
        return items

    bank_df = normalize_bank_industry(bank_df, industry_map)

    for item in items:
        item["external_analysis"] = run_bank_analysis_for_item(
            item=item,
            bank_df=bank_df,
            rules_df=rules_df,
            mapped_industry=mapped_industry,
        )

        ext = item.get("external_analysis", {})
        if ext.get("available"):
            item["rag_context"] = str(item.get("rag_context", "")) + "\n" + "\n".join([
                "[은행데이터 동일업종 비교]",
                f"업종: {ext.get('mapped_industry', '')}",
                f"은행 변수: {ext.get('bank_column', '')}",
                f"동일업종 중앙값: {ext.get('industry_stats', {}).get('median', '')}",
                f"재무제표 값: {ext.get('company_value', '')}",
                f"중앙값 대비 비율: {ext.get('ratio_to_median', '')}",
                f"해석: {ext.get('interpretation', '')}",
            ])

    return items
