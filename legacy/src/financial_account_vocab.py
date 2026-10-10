# -*- coding: utf-8 -*-
"""
src/financial_account_vocab.py

재무제표 계정과목 보캡/룰 매핑 초안.

역할:
- K-IFRS 계정과목 기반으로 흔한 재무제표 변수명을 카테고리화한다.
- 계정과목 → 검토 카테고리 → 분석방법 → 수식/필요 데이터 구조를 제공한다.
- 실제 은행 원장/거래 데이터 컬럼명은 프로젝트 환경에 맞춰 BANK_DATA_FIELDS를 수정하면 된다.

주의:
- 이 파일은 "위험 확정 모델"이 아니라 검토 우선순위 및 RAG item 생성을 위한 룰북이다.
"""

from __future__ import annotations

from typing import Any, Dict, List


FINANCIAL_ACCOUNT_VOCAB: List[Dict[str, Any]] = [
    {
        "account_keywords": ["단기대여금", "장기대여금", "대여금"],
        "account_category": "자산리스크",
        "risk_category": "자산 회수가능성 리스크",
        "analysis_method": "대여금 규모와 동일업종 은행 대출/여신 관련 지표를 비교하여 회수가능성을 검토",
        "formula_name": "동일업종 대출잔액 대비 대여금 비율",
        "formula": "재무제표_대여금 / 동일업종_은행데이터_대출잔액_median",
        "bank_data_required": ["loan_balance_median_by_industry"],
        "priority_base": 4,
        "check_points": [
            "대여금 상대방 확인",
            "특수관계자 대여 여부",
            "회수기간 및 연체 여부",
            "은행 여신잔액 대비 비정상적 증가 여부",
        ],
    },
    {
        "account_keywords": ["매출채권", "미수금", "장기성매출채권"],
        "account_category": "채권리스크",
        "risk_category": "채권 회수 리스크",
        "analysis_method": "매출채권/미수금 변동과 동일업종 현금유입 또는 입금흐름을 비교하여 회수 지연 가능성을 검토",
        "formula_name": "동일업종 입금흐름 대비 채권 비율",
        "formula": "재무제표_채권성계정 / 동일업종_은행데이터_입금액_median",
        "bank_data_required": ["deposit_inflow_median_by_industry"],
        "priority_base": 4,
        "check_points": [
            "매출 증가율 대비 채권 증가율",
            "대손충당금 설정률",
            "장기 미회수채권 여부",
            "입금흐름과 매출채권 변동의 방향성 비교",
        ],
    },
    {
        "account_keywords": ["재고", "재고자산", "상품", "제품", "재공품", "원재료"],
        "account_category": "자산리스크",
        "risk_category": "재고자산 평가 리스크",
        "analysis_method": "재고자산 변동과 매출/매출원가, 동일업종 매출흐름을 비교하여 체화 또는 평가손실 가능성을 검토",
        "formula_name": "재고 증가율과 매출 증가율 차이",
        "formula": "재고자산_증감률 - 매출액_증감률",
        "bank_data_required": ["sales_inflow_median_by_industry"],
        "priority_base": 3,
        "check_points": [
            "재고자산평가손실 발생 여부",
            "매출액 증가율 대비 재고 증가율",
            "장기체화재고 가능성",
            "매출원가율 변화",
        ],
    },
    {
        "account_keywords": ["단기차입금", "장기차입금", "차입금", "사채", "유동성장기부채"],
        "account_category": "부채리스크",
        "risk_category": "재무부담 증가 리스크",
        "analysis_method": "차입성 부채 변동과 은행 대출잔액/상환흐름을 비교하여 재무부담 확대 여부를 검토",
        "formula_name": "동일업종 대출잔액 대비 차입성부채 비율",
        "formula": "재무제표_차입성부채 / 동일업종_은행데이터_대출잔액_median",
        "bank_data_required": ["loan_balance_median_by_industry", "debt_repayment_median_by_industry"],
        "priority_base": 5,
        "check_points": [
            "단기차입금 증가 여부",
            "만기 도래 차입금 규모",
            "이자비용 증가 여부",
            "은행 상환흐름과 차입금 변동 비교",
        ],
    },
    {
        "account_keywords": ["손상", "감액", "평가손실", "대손상각비", "영업권감액손실"],
        "account_category": "손상리스크",
        "risk_category": "자산손상 리스크",
        "analysis_method": "손상/감액 관련 계정 발생 여부와 관련 자산의 회수가능액 산정 근거를 검토",
        "formula_name": "손상성 비용 발생 여부",
        "formula": "당기_손상성비용 - 전기_손상성비용",
        "bank_data_required": [],
        "priority_base": 5,
        "check_points": [
            "손상차손 발생 자산",
            "회수가능액 산정 방식",
            "영업권/무형자산 손상 여부",
            "투자자산 감액 여부",
        ],
    },
    {
        "account_keywords": ["매출액", "매출", "수익"],
        "account_category": "수익리스크",
        "risk_category": "수익 인식 및 매출 변동 리스크",
        "analysis_method": "매출액 변동과 은행 입금흐름을 비교하여 수익 인식의 현금흐름 뒷받침 여부를 검토",
        "formula_name": "은행 입금흐름 대비 매출액 비율",
        "formula": "재무제표_매출액 / 동일업종_은행데이터_입금액_median",
        "bank_data_required": ["deposit_inflow_median_by_industry"],
        "priority_base": 4,
        "check_points": [
            "매출액 증가율",
            "매출채권 동반 증가 여부",
            "은행 입금흐름과 매출 변동의 방향성",
            "일회성 매출 여부",
        ],
    },
]


def match_financial_account_vocab(account_name: str) -> Dict[str, Any] | None:
    """계정과목 문자열에 매칭되는 보캡 항목 하나를 반환한다."""
    account_name = str(account_name or "")

    for item in FINANCIAL_ACCOUNT_VOCAB:
        keywords = item.get("account_keywords", [])
        if any(str(k) in account_name for k in keywords):
            return item

    return None


def get_financial_account_vocab() -> List[Dict[str, Any]]:
    return FINANCIAL_ACCOUNT_VOCAB
