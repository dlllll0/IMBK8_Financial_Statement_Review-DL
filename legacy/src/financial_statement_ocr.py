"""
financial_statement_ocr.py

재무제표 표 OCR 전용 모듈.
- HTML/FastAPI에서 t_minus_1, t로 받은 PDF의 period_type을 그대로 받아 결과 컬럼에 적재
- 재무제표 표는 Gemini API로 추출
- 기본 목차 기반 추출이 실패하면 태양 디버깅 fallback 로직으로 2차 추출
- 출력: financial_value_data DataFrame 및 CSV/JSON/XLSX 저장

필수 설치:
    pip install -r requirements-ocr.txt (repository root)

GCP 인증:
    로컬/서버에서는 GOOGLE_APPLICATION_CREDENTIALS 환경변수 또는 gcloud auth application-default login 사용
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import warnings
from pathlib import Path
from typing import Optional, Dict, Any

import fitz
import numpy as np
import pandas as pd
import unicodedata
import time
import random


try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

warnings.filterwarnings("ignore")


def wait_if_quota_error(error, retry_idx):
    msg = str(error)

    if "429" in msg or "Resource exhausted" in msg:
        wait = min(90, (2 ** retry_idx) * 10 + random.uniform(0, 5))
        print(f"[429 QUOTA] {wait:.1f}초 대기 후 재시도")
        time.sleep(wait)
        return True

    return False


# =========================================================
# 0. 기본 설정
# =========================================================

BASE_DIR = Path(os.getenv("DART_OCR_BASE_DIR", ".")).resolve()
OUTPUT_DIR = Path(os.getenv("DART_OCR_OUTPUT_DIR", BASE_DIR / "outputs")).resolve()
TEST_OUTPUT_DIR = OUTPUT_DIR / "test_outputs"
DEBUG_DIR = OUTPUT_DIR / "debug_outputs"

# =========================================================
# Google Credential 자동 연결
# =========================================================

SERVICE_ACCOUNT_PATH = BASE_DIR / "service-account.json"

if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    pass  # Caller provided credentials outside the job/output folders.
elif SERVICE_ACCOUNT_PATH.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(SERVICE_ACCOUNT_PATH)
    print(f"[GOOGLE CREDENTIAL LOADED] {SERVICE_ACCOUNT_PATH}")
else:
    print(f"[WARN] service-account.json 없음: {SERVICE_ACCOUNT_PATH}")

TEMP_FINANCIAL_PDF_DIR = OUTPUT_DIR / "temp_financial_pages"
GEMINI_TABLE_OUTPUT_DIR = OUTPUT_DIR / "gemini_financial_table_csv"
IMAGE_SAVE_DIR = OUTPUT_DIR / "saved_page_images"

for _d in [
    OUTPUT_DIR,
    TEST_OUTPUT_DIR,
    DEBUG_DIR,
    TEMP_FINANCIAL_PDF_DIR,
    GEMINI_TABLE_OUTPUT_DIR,
    IMAGE_SAVE_DIR,
]:
    _d.mkdir(parents=True, exist_ok=True)


PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "")
LOCATION = os.getenv("VERTEX_LOCATION", "global")
MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")

model = None


def init_gemini(
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    model_name: Optional[str] = None,
):
    """
    Gemini/Vertex AI 모델 초기화.
    FastAPI 시작 시 1번 호출하거나, run_financial_ocr_with_fallback 내부에서 자동 호출됨.
    """
    global PROJECT_ID, LOCATION, MODEL_NAME, model

    PROJECT_ID = project_id or PROJECT_ID
    LOCATION = location or LOCATION
    MODEL_NAME = model_name or MODEL_NAME

    if genai is None or types is None:
        raise ImportError(
            "google-genai 설치가 필요합니다. "
            "pip install google-genai"
        )

    if os.getenv('REVIEW_AI_PROVIDER','vertex') != 'gemini' and not PROJECT_ID:
        raise ValueError(
            "VERTEX_PROJECT_ID가 비어 있습니다. "
            "환경변수 VERTEX_PROJECT_ID를 설정하거나 init_gemini(project_id=...)로 넘겨주세요."
        )

    if os.getenv('REVIEW_AI_PROVIDER','vertex') == 'gemini':
        if not os.getenv('GEMINI_API_KEY'):raise ValueError('GEMINI_API_KEY is required')
        model = genai.Client(vertexai=False, api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(timeout=120000))
    else:
        model = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION, http_options=types.HttpOptions(timeout=120000))
    print(f"[Gemini READY] project={PROJECT_ID}, location={LOCATION}, model={MODEL_NAME}")
    return model



# =========================================================
# 6. 기본 유틸 / 메타데이터
# =========================================================

INDUSTRIES = [
    "제조업",
    "금융업",
    "유통_소매",
    "건설업",
    "IT_정보통신",
    "숙박_서비스"
]


def normalize_text(s):
    s = str(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_extracted_text(text):
    """
    PyMuPDF/PaddleOCR 공통 텍스트 정리.
    특히 주석 쪽에서 OCR row 구분자처럼 섞이는 | 제거.
    """
    text = str(text)
    text = text.replace("|", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_safe_name(name):
    name = str(name)
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:150]


def norm_path_text(x):
    """한글 자모 분리(NFD) 경로를 NFC로 정규화."""
    return unicodedata.normalize("NFC", str(x))


def get_data_type(pdf_path):
    path_str = norm_path_text(pdf_path)

    if "정상" in path_str:
        return "normal"

    if "회계이상" in path_str or ("회계" in path_str and "이상" in path_str):
        return "abnormal"

    return "unknown"


def get_industry(pdf_path):
    """
    정상기업은 보통 정상기업/업종명/PDF 구조.
    회계이상기업은 업종 폴더가 없으므로 미분류.
    """
    parts = [norm_path_text(p) for p in Path(pdf_path).parts]

    if "정상기업" in parts:
        idx = parts.index("정상기업")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    return "미분류"


def get_report_type(pdf_path):
    """
    [정정]사업보고서처럼 파일명에 정정과 사업보고서가 같이 있으면 정정을 우선.
    """
    name = norm_path_text(Path(pdf_path).name)

    if "정정" in name:
        return "CORRECTION"
    if "사업보고서" in name:
        return "BUSINESS"
    if "감사보고서" in name:
        return "AUDIT"

    return "UNKNOWN"


def extract_company_name(pdf_path):
    """파일명 첫 대괄호 안 기업명 추출."""
    name = norm_path_text(Path(pdf_path).name)
    m = re.search(r"\[([^\]]+)\]", name)
    if m:
        return m.group(1)
    return Path(name).stem

# =========================================================
# 7. PDF 텍스트 추출 / printed page 매핑
# =========================================================
# PyMuPDF text layer 기반.


def extract_page_texts(pdf_path):
    doc = fitz.open(str(pdf_path))
    rows = []

    for i, page in enumerate(doc, start=1):
        rows.append({
            "file_name": Path(pdf_path).name,
            "pdf_page": i,
            "text": clean_extracted_text(page.get_text("text"))
        })

    doc.close()
    return pd.DataFrame(rows)


def detect_printed_page_from_text(text):
    text = str(text)

    matches = re.findall(r"Page\s*(\d+)", text, flags=re.IGNORECASE)
    if matches:
        return int(matches[-1])

    tail = "\n".join(text.split("\n")[-6:])
    matches = re.findall(r"(?:^|\s)[-–—]?\s*(\d{1,4})\s*[-–—]?\s*$", tail, flags=re.MULTILINE)
    if matches:
        return int(matches[-1])

    return None


def build_page_mapping(pdf_path):
    doc = fitz.open(str(pdf_path))
    rows = []

    for pdf_page, page in enumerate(doc, start=1):
        text = page.get_text("text")
        printed_page = detect_printed_page_from_text(text)

        rows.append({
            "file_name": Path(pdf_path).name,
            "pdf_page": pdf_page,
            "printed_page": printed_page
        })

    doc.close()
    return pd.DataFrame(rows)


def printed_to_pdf_page(printed_page, page_mapping_df):
    hit = page_mapping_df[page_mapping_df["printed_page"] == printed_page]
    if len(hit) > 0:
        return int(hit.iloc[0]["pdf_page"])
    return None


def infer_pdf_page_by_offset(printed_page, page_mapping_df):
    valid = page_mapping_df.dropna(subset=["printed_page"]).copy()
    if len(valid) == 0:
        return None

    valid["printed_page"] = valid["printed_page"].astype(int)
    valid["offset"] = valid["pdf_page"] - valid["printed_page"]
    valid["dist"] = (valid["printed_page"] - int(printed_page)).abs()

    nearest = valid.sort_values("dist").iloc[0]
    estimated = int(printed_page) + int(nearest["offset"])
    max_pdf = int(page_mapping_df["pdf_page"].max())

    if 1 <= estimated <= max_pdf:
        return estimated
    return None

# =========================================================
# 8. 목차 탐색 / 섹션 인덱싱
# =========================================================
# 핵심:
# - 정 정 신 고 (보고), 감 사 보 고 서, (첨부)재 무 제 표 처럼 글자 사이 공백이 있는 제목도 인식
# - 연결재무제표 / 연결재무제표 주석도 대상에 포함
# - 내부회계관리제도, 외부감사 실시내용 등은 boundary로만 사용해서 앞 섹션의 끝 페이지를 정확히 잡음


def is_real_toc_line(line):
    """
    진짜 목차형 줄만 통과.
    예:
    정 정 신 고 (보고) ........ 1
    감 사 보 고 서 ............ 5
    (첨부)재 무 제 표 ......... 9
    주석 ...................... 15
    4. 재무제표 ............... 209
    """
    line = normalize_text(line)

    # 점선/말줄임 뒤에 페이지 숫자로 끝나는 줄이면 목차 후보
    toc_regex = re.compile(r"^\s*.+?[\.·…]{2,}\s*\d{1,4}\s*$")
    return bool(toc_regex.match(line))


def clean_toc_title(raw_title):
    title = normalize_text(raw_title)

    # 앞 번호 제거
    title = re.sub(r"^[IVXLC]+\.\s*", "", title)
    title = re.sub(r"^\d+\.\s*", "", title)

    # 뒤 점선/페이지 제거
    title = re.sub(r"[\.·…]+[\s\.]*\d{1,4}\s*$", "", title)
    title = re.sub(r"\s+\d{1,4}\s*$", "", title)
    title = normalize_text(title)

    # 비교용: 모든 공백 제거
    compact = re.sub(r"\s+", "", title)

    if "정정신고" in compact or "정정" in compact:
        return "정정신고"

    if "연결재무제표주석" in compact:
        return "연결재무제표 주석"

    if "재무제표주석" in compact or compact == "주석":
        return "재무제표 주석"

    if "연결재무제표" in compact:
        return "연결재무제표"

    if "재무제표" in compact:
        return "재무제표"

    if "감사보고서" in compact or "감사의견" in compact or "회계감사인의감사의견" in compact:
        return "감사보고서"

    if "내부회계관리제도" in compact:
        return "내부회계관리제도"

    if "외부감사실시내용" in compact:
        return "외부감사 실시내용"

    return title


def detect_section_type_from_title(title):
    title = normalize_text(title)
    compact = re.sub(r"\s+", "", title)

    if "정정신고" in compact or "정정" in compact:
        return "correction_report"

    if "연결재무제표주석" in compact:
        return "notes"

    if "연결재무제표" in compact:
        return "financial_statement"

    if "재무제표주석" in compact or compact == "주석":
        return "notes"

    if "재무제표" in compact:
        return "financial_statement"

    if "감사보고서" in compact or "감사의견" in compact or "회계감사인의감사의견" in compact:
        return "audit_report"

    # 섹션 끝 경계 잡기용. 실제 추출은 하지 않음.
    if "내부회계관리제도" in compact or "외부감사실시내용" in compact:
        return "boundary"

    return None


def extract_toc_df(page_df, max_search_pages=40):
    rows = []
    search_df = page_df[page_df["pdf_page"] <= max_search_pages]

    for _, row in search_df.iterrows():
        pdf_page = int(row["pdf_page"])
        text = str(row["text"])

        for line in text.split("\n"):
            line = normalize_text(line)
            if not is_real_toc_line(line):
                continue

            nums = re.findall(r"\d{1,4}", line)
            if not nums:
                continue

            printed_start_page = int(nums[-1])
            title = clean_toc_title(line)
            section_type = detect_section_type_from_title(title)

            if section_type is None:
                continue

            rows.append({
                "toc_pdf_page": pdf_page,
                "section_type": section_type,
                "section_title": title,
                "printed_start_page": printed_start_page,
                "raw_line": line
            })

    toc_df = pd.DataFrame(rows)
    if len(toc_df) == 0:
        return toc_df

    toc_df = toc_df.sort_values("printed_start_page").reset_index(drop=True)
    toc_df = toc_df.drop_duplicates(subset=["section_type", "section_title", "printed_start_page"], keep="first")
    toc_df = toc_df.sort_values("printed_start_page").reset_index(drop=True)
    return toc_df


def filter_target_sections(toc_df):
    """
    target + boundary를 같이 유지한다.
    boundary는 추출하지 않고, 앞 섹션의 end_page 계산에만 사용한다.
    """
    if toc_df is None or len(toc_df) == 0:
        return pd.DataFrame()

    target = toc_df[
        toc_df["section_type"].isin([
            "financial_statement",
            "notes",
            "correction_report",
            "audit_report",
            "boundary"
        ])
    ].copy()

    target = target.sort_values("printed_start_page").reset_index(drop=True)
    return target


def make_section_ranges(target_toc_df, page_mapping_df, max_last_section_pages=100):
    if target_toc_df is None or len(target_toc_df) == 0:
        return pd.DataFrame()

    df = target_toc_df.sort_values("printed_start_page").reset_index(drop=True)
    rows = []

    for i, row in df.iterrows():
        start_printed = int(row["printed_start_page"])

        # 현재 start보다 큰 다음 printed_start_page만 다음 섹션으로 인정
        later_pages = df.loc[
            df["printed_start_page"].astype(int) > start_printed,
            "printed_start_page"
        ]

        if len(later_pages) > 0:
            end_printed = int(later_pages.min()) - 1
        else:
            end_printed = start_printed + max_last_section_pages

        start_pdf = printed_to_pdf_page(start_printed, page_mapping_df)
        end_pdf = printed_to_pdf_page(end_printed, page_mapping_df)

        if start_pdf is None:
            start_pdf = infer_pdf_page_by_offset(start_printed, page_mapping_df)

        if end_pdf is None:
            end_pdf = infer_pdf_page_by_offset(end_printed, page_mapping_df)

        if start_pdf is None:
            print("페이지 매핑 실패:", row["section_title"], start_printed)
            continue

        if end_pdf is None:
            max_pdf = int(page_mapping_df["pdf_page"].max())
            end_pdf = min(start_pdf + max_last_section_pages, max_pdf)

        rows.append({
            **row.to_dict(),
            "printed_end_page": end_printed,
            "pdf_start_page": int(start_pdf),
            "pdf_end_page": int(end_pdf)
        })

    out = pd.DataFrame(rows)

    # 그래도 잘못된 range는 마지막 방어로 제거
    if len(out) > 0:
        out = out[
            out["pdf_end_page"] >= out["pdf_start_page"]
        ].reset_index(drop=True)

    return out

# =========================================================
# 9. PDF 페이지 추출 유틸
# =========================================================
# 기존 PaddleOCR용 이미지 저장 대신,
# 재무제표 페이지만 임시 PDF로 잘라 Gemini에 전달한다.


def extract_pdf_pages_to_temp_pdf(
    input_pdf_path,
    start_page,
    end_page,
    output_pdf_path
):
    """
    start_page, end_page는 사람이 보는 PDF 페이지 번호 기준.
    예: 1페이지부터 시작.

    반환:
    - output_pdf_path
    """
    input_pdf_path = Path(input_pdf_path)
    output_pdf_path = Path(output_pdf_path)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(input_pdf_path))
    new_doc = fitz.open()

    total_pages = len(doc)

    start_idx = max(int(start_page) - 1, 0)
    end_idx = min(int(end_page) - 1, total_pages - 1)

    if start_idx > end_idx:
        doc.close()
        raise ValueError(
            f"페이지 범위 오류: start={start_page}, end={end_page}, total={total_pages}"
        )

    for page_idx in range(start_idx, end_idx + 1):
        new_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)

    new_doc.save(str(output_pdf_path))
    new_doc.close()
    doc.close()

    return output_pdf_path


def pdf_page_to_image(pdf_path, page_num, output_dir=IMAGE_SAVE_DIR, dpi=180):
    """
    기존 디버깅용 이미지 저장 함수.
    Gemini 추출에서는 기본적으로 사용하지 않지만,
    특정 페이지 육안 확인이 필요할 때 사용 가능.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = make_safe_name(Path(pdf_path).stem)
    save_path = output_dir / f"{safe_name}_page_{page_num}.png"

    if save_path.exists():
        return save_path

    images = convert_from_path(
        str(pdf_path),
        first_page=int(page_num),
        last_page=int(page_num),
        dpi=dpi
    )
    images[0].save(save_path)
    return save_path


