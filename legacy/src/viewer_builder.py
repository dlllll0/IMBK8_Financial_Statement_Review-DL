# -*- coding: utf-8 -*-
"""
src/viewer_builder.py

좌측 UI 구조화 문서 뷰어 데이터 생성기 v2.

목표:
1. 재무제표 탭: 재무제표를 최대한 표 형태로 제공
2. 재무제표 주석 / 감사보고서 / 정정신고 탭:
   - fulltext 전체 dump 금지
   - OCR fulltext 전처리
   - 문단/청크 단위 분리
   - ui_chunks 결과 기반 표시
3. 하이라이터:
   - HTML replace 방식 사용 금지
   - 원문 text는 그대로 유지
   - highlights는 offset 기반 start/end로 제공
   - 프론트에서 span highlight 처리

출력:
outputs/jobs/{job_id}/viewer_data/viewer_data.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from src.highlight_builder import (
    attach_highlights_to_chunks,
    build_financial_row_highlights,
)


# =========================================================
# 0. 공통 IO
# =========================================================

def read_csv_safely(path: Path | str | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()

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
        except Exception:
            return pd.DataFrame()

    return pd.DataFrame()


def read_text_safely(path: Path, limit: int = 500000) -> str:
    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return ""

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return path.read_text(encoding=enc, errors="ignore")[:limit]
        except Exception:
            continue

    return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def df_to_records(df: pd.DataFrame, max_rows: int = 500) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    df = df.copy().fillna("")
    return df.head(max_rows).to_dict(orient="records")


def to_number(x):
    try:
        if pd.isna(x):
            return 0
        v = float(x)
        if v.is_integer():
            return int(v)
        return v
    except Exception:
        return 0


# =========================================================
# 1. 카테고리/우선순위/하이라이트 로드
# =========================================================

def load_priority_map(job_dir: Path) -> Dict[str, Dict[str, Any]]:
    path = job_dir / "priority_scoring" / "priority_scoring.csv"
    df = read_csv_safely(path)

    if df.empty or "카테고리" not in df.columns:
        return {}

    out = {}

    for _, row in df.iterrows():
        category = str(row.get("카테고리", "")).strip()

        if not category:
            continue

        out[category] = {
            "priority_score": to_number(row.get("priority_score", 0)),
            "priority_level": str(row.get("priority_level", "하")),
            "created_count": to_number(row.get("created_count", 0)),
            "removed_count": to_number(row.get("removed_count", 0)),
            "changed_count": to_number(row.get("changed_count", 0)),
        }

    return out


def find_word_col(df: pd.DataFrame) -> str | None:
    candidates = [
        "단어", "명사", "word", "WORD", "noun", "NOUN", "token", "TOKEN", "키워드"
    ]

    for col in candidates:
        if col in df.columns:
            return col

    return None


def load_keyword_df_for_period(period_dir: Path) -> pd.DataFrame:
    """
    offset highlight 생성을 위한 keyword dataframe 로드.
    highlight_builder.normalize_keyword_records()에서
    단어/카테고리/priority 컬럼을 자동 인식한다.
    """
    cat_path = period_dir / "category_mapped" / "total_noun_category_mapped.csv"
    df = read_csv_safely(cat_path)

    if df.empty:
        return pd.DataFrame()

    # priority_level을 하이라이터의 priority_grade로 붙여주기
    # 없으면 highlight_builder 쪽에서 빈 값으로 처리됨.
    return df


def collect_highlights(period_dir: Path, priority_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    category_mapped 결과에서 전체 하이라이트 후보 단어 목록을 만든다.
    이 목록은 UI 전역 metadata용이고,
    실제 chunk별 offset highlights는 collect_ui_chunks()에서 생성한다.
    """
    cat_path = period_dir / "category_mapped" / "total_noun_category_mapped.csv"
    df = read_csv_safely(cat_path)

    if df.empty:
        return []

    word_col = find_word_col(df)

    if word_col is None or "카테고리" not in df.columns:
        return []

    rows = []

    for _, row in df.iterrows():
        word = str(row.get(word_col, "")).strip()
        category = str(row.get("카테고리", "")).strip()

        if not word or not category:
            continue

        if len(word) < 2:
            continue

        priority = priority_map.get(category, {})

        rows.append({
            "word": word,
            "keyword": word,
            "word_norm": str(row.get("단어_norm", "")).strip(),
            "category": category,
            "source_type": str(row.get("source_type", "")).strip(),
            "priority_score": priority.get("priority_score", 0),
            "priority_level": priority.get("priority_level", "하"),
            "priority_grade": priority.get("priority_level", "하"),
            "created_count": priority.get("created_count", 0),
            "removed_count": priority.get("removed_count", 0),
            "changed_count": priority.get("changed_count", 0),
        })

    unique = {}

    for h in rows:
        key = (h["word"], h["category"], h["source_type"])
        unique[key] = h

    result = sorted(
        unique.values(),
        key=lambda x: (x.get("priority_score", 0), len(x["word"])),
        reverse=True,
    )

    return result


