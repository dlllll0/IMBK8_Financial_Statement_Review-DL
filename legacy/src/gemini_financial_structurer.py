# -*- coding: utf-8 -*-

from pathlib import Path
import csv
import re
import pandas as pd


def read_raw_rows(input_csv: str | Path):
    input_csv = Path(input_csv)

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            with open(input_csv, "r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                return [
                    [str(x).strip() for x in row]
                    for row in reader
                    if any(str(x).strip() for x in row)
                ]
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError("csv", b"", 0, 1, f"인코딩 실패: {input_csv}")


def clean_cell(x):
    x = str(x or "").strip()
    x = x.replace("\ufeff", "")
    return x


def make_unique_columns(cols):
    out = []
    seen = {}

    for i, c in enumerate(cols):
        c = clean_cell(c)

        if not c:
            c = f"col_{i}"

        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 0

        out.append(c)

    return out


def normalize_table_name(name: str) -> str:
    name = clean_cell(name)

    if not name:
        return "재무제표"

    mapping = {
        "대차대조표": "재무상태표",
        "재무상태표": "재무상태표",
        "손익계산서": "손익계산서",
        "포괄손익계산서": "포괄손익계산서",
        "자본변동표": "자본변동표",
        "현금흐름표": "현금흐름표",
        "결손금처리계산서": "결손금처리계산서",
        "이익잉여금처분계산서": "이익잉여금처분계산서",
    }

    for k, v in mapping.items():
        if k in name:
            return v

    return name


def is_metadata_row(row):
    joined = ",".join(row).lower()
    meta_words = [
        "printed_start_page",
        "printed_end_page",
        "pdf_start_page",
        "pdf_end_page",
        "gemini_model",
        "source",
    ]
    return sum(w in joined for w in meta_words) >= 2


def parse_gemini_financial_csv(input_csv: str | Path):
    rows = read_raw_rows(input_csv)

    tables = {}
    current_table = None
    header = None
    data_rows = []

    def flush():
        nonlocal current_table, header, data_rows

        if not current_table or not header or not data_rows:
            header = None
            data_rows = []
            return

        max_len = max(
            len(header),
            max(len(r) for r in data_rows)
        )

        header_fixed = header + [f"col_{i}" for i in range(len(header), max_len)]
        header_fixed = make_unique_columns(header_fixed)

        fixed_rows = []
        for r in data_rows:
            fixed_rows.append(r + [""] * (max_len - len(r)))

        df = pd.DataFrame(fixed_rows, columns=header_fixed)
        df.insert(0, "table_name", current_table)

        # 완전 빈 행 제거
        df = df[df.astype(str).apply(lambda x: "".join(x).strip(), axis=1) != ""]

        if not df.empty:
            tables[current_table] = df

        header = None
        data_rows = []

    for row in rows:
        row = [clean_cell(x) for x in row]

        if not row or is_metadata_row(row):
            continue

        first = row[0]

        # 표 시작
        if first == "표구분":
            flush()
            current_table = normalize_table_name(row[1] if len(row) > 1 else "재무제표")
            header = None
            data_rows = []
            continue

        # 헤더
        if first in ["항목", "계정과목", "과목"]:
            header = row
            continue

        # 표구분 없이 바로 표가 시작된 경우 대비
        if current_table is None:
            # 첫 컬럼이 재무제표명인 경우
            if any(x in first for x in ["재무상태표", "대차대조표", "손익계산서", "자본변동표", "현금흐름표", "결손금처리계산서"]):
                current_table = normalize_table_name(first)
                continue
            else:
                continue

        # 헤더가 아직 없는데 데이터가 온 경우
        if header is None:
            # 기수 컬럼처럼 보이면 헤더로 간주
            if any("제" in x and "기" in x for x in row) or first in ["항목", "계정과목", "과목"]:
                header = row
            continue

        data_rows.append(row)

    flush()

    return tables


def save_gemini_tables(input_csv: str | Path, output_dir: str | Path):
    input_csv = Path(input_csv)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tables = parse_gemini_financial_csv(input_csv)

    all_dfs = []

    for table_name, df in tables.items():
        safe_name = re.sub(r'[\\/:*?"<>| ]+', "_", table_name)
        out_path = output_dir / f"{safe_name}.csv"

        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        all_dfs.append(df)

    if all_dfs:
        total_df = pd.concat(all_dfs, ignore_index=True)
    else:
        total_df = pd.DataFrame()

    total_path = output_dir / "gemini_financial_tables_all.csv"
    total_df.to_csv(total_path, index=False, encoding="utf-8-sig")

    print("[GEMINI FINANCIAL STRUCTURE DONE]")
    print("input:", input_csv)
    print("output_dir:", output_dir)
    print("tables:", list(tables.keys()))
    print("total_rows:", len(total_df))
    print("total:", total_path)

    return {
        "status": "success",
        "input_csv": str(input_csv),
        "output_dir": str(output_dir),
        "total_path": str(total_path),
        "tables": list(tables.keys()),
        "rows": len(total_df),
    }


def run_gemini_financial_structurer(job_dir: str | Path, period_type: str = "t"):
    job_dir = Path(job_dir)

    financial_dir = job_dir / period_type / "financial"

    if not financial_dir.exists():
        return {
            "status": "missing",
            "reason": f"financial 폴더 없음: {financial_dir}",
        }

    candidates = [
        p for p in financial_dir.glob("*.csv")
        if "_gemini" in p.name.lower()
    ]

    # OCR records the actual raw CSV location; do not guess its output folder.
    if not candidates:
        for metadata in financial_dir.glob('*financial_value_data.csv'):
            frame = pd.read_csv(metadata, encoding='utf-8-sig')
            if 'raw_csv_save_path' not in frame.columns:
                continue
            for value in frame['raw_csv_save_path'].dropna().unique():
                candidate = Path(str(value)).resolve()
                if candidate.is_relative_to(job_dir.resolve()) and candidate.is_file():
                    candidates.append(candidate)
        candidates = list(dict.fromkeys(candidates))

    if not candidates:
        return {
            "status": "missing",
            "reason": f"Gemini 원본 CSV 없음: {financial_dir}",
            "files": [p.name for p in financial_dir.glob("*.csv")],
        }

    input_csv = sorted(
        candidates,
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]

    output_dir = job_dir / period_type / "financial_gemini_structured"

    return save_gemini_tables(input_csv, output_dir)


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent

    TEST_JOB_DIR = (
        BASE_DIR
        / "outputs"
        / "jobs"
        / "job_3957f32c"
    )

    print(run_gemini_financial_structurer(TEST_JOB_DIR, "t"))
    print(run_gemini_financial_structurer(TEST_JOB_DIR, "t_minus_1"))
