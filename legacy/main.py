"""
main.py

DL_final 로컬 테스트용 전체 파이프라인 실행 파일.

기본 실행:
    python main.py

기본 입력 파일:
    uploads/t.pdf
    uploads/t_minus_1.pdf

전체 흐름:
    1) t/t-1 PDF를 uploads/{job_id}/ 로 복사
    2) 재무제표 표 OCR (Gemini)
    3) 주석/감사/정정 텍스트 OCR (PyMuPDF)
    4) 텍스트 OCR 결과를 Kiwi 명사 데이터로 분석
    5) outputs/jobs/{job_id}/ 아래 저장
"""

from __future__ import annotations

import os
import argparse
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from typing import Dict, Optional
from src.category_mapper import run_category_mapping
from src.change_detector import run_change_detection
from src.analysis_runner import run_analysis_runner
from src.priority_scorer import run_priority_scoring
from src.rag_builder import run_rag_builder
from src.viewer_builder import run_viewer_builder
from src.gemini_financial_structurer import run_gemini_financial_structurer
from src.ui_chunk_builder import run_ui_chunk_builder

import time
import pandas as pd


# =========================================================
# 0. 경로 설정
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"

# src 패키지 import가 꼬이지 않도록 강제 등록
for p in [BASE_DIR, SRC_DIR]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