def filter_keyword_df_by_source(keyword_df: pd.DataFrame, source_type: str) -> pd.DataFrame:
    """
    문서 타입별 keyword 필터링.
    source_type 컬럼이 있으면 doc_type과 맞는 것만 우선 사용.
    없거나 필터 결과가 비면 전체 사용.
    """
    if keyword_df is None or keyword_df.empty:
        return pd.DataFrame()

    if "source_type" not in keyword_df.columns:
        return keyword_df

    filtered = keyword_df[
        keyword_df["source_type"].astype(str).str.strip().eq(source_type)
    ].copy()

    if not filtered.empty:
        return filtered

    return keyword_df


# =========================================================
# 2. 재무제표 표 구조화
# =========================================================

def is_valid_csv(path: Path | None) -> bool:
    if path is None:
        return False

    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return False

    df = read_csv_safely(path)
    return df is not None and not df.empty


def is_valid_financial_table_csv(path: Path | None) -> bool:
    if path is None:
        return False

    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return False

    if path.name == "gemini_financial_tables_all.csv":
        df = read_csv_safely(path)
        return df is not None and not df.empty

    df = read_csv_safely(path)

    if df is None or df.empty:
        return False

    cols = set(df.columns)

    meta_cols = {
        "printed_start_page",
        "printed_end_page",
        "pdf_start_page",
        "pdf_end_page",
        "source",
        "gemini_model",
    }

    if len(cols & meta_cols) >= 3:
        return False

    financial_cols = {
        "table_name",
        "표구분",
        "statement_type",
        "과목",
        "항목",
        "account_name",
        "account",
        "제27기",
        "제26기",
        "제25기",
        "제24기",
        "제23기",
        "제12기",
        "제11기",
        "제10기",
        "current_value_fmt",
        "previous_value_fmt",
    }

    return len(cols & financial_cols) >= 2


def pick_financial_table_file(period_dir: Path) -> Path | None:
    candidates = [
        period_dir / "financial_gemini_structured" / "gemini_financial_tables_all.csv",
        period_dir / "financial_structured" / "financial_statement_compare.csv",
        period_dir / "financial_structured" / "financial_statement_structured.csv",
    ]

    for p in candidates:
        if is_valid_financial_table_csv(p):
            return p

    return None


def pick_financial_highlight_file(period_dir: Path) -> Path | None:
    p = period_dir / "financial_structured" / "financial_highlight_items.csv"

    if is_valid_csv(p):
        return p

    return None


def clean_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    drop_cols = {
        "job_id",
        "period_type",
        "file_name",
        "industry",
        "data_type",
        "report_type",
        "section_type",
        "section_title",
        "source_file",
    }

    keep_cols = [c for c in df.columns if c not in drop_cols]
    df = df[keep_cols]

    empty_cols = []
    for col in df.columns:
        if df[col].astype(str).str.strip().replace("nan", "").eq("").all():
            empty_cols.append(col)

    if empty_cols:
        df = df.drop(columns=empty_cols)

    preferred_cols = [
        "table_name",
        "표구분",
        "statement_type",

        "과목",
        "항목",
        "account_name",
        "account",

        "제27기",
        "제26기",
        "제25기",
        "제24기",
        "제23기",
        "제12기",
        "제11기",
        "제10기",
        "당기",
        "전기",

        "current_value_fmt",
        "previous_value_fmt",
        "diff_fmt",
        "growth_rate_fmt",
        "risk_level",
        "current_value",
        "previous_value",
        "diff",
        "growth_rate",

        "자본금",
        "자본잉여금",
        "기타자본항목",
        "기타자본구성요소",
        "이익잉여금",
        "자본 합계",
    ]

    ordered = [c for c in preferred_cols if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]

    return df[ordered + rest]


