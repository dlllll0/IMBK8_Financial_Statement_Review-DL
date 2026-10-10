"""
text_ocr.py

사업보고서/감사보고서 PDF에서 텍스트 섹션을 PyMuPDF로 추출하는 모듈.
- 대상: 재무제표 주석(notes), 감사보고서(audit_report), 정정신고(correction_report)
- HTML input에서 t / t_minus_1로 받은 PDF의 period_type을 결과 컬럼에 적재
- 업종은 PDF 경로가 아니라 사용자 입력값(industry)을 우선 사용
- 목차 파싱 실패/유니코드 이슈로 섹션이 안 잡힐 때 2차 fallback 실행

필수 설치:
    pip install pymupdf pandas openpyxl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import pandas as pd

warnings.filterwarnings("ignore")


# =========================================================
# 0. 경로 / 기본 설정
# =========================================================

BASE_DIR = Path(os.getenv("DART_OCR_BASE_DIR", ".")).resolve()
OUTPUT_DIR = Path(os.getenv("DART_OCR_OUTPUT_DIR", BASE_DIR / "outputs")).resolve()
TEXT_OUTPUT_DIR = OUTPUT_DIR / "text_ocr"
DEBUG_OUTPUT_DIR = OUTPUT_DIR / "debug_outputs"

for _d in [OUTPUT_DIR, TEXT_OUTPUT_DIR, DEBUG_OUTPUT_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

TARGET_SECTION_TYPES = {"notes", "audit_report", "correction_report"}
OUTPUT_KEY_MAP = {
    "notes": "notes_fulltext_data",
    "audit_report": "audit_fulltext_data",
    "correction_report": "correction_fulltext_data",
}
OUTPUT_SECTION_TYPE_MAP = {
    "notes": "notes_fulltext",
    "audit_report": "audit_fulltext",
    "correction_report": "correction_fulltext",
}


# =========================================================
# 1. 공통 유틸 / 메타데이터
# =========================================================

def norm_text(x) -> str:
    """한글 자모 분리 문제 방지를 위해 NFC 정규화."""
    return unicodedata.normalize("NFC", str(x))


def normalize_text(s) -> str:
    s = norm_text(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_extracted_text(text) -> str:
    text = norm_text(text)
    text = text.replace("|", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_safe_name(name) -> str:
    name = norm_text(name)
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:150]


def get_report_type(pdf_path) -> str:
    name = norm_text(Path(pdf_path).name)
    if "정정" in name:
        return "CORRECTION"
    if "사업보고서" in name:
        return "BUSINESS"
    if "감사보고서" in name:
        return "AUDIT"
    return "UNKNOWN"


def get_data_type_from_input(data_type: Optional[str] = None) -> str:
    # 기존 노트북은 경로명(정상기업/회계이상기업)으로 분류했지만,
    # 웹 업로드 PDF는 그런 폴더 구조가 없으므로 인자값을 우선 사용.
    return data_type or "unknown"


def extract_company_name(pdf_path) -> str:
    name = norm_text(Path(pdf_path).name)
    m = re.search(r"\[([^\]]+)\]", name)
    if m:
        return m.group(1)
    return Path(name).stem


def validate_period_type(period_type: str) -> str:
    if period_type == "t-1":
        period_type = "t_minus_1"
    if period_type not in {"t", "t_minus_1"}:
        raise ValueError("period_type은 't' 또는 't_minus_1'/'t-1'로 넣어주세요.")
    return period_type


# =========================================================
# 2. PDF 페이지 텍스트 / printed page 매핑
# =========================================================

def extract_page_texts(pdf_path) -> pd.DataFrame:
    pdf_path = Path(pdf_path)
    rows = []
    doc = fitz.open(str(pdf_path))

    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        rows.append({
            "file_name": pdf_path.name,
            "pdf_page": i,
            "text": clean_extracted_text(text),
        })

    doc.close()
    return pd.DataFrame(rows)


def detect_printed_page_from_text(text) -> Optional[int]:
    text = norm_text(text)

    matches = re.findall(r"Page\s*(\d+)", text, flags=re.IGNORECASE)
    if matches:
        return int(matches[-1])

    tail = "\n".join(text.split("\n")[-6:])
    matches = re.findall(r"(?:^|\s)[-–—]?\s*(\d{1,4})\s*[-–—]?\s*$", tail, flags=re.MULTILINE)
    if matches:
        return int(matches[-1])

    return None


def build_page_mapping(pdf_path) -> pd.DataFrame:
    pdf_path = Path(pdf_path)
    rows = []
    doc = fitz.open(str(pdf_path))

    for pdf_page, page in enumerate(doc, start=1):
        text = page.get_text("text")
        printed_page = detect_printed_page_from_text(text)
        rows.append({
            "file_name": pdf_path.name,
            "pdf_page": pdf_page,
            "printed_page": printed_page,
        })

    doc.close()
    return pd.DataFrame(rows)


def printed_to_pdf_page(printed_page, page_mapping_df) -> Optional[int]:
    hit = page_mapping_df[page_mapping_df["printed_page"] == printed_page]
    if len(hit) > 0:
        return int(hit.iloc[0]["pdf_page"])
    return None


def infer_pdf_page_by_offset(printed_page, page_mapping_df) -> Optional[int]:
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
# 3. 목차 탐색 / 섹션 인덱싱
# =========================================================

def is_real_toc_line(line) -> bool:
    line = normalize_text(line)
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

    compact = re.sub(r"\s+", "", title)

    if "정정신고" in compact or "정정" in compact:
        return "정정신고"

    if "연결재무제표주석" in compact:
        return "연결재무제표 주석"

    # 창은/사용자 디버깅 반영: '재무제표에 대한 주석' 케이스 추가
    if "재무제표에대한주석" in compact:
        return "재무제표 주석"

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
        return "connected_notes"

    if "연결재무제표" in compact:
        return "connected_financial_statement"

    # '재무제표에 대한 주석' 케이스 추가
    if "재무제표에대한주석" in compact:
        return "notes"

    if "재무제표주석" in compact or compact == "주석":
        return "notes"

    if "재무제표" in compact:
        return "financial_statement"

    if "감사보고서" in compact or "감사의견" in compact or "회계감사인의감사의견" in compact:
        return "audit_report"

    if "내부회계관리제도" in compact or "외부감사실시내용" in compact:
        return "boundary"

    return None


def extract_toc_df(page_df, max_search_pages=40) -> pd.DataFrame:
    rows = []
    search_df = page_df[page_df["pdf_page"] <= max_search_pages]

    for _, row in search_df.iterrows():
        pdf_page = int(row["pdf_page"])
        text = norm_text(row["text"])

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
                "raw_line": line,
            })

    toc_df = pd.DataFrame(rows)
    if len(toc_df) == 0:
        return toc_df

    toc_df = toc_df.sort_values("printed_start_page").reset_index(drop=True)
    toc_df = toc_df.drop_duplicates(
        subset=["section_type", "section_title", "printed_start_page"], keep="first"
    )
    toc_df = toc_df.sort_values("printed_start_page").reset_index(drop=True)
    return toc_df


def filter_target_sections(toc_df) -> pd.DataFrame:
    if toc_df is None or len(toc_df) == 0:
        return pd.DataFrame()

    target = toc_df[
        toc_df["section_type"].isin([
            "financial_statement",  # boundary 역할
            "notes",
            "correction_report",
            "audit_report",
            "boundary",
        ])
    ].copy()

    # 연결 재무제표 계열 제외. 이 프로젝트는 별도 재무제표/주석 중심.
    target = target[
        ~target["section_title"].astype(str).str.contains("연결재무제표", na=False)
    ]

    target = target.sort_values("printed_start_page").reset_index(drop=True)
    return target


def make_section_ranges(target_toc_df, page_mapping_df, max_last_section_pages=100) -> pd.DataFrame:
    if target_toc_df is None or len(target_toc_df) == 0:
        return pd.DataFrame()

    df = target_toc_df.sort_values("printed_start_page").reset_index(drop=True)
    rows = []

    for _, row in df.iterrows():
        start_printed = int(row["printed_start_page"])

        later_pages = df.loc[
            df["printed_start_page"].astype(int) > start_printed,
            "printed_start_page",
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
            print("[PAGE MAPPING FAIL]", row.get("section_title"), start_printed)
            continue

        if end_pdf is None:
            max_pdf = int(page_mapping_df["pdf_page"].max())
            end_pdf = min(start_pdf + max_last_section_pages, max_pdf)

        rows.append({
            **row.to_dict(),
            "printed_end_page": end_printed,
            "pdf_start_page": int(start_pdf),
            "pdf_end_page": int(end_pdf),
        })

    out = pd.DataFrame(rows)
    if len(out) > 0:
        out = out[out["pdf_end_page"] >= out["pdf_start_page"]].reset_index(drop=True)
    return out


# =========================================================
# 4. 섹션 텍스트 추출 / fulltext 생성
# =========================================================

def make_section_info(
    pdf_path,
    section_type: str,
    section_title: str,
    industry: str,
    data_type: str,
    printed_start_page: Optional[int] = None,
    printed_end_page: Optional[int] = None,
) -> Dict:
    return {
        "file_name": Path(pdf_path).name,
        "industry": industry or "미분류",
        "data_type": get_data_type_from_input(data_type),
        "report_type": get_report_type(pdf_path),
        "section_type": section_type,
        "section_title": section_title,
        "printed_start_page": printed_start_page,
        "printed_end_page": printed_end_page,
    }


def extract_text_layer_section(pdf_path, start_page, end_page, section_info) -> pd.DataFrame:
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    rows = []
    max_page = len(doc)

    start_page = max(1, int(start_page))
    end_page = min(max_page, int(end_page))

    for page_num in range(start_page, end_page + 1):
        try:
            page = doc[page_num - 1]
            text = page.get_text("text")
            text = clean_extracted_text(text)
            rows.append({
                **section_info,
                "pdf_page": page_num,
                "text": text,
                "source": "pdf_text_layer",
            })
        except Exception as e:
            print("[PyMuPDF TEXT ERROR]", pdf_path.name, page_num, e)

    doc.close()
    return pd.DataFrame(rows)


def make_fulltext_df(text_df, output_section_type="fulltext") -> pd.DataFrame:
    if text_df is None or len(text_df) == 0:
        return pd.DataFrame()

    df = text_df.sort_values(["pdf_page"]).reset_index(drop=True)
    rows = []
    group_cols = [
        "file_name", "industry", "data_type", "report_type",
        "section_type", "section_title",
    ]

    for keys, g in df.groupby(group_cols, dropna=False):
        full_text = "\n\n".join(g["text"].astype(str).tolist())
        full_text = clean_extracted_text(full_text)
        row = dict(zip(group_cols, keys))
        row["output_section_type"] = output_section_type
        row["full_text"] = full_text
        row["line_count"] = int(len(g))
        rows.append(row)

    return pd.DataFrame(rows)


def empty_fulltext_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "job_id", "period_type", "file_name", "industry", "data_type", "report_type",
        "section_type", "section_title", "output_section_type", "full_text", "line_count",
    ])


def attach_job_period(df: pd.DataFrame, job_id: Optional[str], period_type: str, industry: Optional[str]) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return empty_fulltext_df()

    df = df.copy()
    df.insert(0, "period_type", period_type)
    df.insert(0, "job_id", job_id or "")
    if industry:
        df["industry"] = industry

    desired = [
        "job_id", "period_type", "file_name", "industry", "data_type", "report_type",
        "section_type", "section_title", "output_section_type", "full_text", "line_count",
    ]
    for col in desired:
        if col not in df.columns:
            df[col] = None
    return df[desired]


# =========================================================
# 5. 1차 추출: 목차 기반
# =========================================================

def extract_sections_by_toc(
    pdf_path,
    industry: str,
    data_type: str = "unknown",
    max_search_pages: int = 40,
    max_last_section_pages: int = 100,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    pdf_path = Path(pdf_path)

    page_df = extract_page_texts(pdf_path)
    page_mapping_df = build_page_mapping(pdf_path)
    toc_df = extract_toc_df(page_df, max_search_pages=max_search_pages)
    target_toc_df = filter_target_sections(toc_df)
    section_df = make_section_ranges(
        target_toc_df,
        page_mapping_df,
        max_last_section_pages=max_last_section_pages,
    )

    if section_df is not None and len(section_df) > 0:
        section_df = section_df[
            section_df["pdf_end_page"] >= section_df["pdf_start_page"]
        ].reset_index(drop=True)

    debug = {
        "page_df": page_df,
        "page_mapping_df": page_mapping_df,
        "toc_df": toc_df,
        "target_toc_df": target_toc_df,
        "section_df": section_df,
    }

    section_text_lists: Dict[str, List[pd.DataFrame]] = {
        "notes": [],
        "audit_report": [],
        "correction_report": [],
    }

    if section_df is None or len(section_df) == 0:
        print("[WARN] 선택된 section 없음:", pdf_path.name)
        return {k: pd.DataFrame() for k in section_text_lists}, debug

    print("[TEXT OCR] selected_sections")
    print(section_df[["section_type", "section_title", "pdf_start_page", "pdf_end_page"]].to_string(index=False))

    for _, sec in section_df.iterrows():
        section_type = sec.get("section_type")
        if section_type not in TARGET_SECTION_TYPES:
            continue

        start_page = int(sec["pdf_start_page"])
        end_page = int(sec["pdf_end_page"])
        if end_page < start_page:
            continue

        section_info = make_section_info(
            pdf_path=pdf_path,
            section_type=section_type,
            section_title=sec.get("section_title", section_type),
            industry=industry,
            data_type=data_type,
            printed_start_page=int(sec["printed_start_page"]),
            printed_end_page=int(sec["printed_end_page"]),
        )

        print(f"[TextLayer/PyMuPDF] {section_type}: PDF {start_page} ~ {end_page}")
        text_df = extract_text_layer_section(pdf_path, start_page, end_page, section_info)
        print(f"{section_type}_text_df:", text_df.shape)
        section_text_lists[section_type].append(text_df)

    result = {}
    for section_type, parts in section_text_lists.items():
        result[section_type] = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    return result, debug


# =========================================================
# 6. 2차 fallback: 유니코드/목차 누락 방어
# =========================================================

def find_keyword_pages(page_df: pd.DataFrame, keywords: List[str]) -> List[int]:
    pages = []
    for _, row in page_df.iterrows():
        text = norm_text(row.get("text", ""))
        compact = re.sub(r"\s+", "", text)
        if any(k in text or k in compact for k in keywords):
            pages.append(int(row["pdf_page"]))
    return sorted(set(pages))


def fallback_direct_keyword_ranges(page_df: pd.DataFrame) -> pd.DataFrame:
    """
    목차가 안 잡힌 경우 전체 page text에서 키워드를 직접 찾아 대략적인 구간 생성.
    정확도는 목차 기반보다 낮지만, 완전 누락을 줄이기 위한 fallback.
    """
    max_pdf = int(page_df["pdf_page"].max()) if len(page_df) else 0
    candidates = []

    rules = [
        ("correction_report", "정정신고", ["정정신고", "정정신고보고", "정정내용", "정정사항"]),
        ("audit_report", "감사보고서", ["감사보고서", "감사의견", "회계감사인의감사의견"]),
        ("notes", "재무제표 주석", ["재무제표에대한주석", "재무제표주석", "주석"]),
    ]

    for section_type, section_title, keywords in rules:
        pages = find_keyword_pages(page_df, keywords)
        if not pages:
            continue
        start = min(pages)
        # 다음 후보 section 시작 전까지로 자르기 위해 일단 end는 뒤에서 계산
        candidates.append({
            "section_type": section_type,
            "section_title": section_title,
            "pdf_start_page": start,
            "raw_line": f"fallback_keyword:{section_title}",
        })

    if not candidates:
        return pd.DataFrame()

    cand = pd.DataFrame(candidates).sort_values("pdf_start_page").reset_index(drop=True)
    rows = []
    for i, row in cand.iterrows():
        start = int(row["pdf_start_page"])
        if i + 1 < len(cand):
            end = int(cand.iloc[i + 1]["pdf_start_page"]) - 1
        else:
            end = min(start + 100, max_pdf)
        rows.append({
            **row.to_dict(),
            "pdf_end_page": max(start, end),
            "printed_start_page": None,
            "printed_end_page": None,
        })

    return pd.DataFrame(rows)


def extract_missing_sections_with_fallback(
    pdf_path,
    existing_text_dfs: Dict[str, pd.DataFrame],
    debug: Dict[str, pd.DataFrame],
    industry: str,
    data_type: str = "unknown",
    max_search_pages: int = 80,
) -> Dict[str, pd.DataFrame]:
    pdf_path = Path(pdf_path)
    out = {k: v.copy() if isinstance(v, pd.DataFrame) else pd.DataFrame() for k, v in existing_text_dfs.items()}

    missing = [k for k in TARGET_SECTION_TYPES if out.get(k) is None or len(out.get(k)) == 0]
    if not missing:
        return out

    print("[FALLBACK] 누락 섹션 재탐색:", missing)

    # 2-1. 목차 탐색 범위 확대 + 유니코드 정규화된 title 로직으로 재시도
    page_df = debug.get("page_df") if debug and "page_df" in debug else extract_page_texts(pdf_path)
    page_mapping_df = debug.get("page_mapping_df") if debug and "page_mapping_df" in debug else build_page_mapping(pdf_path)

    toc_df = extract_toc_df(page_df, max_search_pages=max_search_pages)
    target_toc_df = filter_target_sections(toc_df)
    section_df = make_section_ranges(target_toc_df, page_mapping_df, max_last_section_pages=120)

    if section_df is not None and len(section_df) > 0:
        print("[FALLBACK] expanded section_df")
        print(section_df[["section_type", "section_title", "pdf_start_page", "pdf_end_page"]].to_string(index=False))

        for _, sec in section_df.iterrows():
            section_type = sec.get("section_type")
            if section_type not in missing:
                continue
            start_page = int(sec["pdf_start_page"])
            end_page = int(sec["pdf_end_page"])
            if end_page < start_page:
                continue

            section_info = make_section_info(
                pdf_path=pdf_path,
                section_type=section_type,
                section_title=sec.get("section_title", section_type),
                industry=industry,
                data_type=data_type,
                printed_start_page=int(sec["printed_start_page"]) if pd.notna(sec.get("printed_start_page")) else None,
                printed_end_page=int(sec["printed_end_page"]) if pd.notna(sec.get("printed_end_page")) else None,
            )
            print(f"[FALLBACK TextLayer] {section_type}: PDF {start_page} ~ {end_page}")
            text_df = extract_text_layer_section(pdf_path, start_page, end_page, section_info)
            if text_df is not None and len(text_df) > 0:
                out[section_type] = text_df

    # 2-2. 그래도 누락이면 page text 직접 keyword range fallback
    missing = [k for k in TARGET_SECTION_TYPES if out.get(k) is None or len(out.get(k)) == 0]
    if missing:
        print("[FALLBACK-2] 직접 키워드 기반 재탐색:", missing)
        direct_df = fallback_direct_keyword_ranges(page_df)
        if direct_df is not None and len(direct_df) > 0:
            print(direct_df[["section_type", "section_title", "pdf_start_page", "pdf_end_page"]].to_string(index=False))

            for _, sec in direct_df.iterrows():
                section_type = sec.get("section_type")
                if section_type not in missing:
                    continue

                start_page = int(sec["pdf_start_page"])
                end_page = int(sec["pdf_end_page"])
                section_info = make_section_info(
                    pdf_path=pdf_path,
                    section_type=section_type,
                    section_title=sec.get("section_title", section_type),
                    industry=industry,
                    data_type=data_type,
                )
                print(f"[FALLBACK-2 TextLayer] {section_type}: PDF {start_page} ~ {end_page}")
                text_df = extract_text_layer_section(pdf_path, start_page, end_page, section_info)
                if text_df is not None and len(text_df) > 0:
                    out[section_type] = text_df

    return out


# =========================================================
# 7. 결과 저장
# =========================================================

def save_text_outputs(result: Dict[str, pd.DataFrame], save_dir: Path, prefix: str):
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    serializable = {}
    excel_path = save_dir / f"{prefix}_text_ocr_outputs.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for key, df in result.items():
            if df is None:
                continue
            csv_path = save_dir / f"{prefix}_{key}.csv"
            json_path = save_dir / f"{prefix}_{key}.json"

            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            serializable[key] = df.to_dict(orient="records")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(serializable[key], f, ensure_ascii=False, indent=2)

            sheet = key[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)

            print(f"[저장] {key}: {df.shape} -> {csv_path}")

    print(f"[EXCEL 저장] {excel_path}")


# =========================================================
# 8. 메인 함수
# =========================================================

def run_text_ocr_with_fallback(
    pdf_path,
    period_type: str,
    industry: Optional[str] = None,
    job_id: Optional[str] = None,
    data_type: str = "unknown",
    max_search_pages: int = 40,
    fallback_max_search_pages: int = 80,
    save_dir: Optional[Path] = None,
    save_output: bool = True,
) -> Dict[str, pd.DataFrame]:
    """
    PyMuPDF 텍스트 OCR 메인 함수.

    Returns
    -------
    {
        "notes_fulltext_data": DataFrame,
        "correction_fulltext_data": DataFrame,
        "audit_fulltext_data": DataFrame,
    }
    """
    period_type = validate_period_type(period_type)
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일이 없습니다: {pdf_path}")

    industry = industry or "미분류"
    data_type = get_data_type_from_input(data_type)

    print("=" * 100)
    print("[TEXT OCR START]")
    print("파일:", pdf_path.name)
    print("전체 경로:", pdf_path)
    print("period_type:", period_type)
    print("job_id:", job_id)
    print("industry:", industry)
    print("=" * 100)

    # 1차 목차 기반 추출
    text_dfs, debug = extract_sections_by_toc(
        pdf_path=pdf_path,
        industry=industry,
        data_type=data_type,
        max_search_pages=max_search_pages,
    )

    # 2차 fallback
    text_dfs = extract_missing_sections_with_fallback(
        pdf_path=pdf_path,
        existing_text_dfs=text_dfs,
        debug=debug,
        industry=industry,
        data_type=data_type,
        max_search_pages=fallback_max_search_pages,
    )

    # fulltext 변환 + job/period 컬럼 부착
    result: Dict[str, pd.DataFrame] = {}
    for section_type, output_key in OUTPUT_KEY_MAP.items():
        fulltext_df = make_fulltext_df(
            text_dfs.get(section_type, pd.DataFrame()),
            output_section_type=OUTPUT_SECTION_TYPE_MAP[section_type],
        )
        result[output_key] = attach_job_period(fulltext_df, job_id=job_id, period_type=period_type, industry=industry)

    if save_output:
        save_dir = Path(save_dir) if save_dir is not None else TEXT_OUTPUT_DIR / (job_id or "manual") / period_type
        prefix = f"TEXT_{period_type}_{make_safe_name(pdf_path.stem)}"
        save_text_outputs(result, save_dir=save_dir, prefix=prefix)

    print("[TEXT OCR DONE]")
    for key, df in result.items():
        print(f"- {key}: {df.shape}")

    return result


# =========================================================
# 9. CLI 실행부
# =========================================================

def parse_args():
    parser = argparse.ArgumentParser(description="PyMuPDF 텍스트 OCR: 주석/감사보고서/정정신고 추출")
    parser.add_argument("--pdf", required=True, help="입력 PDF 경로")
    parser.add_argument("--period-type", required=True, choices=["t", "t_minus_1", "t-1"], help="t 또는 t_minus_1")
    parser.add_argument("--industry", default="미분류", help="사용자 입력 업종")
    parser.add_argument("--job-id", default="manual_job", help="작업 ID")
    parser.add_argument("--data-type", default="unknown", help="normal/abnormal/unknown")
    parser.add_argument("--save-dir", default=None, help="결과 저장 폴더")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_text_ocr_with_fallback(
        pdf_path=args.pdf,
        period_type=args.period_type,
        industry=args.industry,
        job_id=args.job_id,
        data_type=args.data_type,
        save_dir=Path(args.save_dir) if args.save_dir else None,
        save_output=True,
    )
