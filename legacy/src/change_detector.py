import json
import pandas as pd
from pathlib import Path

from src.category_analysis_rules import CATEGORY_ANALYSIS_RULES


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


def count_by_category(df, prefix):
    if df.empty or "카테고리" not in df.columns:
        return pd.DataFrame(columns=["카테고리", f"{prefix}_word_count"])

    out = (
        df.groupby("카테고리")
        .size()
        .reset_index(name=f"{prefix}_word_count")
    )

    return out


def count_by_category_source(df, prefix):
    if df.empty or "카테고리" not in df.columns or "source_type" not in df.columns:
        return pd.DataFrame(columns=["카테고리"])

    out = (
        df.groupby(["카테고리", "source_type"])
        .size()
        .reset_index(name="count")
        .pivot_table(
            index="카테고리",
            columns="source_type",
            values="count",
            fill_value=0
        )
        .reset_index()
    )

    rename_map = {
        col: f"{prefix}_{col}_count"
        for col in out.columns
        if col != "카테고리"
    }

    out = out.rename(columns=rename_map)

    return out


def detect_word_changes(t_df, t_minus_1_df):
    t_words = set(t_df["단어"].dropna().astype(str)) if "단어" in t_df.columns else set()
    prev_words = set(t_minus_1_df["단어"].dropna().astype(str)) if "단어" in t_minus_1_df.columns else set()

    new_words = sorted(list(t_words - prev_words))
    removed_words = sorted(list(prev_words - t_words))
    common_words = sorted(list(t_words & prev_words))

    return {
        "new_words": new_words,
        "removed_words": removed_words,
        "common_words": common_words,
    }


def build_category_change_table(t_df, t_minus_1_df):
    t_count = count_by_category(t_df, "t")
    prev_count = count_by_category(t_minus_1_df, "t_minus_1")

    change_df = pd.merge(prev_count, t_count, on="카테고리", how="outer").fillna(0)

    change_df["t_minus_1_word_count"] = change_df["t_minus_1_word_count"].astype(int)
    change_df["t_word_count"] = change_df["t_word_count"].astype(int)

    change_df["word_diff"] = (
        change_df["t_word_count"]
        - change_df["t_minus_1_word_count"]
    )

    change_df["word_growth_rate"] = (
        change_df["word_diff"]
        / change_df["t_minus_1_word_count"].replace(0, 1)
    )

    change_df["new_category_flag"] = (
        (change_df["t_word_count"] > 0)
        & (change_df["t_minus_1_word_count"] == 0)
    ).astype(int)

    change_df["removed_category_flag"] = (
        (change_df["t_word_count"] == 0)
        & (change_df["t_minus_1_word_count"] > 0)
    ).astype(int)

    t_source = count_by_category_source(t_df, "t")
    prev_source = count_by_category_source(t_minus_1_df, "t_minus_1")

    change_df = change_df.merge(t_source, on="카테고리", how="left")
    change_df = change_df.merge(prev_source, on="카테고리", how="left")
    change_df = change_df.fillna(0)

    return change_df.sort_values("word_diff", ascending=False)


def attach_analysis_rules(change_df):
    rows = []

    for _, row in change_df.iterrows():
        category = row["카테고리"]
        rule = CATEGORY_ANALYSIS_RULES.get(category, {})

        rows.append({
            "카테고리": category,
            "t_minus_1_word_count": int(row.get("t_minus_1_word_count", 0)),
            "t_word_count": int(row.get("t_word_count", 0)),
            "word_diff": int(row.get("word_diff", 0)),
            "word_growth_rate": float(row.get("word_growth_rate", 0)),
            "new_category_flag": int(row.get("new_category_flag", 0)),
            "removed_category_flag": int(row.get("removed_category_flag", 0)),
            "analysis_methods": rule.get("analysis_methods", []),
            "formulas": rule.get("formulas", {}),
        })

    return rows


def run_change_detection(job_dir):
    job_dir = Path(job_dir)

    t_path = job_dir / "t" / "category_mapped" / "total_noun_category_mapped.csv"
    prev_path = job_dir / "t_minus_1" / "category_mapped" / "total_noun_category_mapped.csv"

    output_dir = job_dir / "change_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    t_df = read_csv_safely(t_path)
    prev_df = read_csv_safely(prev_path)

    word_changes = detect_word_changes(t_df, prev_df)
    change_df = build_category_change_table(t_df, prev_df)
    rule_rows = attach_analysis_rules(change_df)

    change_csv_path = output_dir / "category_change_table.csv"
    change_json_path = output_dir / "category_change_analysis.json"

    change_df.to_csv(change_csv_path, index=False, encoding="utf-8-sig")

    result = {
        "status": "success",
        "t_rows": len(t_df),
        "t_minus_1_rows": len(prev_df),
        "new_words": word_changes["new_words"],
        "removed_words": word_changes["removed_words"],
        "category_analysis": rule_rows,
        "change_csv_path": str(change_csv_path),
        "change_json_path": str(change_json_path),
    }

    with open(change_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[CHANGE DETECTION DONE]")
    print("CSV :", change_csv_path)
    print("JSON:", change_json_path)

    return result


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEST_JOB_DIR = BASE_DIR / "outputs" / "jobs" / "job_22452cb4"

    run_change_detection(TEST_JOB_DIR)