def normalize_financial_table_name(name: str) -> str:
    name = str(name or "").strip()

    if not name or name.lower() == "nan":
        return "재무제표"

    mapping = {
        "연결재무상태표": "재무상태표",
        "재무상태표": "재무상태표",
        "대차대조표": "재무상태표",

        "연결포괄손익계산서": "포괄손익계산서",
        "포괄손익계산서": "포괄손익계산서",

        "연결손익계산서": "손익계산서",
        "손익계산서": "손익계산서",

        "연결자본변동표": "자본변동표",
        "자본변동표": "자본변동표",

        "연결현금흐름표": "현금흐름표",
        "현금흐름표": "현금흐름표",

        "이익잉여금처분계산서": "이익잉여금처분계산서",
        "결손금처리계산서": "결손금처리계산서",
    }

    for key, value in mapping.items():
        if key in name:
            return value

    if len(name) > 40:
        return "재무제표"

    return name


def split_financial_tables_by_table_name(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    df = df.copy()

    group_col = None

    for col in ["table_name", "표구분", "statement_type"]:
        if col in df.columns:
            group_col = col
            break

    if group_col is None:
        return [{
            "name": "재무제표",
            "rows": df_to_records(df, max_rows=700),
            "columns": list(df.columns),
        }]

    tables = []

    for table_name, g in df.groupby(group_col, dropna=False):
        table_name = normalize_financial_table_name(table_name)
        show_df = g.copy()

        if group_col in show_df.columns:
            show_df = show_df.drop(columns=[group_col])

        empty_cols = []
        for col in show_df.columns:
            if show_df[col].astype(str).str.strip().replace("nan", "").eq("").all():
                empty_cols.append(col)

        if empty_cols:
            show_df = show_df.drop(columns=empty_cols)

        tables.append({
            "name": table_name,
            "rows": df_to_records(show_df, max_rows=400),
            "columns": list(show_df.columns),
        })

    order = {
        "재무상태표": 1,
        "대차대조표": 1,
        "포괄손익계산서": 2,
        "손익계산서": 2,
        "자본변동표": 3,
        "이익잉여금처분계산서": 3,
        "결손금처리계산서": 3,
        "현금흐름표": 4,
    }

    return sorted(tables, key=lambda x: order.get(x["name"], 99))


def load_financial_comparison_view(job_dir: Path) -> Dict[str, Any]:
    """
    financial_comparator.py가 생성한 비교/룰 분석 JSON을 읽는다.
    """
    path = job_dir / "financial_comparison" / "financial_comparison_viewer.json"

    if not path.exists():
        return {
            "summary": {},
            "comparison_table": [],
            "rule_analysis": [],
            "highlight_items": [],
            "cross_variable_analysis": [],
            "llm_prompt_blocks": [],
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "summary": {},
            "comparison_table": [],
            "rule_analysis": [],
            "highlight_items": [],
            "cross_variable_analysis": [],
            "llm_prompt_blocks": [],
        }


def collect_financial_view(period_dir: Path, job_dir: Path | None = None) -> Dict[str, Any]:
    table_path = pick_financial_table_file(period_dir)
    highlight_path = pick_financial_highlight_file(period_dir)

    table_df = read_csv_safely(table_path) if table_path else pd.DataFrame()
    highlight_df = read_csv_safely(highlight_path) if highlight_path else pd.DataFrame()

    table_df = clean_financial_df(table_df)
    highlight_df = clean_financial_df(highlight_df)

    comparison_view = load_financial_comparison_view(job_dir) if job_dir else {}

    # financial_comparator의 row highlight가 있으면 우선 사용.
    comparison_highlights = comparison_view.get("highlight_items", []) if comparison_view else []
    financial_rag_items_path = (
        job_dir
        / "financial_comparison"
        / "financial_rag_items.json"
    ) if job_dir else None

    financial_rag_items = []

    if financial_rag_items_path and financial_rag_items_path.exists() and financial_rag_items_path.stat().st_size > 0:
        try:
            with open(financial_rag_items_path, "r", encoding="utf-8") as f:
                financial_rag_items = json.load(f)
        except Exception as e:
            print("[WARN] financial_rag_items 로드 실패:", e)
            financial_rag_items = []
    return {
        "source_file": str(table_path) if table_path else "",
        "highlight_file": str(highlight_path) if highlight_path else "",
        "title": "재무제표",
        "description": "재무제표 구조화 결과입니다.",
        "tables": split_financial_tables_by_table_name(table_df),

        # 기존 highlight 파일
        "legacy_highlight_items": df_to_records(highlight_df, max_rows=80),

        # 신규 재무제표 비교/룰 기반 결과
        "comparison": comparison_view,
        "highlight_items": comparison_highlights,

        # item 기반 RAG / LLM 연동용
        "financial_rag_items": financial_rag_items,
        "financial_rag_items_file": str(financial_rag_items_path) if financial_rag_items_path else "",
    }


# =========================================================
# 3. 텍스트 전처리/청킹 보조
# =========================================================

def clean_fulltext(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"전자공시시스템\s+dart\.fss\.or\.kr", " ", text, flags=re.I)
    text = re.sub(r"Page\s+\d+", " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_chunk_for_dedup(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일", "DATE", text)
    text = re.sub(r"\d+", "N", text)
    return text[:500]


def deduplicate_chunks(chunks):
    seen = set()
    out = []

    for chunk in chunks:
        key = normalize_chunk_for_dedup(chunk.get("text", ""))

        if not key:
            continue

        if key in seen:
            continue

        seen.add(key)
        out.append(chunk)

    return out


def is_table_like_block(block: str) -> bool:
    text = block.strip()

    if not text:
        return True

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) <= 1:
        return False

    digit_chars = sum(ch.isdigit() for ch in text)
    total_chars = max(len(text), 1)
    digit_ratio = digit_chars / total_chars
    short_line_ratio = sum(1 for line in lines if len(line) <= 20) / max(len(lines), 1)
    table_tokens = ["사업연도", "구분", "감사인", "보수", "시간", "금액", "단위", "계약체결일"]
    table_token_count = sum(1 for token in table_tokens if token in text)

    if len(lines) >= 5 and digit_ratio > 0.25 and short_line_ratio > 0.45:
        return True

    if len(lines) >= 8 and table_token_count >= 3 and short_line_ratio > 0.5:
        return True

    return False


