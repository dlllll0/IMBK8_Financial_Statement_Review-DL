import json
import pandas as pd
from pathlib import Path

from src.category_prompt_templates import (
    CATEGORY_PROMPT_TEMPLATES,
    DEFAULT_PROMPT_TEMPLATE,
)


def read_csv_safely(path):
    path = Path(path)

    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue

    return pd.DataFrame()


def get_prompt_template(category):
    return CATEGORY_PROMPT_TEMPLATES.get(category, DEFAULT_PROMPT_TEMPLATE).strip()


def build_llm_context(row):
    category = row.get("카테고리", "")
    prompt_template = get_prompt_template(category)

    context = f"""
[카테고리]
{category}

[우선순위]
점수: {row.get("priority_score")}
등급: {row.get("priority_level")}

[전기 대비 변화]
전기 단어 수: {row.get("t_minus_1_word_count")}
당기 단어 수: {row.get("t_word_count")}
증감: {row.get("word_diff")}
증가율: {row.get("word_growth_rate")}
신규 카테고리 여부: {row.get("new_category_flag")}
소멸 카테고리 여부: {row.get("removed_category_flag")}

[추천 분석방법]
{row.get("analysis_method")}

[적용 수식]
{row.get("formula_name")}: {row.get("formula")}

[분석결과]
{row.get("analysis_result")}

[LLM 작성 지침]
{prompt_template}
""".strip()

    return context


def run_rag_builder(job_dir):
    job_dir = Path(job_dir)

    input_csv_path = job_dir / "analysis_result" / "analysis_result.csv"
    output_dir = job_dir / "rag_context"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_csv_path = output_dir / "rag_context.csv"
    output_json_path = output_dir / "rag_context.json"

    df = read_csv_safely(input_csv_path)

    if df.empty:
        print("[RAG BUILDER] analysis_result가 비어 있음")
        return {
            "status": "empty",
            "rows": 0,
            "output_csv_path": None,
            "output_json_path": None,
        }

    df["prompt_template"] = df["카테고리"].apply(get_prompt_template)
    df["llm_context"] = df.apply(lambda row: build_llm_context(row.to_dict()), axis=1)

    keep_cols = [
        "카테고리",
        "priority_score",
        "priority_level",
        "analysis_method_id",
        "analysis_method",
        "formula_name",
        "formula",
        "analysis_result",
        "prompt_template",
        "llm_context",
    ]

    keep_cols = [c for c in keep_cols if c in df.columns]
    rag_df = df[keep_cols].copy()

    rag_df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")

    records = rag_df.to_dict(orient="records")

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print("[RAG BUILDER DONE]")
    print("CSV :", output_csv_path)
    print("JSON:", output_json_path)

    return {
        "status": "success",
        "rows": len(rag_df),
        "output_csv_path": str(output_csv_path),
        "output_json_path": str(output_json_path),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_22452cb4"

    run_rag_builder(TEST_JOB_DIR)