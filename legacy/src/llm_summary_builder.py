# -*- coding: utf-8 -*-
"""
src/llm_summary_builder.py

Vertex AI Gemini 기반
RAG ITEM → LLM SUMMARY 생성기

입력:
outputs/jobs/{job_id}/financial_comparison/rag_items.json
또는
outputs/jobs/{job_id}/financial_comparison/financial_rag_context.json

출력:
outputs/jobs/{job_id}/financial_comparison/llm_summary.json

필수:
pip install google-genai

환경:
service-account.json 사용
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None


# =========================================================
# GCP CONFIG
# =========================================================

PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "")
LOCATION = "global"

MODEL_NAME = "gemini-2.5-flash"

SERVICE_ACCOUNT_PATH = "service-account.json"

# Credentials are provided by the caller environment.


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
너는 기업 재무제표와 주석을 함께 검토하는 재무 리스크 분석 담당자다.

입력으로 제공되는 rag_items는 자동 분석 결과이다.
각 항목에는 재무제표 수치,
전기 대비 증감률,
관련 주석 문장,
명사 빈도,
하이라이트 대상이 포함될 수 있다.

작성 원칙:
1. 수치 변화는 반드시 전기 대비 당기 관점으로 설명한다.
2. 단정하지 말고 "가능성", "검토 필요", "추가 확인 필요" 표현을 사용한다.
3. 관련 주석 근거가 약하면 "자동 매칭 근거는 제한적"이라고 명시한다.
4. OCR 또는 자동 매칭 오류 가능성을 감안해 과도한 결론을 피한다.
5. 한국어로 간결하게 작성한다.
"""


# =========================================================
# IO
# =========================================================

def read_json_safely(path: str | Path, default=None):

    path = Path(path)

    if default is None:
        default = []

    if not path.exists():
        return default

    if path.stat().st_size == 0:
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: Any):

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# RAG ITEM PATH
# =========================================================

def find_rag_items_path(job_dir: str | Path):

    job_dir = Path(job_dir)

    candidates = [
        # 최종 재무제표 RAG item 위치
        job_dir / "financial_comparison" / "financial_rag_items.json",

        # 예전 후보
        job_dir / "financial_comparison" / "rag_items.json",

        # preview 후보
        job_dir / "t" / "rag_preview" / "rag_items.json",
        job_dir / "t_minus_1" / "rag_preview" / "rag_items.json",

        # 마지막 fallback
        job_dir / "financial_comparison" / "financial_rag_context.json",
        job_dir / "rag_context" / "rag_context.json",
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            print("[LLM SUMMARY] selected rag path:", path)
            return path

    matches = list(job_dir.rglob("rag_items.json"))

    if matches:
        matches = [p for p in matches if p.exists() and p.stat().st_size > 0]
        if matches:
            path = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            print("[LLM SUMMARY] selected rag path:", path)
            return path

    raise FileNotFoundError(
        f"rag_items.json not found: {job_dir}"
    )


# =========================================================
# RAG COMPACT
# =========================================================

def compact_rag_item(item: Dict[str, Any]):
    """LLM에 전달할 financial item을 계정/수치/수식 중심으로 압축한다."""
    return {
        "item_id": item.get("item_id", ""),
        "title": item.get("title", ""),
        "summary_type": item.get("summary_type", ""),
        "risk_level": item.get("risk_level", item.get("priority_level", "")),
        "priority_score": item.get("priority_score", 0),
        "priority_level": item.get("priority_level", item.get("risk_level", "")),
        "highlight_targets": item.get("highlight_targets", []),

        # 핵심: 같은 카테고리라도 이 값들이 다르면 LLM 출력이 달라져야 한다.
        "table_name": item.get("table_name", ""),
        "account_name": item.get("account_name", item.get("title", "")),
        "account_category": item.get("account_category", ""),
        "risk_category": item.get("risk_category", ""),
        "financial_evidence": item.get("financial_evidence", {}),
        "previous_value": item.get("previous_value", ""),
        "current_value": item.get("current_value", ""),
        "change_amount": item.get("change_amount", ""),
        "growth_rate": item.get("growth_rate", ""),

        # 분석방법/수식/은행데이터 placeholder
        "analysis_method": item.get("analysis_method", ""),
        "formula_name": item.get("formula_name", ""),
        "formula": item.get("formula", ""),
        "formula_result": item.get("formula_result", {}),
        "bank_data_required": item.get("bank_data_required", ""),
        "check_points": item.get("check_points", ""),
        "benchmark_hint": item.get("benchmark_hint", ""),
        "interpretation": item.get("interpretation", ""),
        "headline_seed": item.get("headline_seed", ""),

        "text_evidence": item.get("text_evidence", [])[:5],
        "noun_context": item.get("noun_context", [])[:20],
        "rag_context": str(item.get("rag_context", ""))[:5000],
    }


# =========================================================
# USER PROMPT
# =========================================================

def build_user_prompt(item: Dict[str, Any]) -> str:
    compact = compact_rag_item(item)
    external_analysis = item.get("external_analysis", {}) or item.get("formula_result", {})

    return f"""
아래 Financial RAG ITEM을 바탕으로 대시보드용 재무제표 검토 요약을 작성하라.

중요 규칙:
1. account_name, table_name, 전기/당기 수치, 증감액, 증감률을 반드시 반영하라.
2. 수치 설명은 financial_evidence를 최우선으로 사용하라.
3. external_analysis.available=True이면 mapped_industry, bank_metric, industry_stats.median, ratio_to_median 또는 ratio_to_median_display를 반드시 반영하라.
4. 동일업종 은행데이터 비교 결과는 “동일업종 중앙값 대비 약 n배” 형식으로 명시하라.
5. 해당 배율은 검토 우선순위 판단을 위한 보조 지표로 설명하고, 원인이나 회계위험을 확정하지 마라.
6. 손실, 감액, 평가손실, 차감계정은 원본 금액의 부호보다 변동 규모와 발생 여부를 중심으로 설명하라.
7. ratio_to_median_display가 있으면 raw ratio_to_median보다 우선 사용하라.
8. RAG ITEM에 없는 계정명, 수치, 원인, 감사의견, 부정 판단을 새로 만들지 마라.
9. 주석 자동 매칭 근거가 없다는 문장은 작성하지 마라.
10. 출력은 JSON만 작성하라.

필수 JSON 스키마:
{{
  "item_id": "{compact.get("item_id", "")}",
  "title": "{compact.get("title", "")}",
  "risk_level": "{compact.get("risk_level", "")}",
  "headline": "계정과목의 전기/당기 변동과 동일업종 은행데이터 비교를 반영한 한 문장",
  "summary": "재무제표 수치 변동과 은행데이터 비교 결과를 연결한 2~3문장",
  "financial_interpretation": "financial_evidence 기준으로 전기/당기/증감액/증감률을 구체적으로 설명",
  "external_interpretation": "동일업종 은행데이터 중앙값 대비 몇 배인지 반드시 명시하고, 해당 배율이 검토 보조 지표임을 함께 설명",
  "check_points": ["계정 세부내역 확인", "변동 원인 확인", "동일업종 은행데이터 비교 결과 확인"],
  "highlight_targets": {json.dumps(compact.get("highlight_targets", []), ensure_ascii=False)}
}}

[외부 은행데이터 비교]
{json.dumps(external_analysis, ensure_ascii=False, indent=2)}

RAG_ITEM:
{json.dumps(compact, ensure_ascii=False, indent=2)}
"""


# =========================================================
# GEMINI CLIENT
# =========================================================

def get_gemini_client():

    if genai is None:
        raise RuntimeError("google-genai 패키지를 불러오지 못했습니다. pip install google-genai 후 사용하세요.")

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
    )

    return client