def split_into_blocks(text: str) -> List[str]:
    text = clean_fulltext(text)

    if not text:
        return []

    raw_blocks = re.split(r"\n\s*\n+", text)
    blocks = []

    for block in raw_blocks:
        block = block.strip()

        if not block:
            continue

        if len(block) > 1400:
            sentences = re.split(r"\s{2,}|\n+", block)
            buf = ""

            for sent in sentences:
                sent = sent.strip()

                if not sent:
                    continue

                if len(buf) + len(sent) > 900:
                    if buf.strip():
                        blocks.append(buf.strip())
                    buf = sent
                else:
                    buf += " " + sent

            if buf.strip():
                blocks.append(buf.strip())
        else:
            blocks.append(block)

    return blocks


def contains_any_keyword(text: str, keywords: List[str]) -> bool:
    if not text or not keywords:
        return False

    for kw in keywords:
        if kw and kw in text:
            return True

    return False


def extract_relevant_blocks(text: str, highlights: List[Dict[str, Any]], max_blocks: int = 40) -> List[Dict[str, Any]]:
    """
    구버전 호환용.
    현재 메인 뷰어는 ui_chunks.csv 기반 collect_ui_chunks()를 사용한다.
    """
    blocks = split_into_blocks(text)

    keywords = sorted(
        list({h.get("word", "") for h in highlights if h.get("word") and len(h.get("word", "")) >= 2}),
        key=len,
        reverse=True,
    )

    relevant = []

    for block in blocks:
        if is_table_like_block(block):
            continue

        hit_words = [kw for kw in keywords if kw in block]

        if not hit_words:
            continue

        matched_highlights = []

        for h in highlights:
            word = h.get("word", "")

            if word and word in block:
                matched_highlights.append(h)

        relevant.append({
            "text": block,
            "separator": "---",
            "hit_words": sorted(list(set(hit_words))),
            "categories": sorted(list({h.get("category", "") for h in matched_highlights if h.get("category")})),
            "highlights": matched_highlights[:20],
        })

    relevant = deduplicate_chunks(relevant)
    return relevant[:max_blocks]


