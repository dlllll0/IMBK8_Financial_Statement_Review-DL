# -*- coding: utf-8 -*-
"""
src/text_llm_summary_builder.py

text_rag_items.json → text_llm_summary.json 생성기.

이전 구조처럼 카테고리 단위 rag_context.json을 바로 LLM에 넣지 않고,
text_rag_item_builder.py가 만든
'카테고리 + 대표단어 + 변화량 + 실제 근거문단' 단위 item을 요약한다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

# llm_summary_builder는 google-genai 의존성이 있으므로 lazy/fallback 처리한다.
try:
    from src.llm_summary_builder import (
        get_gemini_client,
        call_gemini_json,
        write_json,
        read_json_safely,
    )
except Exception as _import_error:
    get_gemini_client = None
    call_gemini_json = None

    def read_json_safely(path, default=None):
        path = Path(path)
        if default is None:
            default = []
        if not path.exists() or path.stat().st_size == 0:
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

try:
    from src.text_rag_item_builder import run_text_rag_item_builder
except Exception:
    run_text_rag_item_builder = None


SYSTEM_GUIDE = """
너는 사업보고서 주석, 감사보고서, 정정신고 텍스트를 검토하는 재무문서 분석 보조자다.

중요 원칙:
1. 입력 Text RAG item에 없는 사실, 수치, 원인, 감사의견을 새로 만들지 않는다.
2. 리스크를 확정하지 말고 "검토 필요", "가능성", "추가 확인" 표현을 사용한다.
3. 단어 변화, 대표 키워드, 실제 근거 문단을 연결해서 설명한다.
4. 근거 문단이 약하면 "자동 매칭 근거는 제한적"이라고 명시한다.
5. 한국어로 간결하게 작성한다.
6. 출력은 JSON만 작성한다.
"""


def find_text_rag_items_path(job_dir: str | Path) -> Path:
    job_dir = Path(job_dir)

    candidates = [
        job_dir / "rag_context" / "text_rag_items.json",
        job_dir / "text_rag_items.json",
        # 마지막 fallback. 단, 이건 카테고리 단위라 맛이 덜함.
        job_dir / "rag_context" / "rag_context.json",
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            print("[TEXT LLM SUMMARY] selected rag path:", path)
            return path

    raise FileNotFoundError(f"text_rag_items.json not found: {job_dir}")


def compact_text_rag_item(item: Dict[str, Any]) -> Dict[str, Any]:
    category = str(item.get("category", item.get("카테고리", "")))
    primary_keyword = str(item.get("primary_keyword", ""))

    keywords = item.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [x.strip() for x in keywords.replace("/", ",").split(",") if x.strip()]
    if not isinstance(keywords, list):
        keywords = []

    if primary_keyword and primary_keyword not in keywords:
        keywords = [primary_keyword] + keywords

    evidence_chunks = item.get("evidence_chunks", [])
    if not isinstance(evidence_chunks, list):
        evidence_chunks = []

    return {
        "summary_type": "text",
        "item_id": item.get("item_id", ""),
        "title": item.get("title", f"{category} - {primary_keyword}"),
        "category": category,
        "primary_keyword": primary_keyword,
        "keywords": keywords[:10],
        "priority_score": item.get("priority_score", 0),
        "priority_level": item.get("priority_level", ""),
        "keyword_stats": item.get("keyword_stats", {}),
        "category_change": item.get("category_change", {}),
        "analysis_method": item.get("analysis_method", ""),
        "formula_name": item.get("formula_name", ""),
        "formula": item.get("formula", ""),
        "analysis_result": item.get("analysis_result", ""),
        "headline_seed": item.get("headline_seed", ""),
        "evidence_chunks": evidence_chunks[:4],
        "highlight_targets": item.get("highlight_targets", keywords[:10]),
        "rag_context": str(item.get("rag_context", ""))[:5000],
        "_source_item": item,
    }


def build_text_user_prompt(item: Dict[str, Any]) -> str:
    compact = compact_text_rag_item(item)

    return f"""
{SYSTEM_GUIDE}

아래 Text RAG ITEM을 바탕으로 웹 대시보드 우측 패널에 표시할 요약을 작성하라.