# =========================================================
# GEMINI CALL
# =========================================================

def call_gemini_json(
    client,
    prompt: str,
    max_retries: int = 2,
):

    last_error = None

    for attempt in range(max_retries + 1):

        try:

            response = client.models.generate_content(

                model=MODEL_NAME,

                contents=prompt,

                config=types.GenerateContentConfig(

                    temperature=0.2,

                    response_mime_type="application/json",
                ),
            )

            text = response.text

            return json.loads(text)

        except Exception as e:

            last_error = e

            time.sleep(1.5 + attempt)

    raise RuntimeError(
        f"Gemini 호출 실패: {last_error}"
    )


# =========================================================
# FALLBACK
# =========================================================

def fallback_summary(item):
    financial = item.get("financial_evidence", {}) or {}

    account = financial.get("account_name", item.get("account_name", item.get("title", "")))
    table_name = financial.get("table_name", item.get("table_name", ""))
    prev = financial.get("previous_value_display", financial.get("previous_value", item.get("previous_value", "")))
    curr = financial.get("current_value_display", financial.get("current_value", item.get("current_value", "")))
    change = financial.get("change_amount_display", financial.get("change_amount", item.get("change_amount", "")))
    growth = financial.get("growth_rate_display", financial.get("growth_rate", item.get("growth_rate", "")))
    direction = financial.get("direction", "변동")

    risk_category = item.get("risk_category", "")
    analysis_method = item.get("analysis_method", "")
    formula_name = item.get("formula_name", "")
    formula = item.get("formula", "")
    formula_result = item.get("formula_result", {}) or {}

    formula_sentence = ""
    if formula_name or formula:
        formula_sentence = f" 적용 가능한 분석 수식은 '{formula_name}: {formula}'입니다."

    if isinstance(formula_result, dict) and formula_result.get("status") == "pending_bank_data":
        formula_sentence += " 은행 원장/거래 데이터가 아직 연결되지 않아 동일업종 기준 비교값은 추후 산출 대상으로 남겨두었습니다."

    return {
        "item_id": item.get("item_id", account),
        "title": account,
        "risk_level": item.get("risk_level", item.get("priority_level", "middle")),
        "headline": f"{account}은 전기 {prev}백만원에서 당기 {curr}백만원으로 {direction}했습니다.",
        "summary": (
            f"{table_name}의 {account} 계정은 전기 {prev}백만원에서 당기 {curr}백만원으로 변동했으며, "
            f"증감액은 {change}백만원, 증감률은 {growth}입니다. "
            f"해당 항목은 {risk_category or '재무제표 수치 변동'} 관점에서 우선 검토할 수 있습니다."
            f"{formula_sentence}"
        ),
        "financial_interpretation": (
            f"{account}의 전기 대비 당기 변화는 증감액 {change}백만원, 증감률 {growth}로 확인됩니다. "
            f"같은 카테고리 항목이라도 계정별 금액과 증감률이 다르므로 개별 원인 확인이 필요합니다."
        ),
        "evidence_interpretation": (
            "관련 주석 자동 매칭 근거는 제한적입니다. "
            "다만 RAG item의 수치 비교 및 룰 기반 분석방법을 근거로 세부내역 확인이 필요합니다."
        ),
        "check_points": [
            "계정 세부내역 확인",
            "전기 대비 변동 원인 확인",
            "관련 주석 및 은행데이터 비교 가능 여부 확인",
        ],
        "highlight_targets": item.get("highlight_targets", []),
    }


