# -*- coding: utf-8 -*-
"""
rag_visualizer_fixed.py

financial_structured 결과와 text/text_analysis 결과를 묶어서 하이라이트/RAG 확인용 JSON, JS, HTML을 생성한다.

핵심 수정점
- 빈 CSV skip
- 빈 account_name/title 제거
- 재무상태표/손익계산서/포괄손익계산서만 사용
- 비정상 초대형 값 제거
- highlight_items의 컬럼명을 우선 사용
- 관련 주석 문장은 계정명/키워드 매칭된 것만 첨부
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ALLOWED_TABLES = {
    "재무상태표", "손익계산서", "포괄손익계산서",
    "연결재무상태표", "연결손익계산서", "연결포괄손익계산서",
}

RISK_LABEL = {
    "high": "중점 검토 항목",
    "mid": "주의 검토",
    "medium": "주의 검토",
    "low": "참고 검토",
}

KEYWORDS_BY_ACCOUNT = {
    "매출채권": ["매출채권", "대손", "손상", "회수", "신용위험", "기대신용손실", "거래처"],
    "기타채권": ["기타채권", "대손", "손상", "회수", "신용위험"],
    "재고자산": ["재고자산", "평가손실", "순실현가능가치", "손상", "폐기"],
    "현금": ["현금", "현금성자산", "유동성", "자금", "차입"],
    "차입금": ["차입금", "단기차입금", "장기차입금", "이자율", "담보", "만기", "유동성"],
    "부채": ["부채", "충당부채", "우발부채", "약정", "소송"],
    "공정가치": ["공정가치", "평가", "수준", "측정", "금융상품"],
    "매출": ["매출", "수익", "계약", "수익인식"],
    "영업이익": ["영업이익", "영업손익", "영업활동"],
    "당기순이익": ["당기순이익", "순손익", "법인세"],
}


def read_csv_safe(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
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
    if x is None or pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def normalize_key(x: Any) -> str:
    s = normalize_text(x)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\(\)\[\]\{\}\-_/·.,:;]", "", s)
    return s


def safe_float(x: Any) -> Optional[float]:
    if x is None or pd.isna(x):
        return None
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


def infer_keywords(account_name: str) -> List[str]:
    key = normalize_key(account_name)
    kws: List[str] = []
    for pat, values in KEYWORDS_BY_ACCOUNT.items():
        if normalize_key(pat) in key:
            kws.extend(values)
    if not kws and account_name:
        kws.append(account_name)
        compact = normalize_key(account_name)
        if compact and compact != account_name:
            kws.append(compact)
    return list(dict.fromkeys([k for k in kws if k]))


def split_sentences(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|\n+", text)
    return [normalize_text(p) for p in parts if len(normalize_text(p)) >= 12]


def search_evidence_sentences(fulltext_df: pd.DataFrame, keywords: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    if fulltext_df.empty or not keywords:
        return []
    out: List[Dict[str, Any]] = []
    for _, row in fulltext_df.iterrows():
        text = normalize_text(row.get("full_text", ""))
        for sent in split_sentences(text):
            matched = [kw for kw in keywords if kw and kw in sent]
            if matched:
                out.append({
                    "source_type": row.get("output_section_type", row.get("section_type", "text")),
                    "section_title": row.get("section_title", ""),
                    "matched_keywords": matched,
                    "sentence": sent[:800],
                })
                if len(out) >= limit:
                    return out
    return out


def collect_fulltext_df(text_dir: Path) -> pd.DataFrame:
    files: List[Path] = []
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
    files: List[Path] = []
    for pat in ["*noun_data.csv", "*_noun_data.csv"]:
        files.extend(text_analysis_dir.glob(pat))
    dfs = []
    for p in files:
        df = read_csv_safe(p)
        if not df.empty:
            df["source_file"] = p.name
            dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


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
    related = df[df[word_col].astype(str).apply(lambda w: any(k in w or w in k for k in keywords))]
    if related.empty:
        return []
    related = related.sort_values(count_col, ascending=False).head(limit)
    return [{"word": str(r[word_col]), "count": int(r[count_col])} for _, r in related.iterrows()]


def prepare_base_df(compare_df: pd.DataFrame, highlight_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    # highlight가 있으면 title/account/value 컬럼이 이미 정리되어 있으므로 우선 사용
    base = highlight_df.copy() if not highlight_df.empty else compare_df.copy()
    if base.empty:
        return base

    # 컬럼 표준화
    if "title" not in base.columns and "account_name" in base.columns:
        base["title"] = base["account_name"]
    if "account_name" not in base.columns and "title" in base.columns:
        base["account_name"] = base["title"]

    if "growth_rate" not in base.columns and "growth_rate_current_previous" in base.columns:
        base["growth_rate"] = base["growth_rate_current_previous"]
    if "diff" not in base.columns and "diff_current_previous" in base.columns:
        base["diff"] = base["diff_current_previous"]

    base["account_name"] = base["account_name"].astype(str).map(normalize_text)
    base = base[base["account_name"] != ""]

    if "table_name" in base.columns:
        base = base[base["table_name"].isin(ALLOWED_TABLES)]

    for c in ["current_value", "previous_value"]:
        if c in base.columns:
            base[c] = pd.to_numeric(base[c], errors="coerce")
    if {"current_value", "previous_value"}.issubset(base.columns):
        base = base[base["current_value"].notna() & base["previous_value"].notna()]
        base = base[(base["current_value"].abs() < 10**18) & (base["previous_value"].abs() < 10**18)]

    if "risk_level" in base.columns:
        order = {"high": 0, "mid": 1, "medium": 1, "low": 2}
        base["_risk_sort"] = base["risk_level"].map(order).fillna(9)
    else:
        base["_risk_sort"] = 9
    if "growth_rate" in base.columns:
        base["_abs_growth"] = base["growth_rate"].apply(lambda x: abs(safe_float(x) or 0))
    else:
        base["_abs_growth"] = 0

    # 전기값이 너무 작아서 증감률이 폭주하는 항목은 메인 RAG에서 제외
    if "previous_value" in base.columns:
        base = base[
            (base["previous_value"].abs() >= 10_000_000_000) &
            (base["_abs_growth"] <= 5.0)
        ].copy()

    base = base.sort_values(["_risk_sort", "_abs_growth"], ascending=[True, False]).head(top_n)
    return base.drop(columns=[c for c in ["_risk_sort", "_abs_growth"] if c in base.columns])


def build_item(row: pd.Series, fulltext_df: pd.DataFrame, noun_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    account = normalize_text(row.get("account_name", row.get("title", "")))
    if not account:
        return None

    item_id = normalize_text(row.get("item_id", "")) or normalize_key(account) or "item"
    risk_level = normalize_text(row.get("risk_level", "low")) or "low"
    if risk_level == "medium":
        risk_level = "mid"

    current = row.get("current_value", None)
    previous = row.get("previous_value", None)
    diff = row.get("diff", row.get("diff_current_previous", None))
    growth = row.get("growth_rate", row.get("growth_rate_current_previous", None))

    keywords = infer_keywords(account)
    evidence = search_evidence_sentences(fulltext_df, keywords, limit=5)
    noun_context = get_noun_context(noun_df, keywords, limit=10)

    fe = {
        "account_name": account,
        "table_name": row.get("table_name", ""),
        "current_value": safe_float(current),
        "previous_value": safe_float(previous),
        "diff": safe_float(diff),
        "growth_rate": safe_float(growth),
        "current_value_display": row.get("current_value_fmt", "") or fmt_money(current),
        "previous_value_display": row.get("previous_value_fmt", "") or fmt_money(previous),
        "diff_display": row.get("diff_fmt", "") or fmt_money(diff),
        "growth_rate_display": row.get("growth_rate_fmt", "") or fmt_rate(growth),
    }

    rag_parts = [f"[재무제표 수치] 계정: {account}"]
    if fe["table_name"]:
        rag_parts.append(f"- 표: {fe['table_name']}")
    if fe["current_value_display"]:
        rag_parts.append(f"- 당기값: {fe['current_value_display']}")
    if fe["previous_value_display"]:
        rag_parts.append(f"- 전기값: {fe['previous_value_display']}")
    if fe["diff_display"]:
        rag_parts.append(f"- 증감액: {fe['diff_display']}")
    if fe["growth_rate_display"]:
        rag_parts.append(f"- 증감률: {fe['growth_rate_display']}")

    if evidence:
        rag_parts.append("\n[관련 주석/감사 문장]")
        for ev in evidence:
            rag_parts.append(f"- ({ev['source_type']}) {ev['sentence']}")

    if noun_context:
        rag_parts.append("\n[관련 명사 빈도]")
        rag_parts.append(", ".join([f"{n['word']}({n['count']})" for n in noun_context]))

    # 가라 요약이지만 빈 제목 방지 + 근거 중심
    growth_display = fe["growth_rate_display"] or "변화율 계산 불가"
    direction = "증가" if (fe["growth_rate"] is not None and fe["growth_rate"] > 0) else "감소"
    summary = {
        "why": f"{account}은 전기 대비 변동이 확인된 계정입니다. 변화폭이 크거나 위험 키워드와 연결될 경우 우선 검토 대상입니다.",
        "diff": f"당기 {fe['current_value_display']}, 전기 {fe['previous_value_display']}로 확인됩니다. 증감률은 {growth_display}이며 방향은 {direction}입니다.",
        "check": "관련 주석 문장, 계정 산정 근거, 회수가능성/손상/공정가치 등 연결 키워드를 함께 확인하세요." if evidence else "현재 자동 매칭된 주석 문장은 제한적입니다. 해당 계정명을 기준으로 원문 주석을 추가 확인하세요.",
    }

    highlight_targets = [account]
    if fe["current_value_display"]:
        highlight_targets.append(fe["current_value_display"])
    if fe["previous_value_display"]:
        highlight_targets.append(fe["previous_value_display"])

    return {
        "item_id": item_id,
        "title": account,
        "risk_level": risk_level,
        "risk_label": RISK_LABEL.get(risk_level, "참고 검토"),
        "highlight_targets": highlight_targets,
        "keywords": keywords,
        "financial_evidence": fe,
        "text_evidence": evidence,
        "noun_context": noun_context,
        "rag_context": "\n".join(rag_parts),
        "summary": summary,
    }


def convert_to_web_item(item: Dict[str, Any]) -> Dict[str, Any]:
    level = item.get("risk_level", "low")
    risk_class = "risk-high" if level == "high" else "risk-mid" if level in {"mid", "medium"} else "risk-low"
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


def build_preview_html(items: List[Dict[str, Any]], html_path: Path) -> None:
    cards = []
    for item in items:
        cards.append(f"""
        <button class='pill' onclick='selectItem("{item['item_id']}")'>{item['title']} · {item['risk_label']}</button>
        """)
    html = f"""<!doctype html>
