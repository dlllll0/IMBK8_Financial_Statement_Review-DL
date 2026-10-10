"""One isolated process per job. Original algorithms, each stage called once."""
import os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from review.storage import write_json
p=Path(sys.argv[1]).resolve();industry=sys.argv[2]
os.environ['DART_OCR_BASE_DIR']=str(p)
os.environ['DART_OCR_OUTPUT_DIR']=str(p/'scratch')
os.environ['REVIEW_UPLOAD_DIR']=str(p/'inputs')
os.environ['REVIEW_JOBS_DIR']=str(p.parent)
sys.path.insert(0,str(ROOT/'legacy'))
def stage(label):
    write_json(p/'status.json',{'job_id':p.name,'state':'running','stage':label,'error':None})
if os.getenv('REVIEW_AI_PROVIDER','vertex')=='gemini':
    if not os.getenv('GEMINI_API_KEY'):raise RuntimeError('GEMINI_API_KEY must be configured')
elif not os.getenv('VERTEX_PROJECT_ID') or not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):raise RuntimeError('External OCR credentials/project must be configured explicitly')
from main import process_one_period, _check_required_modules
from src.change_detector import run_change_detection
from src.priority_scorer import run_priority_scoring
from src.analysis_runner import run_analysis_runner
from src.rag_builder import run_rag_builder
from src.gemini_financial_structurer import run_gemini_financial_structurer
from src.ui_chunk_builder import run_ui_chunk_builder
from src.financial_comparator import run_financial_comparator
from src.viewer_builder import run_viewer_builder
_check_required_modules(True, True, True)
for period,label in [('t_minus_1','전년'),('t','당년')]:
    stage(label+' 문서 추출 · 사전 분석')
    extracted=process_one_period(pdf_path=p/(period+'.pdf'),period_type=period,industry=industry,job_id=p.name,data_type='unknown',run_financial=True,run_text=True,run_analysis=True)
    financial=extracted.get('financial',{}).get('financial_value_data')
    if financial is None or financial.empty:
        raise RuntimeError(label+' financial extraction returned no rows; check OCR output before continuing')
    stage(label+' 재무 구조화')
    structured=run_gemini_financial_structurer(p,period_type=period)
    if isinstance(structured,dict) and structured.get('status') in ['missing','failed','error']:
        raise RuntimeError(label+' financial structuring failed')
for label,fn in [('단어 변화 비교',run_change_detection),('규칙 우선순위',run_priority_scoring),('규칙별 검토 사항',run_analysis_runner),('근거 구성',run_rag_builder),('원문 문단 구성',run_ui_chunk_builder),('재무 계정 비교',run_financial_comparator),('검토 화면 구성',run_viewer_builder)]:
    stage(label);result=fn(p)
    if isinstance(result,dict) and result.get('status') in ['failed','error']:raise RuntimeError(label+' failed')
# LLM explanation generation intentionally not called: rule evidence stands alone.