# =========================================================
# 10. Gemini 응답 정리 / 호출 함수
# =========================================================

GEMINI_FINANCIAL_PROMPT = """
당신은 숙련된 금융 재무제표 OCR 검수자이자 표 복원 전문가입니다.

제공된 PDF에는 재무제표 본문 페이지만 포함되어 있습니다.
PDF 안의 모든 재무제표 표를 원본 표 구조에 가깝게 추출하세요.

추출 대상:
- 재무상태표
- 연결재무상태표
- 포괄손익계산서
- 연결포괄손익계산서
- 손익계산서
- 연결손익계산서
- 자본변동표
- 연결자본변동표
- 현금흐름표
- 연결현금흐름표
- 기타 재무제표 본문 표

출력 규칙:
1. 출력은 순수 CSV만 작성하세요.
2. 설명문, 마크다운 코드블록, 빈 줄, 표 기호는 절대 쓰지 마세요.
3. 여러 표가 있으면 하나의 CSV 안에 이어서 출력하세요.
4. 각 표 시작 전 반드시 표 제목 행을 추가하세요.

예시:
표구분,연결재무상태표
항목,제13기,제12기,제11기
자산,
유동자산,9020235097,8016491361,11402317495
현금및현금성자산,3154016225,2807247102,3727650298

표구분,연결포괄손익계산서
항목,제13기,제12기,제11기
매출액,123456789,987654321,555555555
영업이익,11111111,22222222,33333333

OCR 검수 규칙:
5. 숫자는 원문 기준으로 최대한 정확히 복원하세요.
6. 천 단위 쉼표는 CSV 충돌 방지를 위해 제거하세요.
   예: 3,120,123 → 3120123
7. 소수점(.)은 유지하세요.
   예: 12.35 → 12.35
8. 숫자 1을 쉼표, 점, 세로획, 문자 I, l 과 혼동하지 마세요.
9. 같은 셀 안에서 숫자가 띄어져 보이면 하나로 합치세요.
   예: 312 123521 → 312123521
10. 괄호 음수는 음수로 변환하세요.
   예: (123456) → -123456
11. 한글 항목명은 깨뜨리지 말고 원문 의미가 유지되게 복원하세요.
12. 주석번호는 항목명에서 제거해도 됩니다.
   예: 매출채권및기타채권(주5,6,7) → 매출채권및기타채권
13. 항목명이 여러 줄이면 하나로 합치세요.
14. 읽을 수 없는 셀은 빈칸으로 두세요.
15. 임의로 숫자를 추정하지 마세요.
16. 재무제표 주석의 상세 표는 제외하세요.
17. 최종 출력은 반드시 CSV 데이터만 포함하세요.
"""


