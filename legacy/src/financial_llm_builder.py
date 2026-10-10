# =========================================================
# src/financial_llm_builder.py
# =========================================================

# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path


SYSTEM_PROMPT = """
당신은 재무제표 리스크 분석 전문가이다.

입력된 재무제표 비교 결과와
룰 기반 분석 결과를 바탕으로:

1. 핵심 리스크
2. 주요 이상징후
3. 재무적 해석
4. 추가 검토 필요사항

을 요약하라.

과도한 추측은 금지한다.
"""


def build_financial_llm_payload(
    rag_context_path,
    output_path,
):

    rag_context_path = Path(rag_context_path)

    data = json.loads(
        rag_context_path.read_text(
            encoding="utf-8"
        )
    )

    blocks = []

    for item in data:

        ctx = item.get("llm_context", "")

        if not ctx:
            continue

        blocks.append(ctx)

    final_prompt = f"""
{SYSTEM_PROMPT}

====================

재무 분석 데이터:

{chr(10).join(blocks)}

====================

위 데이터를 기반으로
재무 리스크를 요약하라.
"""

    result = {

        "system_prompt":
            SYSTEM_PROMPT,

        "prompt":
            final_prompt,

        "context_count":
            len(blocks),
    }

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2
        )

    return result