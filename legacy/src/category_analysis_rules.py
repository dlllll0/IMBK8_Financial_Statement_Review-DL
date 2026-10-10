CATEGORY_ANALYSIS_RULES = {
    "자산 평가 및 손상 리스크": {
        "analysis_methods": [
            "자산·금융자산·공정가치·손상 관련 단어의 전기 대비 증가 여부 확인",
            "손상차손, 평가손실, 회수가능액 관련 표현의 신규 등장 여부 확인",
            "재무제표상 총자산, 금융자산, 투자자산 변화와 단어 증가 방향 비교",
            "은행데이터의 예금잔액 및 금융상품잔액 감소 여부와 함께 확인",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "총자산증감률": "(t_total_assets - t_minus_1_total_assets) / max(t_minus_1_total_assets, 1)",
            "예금잔액증감률": "(t_total_deposit - t_minus_1_total_deposit) / max(t_minus_1_total_deposit, 1)",
            "금융상품잔액증감률": "(t_total_fin_product - t_minus_1_total_fin_product) / max(t_minus_1_total_fin_product, 1)",
        },
    },

    "재무제표 표시 및 금융공시 리스크": {
        "analysis_methods": [
            "재무제표 표시, 분류, 측정, 공정가치 공시 관련 단어 증가 여부 확인",
            "금융상품, 위험관리, 공정가치, 수준분류 관련 표현의 신규 등장 여부 확인",
            "주석 데이터에서 금융공시 관련 단어가 집중적으로 등장하는지 확인",
            "은행데이터의 금융상품잔액, 여신한도, 대출잔액 변화와 비교",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "주석등장비중": "t_notes_count / max(t_word_count, 1)",
            "금융상품잔액증감률": "(t_total_fin_product - t_minus_1_total_fin_product) / max(t_minus_1_total_fin_product, 1)",
            "대출잔액증감률": "(t_total_loan - t_minus_1_total_loan) / max(t_minus_1_total_loan, 1)",
        },
    },

    "법적 의무 및 우발채무 리스크": {
        "analysis_methods": [
            "소송, 보증, 담보, 우발부채, 충당부채 관련 단어 증가 여부 확인",
            "신규 소송·지급보증·약정 관련 표현 등장 여부 확인",
            "감사보고서 또는 주석에서 동시에 관련 표현이 등장하는지 확인",
            "은행데이터의 여신한도 및 대출잔액 증가 여부와 함께 확인",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "감사등장여부": "1 if t_audit_count > 0 else 0",
            "대출잔액증감률": "(t_total_loan - t_minus_1_total_loan) / max(t_minus_1_total_loan, 1)",
            "한도소진율변화": "t_loan_limit_usage_ratio - t_minus_1_loan_limit_usage_ratio",
        },
    },

    "수익인식 및 계약 이행 리스크": {
        "analysis_methods": [
            "수익, 계약부채, 선수금, 수행의무 관련 단어의 전기 대비 증가 여부 확인",
            "계약부채·초과청구·분양중도금 관련 단어의 신규 등장 여부 확인",
            "재무제표상 매출액 변화와 계약 관련 단어 증가 방향 비교",
            "은행데이터의 요구불입금금액, 카드사용금액, 자동이체금액 변화와 매출 흐름 비교",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "매출증감률": "(t_revenue - t_minus_1_revenue) / max(t_minus_1_revenue, 1)",
            "계약부채증감률": "(t_contract_liability - t_minus_1_contract_liability) / max(t_minus_1_contract_liability, 1)",
            "입금증감률": "(t_요구불입금금액 - t_minus_1_요구불입금금액) / max(t_minus_1_요구불입금금액, 1)",
            "카드사용증감률": "(t_total_card_amount - t_minus_1_total_card_amount) / max(t_minus_1_total_card_amount, 1)",
        },
    },

    "내부통제 및 감사 의견 리스크": {
        "analysis_methods": [
            "감사의견, 내부회계관리제도, 중요한 불확실성 관련 단어 등장 여부 확인",
            "audit source에서 관련 단어가 신규 등장했는지 확인",
            "전기 대비 감사 관련 카테고리 빈도가 증가했는지 확인",
            "유동성, 우발채무, 손상 리스크 카테고리와 함께 증가하는지 확인",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "감사등장여부": "1 if t_audit_count > 0 else 0",
            "감사비중": "t_audit_count / max(t_word_count, 1)",
        },
    },

    "내부거래 및 계열사 리스크": {
        "analysis_methods": [
            "특수관계자, 계열사, 내부거래, 지급보증 관련 단어 증가 여부 확인",
            "전기 대비 특수관계자 거래 관련 신규 단어 등장 여부 확인",
            "재무제표상 매출채권, 대여금, 지급보증 관련 항목 변화와 비교",
            "은행데이터의 대출잔액, 입출금흐름 변화와 함께 확인",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "대출잔액증감률": "(t_total_loan - t_minus_1_total_loan) / max(t_minus_1_total_loan, 1)",
            "입출금순흐름변화": "t_demand_deposit_net_flow - t_minus_1_demand_deposit_net_flow",
        },
    },

    "법인세 및 특수계정 공시 리스크": {
        "analysis_methods": [
            "법인세, 이연법인세, 충당부채, 특수계정 관련 단어 증가 여부 확인",
            "법인세비용 또는 이연법인세 관련 표현의 신규 등장 여부 확인",
            "재무제표상 법인세비용, 이연법인세자산·부채 변화와 비교",
            "주석에서 법인세 및 특수계정 설명 비중이 증가했는지 확인",
        ],
        "formulas": {
            "단어증감": "t_word_count - t_minus_1_word_count",
            "단어증가율": "(t_word_count - t_minus_1_word_count) / max(t_minus_1_word_count, 1)",
            "신규등장여부": "1 if t_word_count > 0 and t_minus_1_word_count == 0 else 0",
            "주석등장비중": "t_notes_count / max(t_word_count, 1)",
            "법인세비용증감률": "(t_income_tax_expense - t_minus_1_income_tax_expense) / max(t_minus_1_income_tax_expense, 1)",
            "이연법인세순액변화": "(t_deferred_tax_asset - t_deferred_tax_liability) - (t_minus_1_deferred_tax_asset - t_minus_1_deferred_tax_liability)",
        },
    },
}