def find_text_col(df: pd.DataFrame) -> str | None:
    candidates = [
        "text", "fulltext", "full_text", "content", "본문", "텍스트", "문장",
        "raw_text", "page_text", "notes_fulltext", "audit_fulltext", "correction_fulltext",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    for col in df.columns:
        try:
            sample_len = df[col].astype(str).head(20).str.len().mean()
            if sample_len and sample_len > 80:
                return col
        except Exception:
            continue

    return None


def infer_doc_type_from_name(name: str) -> str:
    lower = name.lower()

    if "audit" in lower or "감사" in name:
        return "audit"

    if "correction" in lower or "정정" in name:
        return "correction"

    if "notes" in lower or "note" in lower or "주석" in name:
        return "notes"

    return "unknown"


def doc_title(doc_type: str) -> str:
    return {
        "notes": "재무제표 주석",
        "audit": "감사보고서",
        "correction": "정정신고",
        "unknown": "기타 문서",
    }.get(doc_type, "기타 문서")


# =========================================================
# 4. ui_chunks 기반 문서 뷰어 생성
# =========================================================

def collect_ui_chunks(period_dir: Path) -> Dict[str, Any]:
    result = {
        "notes": {"title": "재무제표 주석", "sections": []},
        "audit": {"title": "감사보고서", "sections": []},
        "correction": {"title": "정정신고", "sections": []},
    }

    chunk_dir = period_dir / "ui_chunks"
    keyword_df_all = load_keyword_df_for_period(period_dir)

    for doc_type in ["notes", "audit", "correction"]:
        path = chunk_dir / f"{doc_type}_ui_chunks.csv"
        df = read_csv_safely(path)

        if df.empty:
            continue

        keyword_df = filter_keyword_df_by_source(keyword_df_all, doc_type)

        # 핵심: 원문 text는 유지하고 highlights offset만 추가
        try:
            df = attach_highlights_to_chunks(
                chunk_df=df,
                keyword_df=keyword_df,
                text_col="chunk_text",
                allow_overlap=False,
                max_highlights_per_chunk=100,
            )
        except Exception as e:
            print(f"[WARN] attach_highlights_to_chunks 실패: {doc_type} / {e}")
            df["highlights"] = [[] for _ in range(len(df))]
            df["highlight_count"] = 0

        for section_title, g in df.groupby("section_title", dropna=False):
            chunks = []

            for _, row in g.iterrows():
                text = row.get("chunk_text", "")
                highlights = row.get("highlights", [])

                if not isinstance(highlights, list):
                    highlights = []

                hit_words = [
                    h.get("keyword", "")
                    for h in highlights
                    if isinstance(h, dict) and h.get("keyword")
                ]

                categories = [
                    h.get("category", "")
                    for h in highlights
                    if isinstance(h, dict) and h.get("category")
                ]

                chunks.append({
                    "text": text,
                    "separator": "---",
                    "hit_words": list(dict.fromkeys(hit_words)),
                    "categories": list(dict.fromkeys(categories)),
                    "highlights": highlights,
                    "highlight_count": len(highlights),
                })

            result[doc_type]["sections"].append({
                "heading": str(section_title),
                "chunks": chunks,
            })

    return result


# =========================================================
# 5. period viewer 생성
# =========================================================

def build_period_viewer(period_dir: Path, priority_map: Dict[str, Dict[str, Any]], job_dir: Path) -> Dict[str, Any]:
    highlights = collect_highlights(period_dir, priority_map)
    financial = collect_financial_view(period_dir, job_dir=job_dir)
    docs = collect_ui_chunks(period_dir)

    return {
        "period": period_dir.name,
        "period_dir": str(period_dir),
        "financial": financial,
        "documents": docs,
        "highlights": highlights,
    }


# =========================================================
# 6. main runner
# =========================================================

def count_section_chunks(doc: dict) -> int:
    total = 0

    for section in doc.get("sections", []):
        total += len(section.get("chunks", []))

    return total


def run_viewer_builder(job_dir: Path | str) -> Dict[str, Any]:
    job_dir = Path(job_dir)

    output_dir = job_dir / "viewer_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "viewer_data.json"
    priority_map = load_priority_map(job_dir)

    # =========================================================
    # LLM SUMMARY LOAD
    # =========================================================
    llm_summary_path = (
        job_dir
        / "financial_comparison"
        / "llm_summary.json"
    )

    llm_summary = []

    if llm_summary_path.exists() and llm_summary_path.stat().st_size > 0:
        try:
            with open(llm_summary_path, "r", encoding="utf-8") as f:
                llm_summary = json.load(f)
        except Exception as e:
            print("[WARN] llm_summary 로드 실패:", e)
            llm_summary = []

    text_llm_summary_path = (
        job_dir
        / "rag_context"
        / "text_llm_summary.json"
    )

    text_llm_summary = []

    if text_llm_summary_path.exists() and text_llm_summary_path.stat().st_size > 0:
        try:
            with open(text_llm_summary_path, "r", encoding="utf-8") as f:
                text_llm_summary = json.load(f)
        except Exception as e:
            print("[WARN] text_llm_summary 로드 실패:", e)
            text_llm_summary = []

    viewer_data = {
        "job_id": job_dir.name,
        "job_dir": str(job_dir),

        "tabs": [
            {"key": "financial", "label": "재무제표"},
            {"key": "notes", "label": "재무제표 주석"},
            {"key": "audit", "label": "감사보고서"},
            {"key": "correction", "label": "정정신고"},
        ],

        "t": build_period_viewer(
            job_dir / "t",
            priority_map,
            job_dir=job_dir,
        ),

        "t_minus_1": build_period_viewer(
            job_dir / "t_minus_1",
            priority_map,
            job_dir=job_dir,
        ),

        # =====================================================
        # LLM SUMMARY
        # =====================================================
        "llm_summary": llm_summary,
        "llm_summary_file": str(llm_summary_path),

        "financial_llm_summary": llm_summary,
        "financial_llm_summary_file": str(llm_summary_path),

        "text_llm_summary": text_llm_summary,
        "text_llm_summary_file": str(text_llm_summary_path),
    }

    write_json(output_path, viewer_data)

    print("[VIEWER BUILDER DONE]")
    print("JSON:", output_path)

    return {
        "status": "success",
        "output_path": str(output_path),

        "llm_summary_count": len(llm_summary),
        "text_llm_summary_count": len(text_llm_summary),

        "t_financial_tables": len(viewer_data["t"]["financial"].get("tables", [])),
        "t_financial_highlights": len(viewer_data["t"]["financial"].get("highlight_items", [])),
        "t_highlights": len(viewer_data["t"].get("highlights", [])),
        "t_notes_chunks": count_section_chunks(viewer_data["t"]["documents"]["notes"]),
        "t_audit_chunks": count_section_chunks(viewer_data["t"]["documents"]["audit"]),
        "t_correction_chunks": count_section_chunks(viewer_data["t"]["documents"]["correction"]),

        "t_minus_1_financial_tables": len(viewer_data["t_minus_1"]["financial"].get("tables", [])),
        "t_minus_1_financial_highlights": len(viewer_data["t_minus_1"]["financial"].get("highlight_items", [])),
        "t_minus_1_highlights": len(viewer_data["t_minus_1"].get("highlights", [])),
        "t_minus_1_notes_chunks": count_section_chunks(viewer_data["t_minus_1"]["documents"]["notes"]),
        "t_minus_1_audit_chunks": count_section_chunks(viewer_data["t_minus_1"]["documents"]["audit"]),
        "t_minus_1_correction_chunks": count_section_chunks(viewer_data["t_minus_1"]["documents"]["correction"]),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_e674e5fc"

    run_viewer_builder(TEST_JOB_DIR)
