import pandas as pd


def safe_divide(a, b):
    if pd.isna(b) or b == 0:
        return 0
    return a / b


def add_bank_features(df):
    df = df.copy()

    money_cols = [
        "요구불예금잔액", "거치식예금잔액", "적립식예금잔액",
        "수익증권잔액", "신탁잔액", "퇴직연금잔액",
        "여신한도금액", "여신_운전자금대출잔액", "여신_시설자금대출잔액",
        "외환_수출실적금액", "외환_수입실적금액",
        "신용카드사용금액", "체크카드사용금액",
        "창구거래금액", "인터넷뱅킹거래금액", "스마트뱅킹거래금액",
        "폰뱅킹거래금액", "ATM거래금액", "자동이체금액",
        "요구불입금금액", "요구불출금금액",
    ]

    for col in money_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["total_deposit"] = (
        df.get("요구불예금잔액", 0)
        + df.get("거치식예금잔액", 0)
        + df.get("적립식예금잔액", 0)
    )

    df["total_fin_product"] = (
        df.get("수익증권잔액", 0)
        + df.get("신탁잔액", 0)
        + df.get("퇴직연금잔액", 0)
    )

    df["total_loan"] = (
        df.get("여신_운전자금대출잔액", 0)
        + df.get("여신_시설자금대출잔액", 0)
    )

    df["loan_limit_usage_ratio"] = (
        df["total_loan"] / df.get("여신한도금액", 0).replace(0, pd.NA)
    ).fillna(0)

    df["demand_deposit_net_flow"] = (
        df.get("요구불입금금액", 0)
        - df.get("요구불출금금액", 0)
    )

    df["total_card_amount"] = (
        df.get("신용카드사용금액", 0)
        + df.get("체크카드사용금액", 0)
    )

    df["total_transaction_amount"] = (
        df.get("창구거래금액", 0)
        + df.get("인터넷뱅킹거래금액", 0)
        + df.get("스마트뱅킹거래금액", 0)
        + df.get("폰뱅킹거래금액", 0)
        + df.get("ATM거래금액", 0)
        + df.get("자동이체금액", 0)
    )

    df["nonface_transaction_amount"] = (
        df.get("인터넷뱅킹거래금액", 0)
        + df.get("스마트뱅킹거래금액", 0)
        + df.get("폰뱅킹거래금액", 0)
    )

    df["nonface_transaction_ratio"] = (
        df["nonface_transaction_amount"]
        / df["total_transaction_amount"].replace(0, pd.NA)
    ).fillna(0)

    df["export_import_net_amount"] = (
        df.get("외환_수출실적금액", 0)
        - df.get("외환_수입실적금액", 0)
    )

    return df