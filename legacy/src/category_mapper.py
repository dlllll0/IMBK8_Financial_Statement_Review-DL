# category_mapper.py

import re
from pathlib import Path

import pandas as pd
import pandas.errors

from category_vocab import CATEGORY_VOCAB


def normalize_word(text):
    if pd.isna(text):
        return ""

    text = str(text).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[()\[\]{}·ㆍ,./\\\-_:;]", "", text)

    return text


def read_csv_safely(path):
    path = Path(path)

    if not path.exists():
        print(f"[SKIP] 파일 없음: {path}")
        return None

    if path.stat().st_size == 0:
        print(f"[EMPTY FILE] {path}")
        return None

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            df = pd.read_csv(path, encoding=enc)

            if df is None or len(df.columns) == 0:
                print(f"[NO COLUMNS] {path}")
                return None

            df.columns = df.columns.astype(str).str.strip()
            return df

        except pandas.errors.EmptyDataError:
            print(f"[EMPTY DATA] {path}")
            return None

        except UnicodeDecodeError:
            continue

    print(f"[READ FAIL] {path}")
    return None

def find_word_col(df):
    candidates = [
        "단어",
        "명사",
        "noun",
        "NOUN",
        "word",
        "WORD",
        "token",
        "TOKEN",
        "term",
        "TERM",
        "키워드",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    raise ValueError(f"단어 컬럼을 찾지 못함: {df.columns.tolist()}")


def map_one_noun_file(source_type, input_path):
    input_path = Path(input_path)

    if not input_path.exists():
        print(f"[SKIP] 파일 없음: {input_path}")
        return None

    df = read_csv_safely(input_path)

    if df is None:
        return None
    
    if len(df) == 0:
        print(f"[SKIP] 빈 파일: {input_path}")
        return None

    word_col = find_word_col(df)

    df[word_col] = df[word_col].astype(str).str.strip()
    df["단어_norm"] = df[word_col].apply(normalize_word)

    # 핵심: 보캡에 있는 명사만 카테고리 매핑
    df["카테고리"] = df["단어_norm"].map(CATEGORY_VOCAB)

    # 매핑 안 된 명사는 전부 제거
    df = df[df["카테고리"].notna()].copy()

    if len(df) == 0:
        print(f"[EMPTY] 매핑 성공 없음: {source_type}")
        return None

    df["source_type"] = source_type
    df = df.rename(columns={word_col: "단어"})

    keep_cols = []

    for col in [
        "file_name",
        "company_name",
        "corp_name",
        "stock_code",
        "corp_code",
        "report_nm",
        "year",
        "단어",
        "단어_norm",
        "카테고리",
        "source_type",
        "count",
        "freq",
        "pos",
    ]:
        if col in df.columns:
            keep_cols.append(col)

    df = df[keep_cols].drop_duplicates().reset_index(drop=True)

    print(f"[OK] {source_type}: {len(df):,}행 매핑")

    return df


def run_category_mapping(period_dir):
    """
    period_dir 예:
    DL_final/outputs/jobs/job_xxxxxxxx/t
    DL_final/outputs/jobs/job_xxxxxxxx/t_minus_1

    입력:
    period_dir/text_analysis/audit_noun_data.csv
    period_dir/text_analysis/correction_noun_data.csv
    period_dir/text_analysis/notes_noun_data.csv

    출력:
    period_dir/category_mapped/total_noun_category_mapped.csv
    """

    period_dir = Path(period_dir)

    text_analysis_dir = period_dir / "text_analysis"
    output_dir = period_dir / "category_mapped"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "total_noun_category_mapped.csv"

    input_files = {
        "audit": text_analysis_dir / "audit_noun_data.csv",
        "correction": text_analysis_dir / "correction_noun_data.csv",
        "notes": text_analysis_dir / "notes_noun_data.csv",
    }

    mapped_list = []

    print("=" * 80)
    print("[CATEGORY MAPPING START]")
    print("period_dir:", period_dir)
    print("text_analysis_dir:", text_analysis_dir)
    print("=" * 80)

    if not text_analysis_dir.exists():
        print("[CATEGORY MAPPING DONE] text_analysis 폴더 없음:", text_analysis_dir)
        return {
            "status": "empty",
            "reason": "text_analysis_dir_not_found",
            "output_path": None,
            "rows": 0,
        }

    for source_type, input_path in input_files.items():
        mapped_df = map_one_noun_file(source_type, input_path)

        if mapped_df is not None and len(mapped_df) > 0:
            mapped_list.append(mapped_df)

    if not mapped_list:
        print("[CATEGORY MAPPING DONE] 매핑된 데이터 없음")
        return {
            "status": "empty",
            "reason": "no_mapped_rows",
            "output_path": None,
            "rows": 0,
        }

    total_df = pd.concat(mapped_list, ignore_index=True)
    total_df = total_df.drop_duplicates().reset_index(drop=True)

    total_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print("=" * 80)
    print("[CATEGORY MAPPING DONE]")
    print("output_path:", output_path)
    print("rows:", len(total_df))
    print("=" * 80)

    return {
        "status": "success",
        "output_path": str(output_path),
        "rows": len(total_df),
    }


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent

    # 단독 테스트할 때만 사용
    # 예: outputs/jobs/job_30c04b44/t
    test_period_dir = base_dir / "outputs" / "jobs" / "job_30c04b44" / "t"

    run_category_mapping(test_period_dir)
