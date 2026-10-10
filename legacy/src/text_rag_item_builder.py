# -*- coding: utf-8 -*-
"""
src/text_rag_item_builder.py

Text 분석 output들을 단순 카테고리 단위가 아니라,
카테고리 + 대표 키워드 + 실제 근거 문단 + 전기/당기 변화량 단위의
LLM 입력용 RAG item으로 재구성한다.

입력:
- outputs/jobs/{job_id}/rag_context/rag_context.json
- outputs/jobs/{job_id}/change_analysis/category_change_table.csv
- outputs/jobs/{job_id}/analysis_result/analysis_result.csv
- outputs/jobs/{job_id}/t/category_mapped/total_noun_category_mapped.csv
- outputs/jobs/{job_id}/t_minus_1/category_mapped/total_noun_category_mapped.csv
- outputs/jobs/{job_id}/t/ui_chunks/*_ui_chunks.csv

출력:
- outputs/jobs/{job_id}/rag_context/text_rag_items.json
- outputs/jobs/{job_id}/rag_context/text_rag_items.csv
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


DOC_TYPES = ["notes", "audit", "correction"]


def read_json_safely(path: Path, default=None):
    if default is None:
        default = []
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
        except Exception:
            return pd.DataFrame()

    return pd.DataFrame()


def safe_int(x, default: int = 0) -> int:
    try:
        if x == "" or pd.isna(x):
            return default
        return int(float(str(x).replace(",", "")))
    except Exception:
        return default


def safe_float(x, default: float = 0.0) -> float:
    try:
        if x == "" or pd.isna(x):
            return default
        return float(str(x).replace(",", "").replace("%", ""))
    except Exception:
        return default


def norm_text(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def make_id(*parts: str) -> str:
    raw = "_".join([str(p) for p in parts if str(p).strip()])
    raw = re.sub(r"[^0-9A-Za-z가-힣_]+", "", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw[:120] or "text_item"


def find_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def load_rag_context(job_dir: Path) -> List[Dict[str, Any]]:
    data = read_json_safely(job_dir / "rag_context" / "rag_context.json", default=[])
    if isinstance(data, dict):
        data = data.get("items", [])
    return [x for x in data if isinstance(x, dict)]


def load_category_mapped(job_dir: Path) -> pd.DataFrame:
    frames = []
    for period in ["t", "t_minus_1"]:
        path = job_dir / period / "category_mapped" / "total_noun_category_mapped.csv"
        df = read_csv_safely(path)
        if not df.empty:
            df["period"] = period
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True).fillna("")

    word_col = find_col(df, ["단어", "word", "keyword", "명사", "token"])
    cat_col = find_col(df, ["카테고리", "category", "risk_category"])

    if word_col is None or cat_col is None:
        return pd.DataFrame()

    df["_word"] = df[word_col].astype(str).str.strip()
    df["_category"] = df[cat_col].astype(str).str.strip()
    df["_source_type"] = df.get("source_type", "").astype(str).str.strip() if "source_type" in df.columns else ""
    df = df[(df["_word"] != "") & (df["_category"] != "")].copy()
    return df


def load_category_change(job_dir: Path) -> pd.DataFrame:
    return read_csv_safely(job_dir / "change_analysis" / "category_change_table.csv")


def load_analysis_result(job_dir: Path) -> pd.DataFrame:
    return read_csv_safely(job_dir / "analysis_result" / "analysis_result.csv")


def load_ui_chunks(job_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for doc_type in DOC_TYPES:
        path = job_dir / "t" / "ui_chunks" / f"{doc_type}_ui_chunks.csv"
        df = read_csv_safely(path)
        if df.empty:
            continue

        for _, r in df.iterrows():
            text = str(r.get("chunk_text", "")).strip()
            if not text:
                continue
            rows.append({
                "doc_type": str(r.get("doc_type", doc_type) or doc_type),
                "section_title": str(r.get("section_title", "")),
                "chunk_id": str(r.get("chunk_id", "")),
                "text": text,
            })

    return rows


def get_base_by_category(rag_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for item in rag_items:
        category = str(item.get("카테고리", item.get("category", ""))).strip()
        if not category:
            continue
        prev = out.get(category)
        if prev is None or safe_float(item.get("priority_score", 0)) > safe_float(prev.get("priority_score", 0)):
            out[category] = item
    return out


def lookup_row_by_category(df: pd.DataFrame, category: str) -> Dict[str, Any]:
    if df.empty or "카테고리" not in df.columns:
        return {}
    sub = df[df["카테고리"].astype(str).str.strip().eq(category)]
    if sub.empty:
        return {}
    return sub.iloc[0].to_dict()


def keyword_stats(mapped_df: pd.DataFrame, category: str) -> List[Dict[str, Any]]:
    if mapped_df.empty:
        return []

    sub = mapped_df[mapped_df["_category"].eq(category)].copy()
    if sub.empty:
        return []

    t_words = Counter(sub[sub["period"].eq("t")]["_word"].tolist())
    p_words = Counter(sub[sub["period"].eq("t_minus_1")]["_word"].tolist())

    all_words = sorted(set(t_words.keys()) | set(p_words.keys()))
    stats = []

    for word in all_words:
        t_count = int(t_words.get(word, 0))
        p_count = int(p_words.get(word, 0))
        diff = t_count - p_count
        created = 1 if t_count > 0 and p_count == 0 else 0
        removed = 1 if t_count == 0 and p_count > 0 else 0

        source_counts = (
            sub[(sub["period"].eq("t")) & (sub["_word"].eq(word))]["_source_type"]
            .value_counts()
            .to_dict()
        )

        stats.append({
            "word": word,
            "t_count": t_count,
            "t_minus_1_count": p_count,
            "diff": diff,
            "created_flag": created,
            "removed_flag": removed,
            "source_counts": source_counts,
            "score": (created * 5) + max(diff, 0) * 2 + t_count,
        })

    stats.sort(key=lambda x: (x["score"], x["t_count"], abs(x["diff"]), len(x["word"])), reverse=True)
    return stats


def find_evidence_chunks(
    chunks: List[Dict[str, Any]],
    primary_word: str,
    keywords: List[str],
    max_evidence: int = 3,
) -> List[Dict[str, Any]]:
    search_words = []
    for w in [primary_word] + keywords:
        w = str(w or "").strip()
        if w and w not in search_words:
            search_words.append(w)

    candidates = []

    for chunk in chunks:
        text = str(chunk.get("text", ""))
        hit_words = [w for w in search_words if w and w in text]
        if not hit_words:
            continue

        # 너무 긴 chunk는 preview만 남기되 원문 느낌은 유지
        preview = norm_text(text)
        if len(preview) > 650:
            preview = preview[:650] + "..."

        candidates.append({
            "doc_type": chunk.get("doc_type", ""),
            "section_title": chunk.get("section_title", ""),
            "chunk_id": chunk.get("chunk_id", ""),
            "hit_words": hit_words,
            "text": preview,
            "rank_score": (5 if primary_word in hit_words else 0) + len(hit_words),
        })

    candidates.sort(key=lambda x: (x["rank_score"], len(x["text"])), reverse=True)

    out = []
    seen = set()
    for c in candidates:
        key = (c.get("doc_type"), c.get("section_title"), c.get("text")[:80])
        if key in seen:
            continue
        seen.add(key)
        c.pop("rank_score", None)
        out.append(c)
        if len(out) >= max_evidence:
            break

    return out


def build_change_summary(base: Dict[str, Any], change_row: Dict[str, Any]) -> Dict[str, Any]:
    def pick(*keys, default=0):
        for k in keys:
            if k in change_row and change_row.get(k, "") != "":
                return change_row.get(k)
            if k in base and base.get(k, "") != "":
                return base.get(k)
        return default

    return {
        "t_minus_1_word_count": safe_int(pick("t_minus_1_word_count", "전기 단어 수")),
        "t_word_count": safe_int(pick("t_word_count", "당기 단어 수")),
        "word_diff": safe_int(pick("word_diff", "증감")),
        "word_growth_rate": pick("word_growth_rate", "증가율", default=""),
        "new_category_flag": safe_int(pick("new_category_flag", "신규 카테고리 여부")),
        "removed_category_flag": safe_int(pick("removed_category_flag", "소멸 카테고리 여부")),
    }


def build_text_rag_items(
    job_dir: str | Path,
    max_categories: int | None = 8,
    max_keywords_per_category: int = 3,
    max_evidence_per_item: int = 3,
) -> List[Dict[str, Any]]:
    job_dir = Path(job_dir)

    rag_context = load_rag_context(job_dir)
    base_by_cat = get_base_by_category(rag_context)
    mapped_df = load_category_mapped(job_dir)
    change_df = load_category_change(job_dir)
    analysis_df = load_analysis_result(job_dir)
    chunks = load_ui_chunks(job_dir)

    if not base_by_cat and not mapped_df.empty and "카테고리" in mapped_df.columns:
        categories_from_mapped = (
            mapped_df["카테고리"]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )

        for cat in categories_from_mapped:
            if not cat:
                continue

            base_by_cat[cat] = {
                "카테고리": cat,
                "category": cat,
                "priority_score": 0,
                "priority_level": "하",
                "analysis_method": "",
                "formula_name": "",
                "formula": "",
                "analysis_result": "",
            }

    categories = list(base_by_cat.keys())
    categories.sort(key=lambda c: safe_float(base_by_cat[c].get("priority_score", 0)), reverse=True)

    if max_categories is not None:
        categories = categories[:max_categories]

    items: List[Dict[str, Any]] = []
    item_map: Dict[str, Dict[str, Any]] = {}
    for category in categories:
        base = base_by_cat.get(category, {})
        change_row = lookup_row_by_category(change_df, category)
        analysis_row = lookup_row_by_category(analysis_df, category)

        stats = keyword_stats(mapped_df, category)

        if not stats and not mapped_df.empty:
            category_col = None
            word_col = None

            for c in ["카테고리", "category", "CATEGORY"]:
                if c in mapped_df.columns:
                    category_col = c
                    break

            for c in ["단어", "word", "keyword", "명사", "token"]:
                if c in mapped_df.columns:
                    word_col = c
                    break

            if category_col and word_col:
                sub = mapped_df[
                    mapped_df[category_col].astype(str).str.strip() == str(category).strip()
                ].copy()

                words = (
                    sub[word_col]
                    .dropna()
                    .astype(str)
                    .str.strip()
                )

                words = words[words != ""]

                stats = [
                    {
                        "word": w,
                        "t_count": int((words == w).sum()),
                        "t_minus_1_count": 0,
                        "diff": int((words == w).sum()),
                        "created_flag": 0,
                        "removed_flag": 0,
                        "source_counts": {},
                        "score": int((words == w).sum()),
                    }
                    for w in words.value_counts().head(max_keywords_per_category).index.tolist()
                ]

        # 단어가 없으면 카테고리 item 하나라도 유지
        selected_stats = stats[:max_keywords_per_category] if stats else [{
            "word": category,
            "t_count": 0,
            "t_minus_1_count": 0,
            "diff": 0,
            "created_flag": 0,
            "removed_flag": 0,
            "source_counts": {},
            "score": 0,
        }]

        category_keywords = [s["word"] for s in stats[:8]] if stats else [category]
        change_summary = build_change_summary(base, change_row)

        for rank, stat in enumerate(selected_stats, start=1):
            primary_word = stat["word"]
            evidence = find_evidence_chunks(
                chunks=chunks,
                primary_word=primary_word,
                keywords=category_keywords,
                max_evidence=max_evidence_per_item,
            )

            analysis_method = (
                base.get("analysis_method")
                or analysis_row.get("analysis_method", "")
            )
            formula_name = base.get("formula_name") or analysis_row.get("formula_name", "")
            formula = base.get("formula") or analysis_row.get("formula", "")
            analysis_result = base.get("analysis_result") or analysis_row.get("analysis_result", "")

            headline_seed = (
                f"{category}에서 '{primary_word}' 표현이 당기 {stat.get('t_count', 0)}건, "
                f"전기 {stat.get('t_minus_1_count', 0)}건으로 확인되었습니다."
            )

            rag_context_text = "\n".join([
                f"[카테고리] {category}",
                f"[대표 단어] {primary_word}",
                f"[카테고리 우선순위] 점수 {base.get('priority_score', 0)} / 등급 {base.get('priority_level', '')}",
                f"[단어 변화] 당기 {stat.get('t_count', 0)}건 / 전기 {stat.get('t_minus_1_count', 0)}건 / 증감 {stat.get('diff', 0)}건",
                f"[카테고리 변화] 당기 {change_summary.get('t_word_count', 0)}건 / 전기 {change_summary.get('t_minus_1_word_count', 0)}건 / 증감 {change_summary.get('word_diff', 0)}건",
                f"[분석방법] {analysis_method}",
                f"[수식] {formula_name}: {formula}",
                f"[분석결과] {analysis_result}",
                "[근거 문단]",
                "\n".join([f"- ({e.get('doc_type')}/{e.get('section_title')}) {e.get('text')}" for e in evidence]) or "- 자동 연결된 근거 문단이 제한적입니다.",
            ])

            item = {
                "summary_type": "text",
                "item_id": make_id("text", category, primary_word, str(rank)),
                "title": f"{category} - {primary_word}",
                "category": category,
                "primary_keyword": primary_word,
                "keywords": category_keywords,
                "keyword_stats": stat,
                "category_change": change_summary,
                "priority_score": safe_int(base.get("priority_score", 0)),
                "priority_level": str(base.get("priority_level", "")),
                "analysis_method": analysis_method,
                "formula_name": formula_name,
                "formula": formula,
                "analysis_result": analysis_result,
                "headline_seed": headline_seed,
                "evidence_chunks": evidence,
                "highlight_targets": list(dict.fromkeys([primary_word] + category_keywords))[:10],
                "rag_context": rag_context_text,
                "_source_category_item": base,
            }
            dedup_key = f"{category}__{primary_word}"

            if dedup_key not in item_map:
                item_map[dedup_key] = item
            else:
                prev = item_map[dedup_key]

                # evidence_chunks 병합
                existing_texts = {
                    str(e.get("text", ""))
                    for e in prev.get("evidence_chunks", [])
                }

                for e in evidence:
                    text = str(e.get("text", ""))
                    if text and text not in existing_texts:
                        prev.setdefault("evidence_chunks", []).append(e)
                        existing_texts.add(text)

                prev["evidence_chunks"] = prev.get("evidence_chunks", [])[:max_evidence_per_item]

                # 카운트/점수는 더 큰 쪽 유지
                if safe_int(stat.get("score", 0)) > safe_int(prev.get("keyword_stats", {}).get("score", 0)):
                    prev["keyword_stats"] = stat
                    prev["headline_seed"] = headline_seed

                # 병합된 evidence 기준으로 rag_context 재생성
                merged_evidence = prev.get("evidence_chunks", [])

                merged_evidence_text = (
                    "\n".join([
                        f"- ({e.get('doc_type')}/{e.get('section_title')}) {e.get('text')}"
                        for e in merged_evidence
                    ])
                    or "- 자동 연결된 근거 문단이 제한적입니다."
                )

                prev["rag_context"] = "\n".join([
                    f"[카테고리] {prev.get('category', category)}",
                    f"[대표 단어] {prev.get('primary_keyword', primary_word)}",
                    f"[카테고리 우선순위] 점수 {prev.get('priority_score', 0)} / 등급 {prev.get('priority_level', '')}",
                    f"[단어 변화] 당기 {prev.get('keyword_stats', {}).get('t_count', 0)}건 / 전기 {prev.get('keyword_stats', {}).get('t_minus_1_count', 0)}건 / 증감 {prev.get('keyword_stats', {}).get('diff', 0)}건",
                    f"[카테고리 변화] 당기 {prev.get('category_change', {}).get('t_word_count', 0)}건 / 전기 {prev.get('category_change', {}).get('t_minus_1_word_count', 0)}건 / 증감 {prev.get('category_change', {}).get('word_diff', 0)}건",
                    f"[분석방법] {prev.get('analysis_method', '')}",
                    f"[수식] {prev.get('formula_name', '')}: {prev.get('formula', '')}",
                    f"[분석결과] {prev.get('analysis_result', '')}",
                    "[근거 문단]",
                    merged_evidence_text,
                ])

    items = list(item_map.values())

    items.sort(
        key=lambda x: (
            safe_int(x.get("priority_score", 0)),
            safe_int(x.get("keyword_stats", {}).get("score", 0)),
            len(x.get("evidence_chunks", [])),
        ),
        reverse=True,
    )
    print("[DEBUG] base_by_cat:", len(base_by_cat))
    print("[DEBUG] categories:", len(categories))
    print("[DEBUG] item_map:", len(item_map))
    print("[DEBUG] mapped_df:", mapped_df.shape)
    return items


def run_text_rag_item_builder(
    job_dir: str | Path,
    max_categories: int | None = 8,
    max_keywords_per_category: int = 3,
    max_evidence_per_item: int = 3,
) -> Dict[str, Any]:
    job_dir = Path(job_dir)
    output_dir = job_dir / "rag_context"
    output_path = output_dir / "text_rag_items.json"
    csv_path = output_dir / "text_rag_items.csv"

    items = build_text_rag_items(
        job_dir=job_dir,
        max_categories=max_categories,
        max_keywords_per_category=max_keywords_per_category,
        max_evidence_per_item=max_evidence_per_item,
    )

    write_json(output_path, items)

    if items:
        flat_rows = []
        for item in items:
            ks = item.get("keyword_stats", {}) or {}
            cc = item.get("category_change", {}) or {}
            flat_rows.append({
                "item_id": item.get("item_id", ""),
                "category": item.get("category", ""),
                "primary_keyword": item.get("primary_keyword", ""),
                "priority_score": item.get("priority_score", 0),
                "priority_level": item.get("priority_level", ""),
                "keyword_t_count": ks.get("t_count", 0),
                "keyword_t_minus_1_count": ks.get("t_minus_1_count", 0),
                "keyword_diff": ks.get("diff", 0),
                "category_t_count": cc.get("t_word_count", 0),
                "category_t_minus_1_count": cc.get("t_minus_1_word_count", 0),
                "category_diff": cc.get("word_diff", 0),
                "analysis_method": item.get("analysis_method", ""),
                "formula": item.get("formula", ""),
                "evidence_count": len(item.get("evidence_chunks", [])),
            })
        pd.DataFrame(flat_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[TEXT RAG ITEM BUILDER DONE] {output_path} count={len(items)}")

    return {
        "status": "success",
        "output_path": str(output_path),
        "csv_path": str(csv_path),
        "count": len(items),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_087e2927"
    print(run_text_rag_item_builder(TEST_JOB_DIR))
