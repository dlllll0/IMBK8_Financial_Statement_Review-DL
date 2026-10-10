# -*- coding: utf-8 -*-
"""
src/highlight_builder.py

문자열 offset 기반 하이라이트 생성기.

- 원문 text는 그대로 유지
- 하이라이트 정보는 start/end offset으로 별도 저장
- 프론트에서 offset을 이용해 span 렌더링
"""

from __future__ import annotations

import re
from typing import Dict, List, Any

import pandas as pd


def normalize_keyword_records(keyword_df: pd.DataFrame) -> List[Dict[str, Any]]:
    if keyword_df is None or keyword_df.empty:
        return []

    df = keyword_df.copy()
    df.columns = df.columns.astype(str).str.strip()

    keyword_col = None
    category_col = None
    priority_col = None

    for c in ["단어", "keyword", "word", "명사", "키워드"]:
        if c in df.columns:
            keyword_col = c
            break

    for c in ["카테고리", "category", "risk_category", "리스크카테고리"]:
        if c in df.columns:
            category_col = c
            break

    for c in ["priority_grade", "우선순위", "등급", "priority"]:
        if c in df.columns:
            priority_col = c
            break

    if not keyword_col:
        return []

    records = []

    for _, row in df.iterrows():
        keyword = str(row.get(keyword_col, "")).strip()

        if not keyword or keyword.lower() == "nan":
            continue

        if len(keyword) < 2:
            continue

        category = str(row.get(category_col, "")).strip() if category_col else ""
        priority_grade = str(row.get(priority_col, "")).strip() if priority_col else ""

        records.append({
            "keyword": keyword,
            "category": category,
            "priority_grade": priority_grade,
        })

    # 긴 단어 우선: 리스채권 > 채권
    records = sorted(records, key=lambda x: len(str(x["keyword"])), reverse=True)

    seen = set()
    unique = []

    for r in records:
        key = (r["keyword"], r.get("category", ""))

        if key in seen:
            continue

        seen.add(key)
        unique.append(r)

    return unique


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def has_overlap(start: int, end: int, spans: List[Dict[str, Any]]) -> bool:
    for span in spans:
        if overlaps(start, end, int(span["start"]), int(span["end"])):
            return True
    return False

def deduplicate_highlights(highlights):
    seen = set()
    deduped = []

    for h in highlights:
        key = (
            h.get("start"),
            h.get("end"),
            h.get("keyword"),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(h)

    return deduped


def remove_overlapping_highlights(highlights):
    if not highlights:
        return []

    highlights = sorted(
        highlights,
        key=lambda x: (
            x["start"],
            -(x["end"] - x["start"])
        )
    )

    result = []
    current_end = -1

    for h in highlights:
        if h["start"] < current_end:
            continue

        result.append(h)
        current_end = h["end"]

    return result


def build_offset_highlights(
    text: str,
    keyword_records: List[Dict[str, Any]],
    allow_overlap: bool = False,
    max_highlights: int = 100,
) -> List[Dict[str, Any]]:
    text = str(text or "")

    if not text:
        return []

    highlights = []

    for record in keyword_records:
        keyword = str(record.get("keyword", "")).strip()

        if not keyword:
            continue

        for match in re.finditer(re.escape(keyword), text):
            start = match.start()
            end = match.end()

            if not allow_overlap and has_overlap(start, end, highlights):
                continue

            highlights.append({
                "start": start,
                "end": end,
                "keyword": keyword,
                "category": record.get("category", ""),
                "priority_grade": record.get("priority_grade", ""),
            })

            if len(highlights) >= max_highlights:
                break

        if len(highlights) >= max_highlights:
            break

    highlights = deduplicate_highlights(highlights)
    highlights = remove_overlapping_highlights(highlights)

    return sorted(highlights, key=lambda x: (x["start"], x["end"]))

def attach_highlights_to_text(
    text: str,
    keyword_records: List[Dict[str, Any]],
    allow_overlap: bool = False,
    max_highlights: int = 100,
) -> Dict[str, Any]:
    text = str(text or "")

    return {
        "text": text,
        "highlights": build_offset_highlights(
            text=text,
            keyword_records=keyword_records,
            allow_overlap=allow_overlap,
            max_highlights=max_highlights,
        ),
    }


def attach_highlights_to_chunks(
    chunk_df: pd.DataFrame,
    keyword_df: pd.DataFrame,
    text_col: str = "chunk_text",
    allow_overlap: bool = False,
    max_highlights_per_chunk: int = 100,
) -> pd.DataFrame:
    if chunk_df is None or chunk_df.empty:
        return pd.DataFrame()

    if text_col not in chunk_df.columns:
        raise ValueError(f"{text_col} 컬럼이 없습니다. columns={list(chunk_df.columns)}")

    keyword_records = normalize_keyword_records(keyword_df)

    out = chunk_df.copy()
    all_highlights = []
    counts = []

    for _, row in out.iterrows():
        text = str(row.get(text_col, ""))

        highlights = build_offset_highlights(
            text=text,
            keyword_records=keyword_records,
            allow_overlap=allow_overlap,
            max_highlights=max_highlights_per_chunk,
        )

        all_highlights.append(highlights)
        counts.append(len(highlights))

    out["highlights"] = all_highlights
    out["highlight_count"] = counts

    return out


def build_financial_row_highlights(rule_df: pd.DataFrame) -> List[Dict[str, Any]]:
    if rule_df is None or rule_df.empty:
        return []

    items = []

    for idx, row in rule_df.iterrows():
        priority_grade = str(row.get("priority_grade", "하"))

        if priority_grade not in ["상", "중"]:
            continue

        items.append({
            "row_index": int(idx),
            "table_name": row.get("table_name", ""),
            "account_name": row.get("계정과목", ""),
            "risk_category": row.get("risk_category", ""),
            "risk_tags": row.get("risk_tags", ""),
            "priority_score": int(row.get("priority_score", 0) or 0),
            "priority_grade": priority_grade,
            "analysis_method": row.get("analysis_method", ""),
            "check_points": row.get("check_points", ""),
            "benchmark_hint": row.get("benchmark_hint", ""),
            "interpretation": row.get("interpretation", ""),
        })

    return items


def render_highlight_html_for_debug(text: str, highlights: List[Dict[str, Any]]) -> str:
    text = str(text or "")

    if not highlights:
        return text

    highlights = sorted(highlights, key=lambda x: x["start"])

    parts = []
    last = 0

    for h in highlights:
        start = int(h["start"])
        end = int(h["end"])

        if start < last:
            continue

        parts.append(text[last:start])
        keyword_text = text[start:end]
        category = str(h.get("category", ""))
        grade = str(h.get("priority_grade", ""))

        parts.append(
            f'<span class="risk-highlight" data-category="{category}" data-priority="{grade}">'
            f'{keyword_text}</span>'
        )

        last = end

    parts.append(text[last:])
    return "".join(parts)