작성 방향:
- count/증감 수치를 먼저 나열하지 말고, 실제 근거 문단의 문맥을 먼저 해석하라.
- evidence_chunks에 포함된 문단 내용을 바탕으로 어떤 거래, 채무, 손상, 평가, 소송, 공시 이슈가 나타나는지 설명하라.
- primary_keyword가 해당 문단에서 어떤 의미로 사용되는지 설명하라.
- 해당 단어가 어떤 문단에서 나오는지, 그 내용에 대한 간단요약도 설명하라.
- 전기/당기 count는 마지막에 검토 우선순위 참고용으로 짧게 언급하라.
- 문단에 없는 원인, 감사의견, 부정 판단은 만들지 마라.
- “검토 필요”, “확인 필요”, “가능성” 표현을 사용하라.

필수 JSON 스키마:
{{
  "summary_type": "text",
  "item_id": "{compact.get("item_id", "")}",
  "category": "{compact.get("category", "")}",
  "primary_keyword": "{compact.get("primary_keyword", "")}",
  "keywords": {json.dumps(compact.get("keywords", []), ensure_ascii=False)},
  "priority_score": {json.dumps(compact.get("priority_score", 0), ensure_ascii=False)},
  "priority_level": "{compact.get("priority_level", "")}",
  "headline": "대표 키워드와 변화량 기반 한 문장 요약",
  "summary": "근거 문단의 문맥을 먼저 해석하고, 마지막에 전기 대비 변화량을 보조 근거로 제시한 2~3문장",
  "evidence_interpretation": "근거 문단에서 확인되는 거래/채무/평가/손상/공시 이슈와 추가 확인 필요사항",
  "check_points": ["원문 주석 확인", "전기 대비 표현 변화 확인", "관련 재무제표 계정 변동 확인"],
  "highlight_targets": {json.dumps(compact.get("highlight_targets", []), ensure_ascii=False)}
}}

