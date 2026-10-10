# -*- coding: utf-8 -*-
"""
src/ui_chunk_builder.py

좌측 UI 전용 텍스트 청킹 데이터 생성기.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pandas as pd


def read_csv_safely(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            df = pd.read_csv(path, encoding=enc)
            df.columns = df.columns.astype(str).str.strip()
            return df.fillna("")
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"[CSV READ ERROR] {path} / {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def clean_text(text: str) -> str:
    text = str(text or "")

    text = re.sub(r"전자공시시스템\s+dart\.fss\.or\.kr", " ", text, flags=re.I)
    text = re.sub(r"Page\s+\d+", " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", " ", text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def infer_doc_type(file_name: str) -> str:
    name = file_name.lower()

    if "fulltext" not in name:
        return "unknown"

    if "notes" in name or "note" in name or "주석" in file_name:
        return "notes"

    if "audit" in name or "감사" in file_name:
        return "audit"

    if "correction" in name or "정정" in file_name:
        return "correction"

    return "unknown"


def find_text_col(df: pd.DataFrame) -> str | None:
    candidates = [
        "text", "fulltext", "full_text", "content",
        "본문", "텍스트", "문장", "raw_text", "page_text",
        "notes_fulltext", "audit_fulltext", "correction_fulltext",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    for col in df.columns:
        try:
            if df[col].astype(str).head(20).str.len().mean() > 80:
                return col
        except Exception:
            pass

    return None


def load_fulltext(period_dir: Path, doc_type: str) -> str:
    texts = []

    folder = period_dir / "text"

    if not folder.exists():
        return ""

    for path in sorted(folder.glob("*.csv")):
        if infer_doc_type(path.name) != doc_type:
            continue

        df = read_csv_safely(path)

        if df.empty:
            continue

        text_col = find_text_col(df)

        if not text_col:
            continue

        parts = (
            df[text_col]
            .astype(str)
            .str.strip()
            .replace("nan", "")
            .drop_duplicates()
            .tolist()
        )

        texts.extend([p for p in parts if p])

    return clean_text("\n\n".join(texts))


def is_heading(line: str) -> bool:
    line = str(line).strip()

    if not line:
        return False

    if len(line) > 90:
        return False

    patterns = [
        r"^[IVXLC]+\.\s*.+",
        r"^\d+\.\s*.+",
        r"^[가-힣]\.\s*.+",
        r"^\([0-9]+\)\s*.+",
        r"^\([가-힣]\)\s*.+",
        r"^[①-⑳]\s*.+",
        r".*에 관한 사항$",
        r".*감사의견.*",
        r".*핵심감사사항.*",
        r".*회계감사인.*",
        r".*법인세.*",
        r".*우발.*",
        r".*특수관계자.*",
        r".*수익.*",
        r".*손상.*",
        r".*공정가치.*",
    ]

    return any(re.search(p, line) for p in patterns)


def split_sections(text: str) -> List[Dict[str, str]]:
    lines = [line.strip() for line in clean_text(text).splitlines()]

    sections = []
    current_title = "관련 문단"
    current_lines = []

    for line in lines:
        if not line:
            if current_lines:
                current_lines.append("")
            continue

        if is_heading(line):
            if current_lines:
                sections.append({
                    "section_title": current_title,
                    "section_text": "\n".join(current_lines).strip(),
                })
                current_lines = []

            current_title = line
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "section_title": current_title,
            "section_text": "\n".join(current_lines).strip(),
        })

    return sections


def get_text_ratios(text: str) -> Dict[str, float]:
    text = str(text or "")
    length = max(len(text), 1)

    return {
        "digit_ratio": sum(ch.isdigit() for ch in text) / length,
        "korean_ratio": len(re.findall(r"[가-힣]", text)) / length,
        "alpha_ratio": len(re.findall(r"[A-Za-z]", text)) / length,
        "special_ratio": len(re.findall(r"[^가-힣A-Za-z0-9\s]", text)) / length,
    }


def is_table_like(text: str) -> bool:
    text = str(text or "").strip()

    if not text:
        return True

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) <= 2:
        return False

    ratios = get_text_ratios(text)
    digit_ratio = ratios["digit_ratio"]
    korean_ratio = ratios["korean_ratio"]

    short_line_ratio = sum(len(line) <= 20 for line in lines) / max(len(lines), 1)
    numeric_line_ratio = sum(
        bool(re.fullmatch(r"[\d,\.\-\(\)￦%\s]+", line))
        for line in lines
    ) / max(len(lines), 1)

    table_tokens = [
        "사업연도", "구분", "감사인", "보수", "시간", "금액", "단위",
        "계약체결일", "제27기", "제26기", "제25기", "당기", "전기",
        "합계", "소계", "과목", "장부가액", "취득가액", "공정가액",
        "지분율", "소유주식수", "자산", "부채", "자본",
    ]
    token_count = sum(token in text for token in table_tokens)

    # 숫자 많고 짧은 줄이 많으면 표
    if len(lines) >= 5 and digit_ratio > 0.22 and short_line_ratio > 0.40:
        return True

    # 숫자 라인 비중이 높으면 표
    if len(lines) >= 4 and numeric_line_ratio > 0.45:
        return True

    # 표 키워드가 많고 짧은 줄이 많으면 표
    if len(lines) >= 5 and token_count >= 3 and short_line_ratio > 0.35:
        return True

    # 한글 거의 없고 숫자가 많으면 표/노이즈
    if len(lines) >= 4 and korean_ratio < 0.08 and digit_ratio > 0.20:
        return True

    return False


def is_meaningless_chunk(text: str) -> bool:
    text = str(text or "").strip()

    if not text:
        return True

    ratios = get_text_ratios(text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 너무 짧음
    if len(text) < 40:
        return True

    # 한글 거의 없음
    if ratios["korean_ratio"] < 0.05:
        return True

    # 숫자 과다
    if ratios["digit_ratio"] > 0.45:
        return True

    # 특수문자 과다
    if ratios["special_ratio"] > 0.35:
        return True

    # 짧은 줄 반복
    if len(lines) >= 5:
        short_line_ratio = sum(len(line) <= 15 for line in lines) / len(lines)
        if short_line_ratio > 0.65:
            return True

    return False


def split_paragraphs(section_text: str) -> List[str]:
    section_text = clean_text(section_text)

    if not section_text:
        return []

    blocks = re.split(r"\n\s*\n+", section_text)
    out = []

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        if len(block) > 1000:
            parts = re.split(r"(?<=[.!?다요음함됨됨니다])\s+|\n+", block)
            buf = ""

            for part in parts:
                part = part.strip()

                if not part:
                    continue

                if len(buf) + len(part) > 750:
                    if buf.strip():
                        out.append(buf.strip())
                    buf = part
                else:
                    buf = (buf + " " + part).strip()

            if buf.strip():
                out.append(buf.strip())
        else:
            out.append(block)

    return out


def normalize_for_dedup(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일", "DATE", text)
    text = re.sub(r"\d+", "N", text)
    return text[:600]

# =========================================================
# 문서별 청킹 함수
# =========================================================

def is_table_intro_sentence(text: str) -> bool:
    text = str(text or "").strip()

    patterns = [
        r".*내역은\s*다음과\s*같습니다.*",
        r".*내용은\s*다음과\s*같습니다.*",
        r".*현황은\s*다음과\s*같습니다.*",
        r".*명세는\s*다음과\s*같습니다.*",
        r".*구성은\s*다음과\s*같습니다.*",
        r".*변동내역은\s*다음과\s*같습니다.*",
        r".*잔액은\s*다음과\s*같습니다.*",
        r".*금액은\s*다음과\s*같습니다.*",
        r".*분류하면\s*다음과\s*같습니다.*",
        r".*요약하면\s*다음과\s*같습니다.*",
        r".*표시하면\s*다음과\s*같습니다.*",
    ]

    return any(re.search(p, text) for p in patterns)


def is_valid_chunk(text: str) -> bool:
    text = str(text or "").strip()

    if not text:
        return False

    if is_table_like(text):
        return False

    if is_table_intro_sentence(text):
        return False

    if is_meaningless_chunk(text):
        return False

    return True


# =========================================================
# 1. 재무제표 주석 청킹
# =========================================================

def build_notes_chunks(fulltext: str, max_rows: int = 80) -> pd.DataFrame:
    sections = split_sections(fulltext)

    rows = []
    seen = set()

    for section in sections:
        title = section["section_title"]
        paragraphs = split_paragraphs(section["section_text"])

        for para in paragraphs:
            para = para.strip()

            if not is_valid_chunk(para):
                continue

            key = normalize_for_dedup(title + " " + para)

            if key in seen:
                continue

            seen.add(key)

            rows.append({
                "doc_type": "notes",
                "section_title": title,
                "chunk_id": len(rows) + 1,
                "chunk_text": para,
            })

            if len(rows) >= max_rows:
                break

        if len(rows) >= max_rows:
            break

    return pd.DataFrame(rows)


# =========================================================
# 2. 감사보고서 청킹
# =========================================================

def split_audit_sections(text: str) -> List[Dict[str, str]]:
    text = clean_text(text)

    audit_titles = [
        "감사의견",
        "의견근거",
        "핵심감사사항",
        "강조사항",
        "기타사항",
        "계속기업",
        "재무제표에 대한 경영진의 책임",
        "감사인의 책임",
        "내부회계관리제도",
        "감사보고서일",
    ]

    pattern = r"(?=(" + "|".join(map(re.escape, audit_titles)) + r"))"

    parts = re.split(pattern, text)

    sections = []
    current_title = "감사보고서 관련 문단"
    current_text = ""

    i = 0
    while i < len(parts):
        part = parts[i].strip()

        if not part:
            i += 1
            continue

        if part in audit_titles:
            if current_text.strip():
                sections.append({
                    "section_title": current_title,
                    "section_text": current_text.strip(),
                })

            current_title = part
            current_text = ""
        else:
            current_text += "\n" + part

        i += 1

    if current_text.strip():
        sections.append({
            "section_title": current_title,
            "section_text": current_text.strip(),
        })

    return sections


def build_audit_chunks(fulltext: str, max_rows: int = 80) -> pd.DataFrame:
    sections = split_audit_sections(fulltext)

    rows = []
    seen = set()

    for section in sections:
        title = section["section_title"]
        text = section["section_text"].strip()

        if not text:
            continue

        paragraphs = split_paragraphs(text)

        for para in paragraphs:
            para = para.strip()

            if not is_valid_chunk(para):
                continue

            key = normalize_for_dedup(title + " " + para)

            if key in seen:
                continue

            seen.add(key)

            rows.append({
                "doc_type": "audit",
                "section_title": title,
                "chunk_id": len(rows) + 1,
                "chunk_text": para,
            })

            if len(rows) >= max_rows:
                break

        if len(rows) >= max_rows:
            break

    return pd.DataFrame(rows)


# =========================================================
# 3. 정정신고 청킹
# =========================================================

def build_correction_chunks(fulltext: str, max_rows: int = 80) -> pd.DataFrame:
    text = clean_text(fulltext)

    rows = []
    seen = set()

    # 정정신고는 항목명/정정전/정정후/비고 구조를 우선 탐색
    blocks = re.split(
        r"(?=항목명|정정전|정정후|정정\s*전|정정\s*후|비고|정정내용|정정사유)",
        text
    )

    if len(blocks) <= 2:
        blocks = re.split(r"\n\s*\n+", text)

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        if len(block) < 40:
            continue

        # 정정신고는 표 느낌이어도 정정 전후 정보가 있을 수 있으므로 완전 제거는 약하게
        has_correction_signal = any(
            token in block
            for token in ["정정전", "정정후", "정정 전", "정정 후", "정정사유", "비고", "변경"]
        )

        if not has_correction_signal and is_table_like(block):
            continue

        if is_meaningless_chunk(block):
            continue

        key = normalize_for_dedup(block)

        if key in seen:
            continue

        seen.add(key)

        rows.append({
            "doc_type": "correction",
            "section_title": "정정신고 변경사항",
            "chunk_id": len(rows) + 1,
            "chunk_text": block,
        })

        if len(rows) >= max_rows:
            break

    return pd.DataFrame(rows)


# =========================================================
# 최종 분기 함수
# =========================================================

def build_chunks_for_doc(period_dir: Path, doc_type: str, max_rows: int = 80) -> pd.DataFrame:
    fulltext = load_fulltext(period_dir, doc_type)

    columns = [
        "doc_type",
        "section_title",
        "chunk_id",
        "chunk_text",
    ]

    if not fulltext:
        return pd.DataFrame(columns=columns)

    if doc_type == "notes":
        df = build_notes_chunks(fulltext, max_rows=max_rows)

    elif doc_type == "audit":
        df = build_audit_chunks(fulltext, max_rows=max_rows)

    elif doc_type == "correction":
        df = build_correction_chunks(fulltext, max_rows=max_rows)

    else:
        df = pd.DataFrame(columns=columns)

    if df.empty:
        return pd.DataFrame(columns=columns)

    return df[columns]


def run_ui_chunk_builder_for_period(job_dir: Path, period_type: str) -> Dict[str, object]:
    period_dir = job_dir / period_type
    output_dir = period_dir / "ui_chunks"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {}

    for doc_type in ["notes", "audit", "correction"]:
        df = build_chunks_for_doc(period_dir, doc_type)
        out_path = output_dir / f"{doc_type}_ui_chunks.csv"

        df.to_csv(out_path, index=False, encoding="utf-8-sig")

        result[doc_type] = {
            "rows": len(df),
            "path": str(out_path),
        }

        print(f"[UI CHUNK] {period_type}/{doc_type}: {len(df)} rows -> {out_path}")

    return result


def run_ui_chunk_builder(job_dir: str | Path) -> Dict[str, object]:
    job_dir = Path(job_dir)

    result = {
        "status": "success",
        "t": run_ui_chunk_builder_for_period(job_dir, "t"),
        "t_minus_1": run_ui_chunk_builder_for_period(job_dir, "t_minus_1"),
    }

    return result


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent

    TEST_JOB_DIR = (
        BASE_DIR
        / "outputs"
        / "jobs"
        / "job_c4739b48"
    )

    result = run_ui_chunk_builder(TEST_JOB_DIR)

    print(result)