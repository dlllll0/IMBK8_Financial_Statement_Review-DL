# -*- coding: utf-8 -*-
"""
rule_based_rag_builder.py

재무제표 구조화 결과 + 텍스트 OCR/명사분석 결과를 이용해서
우측 패널/LLM 프롬프트에 넣기 좋은 룰 기반 RAG item을 생성한다.

입력:
- outputs/jobs/{job_id}/{period}/financial_structured/financial_statement_compare.csv
- outputs/jobs/{job_id}/{period}/text/*_fulltext_data.csv
- outputs/jobs/{job_id}/{period}/text_analysis/*noun_data.csv

출력:
- rule_based_rag_items.json
- rule_based_analysisData.js
- rule_based_rag_table.csv
- rule_based_rag_prompt.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


ALLOWED_TABLES = {
    "재무상태표", "손익계산서", "포괄손익계산서", "현금흐름표",
    "연결재무상태표", "연결손익계산서", "연결포괄손익계산서", "연결현금흐름표",
}

RISK_LABEL = {
    "high": "중점 검토 항목",
    "mid": "주의 검토",
    "low": "참고 검토",
}

RISK_SORT = {"high": 0, "mid": 1, "low": 2}


ACCOUNT_RULES = [
    {
        "rule_id": "asset_receivable",
        "match": ["매출채권", "기타채권", "채권"],
        "category": "자산리스크",
        "direction": "increase",
        "why": "매출채권 및 기타채권은 회수가능성, 대손충당금, 신용위험과 직접 연결됩니다.",
        "keywords": ["매출채권", "기타채권", "대손", "손상", "회수", "신용위험", "기대신용손실", "거래처", "연체"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "inventory",
        "match": ["재고자산", "상품", "제품", "원재료"],
        "category": "자산리스크",
        "direction": "increase",
        "why": "재고자산 증가는 평가손실, 순실현가능가치 하락, 장기체화 가능성과 연결됩니다.",
        "keywords": ["재고자산", "평가손실", "순실현가능가치", "손상", "폐기", "체화", "평가충당금"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "cash_liquidity",
        "match": ["현금", "현금성자산", "현금및현금성자산"],
        "category": "유동성리스크",
        "direction": "decrease",
        "why": "현금 및 현금성자산 감소는 단기 지급능력과 유동성 부담을 검토해야 하는 신호입니다.",
        "keywords": ["현금", "현금성자산", "유동성", "자금", "운전자본", "차입", "상환"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "borrowings",
        "match": ["차입금", "단기차입금", "장기차입금", "사채", "리스부채"],
        "category": "부채/유동성리스크",
        "direction": "increase",
        "why": "차입금 증가는 이자비용, 차환위험, 담보 제공 및 만기 구조와 연결됩니다.",
        "keywords": ["차입금", "사채", "이자율", "담보", "만기", "유동성", "차환", "약정", "상환"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "fair_value_assets",
        "match": ["공정가치", "금융자산", "당기손익"],
        "category": "평가리스크",
        "direction": "both",
        "why": "공정가치 측정 금융자산은 평가손익, 시장가격 변동, 수준분류와 연결되므로 변동성이 큰 항목입니다.",
        "keywords": ["공정가치", "평가", "금융상품", "수준", "측정", "평가손익", "당기손익"],
        "high_growth": 1.0,
        "mid_growth": 0.5,
    },
    {
        "rule_id": "revenue",
        "match": ["매출", "수익"],
        "category": "수익성/수익인식",
        "direction": "both",
        "why": "매출 변동은 영업성과와 수익인식 정책의 변화를 함께 확인해야 하는 핵심 항목입니다.",
        "keywords": ["매출", "수익", "계약", "수익인식", "고객", "거래가격"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "operating_profit",
        "match": ["영업이익", "영업손익"],
        "category": "수익성리스크",
        "direction": "decrease",
        "why": "영업이익 감소는 본업 수익성 악화 가능성을 의미하므로 매출과 비용 구조를 함께 확인해야 합니다.",
        "keywords": ["영업이익", "영업손익", "매출원가", "판매비", "관리비", "수익성"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "net_income",
        "match": ["당기순이익", "순이익", "당기순손익"],
        "category": "수익성리스크",
        "direction": "decrease",
        "why": "당기순이익 변동은 영업성과, 금융손익, 일회성 손익의 영향을 함께 검토해야 합니다.",
        "keywords": ["당기순이익", "순이익", "법인세", "금융손익", "일회성", "손상"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
    {
        "rule_id": "operating_cash_flow",
        "match": ["영업활동현금흐름", "영업활동으로인한현금흐름"],
        "category": "현금흐름리스크",
        "direction": "decrease",
        "why": "영업활동현금흐름 감소는 이익의 현금 전환 능력과 운영자금 부담을 검토해야 하는 신호입니다.",
        "keywords": ["영업활동현금흐름", "현금흐름", "운전자본", "매출채권", "재고자산", "미지급금"],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    },
]


def read_csv_safe(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        print(f"[SKIP NOT FOUND] {path}")
        return pd.DataFrame()
    if path.stat().st_size == 0:
        print(f"[SKIP EMPTY FILE] {path}")
        return pd.DataFrame()
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except pd.errors.EmptyDataError:
            print(f"[SKIP EMPTY CSV] {path}")
            return pd.DataFrame()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"[CSV READ ERROR] {path} / {e}")
            return pd.DataFrame()
    return pd.DataFrame()


def normalize_text(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return re.sub(r"\s+", " ", str(x)).strip()


def normalize_key(x: Any) -> str:
    s = normalize_text(x)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\(\)\[\]\{\}\-_/·.,:;]", "", s)
    return s


def safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def fmt_money(x: Any) -> str:
    v = safe_float(x)
    if v is None:
        return ""
    return f"{v:,.0f}"


def fmt_rate(x: Any) -> str:
    v = safe_float(x)
    if v is None:
        return ""
    return f"{v * 100:.1f}%"


def split_sentences(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|\n+", text)
    return [normalize_text(p) for p in parts if len(normalize_text(p)) >= 12]


def collect_fulltext_df(text_dir: Path) -> pd.DataFrame:
    files = []
    for pat in ["*_notes_fulltext_data.csv", "*_audit_fulltext_data.csv", "*_correction_fulltext_data.csv"]:
        files.extend(text_dir.glob(pat))
    dfs = []
    for p in files:
        df = read_csv_safe(p)
        if not df.empty:
            df["source_file"] = p.name
            dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def collect_noun_df(text_analysis_dir: Path) -> pd.DataFrame:
    files = []
    for pat in ["*noun_data.csv", "*_noun_data.csv"]:
        files.extend(text_analysis_dir.glob(pat))
    dfs = []
    for p in files:
        df = read_csv_safe(p)
        if not df.empty:
            df["source_file"] = p.name
            dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def prepare_compare_df(compare_df: pd.DataFrame) -> pd.DataFrame:
    if compare_df.empty:
        return compare_df
    df = compare_df.copy()

    if "account_name" not in df.columns and "title" in df.columns:
        df["account_name"] = df["title"]
    if "growth_rate" not in df.columns and "growth_rate_current_previous" in df.columns:
        df["growth_rate"] = df["growth_rate_current_previous"]
    if "diff" not in df.columns and "diff_current_previous" in df.columns:
        df["diff"] = df["diff_current_previous"]

    if "account_name" not in df.columns:
        return pd.DataFrame()

    df["account_name"] = df["account_name"].astype(str).map(normalize_text)
    df = df[df["account_name"] != ""]

    if "table_name" in df.columns:
        df["table_name"] = df["table_name"].astype(str).map(normalize_text)
        df = df[df["table_name"].isin(ALLOWED_TABLES)]
        df = df[~df["table_name"].str.contains("자본변동표", na=False)]

    for c in ["current_value", "previous_value", "diff", "growth_rate"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if not {"current_value", "previous_value", "growth_rate"}.issubset(df.columns):
        return pd.DataFrame()

    df = df[df["current_value"].notna() & df["previous_value"].notna() & df["growth_rate"].notna()]
    df = df[(df["current_value"].abs() < 10**18) & (df["previous_value"].abs() < 10**18)]
    return df


def get_total_asset(compare_df: pd.DataFrame) -> Optional[float]:
    if compare_df.empty or "account_name" not in compare_df.columns:
        return None
    names = compare_df["account_name"].astype(str).map(normalize_key)
    target = compare_df[names.isin(["자산총계", "총자산"])]
    if target.empty:
        target = compare_df[names.str.contains("자산총계", na=False)]
    if target.empty:
        return None
    return safe_float(target.iloc[0].get("current_value"))


def find_account_rule(account_name: str) -> Dict[str, Any]:
    key = normalize_key(account_name)
    for rule in ACCOUNT_RULES:
        for pat in rule["match"]:
            if normalize_key(pat) in key:
                return rule
    return {
        "rule_id": "generic_numeric_change",
        "category": "수치변동",
        "direction": "both",
        "why": "전기 대비 금액 변동이 확인된 항목입니다. 금액 규모와 변동률을 기준으로 원인을 확인할 필요가 있습니다.",
        "keywords": [account_name],
        "high_growth": 0.5,
        "mid_growth": 0.2,
    }


def infer_direction(growth_rate: Optional[float]) -> str:
    if growth_rate is None:
        return "unknown"
    if growth_rate > 0:
        return "increase"
    if growth_rate < 0:
        return "decrease"
    return "flat"


def evaluate_numeric_rule(row: pd.Series, total_asset: Optional[float]) -> Dict[str, Any]:
    account = normalize_text(row.get("account_name", ""))
    current = safe_float(row.get("current_value"))
    previous = safe_float(row.get("previous_value"))
    diff = safe_float(row.get("diff"))
    growth = safe_float(row.get("growth_rate"))

    rule = find_account_rule(account)
    direction = infer_direction(growth)
    abs_growth = abs(growth) if growth is not None else 0.0
    abs_diff = abs(diff) if diff is not None else None

    direction_matched = (
        rule["direction"] == "both"
        or (rule["direction"] == "increase" and direction == "increase")
        or (rule["direction"] == "decrease" and direction == "decrease")
    )

    materiality_ratio = None
    amount_materiality = "unknown"
    if total_asset and abs_diff is not None and total_asset != 0:
        materiality_ratio = abs_diff / abs(total_asset)
        if materiality_ratio >= 0.05:
            amount_materiality = "high"
        elif materiality_ratio >= 0.02:
            amount_materiality = "mid"
        else:
            amount_materiality = "low"

    risk_level = "low"
    numeric_rule = "단순 변동"

    if direction_matched and abs_growth >= rule.get("high_growth", 0.5):
        risk_level = "high"
        numeric_rule = "계정 방향성 + 고변동률"
    elif direction_matched and abs_growth >= rule.get("mid_growth", 0.2):
        risk_level = "mid"
        numeric_rule = "계정 방향성 + 중간 변동률"
    elif abs_growth >= 1.0:
        risk_level = "mid"
        numeric_rule = "일반 고변동률"

    if amount_materiality == "high" and risk_level == "mid":
        risk_level = "high"
    elif amount_materiality == "high" and risk_level == "low":
        risk_level = "mid"

    small_base_warning = False
    if previous is not None and abs(previous) < 10_000_000_000 and abs_growth >= 5.0:
        small_base_warning = True
        if risk_level == "high":
            risk_level = "mid"
        numeric_rule += " / 전기값 작음 주의"

    return {
        "rule_id": rule["rule_id"],
        "category": rule["category"],
        "why_important": rule["why"],
        "keywords": list(dict.fromkeys(rule.get("keywords", []) + [account])),
        "direction": direction,
        "direction_matched": direction_matched,
        "abs_growth_rate": abs_growth,
        "amount_materiality": amount_materiality,
        "materiality_ratio": materiality_ratio,
        "numeric_rule": numeric_rule,
        "risk_level": risk_level,
        "small_base_warning": small_base_warning,
    }


def search_evidence_sentences(fulltext_df: pd.DataFrame, keywords: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    if fulltext_df.empty or not keywords:
        return []
    out = []
    keywords = [k for k in keywords if k]
    for _, row in fulltext_df.iterrows():
        text = normalize_text(row.get("full_text", ""))
        source_type = row.get("output_section_type", row.get("section_type", "text"))
        section_title = row.get("section_title", "")
        for sent in split_sentences(text):
            matched = [kw for kw in keywords if kw in sent]
            if matched:
                out.append({
                    "source_type": source_type,
                    "section_title": section_title,
                    "matched_keywords": matched,
                    "sentence": sent[:900],
                })
                if len(out) >= limit:
                    return out
    return out


def get_noun_context(noun_df: pd.DataFrame, keywords: List[str], limit: int = 10) -> List[Dict[str, Any]]:
    if noun_df.empty:
        return []
    word_col = next((c for c in ["단어", "word", "noun", "token"] if c in noun_df.columns), None)
    if word_col is None:
        return []

    count_col = next((c for c in ["해당단어_등장_횟수", "count", "tf", "word_count"] if c in noun_df.columns), None)
    df = noun_df.copy()
    if count_col is None:
        df["_count"] = 1
        count_col = "_count"

    df[count_col] = pd.to_numeric(df[count_col], errors="coerce").fillna(0)
    keyword_keys = [normalize_key(k) for k in keywords if k]

    related = df[
        df[word_col].astype(str).apply(
            lambda w: any(kk and (kk in normalize_key(w) or normalize_key(w) in kk) for kk in keyword_keys)
        )
    ]

    if related.empty:
        return []

    related = related.sort_values(count_col, ascending=False).head(limit)
    return [{"word": str(r[word_col]), "count": int(r[count_col])} for _, r in related.iterrows()]


def build_rule_based_item(row: pd.Series, rule_eval: Dict[str, Any], fulltext_df: pd.DataFrame, noun_df: pd.DataFrame) -> Dict[str, Any]:
    account = normalize_text(row.get("account_name", ""))
    table_name = normalize_text(row.get("table_name", ""))
    current = safe_float(row.get("current_value"))
    previous = safe_float(row.get("previous_value"))
    diff = safe_float(row.get("diff"))
    growth = safe_float(row.get("growth_rate"))

    item_id = f"{rule_eval['category']}_{normalize_key(account)}"
    item_id = re.sub(r"[^0-9A-Za-z가-힣_]+", "_", item_id)[:80]

    keywords = rule_eval["keywords"]
    text_evidence = search_evidence_sentences(fulltext_df, keywords, limit=5)
    noun_context = get_noun_context(noun_df, keywords, limit=10)

    fe = {
        "account_name": account,
        "table_name": table_name,
        "current_value": current,
        "previous_value": previous,
        "diff": diff,
        "growth_rate": growth,
        "current_value_display": fmt_money(current),
        "previous_value_display": fmt_money(previous),
        "diff_display": fmt_money(diff),
        "growth_rate_display": fmt_rate(growth),
    }

    rag_lines = [
        f"[분석 항목] {account}",
        f"- 카테고리: {rule_eval['category']}",
        f"- 표: {table_name}",
        f"- 중요 판단 룰: {rule_eval['numeric_rule']}",
        f"- 왜 중요한가: {rule_eval['why_important']}",
        f"- 방향: {rule_eval['direction']}",
        f"- 위험도: {rule_eval['risk_level']}",
        "",
        "[재무제표 수치 근거]",
        f"- 당기값: {fe['current_value_display']}",
        f"- 전기값: {fe['previous_value_display']}",
        f"- 증감액: {fe['diff_display']}",
        f"- 증감률: {fe['growth_rate_display']}",
    ]

    if rule_eval.get("amount_materiality") != "unknown":
        ratio = rule_eval.get("materiality_ratio")
        ratio_text = f"{ratio * 100:.2f}%" if ratio is not None else ""
        rag_lines.insert(7, f"- 금액 중요도: {rule_eval['amount_materiality']} ({ratio_text})")

    if rule_eval.get("small_base_warning"):
        rag_lines.append("- 주의: 전기값이 작아 증감률이 과대 표시될 수 있습니다.")

    if text_evidence:
        rag_lines.append("")
        rag_lines.append("[관련 주석/감사/정정 문장]")
        for ev in text_evidence:
            rag_lines.append(f"- ({ev['source_type']}) {ev['sentence']}")

    if noun_context:
        rag_lines.append("")
        rag_lines.append("[관련 명사 빈도]")
        rag_lines.append(", ".join([f"{n['word']}({n['count']})" for n in noun_context]))

    summary = {
        "why": rule_eval["why_important"],
        "diff": f"{account}은 당기 {fe['current_value_display']}, 전기 {fe['previous_value_display']}로 확인됩니다. 증감률은 {fe['growth_rate_display']}입니다.",
        "check": "관련 주석 문장과 계정 산정 근거를 함께 확인하세요." if text_evidence else "자동 매칭된 주석 문장이 제한적입니다. 계정명과 키워드 기준으로 원문 주석을 추가 확인하세요.",
    }

    highlight_targets = [account]
    for v in [fe["current_value_display"], fe["previous_value_display"], fe["growth_rate_display"]]:
        if v:
            highlight_targets.append(v)

    return {
        "item_id": item_id,
        "title": account,
        "source_type": "financial_numeric_rule",
        "category": rule_eval["category"],
        "risk_level": rule_eval["risk_level"],
        "risk_label": RISK_LABEL.get(rule_eval["risk_level"], "참고 검토"),
        "rule_id": rule_eval["rule_id"],
        "numeric_rule": rule_eval["numeric_rule"],
        "why_important": rule_eval["why_important"],
        "highlight_targets": highlight_targets,
        "keywords": keywords,
        "financial_evidence": fe,
        "text_evidence": text_evidence,
        "noun_context": noun_context,
        "rag_context": "\n".join(rag_lines),
        "summary": summary,
    }


def select_top_items(items: List[Dict[str, Any]], top_n: int = 12) -> List[Dict[str, Any]]:
    def score(item: Dict[str, Any]):
        risk = item.get("risk_level", "low")
        growth = safe_float(item.get("financial_evidence", {}).get("growth_rate"))
        abs_growth = abs(growth) if growth is not None else 0.0
        text_bonus = 0.5 if item.get("text_evidence") else 0.0
        return (RISK_SORT.get(risk, 9), -(abs_growth + text_bonus))

    items = sorted(items, key=score)
    seen = set()
    selected = []
    for item in items:
        key = normalize_key(item.get("title", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= top_n:
            break
    return selected


def convert_to_web_item(item: Dict[str, Any]) -> Dict[str, Any]:
    level = item.get("risk_level", "low")
    risk_class = "risk-high" if level == "high" else "risk-mid" if level == "mid" else "risk-low"
    s = item.get("summary", {})
    fe = item.get("financial_evidence", {})
    return {
        "title": item.get("title", ""),
        "riskClass": risk_class,
        "riskText": item.get("risk_label", "참고 검토"),
        "why": s.get("why", ""),
        "diff": s.get("diff", ""),
        "check": s.get("check", ""),
        "metric": "증감률",
        "metricValue": fe.get("growth_rate_display", ""),
        "ragContext": item.get("rag_context", ""),
    }


def build_rule_based_rag(job_dir: str | Path, period_type: str = "t", output_dir: Optional[str | Path] = None, top_n: int = 12) -> Dict[str, Any]:
    job_dir = Path(job_dir)
    period_dir = job_dir / period_type
    output_dir = Path(output_dir) if output_dir else period_dir / "rule_based_rag"
    output_dir.mkdir(parents=True, exist_ok=True)

    financial_dir = period_dir / "financial_structured"
    text_dir = period_dir / "text"
    text_analysis_dir = period_dir / "text_analysis"

    compare_df = read_csv_safe(financial_dir / "financial_statement_compare.csv")
    fulltext_df = collect_fulltext_df(text_dir)
    noun_df = collect_noun_df(text_analysis_dir)

    compare_df = prepare_compare_df(compare_df)
    if compare_df.empty:
        raise RuntimeError(f"재무제표 비교 데이터가 없습니다: {financial_dir / 'financial_statement_compare.csv'}")

    total_asset = get_total_asset(compare_df)

    items = []
    rule_rows = []
    for _, row in compare_df.iterrows():
        rule_eval = evaluate_numeric_rule(row, total_asset=total_asset)

        if rule_eval["risk_level"] == "low" and rule_eval["abs_growth_rate"] < 0.2:
            continue

        item = build_rule_based_item(row, rule_eval, fulltext_df, noun_df)
        items.append(item)
        fe = item["financial_evidence"]
        rule_rows.append({
            "item_id": item["item_id"],
            "account_name": item["title"],
            "table_name": fe["table_name"],
            "category": item["category"],
            "risk_level": item["risk_level"],
            "numeric_rule": item["numeric_rule"],
            "why_important": item["why_important"],
            "current_value": fe["current_value"],
            "previous_value": fe["previous_value"],
            "diff": fe["diff"],
            "growth_rate": fe["growth_rate"],
            "current_value_display": fe["current_value_display"],
            "previous_value_display": fe["previous_value_display"],
            "diff_display": fe["diff_display"],
            "growth_rate_display": fe["growth_rate_display"],
            "text_evidence_count": len(item["text_evidence"]),
            "noun_context_count": len(item["noun_context"]),
        })

    items = select_top_items(items, top_n=top_n)
    analysis_data = {item["item_id"]: convert_to_web_item(item) for item in items}

    json_path = output_dir / "rule_based_rag_items.json"
    js_path = output_dir / "rule_based_analysisData.js"
    table_path = output_dir / "rule_based_rag_table.csv"
    prompt_path = output_dir / "rule_based_rag_prompt.md"

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    js_path.write_text("const analysisData = " + json.dumps(analysis_data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    pd.DataFrame(rule_rows).to_csv(table_path, index=False, encoding="utf-8-sig")

    prompt_lines = [
        "# LLM 요약용 RAG Context",
        "",
        "아래 근거만 사용해서 사용자가 이해하기 쉬운 말투로 요약하세요.",
        "과도하게 단정하지 말고, 확인 필요/검토 필요 표현을 사용하세요.",
        "",
    ]
    for idx, item in enumerate(items, start=1):
        prompt_lines.append(f"## {idx}. {item['title']}")
        prompt_lines.append(item["rag_context"])
        prompt_lines.append("")
    prompt_path.write_text("\n".join(prompt_lines), encoding="utf-8")

    print("=" * 80)
    print("[RULE BASED RAG DONE]")
    print("items:", len(items))
    print("json:", json_path)
    print("js:", js_path)
    print("prompt:", prompt_path)
    print("table:", table_path)
    if items:
        print("sample:", items[0]["title"], items[0]["category"], items[0]["financial_evidence"]["growth_rate_display"])
    print("=" * 80)

    return {
        "items": items,
        "analysis_data": analysis_data,
        "json_path": str(json_path),
        "js_path": str(js_path),
        "prompt_path": str(prompt_path),
        "table_path": str(table_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--period-type", default="t")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    build_rule_based_rag(
        job_dir=args.job_dir,
        period_type=args.period_type,
        output_dir=args.output_dir,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    main()