UPLOAD_DIR = Path(os.getenv("REVIEW_UPLOAD_DIR", BASE_DIR / "uploads"))
OUTPUT_DIR = Path(os.getenv("REVIEW_JOBS_DIR", BASE_DIR / "outputs" / "jobs"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 1. 모듈 import
# =========================================================

FIN_IMPORT_ERROR = None
TEXT_IMPORT_ERROR = None
ANALYSIS_IMPORT_ERROR = None

try:
    from src.financial_statement_ocr import run_financial_ocr_with_fallback
except Exception as e:
    run_financial_ocr_with_fallback = None
    FIN_IMPORT_ERROR = e

try:
    from src.text_ocr import run_text_ocr_with_fallback
except Exception as e:
    run_text_ocr_with_fallback = None
    TEXT_IMPORT_ERROR = e

try:
    from src.text_analysis import run_text_analysis
except Exception as e:
    run_text_analysis = None
    ANALYSIS_IMPORT_ERROR = e


# =========================================================
# 2. 유틸 함수
# =========================================================

def _print_import_status() -> None:
    print("\n[IMPORT CHECK]")
    print("financial_statement_ocr:", "OK" if run_financial_ocr_with_fallback else f"FAIL - {FIN_IMPORT_ERROR}")
    print("text_ocr:", "OK" if run_text_ocr_with_fallback else f"FAIL - {TEXT_IMPORT_ERROR}")
    print("text_analysis:", "OK" if run_text_analysis else f"FAIL - {ANALYSIS_IMPORT_ERROR}")
    print()


def copy_input_pdf(src_path: Path, job_id: str, period_type: str) -> Path:
    """
    로컬 테스트용 PDF를 uploads/{job_id}/{period_type}.pdf로 복사.
    period_type: t 또는 t_minus_1
    """
    src_path = Path(src_path)

    if not src_path.exists():
        raise FileNotFoundError(
            f"PDF 파일이 없습니다: {src_path}\n"
            f"기본 실행을 쓰려면 아래 파일이 있어야 합니다.\n"
            f"  - {UPLOAD_DIR / 't.pdf'}\n"
            f"  - {UPLOAD_DIR / 't_minus_1.pdf'}"
        )

    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    dst_path = job_upload_dir / f"{period_type}.pdf"
    shutil.copy2(src_path, dst_path)
    return dst_path


def save_manifest(job_id: str, industry: str, t_pdf: Path, t_minus_1_pdf: Path) -> Path:
    """
    업로드/입력 파일 메타데이터 저장.
    """
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    manifest = pd.DataFrame([
        {
            "job_id": job_id,
            "period_type": "t",
            "industry": industry,
            "saved_file_path": str(t_pdf),
            "original_file_name": Path(t_pdf).name,
        },
        {
            "job_id": job_id,
            "period_type": "t_minus_1",
            "industry": industry,
            "saved_file_path": str(t_minus_1_pdf),
            "original_file_name": Path(t_minus_1_pdf).name,
        },
    ])

    out_path = job_dir / "upload_manifest.csv"
    manifest.to_csv(out_path, index=False, encoding="utf-8-sig")
    print("[MANIFEST 저장]", out_path)
    return out_path


def _check_required_modules(run_financial: bool, run_text: bool, run_analysis: bool) -> None:
    """
    실행 옵션에 필요한 모듈이 import 되었는지 확인.
    """
    if run_financial and run_financial_ocr_with_fallback is None:
        raise ImportError(
            "financial_statement_ocr import 실패\n"
            f"원인: {FIN_IMPORT_ERROR}\n"
            "확인: src/financial_statement_ocr.py 파일이 있는지, src/__init__.py가 있는지 확인하세요."
        )

    if run_text and run_text_ocr_with_fallback is None:
        raise ImportError(
            "text_ocr import 실패\n"
            f"원인: {TEXT_IMPORT_ERROR}\n"
            "확인: src/text_ocr.py 파일이 있는지, src/__init__.py가 있는지 확인하세요."
        )

    if run_analysis and run_text_analysis is None:
        raise ImportError(
            "text_analysis import 실패\n"
            f"원인: {ANALYSIS_IMPORT_ERROR}\n"
            "확인: src/text_analysis.py 파일이 있는지, src/__init__.py가 있는지 확인하세요."
        )


# =========================================================
# 3. period 단위 처리
# =========================================================

def process_one_period(
    pdf_path: Path,
    period_type: str,
    industry: str,
    job_id: str,
    data_type: str = "unknown",
    run_financial: bool = True,
    run_text: bool = True,
    run_analysis: bool = True,
) -> Dict[str, object]:
    """
    t 또는 t_minus_1 하나에 대해:
    - 재무제표 표 OCR
    - 주석/감사/정정 텍스트 OCR
    - 텍스트분석
    실행.
    """
    result: Dict[str, object] = {}

    period_output_dir = OUTPUT_DIR / job_id / period_type
    financial_output_dir = period_output_dir / "financial"
    text_output_dir = period_output_dir / "text"
    analysis_output_dir = period_output_dir / "text_analysis"

    period_output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 100)
    print(f"[PERIOD START] {period_type}")
    print("PDF:", pdf_path)
    print("OUTPUT:", period_output_dir)
    print("=" * 100)

    # -----------------------------------------------------
    # 1) 재무제표 표 OCR
    # -----------------------------------------------------
    if run_financial:
        print("\n[1/3] 재무제표 표 OCR 시작")
        financial_result = run_financial_ocr_with_fallback(
            pdf_path=pdf_path,
            period_type=period_type,
            industry=industry,
            job_id=job_id,
            save_dir=financial_output_dir,
            save_output=True,
        )
        result["financial"] = financial_result

        fin_df = financial_result.get("financial_value_data") if isinstance(financial_result, dict) else None
        if hasattr(fin_df, "shape"):
            print("[1/3] 재무제표 표 OCR 완료:", fin_df.shape)
        else:
            print("[1/3] 재무제표 표 OCR 완료")
    else:
        print("\n[1/3] 재무제표 표 OCR SKIP")

    # -----------------------------------------------------
    # 2) 텍스트 OCR
    # -----------------------------------------------------
    if run_text:
        print("\n[2/3] 주석/감사/정정 텍스트 OCR 시작")
        text_result = run_text_ocr_with_fallback(
            pdf_path=pdf_path,
            period_type=period_type,
            industry=industry,
            job_id=job_id,
            data_type=data_type,
            save_dir=text_output_dir,
            save_output=True,
        )
        result["text"] = text_result

        if isinstance(text_result, dict):
            for k, v in text_result.items():
                if hasattr(v, "shape"):
                    print(f"    - {k}: {v.shape}")
        print("[2/3] 주석/감사/정정 텍스트 OCR 완료")
    else:
        print("\n[2/3] 주석/감사/정정 텍스트 OCR SKIP")

    # -----------------------------------------------------
    # 3) 텍스트분석
    # -----------------------------------------------------
    if run_analysis:
        print("\n[3/3] 텍스트분석 시작")
        analysis_result = run_text_analysis(
            input_dir=text_output_dir,
            output_dir=analysis_output_dir,
            save_output=True,
        )
        result["text_analysis"] = analysis_result

        if isinstance(analysis_result, dict):
            for k, v in analysis_result.items():
                if hasattr(v, "shape"):
                    print(f"    - {k}: {v.shape}")
        print("[3/3] 텍스트분석 완료")
    else:
        print("\n[3/3] 텍스트분석 SKIP")


    # -----------------------------------------------------
    # 4) 카테고리 매핑
    # -----------------------------------------------------
    # period_output_dir 예:
    # outputs/jobs/{job_id}/t
    # outputs/jobs/{job_id}/t_minus_1
    #
    # category_mapper.py는 이 period_output_dir 안의
    # text_analysis/*.csv를 읽고,
    # category_mapped/total_noun_category_mapped.csv를 저장함.
    if run_analysis:
        print("\n[4/4] 카테고리 매핑 시작")
        category_result = run_category_mapping(period_output_dir)
        result["category_mapping"] = category_result
        print("[4/4] 카테고리 매핑 완료:", category_result)
    else:
        print("\n[4/4] 카테고리 매핑 SKIP - 텍스트분석이 SKIP됨")
        result["category_mapping"] = {
            "status": "skipped",
            "reason": "run_analysis=False",
            "output_path": None,
            "rows": 0,
        }

    print(f"\n[PERIOD DONE] {period_type}")
    return result

# =========================================================
# 4. job 전체 처리
# =========================================================

def process_job(
    t_pdf_path: str | Path = UPLOAD_DIR / "t.pdf",
    t_minus_1_pdf_path: str | Path = UPLOAD_DIR / "t_minus_1.pdf",
    industry: str = "제조업",
    job_id: Optional[str] = None,
    data_type: str = "unknown",
    run_financial: bool = True,
    run_text: bool = True,
    run_analysis: bool = True,
) -> Dict[str, object]:
    """
    t/t-1 전체 파이프라인 실행.
    기본적으로 financial + text OCR + text_analysis 전부 실행.
    """
    _print_import_status()
    _check_required_modules(
        run_financial=run_financial,
        run_text=run_text,
        run_analysis=run_analysis,
    )

    job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("[JOB START]", job_id)
    print("industry:", industry)
    print("run_financial:", run_financial)
    print("run_text:", run_text)
    print("run_analysis:", run_analysis)
    print("=" * 100)

    saved_t_pdf = copy_input_pdf(Path(t_pdf_path), job_id=job_id, period_type="t")
    saved_t_minus_1_pdf = copy_input_pdf(Path(t_minus_1_pdf_path), job_id=job_id, period_type="t_minus_1")
    save_manifest(job_id, industry, saved_t_pdf, saved_t_minus_1_pdf)

    t_result = process_one_period(
        pdf_path=saved_t_pdf,
        period_type="t",
        industry=industry,
        job_id=job_id,
        data_type=data_type,
        run_financial=run_financial,
        run_text=run_text,
        run_analysis=run_analysis,
    )

    if run_financial:
        print("[쿼터 보호] t 완료 후 t-1 실행 전 20초 대기")
        time.sleep(20)

    t_minus_1_result = process_one_period(
        pdf_path=saved_t_minus_1_pdf,
        period_type="t_minus_1",
        industry=industry,
        job_id=job_id,
        data_type=data_type,
        run_financial=run_financial,
        run_text=run_text,
        run_analysis=run_analysis,
    )

    change_result = run_change_detection(job_dir)
    priority_result = run_priority_scoring(job_dir)
    analysis_result = run_analysis_runner(job_dir)
    rag_result = run_rag_builder(job_dir)

    # 재무제표 Gemini 구조화: t / t-1 둘 다
    try:
        gemini_financial_t_result = run_gemini_financial_structurer(
            job_dir,
            period_type="t",
        )
    except Exception as e:
        print("[WARN] gemini_financial_structurer t 실패:", e)
        gemini_financial_t_result = {
            "status": "failed",
            "error": str(e),
        }

    try:
        gemini_financial_t_minus_1_result = run_gemini_financial_structurer(
            job_dir,
            period_type="t_minus_1",
        )
    except Exception as e:
        print("[WARN] gemini_financial_structurer t_minus_1 실패:", e)
        gemini_financial_t_minus_1_result = {
            "status": "failed",
            "error": str(e),
        }

    # 좌측 UI 청킹 데이터 생성
    try:
        ui_chunk_result = run_ui_chunk_builder(job_dir)
    except Exception as e:
        print("[WARN] ui_chunk_builder 실패:", e)
        ui_chunk_result = {
            "status": "failed",
            "error": str(e),
        }

    # viewer_data 생성
    try:
        viewer_result = run_viewer_builder(job_dir)
    except Exception as e:
        print("[WARN] viewer_builder 실패:", e)
        viewer_result = {
            "status": "failed",
            "error": str(e),
        }

    print("\n" + "=" * 100)
    print("[JOB DONE]", job_id)
    print("결과 폴더:", job_dir)
    print("=" * 100)

    return {
        "job_id": job_id,
        "industry": industry,
        "output_dir": str(job_dir),
        "t_result": t_result,
        "t_minus_1_result": t_minus_1_result,
        "change_result": change_result,
        "priority_result": priority_result,
        "analysis_result": analysis_result,
        "rag_result": rag_result,
        "gemini_financial_t_result": gemini_financial_t_result,
        "gemini_financial_t_minus_1_result": gemini_financial_t_minus_1_result,
        "ui_chunk_result": ui_chunk_result,
        "viewer_result": viewer_result,
    }


# =========================================================
# 5. CLI
# =========================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="t/t-1 사업보고서 OCR + 텍스트분석 전체 실행")

    parser.add_argument(
        "--t-pdf",
        default=str(UPLOAD_DIR / "t.pdf"),
        help="t 시점 PDF 경로. 기본값: uploads/t.pdf",
    )

    parser.add_argument(
        "--t-minus-1-pdf",
        default=str(UPLOAD_DIR / "t_minus_1.pdf"),
        help="t-1 시점 PDF 경로. 기본값: uploads/t_minus_1.pdf",
    )

    parser.add_argument(
        "--industry",
        default="제조업",
        help="업종명. 기본값: 제조업",
    )

    parser.add_argument(
        "--job-id",
        default=None,
        help="직접 지정할 job_id. 미입력 시 자동 생성",
    )

    parser.add_argument(
        "--data-type",
        default="unknown",
        help="normal/abnormal/unknown. 기본값: unknown",
    )

    # 기본은 전부 실행. 필요할 때만 스킵.
    parser.add_argument(
        "--skip-financial",
        action="store_true",
        help="재무제표 표 OCR만 건너뛰기",
    )

    parser.add_argument(
        "--skip-text",
        action="store_true",
        help="주석/감사/정정 텍스트 OCR만 건너뛰기",
    )

    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="텍스트분석만 건너뛰기",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        process_job(
            t_pdf_path=args.t_pdf,
            t_minus_1_pdf_path=args.t_minus_1_pdf,
            industry=args.industry,
            job_id=args.job_id,
            data_type=args.data_type,
            run_financial=not args.skip_financial,
            run_text=not args.skip_text,
            run_analysis=not args.skip_analysis,
        )
    except Exception as e:
        print("\n" + "=" * 100)
        print("[ERROR] main.py 실행 실패")
        print("에러:", e)
        print("=" * 100)
        traceback.print_exc()
        raise