def clean_gemini_csv_text(text):
    """Gemini가 실수로 붙이는 ```csv / ``` 제거."""
    if text is None:
        return None

    text = str(text).strip()
    text = re.sub(r"^```csv", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def call_gemini_for_financial_tables(pdf_path, max_retries=2, sleep_sec=5):
    """
    잘라낸 재무제표 PDF를 Gemini에 전달하고 raw CSV 텍스트를 받는다.
    """
    pdf_path = Path(pdf_path)

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    pdf_part = types.Part.from_bytes(
        data=pdf_bytes,
        mime_type="application/pdf"
    )

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = model.models.generate_content(model=MODEL_NAME, contents=[pdf_part, GEMINI_FINANCIAL_PROMPT])
            return clean_gemini_csv_text(response.text)

        except Exception as e:
            last_error = e

            print(f"[Gemini 재시도 {attempt}/{max_retries}] {pdf_path.name}")
            print("에러:", e)

            # 429 quota 대응
            if wait_if_quota_error(e, attempt):
                continue

            time.sleep(sleep_sec)

    print("❌ Gemini 최종 실패:", pdf_path.name)
    print("마지막 에러:", last_error)
    return None


# =========================================================
# 11. 재무제표 RAW 추출: Gemini API
# =========================================================
# 기존 PaddleOCR 기반 row 추출을 Gemini 기반 표 복원으로 교체.
# 반환 컬럼은 기존 저장/통합 로직과 맞추기 위해 raw_text, value_json 등을 유지한다.


def extract_values_from_text(text):
    text = str(text)
    values = re.findall(r"\(?-?\d[\d,]*\)?(?:\.\d+)?", text)
    return values


def extract_account_candidate(raw_text):
    text = str(raw_text)
    parts = re.split(r"\(?-?\d[\d,]*\)?(?:\.\d+)?", text, maxsplit=1)

    if len(parts) > 0:
        account = parts[0]
        account = account.replace("|", " ")
        account = normalize_text(account)
        account = account.strip(",")
        return account
    return ""


def save_gemini_raw_csv(csv_text, pdf_path, section_info, start_page, end_page):
    """Gemini가 복원한 재무제표 CSV 원문 저장.

    1) 기존 공용 폴더에도 저장
    2) 현재 job/period financial 폴더에도 저장
    """
    pdf_path = Path(pdf_path)

    safe_file = make_safe_name(pdf_path.stem)
    section_title = make_safe_name(section_info.get("section_title", "financial_statement"))
    save_name = f"{safe_file}_p{start_page}_{end_page}_{section_title}_gemini.csv"

    # 1. 기존 공용 저장
    common_save_path = GEMINI_TABLE_OUTPUT_DIR / save_name
    common_save_path.parent.mkdir(parents=True, exist_ok=True)

    with open(common_save_path, "w", encoding="utf-8-sig") as f:
        f.write(str(csv_text).strip())

    # 2. job/period financial 폴더 저장
    # pdf_path 예: uploads/job_xxxx/t.pdf
    job_id = pdf_path.parent.name
    period_type = pdf_path.stem

    job_financial_dir = OUTPUT_DIR / "jobs" / job_id / period_type / "financial"
    job_financial_dir.mkdir(parents=True, exist_ok=True)

    job_save_path = job_financial_dir / save_name

    with open(job_save_path, "w", encoding="utf-8-sig") as f:
        f.write(str(csv_text).strip())

    print(f"[Gemini raw CSV 공용 저장] {common_save_path}")
    print(f"[Gemini raw CSV job 저장] {job_save_path}")

    return job_save_path


def gemini_csv_text_to_raw_df(
    csv_text,
    pdf_path,
    start_page,
    end_page,
    section_info,
    temp_pdf_path,
    raw_csv_save_path
):
    """
    Gemini CSV 텍스트를 line 단위 DataFrame으로 변환.
    표마다 컬럼 수가 다를 수 있으므로 pandas read_csv로 강제 파싱하지 않는다.
    """
    rows = []

    lines = [line.strip() for line in str(csv_text).splitlines() if line.strip()]

    current_table = None

    for row_no, line in enumerate(lines, start=1):
        if line.startswith("표구분,"):
            current_table = line.split(",", 1)[1].strip() if "," in line else None

        values = extract_values_from_text(line)

        rows.append({
            **section_info,
            "pdf_page": None,
            "pdf_start_page": int(start_page),
            "pdf_end_page": int(end_page),
            "source": "gemini_api",
            "gemini_model": MODEL_NAME,
            "temp_pdf_path": str(temp_pdf_path),
            "raw_csv_save_path": str(raw_csv_save_path),
            "table_name": current_table,
            "row_group": row_no,
            "raw_text": line,
            "value": values,
            "value_json": json.dumps(values, ensure_ascii=False),
            "value_count": len(values),
            "account_candidate": extract_account_candidate(line)
        })

    return pd.DataFrame(rows)


def extract_financial_raw_section(pdf_path, start_page, end_page, section_info, dpi=180):
    """
    기존 함수명 유지.
    내부 동작만 PaddleOCR → Gemini API로 변경.

    흐름:
    1. 재무제표 page 범위만 임시 PDF로 자름
    2. Gemini로 표 전체 복원
    3. raw CSV 저장
    4. line 단위 DataFrame 반환
    """
    pdf_path = Path(pdf_path)

    try:
        safe_name = make_safe_name(pdf_path.stem)
        section_title = make_safe_name(section_info.get("section_title", "financial_statement"))

        temp_pdf_path = TEMP_FINANCIAL_PDF_DIR / (
            f"{safe_name}_p{int(start_page)}_{int(end_page)}_{section_title}.pdf"
        )

        extract_pdf_pages_to_temp_pdf(
            input_pdf_path=pdf_path,
            start_page=start_page,
            end_page=end_page,
            output_pdf_path=temp_pdf_path
        )

        print(f"[Gemini] 재무제표 PDF 추출 완료: {temp_pdf_path.name}")

        csv_text = call_gemini_for_financial_tables(temp_pdf_path)

        if not csv_text:
            print("[WARN] Gemini 응답 없음:", pdf_path.name)
            return pd.DataFrame()

        raw_csv_save_path = save_gemini_raw_csv(
            csv_text=csv_text,
            pdf_path=pdf_path,
            section_info=section_info,
            start_page=start_page,
            end_page=end_page
        )

        print(f"[Gemini CSV 저장] {raw_csv_save_path}")

        financial_raw_df = gemini_csv_text_to_raw_df(
            csv_text=csv_text,
            pdf_path=pdf_path,
            start_page=start_page,
            end_page=end_page,
            section_info=section_info,
            temp_pdf_path=temp_pdf_path,
            raw_csv_save_path=raw_csv_save_path
        )

        return financial_raw_df

    except Exception as e:
        print("재무제표 Gemini 추출 오류:", Path(pdf_path).name, start_page, end_page, e)
        return pd.DataFrame()


# =========================================================
# 15. 저장 유틸 - 엑셀 + CSV 둘 다 저장
# =========================================================


def convert_complex_columns_for_save(df):
    if df is None:
        return pd.DataFrame()

    out = df.copy()

    if len(out.columns) == 0:
        return pd.DataFrame({"EMPTY": ["NO DATA"]})

    for col in out.columns:
        if out[col].apply(lambda x: isinstance(x, (list, dict, tuple, np.ndarray))).any():
            out[col] = out[col].apply(lambda x: json.dumps(x, ensure_ascii=False))

    return out


def save_final_outputs(result, save_dir, prefix):
    save_dir = Path(save_dir)

    # 저장 폴더 자동 생성
    save_dir.mkdir(parents=True, exist_ok=True)

    # CSV + JSON 저장
    for name, df in result.items():
        if df is None:
            df = pd.DataFrame()

        save_df = convert_complex_columns_for_save(df)

        csv_path = save_dir / f"{prefix}_{name}.csv"
        json_path = save_dir / f"{prefix}_{name}.json"

        save_df.to_csv(
            csv_path,
            index=False,
            encoding="utf-8-sig"
        )

        save_df.to_json(
            json_path,
            orient="records",
            force_ascii=False,
            indent=2
        )

        print(f"[CSV 저장] {csv_path}")
        print(f"[JSON 저장] {json_path}")

    # 통합 Excel 저장
    excel_path = save_dir / f"{prefix}_final_outputs.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for name, df in result.items():
            if df is None:
                df = pd.DataFrame()

            save_df = convert_complex_columns_for_save(df)

            save_df.to_excel(
                writer,
                sheet_name=name[:31],
                index=False
            )

    print(f"[EXCEL 저장] {excel_path}")

# =========================================================
# FINAL OVERRIDE 1. 목차 인식 / 재무제표 only 섹션 필터
# =========================================================
# 이 셀은 기존 detect/filter/make_section_ranges를 덮어씁니다.
# 목적: 연결재무제표가 아니라 '별도 재무제표' 본문만 Gemini로 추출하기.


def clean_toc_title(raw_title):
    title = normalize_text(raw_title)

    # 앞 번호 제거
    title = re.sub(r"^[IVXLC]+\.\s*", "", title)
    title = re.sub(r"^\d+\.\s*", "", title)

    # 뒤 점선/페이지 제거
    title = re.sub(r"[\.·…]+[\s\.]*\d{1,4}\s*$", "", title)
    title = re.sub(r"\s+\d{1,4}\s*$", "", title)
    title = normalize_text(title)

    compact = re.sub(r"\s+", "", title)

    if "정정신고" in compact or "정정" in compact:
        return "정정신고"

    if "연결재무제표주석" in compact:
        return "연결재무제표 주석"

    if "연결재무제표" in compact:
        return "연결재무제표"

    if "재무제표주석" in compact or compact == "주석":
        return "재무제표 주석"

    if "재무제표" in compact:
        return "재무제표"

    if "감사보고서" in compact or "감사의견" in compact or "회계감사인의감사의견" in compact:
        return "감사보고서"

    if "내부회계관리제도" in compact:
        return "내부회계관리제도"

    if "외부감사실시내용" in compact:
        return "외부감사 실시내용"

    return title


def detect_section_type_from_title(title):
    title = normalize_text(title)
    compact = re.sub(r"\s+", "", title)

    # 연결재무제표는 명확히 분리해서 나중에 제외
    if "연결재무제표주석" in compact:
        return "connected_notes"

    if "연결재무제표" in compact:
        return "connected_financial_statement"

    # 별도 재무제표 주석은 추출 대상은 아니지만 재무제표 끝 경계로 사용
    if "재무제표주석" in compact or compact == "주석":
        return "notes"

    # 별도 재무제표 후보
    if "재무제표" in compact:
        return "financial_statement"

    # 경계용
    if "정정신고" in compact or "정정" in compact:
        return "correction_report"

    if "감사보고서" in compact or "감사의견" in compact or "회계감사인의감사의견" in compact:
        return "audit_report"

    if "내부회계관리제도" in compact or "외부감사실시내용" in compact:
        return "boundary"

    return None


def is_true_financial_statement_row(row):
    """
    Gemini로 실제 추출할 '별도 재무제표 본문' 목차행만 True.
    제외 예시:
    - 연결재무제표
    - 연결재무제표 주석
    - 재무제표 주석
    - 재무제표의 승인
    - 별도재무제표 작성기준 및 중요한 회계정책
    """
    raw = normalize_text(row.get("raw_line", ""))
    title = normalize_text(row.get("section_title", ""))

    compact_raw = re.sub(r"\s+", "", raw)
    compact_title = re.sub(r"\s+", "", title)

    # 연결/주석/감사/정정은 제외
    exclude_keywords = [
        "연결", "주석", "감사", "정정", "내부회계", "외부감사",
        "작성기준", "회계정책", "승인", "첨부사항"
    ]

    if any(k in compact_raw for k in exclude_keywords):
        return False

    if any(k in compact_title for k in ["연결", "주석", "감사", "정정"]):
        return False

    # 반드시 재무제표가 들어가야 함
    if "재무제표" not in compact_raw and "재무제표" not in compact_title:
        return False

    return True


def filter_target_sections(toc_df):
    """
    재무제표 only 추출용 필터.
    - 실제 추출: 별도 재무제표 본문만 financial_statement 유지
    - 나머지 재무제표 주석/감사/정정/내부회계 등은 boundary로 유지해서 end_page 계산에만 사용
    - 연결재무제표는 완전히 제외
    """
    if toc_df is None or len(toc_df) == 0:
        return pd.DataFrame()

    rows = []

    for _, row in toc_df.copy().iterrows():
        stype = row.get("section_type")
        raw = normalize_text(row.get("raw_line", ""))
        title = normalize_text(row.get("section_title", ""))
        compact_raw = re.sub(r"\s+", "", raw)
        compact_title = re.sub(r"\s+", "", title)

        # 연결재무제표 계열은 아예 제외
        if stype in ["connected_financial_statement", "connected_notes"]:
            continue
        if "연결재무제표" in compact_raw or "연결재무제표" in compact_title:
            continue

        # 진짜 별도 재무제표 본문만 추출 대상
        if stype == "financial_statement" and is_true_financial_statement_row(row):
            new_row = row.copy()
            new_row["section_type"] = "financial_statement"
            new_row["section_title"] = "재무제표"
            rows.append(new_row)
            continue

        # 그 외 주요 섹션은 재무제표 끝 경계로만 사용
        if stype in ["financial_statement", "notes", "correction_report", "audit_report", "boundary"]:
            new_row = row.copy()
            new_row["section_type"] = "boundary"
            rows.append(new_row)

    if len(rows) == 0:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out = out.sort_values("printed_start_page").reset_index(drop=True)
    return out


def make_section_ranges(target_toc_df, page_mapping_df, max_last_section_pages=100):
    """
    같은 printed_start_page가 연속으로 나와도 깨지지 않게 보완한 range 계산.
    현재 start보다 '큰' 다음 printed_start_page만 다음 섹션으로 인정한다.
    """
    if target_toc_df is None or len(target_toc_df) == 0:
        return pd.DataFrame()

    df = target_toc_df.sort_values("printed_start_page").reset_index(drop=True)
    rows = []

    for _, row in df.iterrows():
        start_printed = int(row["printed_start_page"])

        later_pages = df.loc[
            df["printed_start_page"].astype(int) > start_printed,
            "printed_start_page"
        ]

        if len(later_pages) > 0:
            end_printed = int(later_pages.min()) - 1
        else:
            end_printed = start_printed + max_last_section_pages

        start_pdf = printed_to_pdf_page(start_printed, page_mapping_df)
        end_pdf = printed_to_pdf_page(end_printed, page_mapping_df)

        if start_pdf is None:
            start_pdf = infer_pdf_page_by_offset(start_printed, page_mapping_df)

        if end_pdf is None:
            end_pdf = infer_pdf_page_by_offset(end_printed, page_mapping_df)

        if start_pdf is None:
            print("페이지 매핑 실패:", row.get("section_title"), start_printed)
            continue

        if end_pdf is None:
            max_pdf = int(page_mapping_df["pdf_page"].max())
            end_pdf = min(start_pdf + max_last_section_pages, max_pdf)

        rows.append({
            **row.to_dict(),
            "printed_end_page": end_printed,
            "pdf_start_page": int(start_pdf),
            "pdf_end_page": int(end_pdf)
        })

    out = pd.DataFrame(rows)

    if len(out) > 0:
        out = out[
            out["pdf_end_page"] >= out["pdf_start_page"]
        ].reset_index(drop=True)

    return out


# =========================================================
# FINAL OVERRIDE 2. PDF 1개 재무제표 only 처리 함수
# =========================================================
# 기존 process_one_pdf_full()은 주석/정정/감사까지 처리합니다.
# 이 함수는 Gemini로 '별도 재무제표'만 처리합니다.


def process_one_pdf_financial_only(
    pdf_path,
    dpi=300,
    max_search_pages=80
):
    pdf_path = Path(pdf_path)
    print("\n처리:", pdf_path.name)

    page_df = extract_page_texts(pdf_path)
    page_mapping_df = build_page_mapping(pdf_path)

    toc_df = extract_toc_df(page_df, max_search_pages=max_search_pages)
    target_toc_df = filter_target_sections(toc_df)
    section_df = make_section_ranges(target_toc_df, page_mapping_df)

    if section_df is not None and len(section_df) > 0:
        section_df = section_df[
            section_df["pdf_end_page"] >= section_df["pdf_start_page"]
        ].reset_index(drop=True)

    print("\n[selected financial sections + boundaries]")
    print(section_df.to_string(index=False) if section_df is not None and len(section_df) > 0 else section_df)

    financial_raw_list = []

    if section_df is None or len(section_df) == 0:
        print("[WARN] 재무제표 section 없음:", pdf_path.name)
        return {"financial_value_data": pd.DataFrame()}

    for _, sec in section_df.iterrows():
        section_type = sec["section_type"]

        # boundary는 end_page 계산용. 실제 추출하지 않음.
        if section_type != "financial_statement":
            continue

        raw_line = str(sec.get("raw_line", ""))
        section_title = str(sec.get("section_title", ""))
        compact_raw = re.sub(r"\s+", "", raw_line)
        compact_title = re.sub(r"\s+", "", section_title)

        # 최종 방어: 연결재무제표/주석/회계정책/승인 등 제외
        skip_keywords = ["연결", "주석", "작성기준", "회계정책", "승인", "감사", "정정"]
        if any(k in compact_raw for k in skip_keywords) or any(k in compact_title for k in ["연결", "주석"]):
            print("[SKIP 재무제표 제외 대상]", raw_line)
            continue

        start_page = int(sec["pdf_start_page"])
        end_page = int(sec["pdf_end_page"])

        if end_page < start_page:
            print(f"[SKIP] 잘못된 range: PDF {start_page} ~ {end_page}")
            continue

        section_info = {
            "file_name": pdf_path.name,
            "industry": get_industry(pdf_path),
            "data_type": get_data_type(pdf_path),
            "report_type": get_report_type(pdf_path),
            "section_type": "financial_statement",
            "section_title": "재무제표",
            "printed_start_page": int(sec["printed_start_page"]),
            "printed_end_page": int(sec["printed_end_page"])
        }

        print(f"\n[Gemini API] financial_statement only: PDF {start_page} ~ {end_page}")

        financial_raw_df = extract_financial_raw_section(
            pdf_path=pdf_path,
            start_page=start_page,
            end_page=end_page,
            section_info=section_info,
            dpi=dpi
        )

        print("financial_raw_df:", financial_raw_df.shape)

        if financial_raw_df is not None and len(financial_raw_df) > 0:
            financial_raw_list.append(financial_raw_df)

    if len(financial_raw_list) > 0:
        financial_raw_df = pd.concat(financial_raw_list, ignore_index=True)
    else:
        financial_raw_df = pd.DataFrame()

    return {"financial_value_data": financial_raw_df}




# =========================================================
# 1. period_type 포함 실행 wrapper + fallback
# =========================================================

def _add_period_columns(
    df: pd.DataFrame,
    period_type: str,
    job_id: Optional[str] = None,
    industry: Optional[str] = None,
) -> pd.DataFrame:
    """모든 결과 데이터에 period_type/job_id/industry를 강제로 붙인다."""
    if df is None or len(df) == 0:
        return pd.DataFrame()

    out = df.copy()
    out.insert(0, "period_type", period_type)

    if job_id is not None:
        out.insert(0, "job_id", job_id)

    if industry is not None:
        # 기존 industry 컬럼이 있어도 사용자가 입력한 업종을 우선 적용
        out["industry"] = industry

    return out


def _empty_result(period_type: str, job_id: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    cols = [
        "job_id", "period_type", "file_name", "industry", "data_type", "report_type",
        "section_type", "section_title", "printed_start_page", "printed_end_page",
        "pdf_page", "pdf_start_page", "pdf_end_page", "source", "gemini_model",
        "temp_pdf_path", "raw_csv_save_path", "table_name", "row_group",
        "raw_text", "value", "value_json", "value_count", "account_candidate"
    ]
    if job_id is None:
        cols.remove("job_id")
    return {"financial_value_data": pd.DataFrame(columns=cols)}


def run_financial_ocr_with_fallback(
    pdf_path,
    period_type: str,
    industry: Optional[str] = None,
    job_id: Optional[str] = None,
    dpi: int = 180,
    max_search_pages: int = 80,
    save_dir: Optional[Path] = None,
    save_output: bool = True,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """
    재무제표 표 OCR 메인 함수.

    Parameters
    ----------
    pdf_path:
        업로드된 PDF 저장 경로
    period_type:
        "t" 또는 "t_minus_1" 권장. HTML input name에서 서버가 지정해 넘김.
    industry:
        HTML Form에서 입력받은 업종. 있으면 결과 industry 컬럼에 강제 반영.
    job_id:
        업로드 묶음 ID. t/t-1 두 PDF를 같은 분석 단위로 묶는 값.
    dpi:
        Gemini에 넣을 임시 PDF/이미지 관련 해상도. 기본 180.
    max_search_pages:
        목차 탐색 최대 페이지.
    save_dir:
        결과 저장 폴더.
    save_output:
        True면 CSV/JSON/XLSX 저장.

    Returns
    -------
    {
        "financial_value_data": DataFrame
    }
    """
    if period_type not in {"t", "t_minus_1", "t-1"}:
        raise ValueError("period_type은 't' 또는 't_minus_1'/'t-1'로 넣어주세요.")

    if period_type == "t-1":
        period_type = "t_minus_1"

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일이 없습니다: {pdf_path}")

    # Gemini 모델이 아직 없으면 초기화
    global model
    if model is None:
        init_gemini(project_id=project_id, location=location, model_name=model_name)

    print("=" * 100)
    print("[FINANCIAL OCR START]")
    print("파일:", pdf_path.name)
    print("전체 경로:", pdf_path)
    print("period_type:", period_type)
    print("job_id:", job_id)
    print("industry:", industry)
    print("=" * 100)

    # 1차: 기본 재무제표 only 추출
    result = process_one_pdf_financial_only(
        pdf_path=pdf_path,
        dpi=dpi,
        max_search_pages=max_search_pages,
    )

    financial_df = result.get("financial_value_data", pd.DataFrame())

    # 2차 fallback: 기본 결과가 비었을 때만 실행
    if financial_df is None or financial_df.empty:
        print("[FALLBACK] 기본 추출 결과가 비어 있어서 boundary 재무제표 후보를 다시 추출합니다.")

        page_df = extract_page_texts(pdf_path)
        page_mapping_df = build_page_mapping(pdf_path)

        toc_df = extract_toc_df(page_df, max_search_pages=max_search_pages)
        target_toc_df = filter_target_sections(toc_df)
        section_df = make_section_ranges(target_toc_df, page_mapping_df)

        print("[FALLBACK] section_df")
        if section_df is None or len(section_df) == 0:
            print(section_df)
        else:
            print(section_df.to_string(index=False))

        fallback_raw_list = []

        if section_df is not None and len(section_df) > 0:
            for _, sec in section_df.iterrows():
                raw_line = str(sec.get("raw_line", ""))
                section_title = str(sec.get("section_title", ""))

                compact_raw = re.sub(r"\s+", "", raw_line)
                compact_title = re.sub(r"\s+", "", section_title)

                # 재무제표 관련 행만 후보로 사용
                if "재무제표" not in compact_raw and "재무제표" not in compact_title:
                    continue

                # 주석/감사/정정은 제외
                exclude_keywords = ["주석", "감사", "정정", "내부회계", "외부감사"]
                if any(k in compact_raw for k in exclude_keywords):
                    continue
                if any(k in compact_title for k in exclude_keywords):
                    continue

                start_page = int(sec["pdf_start_page"])
                end_page = int(sec["pdf_end_page"])

                if end_page < start_page:
                    continue

                section_info = {
                    "file_name": pdf_path.name,
                    "industry": industry or get_industry(pdf_path),
                    "data_type": get_data_type(pdf_path),
                    "report_type": get_report_type(pdf_path),
                    "section_type": "financial_statement",
                    "section_title": "재무제표",
                    "printed_start_page": int(sec["printed_start_page"]),
                    "printed_end_page": int(sec["printed_end_page"]),
                }

                print(f"[FALLBACK Gemini API] PDF {start_page} ~ {end_page}")
                print("raw_line:", raw_line)

                fallback_df = extract_financial_raw_section(
                    pdf_path=pdf_path,
                    start_page=start_page,
                    end_page=end_page,
                    section_info=section_info,
                    dpi=dpi,
                )

                print("[FALLBACK] extracted shape:", fallback_df.shape)

                if fallback_df is not None and len(fallback_df) > 0:
                    fallback_raw_list.append(fallback_df)

        if len(fallback_raw_list) > 0:
            financial_df = pd.concat(fallback_raw_list, ignore_index=True)
        else:
            print("[FALLBACK] 그래도 추출된 데이터가 없습니다.")
            financial_df = pd.DataFrame()

    # period_type/job_id/industry 부착
    financial_df = _add_period_columns(
        financial_df,
        period_type=period_type,
        job_id=job_id,
        industry=industry,
    )

    result = {"financial_value_data": financial_df}

    if save_output:
        if save_dir is None:
            save_dir = TEST_OUTPUT_DIR / "financial_only_gemini_test"
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        save_name = make_safe_name(pdf_path.stem)
        prefix_parts = []
        if job_id:
            prefix_parts.append(str(job_id))
        prefix_parts.append(period_type)
        prefix_parts.append(save_name)
        prefix = "_".join(prefix_parts)

        save_final_outputs(result=result, save_dir=save_dir, prefix=prefix)

    print("financial_value_data shape:", financial_df.shape)
    return result


# =========================================================
# 2. FastAPI에서 쓸 수 있는 업로드 처리 예시 함수
# =========================================================

def save_uploaded_pdf_bytes(
    file_bytes: bytes,
    original_filename: str,
    job_id: str,
    period_type: str,
    upload_root: Path = BASE_DIR / "data" / "uploads",
) -> Path:
    """
    FastAPI UploadFile을 받은 뒤 bytes를 넘기면
    data/uploads/{job_id}/{period_type}.pdf 형태로 저장.
    """
    if period_type == "t-1":
        period_type = "t_minus_1"

    if period_type not in {"t", "t_minus_1"}:
        raise ValueError("period_type은 't' 또는 't_minus_1'이어야 합니다.")

    upload_dir = Path(upload_root) / str(job_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    save_path = upload_dir / f"{period_type}.pdf"
    with open(save_path, "wb") as f:
        f.write(file_bytes)

    print(f"[UPLOAD SAVE] {original_filename} -> {save_path}")
    return save_path


# =========================================================
# 3. CLI 실행부
# =========================================================

def main():
    parser = argparse.ArgumentParser(description="재무제표 표 OCR Gemini + fallback 실행")
    parser.add_argument("--pdf", required=True, help="PDF 파일 경로")
    parser.add_argument("--period-type", required=True, choices=["t", "t_minus_1", "t-1"])
    parser.add_argument("--industry", default=None, help="업종")
    parser.add_argument("--job-id", default=None, help="분석 묶음 ID")
    parser.add_argument("--save-dir", default=None, help="결과 저장 폴더")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--max-search-pages", type=int, default=80)
    parser.add_argument("--project-id", default=None, help="Vertex AI Project ID")
    parser.add_argument("--location", default=None, help="Vertex AI Location")
    parser.add_argument("--model-name", default=None, help="Gemini model name")

    args = parser.parse_args()

    run_financial_ocr_with_fallback(
        pdf_path=args.pdf,
        period_type=args.period_type,
        industry=args.industry,
        job_id=args.job_id,
        dpi=args.dpi,
        max_search_pages=args.max_search_pages,
        save_dir=Path(args.save_dir) if args.save_dir else None,
        project_id=args.project_id,
        location=args.location,
        model_name=args.model_name,
        save_output=True,
    )


if __name__ == "__main__":
    main()