Text RAG ITEM:
{json.dumps(compact, ensure_ascii=False, indent=2)}
"""


def fallback_text_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    compact = compact_text_rag_item(item)
    category = compact.get("category", "")
    primary = compact.get("primary_keyword", "")
    keywords = compact.get("keywords", [])
    ks = compact.get("keyword_stats", {}) or {}
    cc = compact.get("category_change", {}) or {}
    evidence = compact.get("evidence_chunks", []) or []

    keyword_text = ", ".join(keywords[:5]) if keywords else primary or "대표 키워드 없음"

    t_count = ks.get("t_count", 0)
    p_count = ks.get("t_minus_1_count", 0)
    diff = ks.get("diff", 0)

    category_t = cc.get("t_word_count", 0)
    category_p = cc.get("t_minus_1_word_count", 0)
    category_diff = cc.get("word_diff", 0)

    if evidence:
        first = evidence[0]
        hit_words = ", ".join(first.get("hit_words", [])[:5])
        section = first.get("section_title", "")
        doc_type = first.get("doc_type", "")

        evidence_text = (
            f"{doc_type}의 '{section}' 문단에서 {hit_words or primary} 관련 표현이 확인됩니다. "
            f"해당 문단은 단순 키워드 등장 여부보다, 관련 거래·채무·평가·손상·공시 내용이 "
            f"어떤 맥락에서 설명되는지 원문 기준으로 확인할 필요가 있습니다."
        )

        context_summary = (
            f"연결된 근거 문단에서는 '{primary}'를 중심으로 {keyword_text} 관련 표현이 함께 확인됩니다. "
            f"따라서 이 항목은 단어 빈도 자체보다, 해당 표현이 실제 주석에서 어떤 거래 구조나 회계처리 맥락으로 사용되었는지 검토하는 것이 중요합니다."
        )
    else:
        evidence_text = (
            "자동으로 연결된 원문 근거 문단은 제한적입니다. "
            "따라서 해당 키워드가 실제 주석·감사보고서·정정신고에서 어떤 맥락으로 사용되었는지 원문 확인이 필요합니다."
        )

        context_summary = (
            f"{category} 항목에서 '{primary}' 관련 표현이 탐지되었지만, 자동 연결된 근거 문단은 제한적입니다. "
            f"이 경우 단어 출현 결과만으로 판단하기보다 원문 문단과 관련 재무제표 계정 변동을 함께 확인해야 합니다."
        )

    headline = (
        f"'{primary}' 표현은 원문 문맥 기준 추가 검토가 필요한 키워드로 확인됩니다."
        if primary else f"{category} 관련 표현이 확인되었습니다."
    )

    summary = (
        f"{context_summary} "
        f"정량적으로는 대표 키워드 '{primary}'가 당기 {t_count}건, 전기 {p_count}건으로 집계되어 증감 {diff}건이 확인됩니다. "
        f"카테고리 전체 기준으로는 당기 {category_t}건, 전기 {category_p}건, 증감 {category_diff}건입니다."
    )

    return {
        "summary_type": "text",
        "item_id": compact.get("item_id", ""),
        "title": compact.get("title", f"{category} - {primary}"),
        "category": category,
        "primary_keyword": primary,
        "keywords": keywords,
        "priority_score": compact.get("priority_score", 0),
        "priority_level": compact.get("priority_level", ""),
        "headline": headline,
        "summary": summary,
        "evidence_interpretation": evidence_text,
        "check_points": [
            "원문 문단에서 해당 키워드가 어떤 거래나 회계처리 맥락으로 사용되었는지 확인",
            "전기 대비 표현 증가 또는 신규 등장 원인 확인",
            "관련 재무제표 계정의 수치 변동 및 주석 설명의 일관성 확인",
        ],
        "highlight_targets": compact.get("highlight_targets", keywords),
        "_source_item": compact.get("_source_item", {}),
    }


def build_text_llm_summary_from_rag_items(
    rag_items_path: str | Path,
    output_path: str | Path,
    use_api: bool = True,
    max_items: int | None = 5,
) -> List[Dict[str, Any]]:

    rag_items = read_json_safely(rag_items_path, default=[])

    if isinstance(rag_items, dict):
        rag_items = rag_items.get("items", [])

    valid_items = [x for x in rag_items if isinstance(x, dict)]

    valid_items = sorted(
        valid_items,
        key=lambda x: (
            float(x.get("priority_score", 0) or 0),
            float((x.get("keyword_stats", {}) or {}).get("score", 0) or 0),
            len(x.get("evidence_chunks", []) or []),
        ),
        reverse=True,
    )

    if max_items is not None:
        valid_items = valid_items[:max_items]

    results = []
    client = get_gemini_client() if use_api and valid_items and get_gemini_client is not None else None

    for idx, item in enumerate(valid_items, start=1):
        compact = compact_text_rag_item(item)
        print(f"[TEXT LLM SUMMARY] {idx}/{len(valid_items)} {compact.get('category')} / {compact.get('primary_keyword')}")

        if use_api and client is not None and call_gemini_json is not None:
            try:
                summary = call_gemini_json(
                    client=client,
                    prompt=build_text_user_prompt(item),
                    max_retries=1,
                )
            except Exception as e:
                print("[WARN] Text Gemini 실패:", e)
                summary = fallback_text_summary(item)
        else:
            summary = fallback_text_summary(item)

        # 원본 핵심 필드 강제 유지
        summary["summary_type"] = "text"
        summary["item_id"] = compact.get("item_id", "")
        summary["title"] = compact.get("title", "")
        summary["category"] = compact.get("category", "")
        summary["primary_keyword"] = compact.get("primary_keyword", "")
        summary["keywords"] = compact.get("keywords", [])
        summary["priority_score"] = compact.get("priority_score", 0)
        summary["priority_level"] = compact.get("priority_level", "")
        summary["highlight_targets"] = compact.get("highlight_targets", compact.get("keywords", []))
        summary["_source_item"] = compact.get("_source_item", {})

        results.append(summary)

        if use_api:
            time.sleep(0.5)

    write_json(output_path, results)
    print(f"[TEXT LLM SUMMARY DONE] {output_path}")

    return results


def run_text_llm_summary_builder(
    job_dir: str | Path,
    use_api: bool = True,
    max_items: int | None = 5,
) -> Dict[str, Any]:

    job_dir = Path(job_dir)

    # text_rag_items가 없으면 먼저 생성한다.
    text_rag_path = job_dir / "rag_context" / "text_rag_items.json"
    if (not text_rag_path.exists() or text_rag_path.stat().st_size == 0) and run_text_rag_item_builder is not None:
        run_text_rag_item_builder(
            job_dir=job_dir,
            max_categories=8,
            max_keywords_per_category=3,
            max_evidence_per_item=3,
        )

    rag_items_path = find_text_rag_items_path(job_dir)
    output_path = job_dir / "rag_context" / "text_llm_summary.json"

    results = build_text_llm_summary_from_rag_items(
        rag_items_path=rag_items_path,
        output_path=output_path,
        use_api=use_api,
        max_items=max_items,
    )

    return {
        "status": "success",
        "rag_items_path": str(rag_items_path),
        "output_path": str(output_path),
        "count": len(results),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_e674e5fc"
    print(run_text_llm_summary_builder(TEST_JOB_DIR, use_api=True, max_items=5))