<html lang='ko'>
<head><meta charset='utf-8'><title>RAG Preview</title>
<style>
body{{font-family:Arial,'Malgun Gothic',sans-serif;background:#f4f7fb;margin:0;padding:24px;color:#1f2937}}
.wrap{{display:grid;grid-template-columns:360px 1fr;gap:20px}}
.panel{{background:white;border:1px solid #d8e0ea;border-radius:14px;padding:18px}}
.pill{{display:block;width:100%;text-align:left;margin:8px 0;padding:10px 12px;border:1px solid #d8e0ea;background:white;border-radius:999px;cursor:pointer;font-weight:700}}
.pill:hover{{background:#e8f1ff}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:16px;border-radius:12px;line-height:1.6}}
.badge{{display:inline-block;padding:5px 9px;border-radius:999px;background:#fee2e2;color:#dc2626;font-weight:800;font-size:12px}}
</style></head>
<body>
<h1>RAG Preview</h1>
<div class='wrap'>
  <div class='panel'>{''.join(cards)}</div>
  <div class='panel'><h2 id='title'></h2><span id='badge' class='badge'></span><h3>요약</h3><div id='summary'></div><h3>RAG Context</h3><pre id='ctx'></pre></div>
</div>
<script>
const items = {json.dumps({item['item_id']: item for item in items}, ensure_ascii=False, indent=2)};
function selectItem(id){{
  const item = items[id]; if(!item) return;
  document.getElementById('title').textContent = item.title;
  document.getElementById('badge').textContent = item.risk_label;
  document.getElementById('summary').innerHTML = `<p><b>왜:</b> ${{item.summary.why}}</p><p><b>변화:</b> ${{item.summary.diff}}</p><p><b>확인:</b> ${{item.summary.check}}</p>`;
  document.getElementById('ctx').textContent = item.rag_context;
}}
selectItem(Object.keys(items)[0]);
</script>
</body></html>"""
    html_path.write_text(html, encoding="utf-8")


def build_rag_items(job_dir: Path, period_type: str = "t", output_dir: Optional[Path] = None, top_n: int = 12) -> Dict[str, Any]:
    period_dir = job_dir / period_type
    if output_dir is None:
        output_dir = period_dir / "rag_preview"
    output_dir.mkdir(parents=True, exist_ok=True)

    financial_dir = period_dir / "financial_structured"
    text_dir = period_dir / "text"
    text_analysis_dir = period_dir / "text_analysis"

    compare_df = read_csv_safe(financial_dir / "financial_statement_compare.csv")
    highlight_df = read_csv_safe(financial_dir / "financial_highlight_items.csv")
    fulltext_df = collect_fulltext_df(text_dir)
    noun_df = collect_noun_df(text_analysis_dir)

    base_df = prepare_base_df(compare_df, highlight_df, top_n=top_n)
    if base_df.empty:
        raise RuntimeError(f"RAG item으로 쓸 정상 재무제표 항목이 없습니다. financial_structurer 결과를 확인하세요: {financial_dir}")

    items: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for _, row in base_df.iterrows():
        item = build_item(row, fulltext_df, noun_df)
        if item is None:
            continue
        base_id = item["item_id"]
        seen[base_id] = seen.get(base_id, 0) + 1
        if seen[base_id] > 1:
            item["item_id"] = f"{base_id}_{seen[base_id]}"
        items.append(item)

    json_path = output_dir / "rag_items.json"
    js_path = output_dir / "analysisData.js"
    html_path = output_dir / "rag_preview.html"

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    analysis_data = {item["item_id"]: convert_to_web_item(item) for item in items}
    js_path.write_text("const analysisData = " + json.dumps(analysis_data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    build_preview_html(items, html_path)

    print("=" * 80)
    print("[RAG VISUALIZER DONE]")
    print("items:", len(items))
    print("json:", json_path)
    print("js:", js_path)
    print("html:", html_path)
    if items:
        print("sample:", items[0]["title"], items[0]["financial_evidence"].get("growth_rate_display"))
    print("=" * 80)
    return {"items": items, "json_path": str(json_path), "js_path": str(js_path), "html_path": str(html_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--period-type", default="t")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    build_rag_items(
        job_dir=Path(args.job_dir),
        period_type=args.period_type,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    main()
