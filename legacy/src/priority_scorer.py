# =========================================================
# src/priority_scorer.py
# =========================================================

from pathlib import Path
import pandas as pd
import json


# =========================================================
# 우선순위 점수 계산
# =========================================================
# 개념:
#
# 1. 생성된 카테고리
#    t-1에는 없고 t에는 있음
#
# 2. 사라진 카테고리
#    t-1에는 있었고 t에는 없음
#
# 3. 변화량
#    abs(t - t-1)
#
# 최종:
# priority_score =
#     생성 여부
#   + 사라진 여부
#   + 변화량
#
# =========================================================


def calculate_priority_score(row: dict) -> int:

    t_count = int(row.get("t_word_count", 0))
    prev_count = int(row.get("t_minus_1_word_count", 0))

    # -----------------------------------------------------
    # 생성
    # -----------------------------------------------------
    created_count = 1 if t_count > 0 and prev_count == 0 else 0

    # -----------------------------------------------------
    # 소멸
    # -----------------------------------------------------
    removed_count = 1 if t_count == 0 and prev_count > 0 else 0

    # -----------------------------------------------------
    # 변화량
    # -----------------------------------------------------
    changed_count = abs(t_count - prev_count)

    # -----------------------------------------------------
    # 최종 점수
    # -----------------------------------------------------
    priority_score = (
        created_count
        + removed_count
        + changed_count
    )

    return int(priority_score)


# =========================================================
# 우선순위 레벨
# =========================================================

def assign_priority_level(score: int) -> str:

    if score >= 5:
        return "상"

    elif score >= 2:
        return "중"

    return "하"


# =========================================================
# change_analysis 기반 scoring
# =========================================================

def build_priority_rows(change_json):

    rows = []

    category_analysis = change_json.get("category_analysis", [])

    for item in category_analysis:

        category = item.get("카테고리")

        score = calculate_priority_score(item)
        level = assign_priority_level(score)

        rows.append({
            "카테고리": category,

            "t_minus_1_word_count":
                item.get("t_minus_1_word_count", 0),

            "t_word_count":
                item.get("t_word_count", 0),

            "word_diff":
                item.get("word_diff", 0),

            "word_growth_rate":
                item.get("word_growth_rate", 0),

            "new_category_flag":
                item.get("new_category_flag", 0),

            "removed_category_flag":
                item.get("removed_category_flag", 0),

            "priority_score":
                score,

            "priority_level":
                level,
        })

    return rows


# =========================================================
# 메인 실행 함수
# =========================================================

def run_priority_scoring(job_dir):

    job_dir = Path(job_dir)

    input_json_path = (
        job_dir
        / "change_analysis"
        / "category_change_analysis.json"
    )

    output_dir = (
        job_dir
        / "priority_scoring"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_csv_path = (
        output_dir
        / "priority_scoring.csv"
    )

    output_json_path = (
        output_dir
        / "priority_scoring.json"
    )

    # -----------------------------------------------------
    # JSON 로드
    # -----------------------------------------------------

    with open(input_json_path, "r", encoding="utf-8") as f:
        change_json = json.load(f)

    # -----------------------------------------------------
    # scoring
    # -----------------------------------------------------

    rows = build_priority_rows(change_json)

    df = pd.DataFrame(rows)

    if len(df) > 0:

        df = df.sort_values(
            by=[
                "priority_score",
                "word_diff",
            ],
            ascending=[
                False,
                False,
            ],
        )

    # -----------------------------------------------------
    # 저장
    # -----------------------------------------------------

    df.to_csv(
        output_csv_path,
        index=False,
        encoding="utf-8-sig",
    )

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(
            rows,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n[PRIORITY SCORING DONE]")
    print("CSV :", output_csv_path)
    print("JSON:", output_json_path)

    return {
        "status": "success",
        "rows": len(df),
        "output_csv_path": str(output_csv_path),
        "output_json_path": str(output_json_path),
    }


# # =========================================================
# # 단독 테스트
# # =========================================================

# if __name__ == "__main__":

#     BASE_DIR = Path(__file__).resolve().parent.parent

#     TEST_JOB_DIR = (
#         BASE_DIR
#         / "outputs"
#         / "jobs"
#         / "job_d6546ab3"
#     )

#     run_priority_scoring(TEST_JOB_DIR)