# =========================================================
# MAIN
# =========================================================

def build_llm_summary_from_rag_items(

    rag_items_path,
    output_path,

    use_api=True,
    max_items=None,
):

    rag_items = read_json_safely(
        rag_items_path,
        default=[],
    )

    # =====================================================
    # 빈 RAG item 제거
    # - financial_evidence가 없거나
    # - title/item_id가 모두 비어있는 항목은 LLM 호출하지 않음
    # =====================================================
    valid_items = []

    for item in rag_items:
        if not isinstance(item, dict):
            continue

        item_id = str(item.get("item_id", "")).strip()
        title = str(item.get("title", "")).strip()
        financial = item.get("financial_evidence", {}) or {}
        rag_context = str(item.get("rag_context", "")).strip()

        if not item_id and not title and not rag_context:
            continue

        valid_items.append(item)

    rag_items = valid_items

    if max_items is not None:
        rag_items = rag_items[:max_items]

    results = []

    client = None

    if use_api:
        client = get_gemini_client()

    for idx, item in enumerate(rag_items, start=1):

        title = item.get("title", "")

        print(
            f"[LLM SUMMARY] "
            f"{idx}/{len(rag_items)} "
            f"{title}"
        )

        if use_api:

            try:

                prompt = build_user_prompt(item)

                summary = call_gemini_json(
                    client=client,
                    prompt=prompt,
                )

            except Exception as e:

                print(
                    "[WARN] Gemini 실패:",
                    e
                )

                summary = fallback_summary(item)

        else:

            summary = fallback_summary(item)

        # =====================================================
        # 원본 RAG item의 핵심 필드는 반드시 유지
        # - LLM이 item_id/title/risk_level/highlight_targets를
        #   새로 만들거나 바꿔도 원본으로 덮어씀
        # =====================================================
        summary["item_id"] = item.get("item_id", "")
        summary["title"] = item.get("title", "")
        summary["risk_level"] = item.get("risk_level", "")
        summary["highlight_targets"] = item.get("highlight_targets", [])

        summary["_source_item"] = {
            "item_id": item.get("item_id", ""),
            "title": item.get("title", ""),
            "risk_level": item.get("risk_level", ""),
            "highlight_targets": item.get("highlight_targets", []),
        }

        results.append(summary)

    write_json(
        output_path,
        results,
    )

    print(
        f"[LLM SUMMARY DONE] "
        f"{output_path}"
    )

    return results


# =========================================================
# RUNNER
# =========================================================

def run_llm_summary_builder(
    job_dir,
    use_api=True,
    max_items=None,
):

    job_dir = Path(job_dir)

    rag_items_path = find_rag_items_path(job_dir)

    output_path = (
        job_dir /
        "financial_comparison" /
        "llm_summary.json"
    )

    results = build_llm_summary_from_rag_items(

        rag_items_path=rag_items_path,

        output_path=output_path,

        use_api=use_api,

        max_items=max_items,
    )

    return {

        "status":
            "success",

        "rag_items_path":
            str(rag_items_path),

        "output_path":
            str(output_path),

        "count":
            len(results),
    }


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    BASE_DIR = (
        Path(__file__)
        .resolve()
        .parent
        .parent
    )

    TEST_JOB_DIR = (
        BASE_DIR /
        "outputs" /
        "jobs" /
        "job_e674e5fc"
    )

    result = run_llm_summary_builder(
        job_dir=TEST_JOB_DIR,
        use_api=True,
    )

    print(result)