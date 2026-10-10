# -*- coding: utf-8 -*-
"""
src/financial_rule_engine.py

재무제표 단순 비교 결과에 룰기반 분석방법을 매핑하는 엔진.

입력 row 기준 필수 컬럼:
- 계정과목
- 계정분류
- 전기_원본
- 당기_원본
- 증감액_원본
- 증감률_원본

출력:
- risk_category
- risk_tags
- priority_score
- priority_grade
- analysis_method
- check_points
- benchmark_hint
- interpretation
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

try:
    from src.financial_account_vocab import match_financial_account_vocab
except Exception:
    match_financial_account_vocab = None


# =========================================================
# 1. 재무제표 룰북
# =========================================================

FINANCIAL_RULES: List[Dict[str, object]] = [
    {
        "rule_id": "CUR_ASSET_SPIKE",
        "account_keywords": ["유동자산"],
        "growth_rate_gte": 150,
        "risk_category": "유동성 변동 리스크",
        "risk_tags": ["유동자산 급증", "운전자본 변동"],
        "priority_score": 3,
        "analysis_method": "유동자산 급증 원인을 현금, 매출채권, 재고자산 중심으로 분해하여 확인",
        "check_points": [
            "현금및현금성자산 증가 여부",
            "매출채권 증가 여부",
            "재고자산 증가 여부",
            "영업활동현금흐름과 동반 개선 여부",
        ],
        "benchmark_hint": "동일업종 평균 유동비율, 당좌비율과 비교 가능",
    },
    {
        "rule_id": "CUR_ASSET_DROP",
        "account_keywords": ["유동자산"],
        "growth_rate_lte": -30,
        "risk_category": "유동성 약화 리스크",
        "risk_tags": ["유동자산 감소", "단기 지급능력 약화"],
        "priority_score": 3,
        "analysis_method": "유동자산 감소가 현금 감소인지, 매출채권 회수인지, 재고 감소인지 구분",
        "check_points": [
            "현금성자산 감소 여부",
            "단기차입금 증가 여부",
            "유동부채 대비 유동자산 수준",
        ],
        "benchmark_hint": "동일업종 평균 유동비율 대비 낮은지 확인",
    },
    {
        "rule_id": "RECEIVABLE_SPIKE",
        "account_keywords": ["매출채권", "미수금"],
        "growth_rate_gte": 50,
        "risk_category": "채권 회수 리스크",
        "risk_tags": ["매출채권 증가", "회수가능성 검토"],
        "priority_score": 4,
        "analysis_method": "매출채권 증가율과 매출액 증가율을 비교하고 대손충당금 설정 여부 확인",
        "check_points": [
            "매출액 증가율 대비 매출채권 증가율",
            "대손충당금 증가 여부",
            "특수관계자 채권 여부",
            "장기 미회수채권 존재 여부",
        ],
        "benchmark_hint": "동일업종 평균 매출채권회전율과 비교 가능",
    },
    {
        "rule_id": "INVENTORY_SPIKE",
        "account_keywords": ["재고", "재고자산"],
        "growth_rate_gte": 50,
        "risk_category": "재고자산 평가 리스크",
        "risk_tags": ["재고 증가", "평가손실 가능성"],
        "priority_score": 3,
        "analysis_method": "재고자산 증가율과 매출액/매출원가 증가율을 비교하고 재고평가손실 여부 확인",
        "check_points": [
            "매출액 증가율 대비 재고자산 증가율",
            "재고자산평가충당금 여부",
            "장기체화재고 가능성",
            "매출원가율 변화",
        ],
        "benchmark_hint": "동일업종 평균 재고자산회전율과 비교 가능",
    },
    {
        "rule_id": "BORROWING_SPIKE",
        "account_keywords": ["차입금", "사채", "리스부채"],
        "growth_rate_gte": 50,
        "risk_category": "재무부담 증가 리스크",
        "risk_tags": ["차입부채 증가", "이자부담 증가"],
        "priority_score": 5,
        "analysis_method": "차입금/사채 증가 원인과 이자비용 증가 여부, 만기구조를 확인",
        "check_points": [
            "단기차입금 증가 여부",
            "장기차입금의 유동성 대체 여부",
            "이자비용 증가 여부",
            "만기 도래 차입금 규모",
            "담보 제공 여부",
        ],
        "benchmark_hint": "동일업종 평균 부채비율, 차입금의존도와 비교 가능",
    },
    {
        "rule_id": "LIABILITY_SPIKE",
        "account_keywords": ["부채"],
        "growth_rate_gte": 50,
        "risk_category": "부채 확대 리스크",
        "risk_tags": ["부채 증가", "재무구조 악화"],
        "priority_score": 4,
        "analysis_method": "부채 증가가 영업채무 증가인지 금융부채 증가인지 구분",
        "check_points": [
            "차입금 증가 여부",
            "매입채무 증가 여부",
            "유동부채 증가 여부",
            "자본 대비 부채 증가 속도",
        ],
        "benchmark_hint": "동일업종 평균 부채비율과 비교 가능",
    },
    {
        "rule_id": "IMPAIRMENT_OCCURRED",
        "account_keywords": ["손상", "감액", "평가손실"],
        "growth_rate_gte": 1,
        "risk_category": "자산손상 리스크",
        "risk_tags": ["손상 발생", "자산가치 하락"],
        "priority_score": 5,
        "analysis_method": "손상차손 발생 계정과 관련 자산의 회수가능액 산정 근거 확인",
        "check_points": [
            "손상차손 발생 자산",
            "회수가능액 산정 방식",
            "영업권/무형자산 손상 여부",
            "투자자산 감액 여부",
        ],
        "benchmark_hint": "동일업종 대비 손상차손 발생 빈도 및 규모 비교 가능",
    },
    {
        "rule_id": "SALES_DROP",
        "account_keywords": ["매출", "수익"],
        "growth_rate_lte": -20,
        "risk_category": "매출 감소 리스크",
        "risk_tags": ["매출 감소", "영업성과 악화"],
        "priority_score": 4,
        "analysis_method": "매출 감소가 영업이익 감소, 재고 증가, 매출채권 증가와 동반되는지 확인",
        "check_points": [
            "영업이익 감소 여부",
            "재고자산 증가 여부",
            "매출채권 회수 지연 여부",
            "주요 고객 또는 사업부문 변화",
        ],
        "benchmark_hint": "동일업종 평균 매출성장률과 비교 가능",
    },
    {
        "rule_id": "OPERATING_PROFIT_DROP",
        "account_keywords": ["영업이익", "영업손실"],
        "growth_rate_lte": -30,
        "risk_category": "수익성 악화 리스크",
        "risk_tags": ["영업이익 감소", "수익성 저하"],
        "priority_score": 4,
        "analysis_method": "매출액, 매출원가, 판매관리비 변동을 함께 비교하여 수익성 악화 원인 확인",
        "check_points": [
            "매출총이익률 변화",
            "판매관리비 증가 여부",
            "매출원가율 상승 여부",
            "일회성 비용 발생 여부",
        ],
        "benchmark_hint": "동일업종 평균 영업이익률과 비교 가능",
    },
    {
        "rule_id": "EQUITY_DROP",
        "account_keywords": ["자본", "이익잉여금"],
        "growth_rate_lte": -30,
        "risk_category": "자본잠식 가능성 리스크",
        "risk_tags": ["자본 감소", "누적손실 가능성"],
        "priority_score": 5,
        "analysis_method": "자본 감소 원인이 당기순손실, 결손금, 자본조정인지 확인",
        "check_points": [
            "당기순손실 발생 여부",
            "이익잉여금 감소 여부",
            "자본총계 대비 자본금 수준",
            "자본잠식 여부",
        ],
        "benchmark_hint": "동일업종 평균 자기자본비율과 비교 가능",
    },
]


# =========================================================
# 2. 룰 조건 판정
# =========================================================

def keyword_match(account_name: str, keywords: List[str]) -> bool:
    account_name = str(account_name or "")
    return any(k in account_name for k in keywords)


def condition_match(row: pd.Series, rule: Dict[str, object]) -> bool:
    growth_rate = float(row.get("증감률_원본", 0) or 0)

    if "growth_rate_gte" in rule:
        if growth_rate < float(rule["growth_rate_gte"]):
            return False

    if "growth_rate_lte" in rule:
        if growth_rate > float(rule["growth_rate_lte"]):
            return False

    return True


def apply_financial_rules(row: pd.Series) -> Dict[str, object]:
    account_name = str(row.get("계정과목", ""))
    matched_rules = []

    for rule in FINANCIAL_RULES:
        keywords = rule.get("account_keywords", [])

        if not keyword_match(account_name, keywords):
            continue

        if not condition_match(row, rule):
            continue

        matched_rules.append(rule)

    # =====================================================
    # 계정과목 보캡 기반 보정
    # - 기존 증감률 룰과 별도로, K-IFRS 계정과목 보캡에서
    #   카테고리/분석방법/수식 정보를 보강한다.
    # - 은행데이터 연계 수식은 formula/bank_data_required에 남겨두고,
    #   실제 계산은 bank_financial_formula_engine.py에서 확장한다.
    # =====================================================
    vocab_meta = None
    if match_financial_account_vocab is not None:
        try:
            vocab_meta = match_financial_account_vocab(account_name)
        except Exception:
            vocab_meta = None

    if vocab_meta:
        matched_rules.append({
            "rule_id": "ACCOUNT_VOCAB_MATCH",
            "account_keywords": vocab_meta.get("account_keywords", []),
            "risk_category": vocab_meta.get("risk_category", vocab_meta.get("account_category", "")),
            "risk_tags": [vocab_meta.get("account_category", "계정과목 보캡 매핑")],
            "priority_score": int(vocab_meta.get("priority_base", 1) or 1),
            "analysis_method": vocab_meta.get("analysis_method", ""),
            "check_points": vocab_meta.get("check_points", []),
            "benchmark_hint": "은행데이터 연결 시 동일업종 기준값과 비교 가능",
            "account_category": vocab_meta.get("account_category", ""),
            "formula_name": vocab_meta.get("formula_name", ""),
            "formula": vocab_meta.get("formula", ""),
            "bank_data_required": vocab_meta.get("bank_data_required", []),
        })

    if not matched_rules:
        return build_default_rule_result(row)

    risk_categories = []
    risk_tags = []
    analysis_methods = []
    check_points = []
    benchmark_hints = []
    account_categories = []
    formula_names = []
    formulas = []
    bank_data_required = []
    priority_score = 0

    for rule in matched_rules:
        risk_categories.append(str(rule.get("risk_category", "")))
        risk_tags.extend(rule.get("risk_tags", []))
        analysis_methods.append(str(rule.get("analysis_method", "")))
        check_points.extend(rule.get("check_points", []))
        benchmark_hints.append(str(rule.get("benchmark_hint", "")))
        if rule.get("account_category"):
            account_categories.append(str(rule.get("account_category", "")))
        if rule.get("formula_name"):
            formula_names.append(str(rule.get("formula_name", "")))
        if rule.get("formula"):
            formulas.append(str(rule.get("formula", "")))
        bank_data_required.extend(rule.get("bank_data_required", []))
        priority_score += int(rule.get("priority_score", 0))

    priority_score = min(priority_score, 10)

    if priority_score >= 7:
        priority_grade = "상"
    elif priority_score >= 4:
        priority_grade = "중"
    else:
        priority_grade = "하"

    interpretation = build_interpretation(row, risk_categories, analysis_methods)

    return {
        "risk_category": " / ".join(dict.fromkeys(risk_categories)),
        "risk_tags": ", ".join(dict.fromkeys(risk_tags)),
        "priority_score": priority_score,
        "priority_grade": priority_grade,
        "analysis_method": " / ".join([x for x in dict.fromkeys(analysis_methods) if x]),
        "check_points": " / ".join([str(x) for x in dict.fromkeys(check_points) if x]),
        "benchmark_hint": " / ".join([x for x in dict.fromkeys(benchmark_hints) if x]),
        "account_category": " / ".join([x for x in dict.fromkeys(account_categories) if x]),
        "formula_name": " / ".join([x for x in dict.fromkeys(formula_names) if x]),
        "formula": " / ".join([x for x in dict.fromkeys(formulas) if x]),
        "bank_data_required": ", ".join([str(x) for x in dict.fromkeys(bank_data_required) if x]),
        "interpretation": interpretation,
    }


def build_default_rule_result(row: pd.Series) -> Dict[str, object]:
    account = str(row.get("계정과목", ""))
    change = float(row.get("증감액_원본", 0) or 0)
    rate = float(row.get("증감률_원본", 0) or 0)

    abs_rate = abs(rate)

    if abs_rate >= 100:
        risk_category = "급격한 계정 변동"
        risk_tags = "급격한 변동"
        priority_score = 3
        priority_grade = "중"
        analysis_method = "전기 대비 증감률 100% 이상인 계정의 변동 원인 확인"
    elif abs_rate >= 50:
        risk_category = "큰 폭의 계정 변동"
        risk_tags = "큰 폭의 변동"
        priority_score = 2
        priority_grade = "하"
        analysis_method = "전기 대비 증감률 50% 이상인 계정의 변동 원인 확인"
    elif abs_rate >= 20:
        risk_category = "유의미한 계정 변동"
        risk_tags = "유의미한 변동"
        priority_score = 1
        priority_grade = "하"
        analysis_method = "전기 대비 증감률 20% 이상인 계정의 변동 원인 확인"
    else:
        risk_category = "일반 계정 변동"
        risk_tags = "일반 변동"
        priority_score = 0
        priority_grade = "하"
        analysis_method = "전기 대비 금액과 증감률을 단순 확인"

    direction = "증가" if change > 0 else "감소" if change < 0 else "변동 없음"

    return {
        "risk_category": risk_category,
        "risk_tags": risk_tags,
        "priority_score": priority_score,
        "priority_grade": priority_grade,
        "analysis_method": analysis_method,
        "check_points": "관련 주석 존재 여부 / 전기 대비 변동 원인 / 일회성 거래 여부",
        "benchmark_hint": "필요 시 동일업종 평균 재무비율과 비교",
        "account_category": "",
        "formula_name": "",
        "formula": "",
        "bank_data_required": "",
        "interpretation": f"{account} 계정은 전기 대비 {direction}했습니다. 증감률은 {rate:.1f}%입니다.",
    }


def build_interpretation(
    row: pd.Series,
    risk_categories: List[str],
    analysis_methods: List[str],
) -> str:
    account = str(row.get("계정과목", ""))
    prev_val = row.get("전기 계정", 0)
    curr_val = row.get("당기 계정", 0)
    change = row.get("증감액", 0)
    rate = row.get("증감률", "0.0%")

    direction = "증가" if change > 0 else "감소" if change < 0 else "변동 없음"

    category_text = ", ".join(dict.fromkeys(risk_categories))
    method_text = " ".join(dict.fromkeys(analysis_methods))

    return (
        f"{account} 계정은 전기 {prev_val:,}백만원에서 당기 {curr_val:,}백만원으로 "
        f"{direction}했습니다. 증감액은 {change:,}백만원이며 증감률은 {rate}입니다. "
        f"해당 변동은 {category_text} 관점에서 검토할 수 있습니다. "
        f"분석 시 {method_text}"
    )


# =========================================================
# 3. 교차 변수 룰
# =========================================================

def find_row_by_keywords(df: pd.DataFrame, keywords: List[str]) -> pd.Series | None:
    if df.empty or "계정과목" not in df.columns:
        return None

    mask = df["계정과목"].astype(str).apply(
        lambda x: any(k in x for k in keywords)
    )

    matched = df[mask]

    if matched.empty:
        return None

    return matched.iloc[0]


def build_cross_variable_rules(df: pd.DataFrame) -> List[Dict[str, object]]:
    """
    여러 계정 간 비교 룰.
    예:
    - 매출채권 증가율 > 매출 증가율
    - 재고자산 증가율 > 매출 증가율
    - 부채 증가율 > 자산 증가율
    """
    results = []

    sales = find_row_by_keywords(df, ["매출", "수익"])
    receivable = find_row_by_keywords(df, ["매출채권", "미수금"])
    inventory = find_row_by_keywords(df, ["재고"])
    liability = find_row_by_keywords(df, ["부채"])
    asset = find_row_by_keywords(df, ["자산"])
    borrowing = find_row_by_keywords(df, ["차입금", "사채"])

    if sales is not None and receivable is not None:
        sales_rate = float(sales.get("증감률_원본", 0) or 0)
        receivable_rate = float(receivable.get("증감률_원본", 0) or 0)

        if receivable_rate > sales_rate + 20:
            results.append({
                "rule_id": "RECEIVABLE_OVER_SALES",
                "risk_category": "채권 회수 리스크",
                "priority_grade": "상",
                "priority_score": 7,
                "analysis_method": "매출채권 증가율이 매출 증가율을 크게 초과하는지 확인",
                "result": (
                    f"매출채권 증가율({receivable_rate:.1f}%)이 "
                    f"매출 증가율({sales_rate:.1f}%)보다 높습니다."
                ),
                "check_points": [
                    "매출채권 연령분석",
                    "대손충당금 설정률",
                    "특수관계자 매출채권 여부",
                ],
            })

    if sales is not None and inventory is not None:
        sales_rate = float(sales.get("증감률_원본", 0) or 0)
        inventory_rate = float(inventory.get("증감률_원본", 0) or 0)

        if inventory_rate > sales_rate + 20:
            results.append({
                "rule_id": "INVENTORY_OVER_SALES",
                "risk_category": "재고자산 평가 리스크",
                "priority_grade": "중",
                "priority_score": 5,
                "analysis_method": "재고자산 증가율이 매출 증가율을 초과하는지 확인",
                "result": (
                    f"재고자산 증가율({inventory_rate:.1f}%)이 "
                    f"매출 증가율({sales_rate:.1f}%)보다 높습니다."
                ),
                "check_points": [
                    "재고자산평가충당금",
                    "장기체화재고",
                    "매출원가율 변화",
                ],
            })

    if liability is not None and asset is not None:
        liability_rate = float(liability.get("증감률_원본", 0) or 0)
        asset_rate = float(asset.get("증감률_원본", 0) or 0)

        if liability_rate > asset_rate + 20:
            results.append({
                "rule_id": "LIABILITY_OVER_ASSET",
                "risk_category": "재무구조 악화 리스크",
                "priority_grade": "상",
                "priority_score": 7,
                "analysis_method": "부채 증가율이 자산 증가율을 초과하는지 확인",
                "result": (
                    f"부채 증가율({liability_rate:.1f}%)이 "
                    f"자산 증가율({asset_rate:.1f}%)보다 높습니다."
                ),
                "check_points": [
                    "부채비율 변화",
                    "차입금 증가 여부",
                    "이자비용 증가 여부",
                ],
            })

    if borrowing is not None and sales is not None:
        borrowing_rate = float(borrowing.get("증감률_원본", 0) or 0)
        sales_rate = float(sales.get("증감률_원본", 0) or 0)

        if borrowing_rate >= 50 and sales_rate <= 10:
            results.append({
                "rule_id": "BORROWING_SPIKE_WITH_LOW_SALES",
                "risk_category": "차입부담 확대 리스크",
                "priority_grade": "상",
                "priority_score": 8,
                "analysis_method": "차입금 증가가 매출 성장으로 뒷받침되는지 확인",
                "result": (
                    f"차입성 부채 증가율({borrowing_rate:.1f}%)은 높지만 "
                    f"매출 증가율({sales_rate:.1f}%)은 낮습니다."
                ),
                "check_points": [
                    "차입금 사용 목적",
                    "이자보상배율",
                    "영업현금흐름",
                    "만기구조",
                ],
            })

    return results