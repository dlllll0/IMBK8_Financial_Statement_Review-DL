import json
import pandas as pd
from pathlib import Path

from src.category_analysis_rules import CATEGORY_ANALYSIS_RULES
from src.priority_scorer import calculate_priority_score, assign_priority_level


def read_json(path):
    path = Path(path)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_analysis_result_rows(change_json):
    rows = []

    category_analysis = change_json.get("category_analysis", [])

    for item in category_analysis:
        category = item.get("카테고리")
        analysis_methods = item.get("analysis_methods", [])
        formulas = item.get("formulas", {})

        priority_score = calculate_priority_score(item)
        priority_level = assign_priority_level(priority_score)

        base = {
            "카테고리": category,
            "t_minus_1_word_count": item.get("t_minus_1_word_count", 0),
            "t_word_count": item.get("t_word_count", 0),
            "word_diff": item.get("word_diff", 0),
            "word_growth_rate": item.get("word_growth_rate", 0),
            "new_category_flag": item.get("new_category_flag", 0),
            "removed_category_flag": item.get("removed_category_flag", 0),
            "priority_score": priority_score,
            "priority_level": priority_level,
        }

        if not analysis_methods:
            rows.append({
                **base,
                "analysis_method_id": f"{category}_METHOD_0",
                "analysis_method": "카테고리 변화 기반 기본 검토",
                "formula_name": None,
                "formula": None,
                "analysis_result": make_basic_result_text(base),
            })
            continue

        for i, method in enumerate(analysis_methods, start=1):
            formula_name = None
            formula = None

            if formulas:
                formula_items = list(formulas.items())
                if i - 1 < len(formula_items):
                    formula_name, formula = formula_items[i - 1]
                else:
                    formula_name, formula = formula_items[0]

            rows.append({
                **base,
                "analysis_method_id": f"{category}_METHOD_{i}",
                "analysis_method": method,
                "formula_name": formula_name,
                "formula": formula,
                "analysis_result": make_analysis_result_text(base, method, formula_name, formula),
            })

    return rows


def make_basic_result_text(base):
    return (
        f"{base['카테고리']} 카테고리는 전기 {base['t_minus_1_word_count']}건에서 "
        f"당기 {base['t_word_count']}건으로 변화했습니다. "
        f"증감은 {base['word_diff']}건이며, 우선순위는 {base['priority_level']}입니다."
    )


def make_analysis_result_text(base, method, formula_name, formula):
    text = (
        f"{base['카테고리']} 카테고리는 전기 {base['t_minus_1_word_count']}건에서 "
        f"당기 {base['t_word_count']}건으로 변화했습니다. "
        f"증감은 {base['word_diff']}건, 증가율은 {base['word_growth_rate']}입니다. "
        f"이 결과에 따라 '{method}' 분석을 우선 검토할 수 있습니다."
    )

    if formula_name and formula:
        text += f" 적용 가능한 계산식은 '{formula_name}: {formula}'입니다."

    return text


def run_analysis_runner(job_dir):
    job_dir = Path(job_dir)

    input_json_path = job_dir / "change_analysis" / "category_change_analysis.json"
    output_dir = job_dir / "analysis_result"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_csv_path = output_dir / "analysis_result.csv"
    output_json_path = output_dir / "analysis_result.json"

    change_json = read_json(input_json_path)
    rows = build_analysis_result_rows(change_json)

    df = pd.DataFrame(rows)
    df = df.sort_values(["priority_score", "카테고리"], ascending=[False, True])

    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print("[ANALYSIS RUNNER DONE]")
    print("CSV :", output_csv_path)
    print("JSON:", output_json_path)

    return {
        "status": "success",
        "rows": len(df),
        "output_csv_path": str(output_csv_path),
        "output_json_path": str(output_json_path),
    }


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_22452cb4"

    run_analysis_runner(TEST_JOB_DIR)