"""Local review API. Raw inputs and outputs are never mounted as static files."""
import json,os,re,sys,uuid,subprocess,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from review.adapter import adapt,demo
from review.explanation import configuration, explain, evidence_pack, evidence_hash
from review.report import build_report
from review.storage import write_json
from review.enrichment import enrich
from review.explanation import error_hint
from review.explanation import EXPLANATION_VERSION
from review.connections import resolve
ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('REVIEW_DATA_DIR',ROOT/'.local')).resolve();DATA.mkdir(parents=True,exist_ok=True)
POOL=ThreadPoolExecutor(max_workers=1);LOCK=threading.Lock()
LLM_LOCK=threading.Lock()
app=FastAPI(title='RM Review Workbench',docs_url='/api/docs')
app.mount('/web',StaticFiles(directory=ROOT/'web'),name='web')
def folder(job):
    if not re.fullmatch(r'job_[a-f0-9]{32}',job):raise HTTPException(400,'잘못된 작업 ID입니다.')
    path=DATA/job
    if not path.is_dir():raise HTTPException(404,'작업을 찾을 수 없습니다.')
    return path

def write(path,data):
    write_json(path,data)
def status(path,state,stage,error=None):write(path/'status.json',{'job_id':path.name,'state':state,'stage':stage,'error':error})
@app.get('/')
@app.get('/result/{job_id}')
def home(job_id=None):return FileResponse(ROOT/'web/index.html')
@app.get('/api/demo')
def get_demo():return demo()
@app.get('/api/config')
def config():return {'ocr_enabled':os.getenv('REVIEW_ENABLE_OCR')=='1','llm':configuration()}
@app.get('/api/jobs')
def jobs():
    out=[]
    for p in sorted(DATA.glob('job_*/status.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        d=json.loads(p.read_text(encoding='utf-8'))
        out.append(d)
    return out
@app.get('/api/jobs/{job}/status')
def get_status(job):return json.loads((folder(job)/'status.json').read_text(encoding='utf-8'))
@app.get('/api/jobs/{job}/result')
def result(job):
    p=folder(job)/'review.json'
    if not p.exists():raise HTTPException(409,'검토 결과가 아직 준비되지 않았습니다.')
    return enrich(json.loads(p.read_text(encoding='utf-8')),p.parent)
@app.post('/api/import',status_code=201)
async def import_result(file:UploadFile=File(...)):
    content=await file.read(20*1024*1024+1)
    if len(content)>20*1024*1024:raise HTTPException(413,'JSON은 20MB 이하만 지원합니다.')
    try:
        raw=json.loads(content)
        if not isinstance(raw,dict) or not isinstance(raw.get('t'),dict) or not isinstance(raw.get('t_minus_1'),dict):raise ValueError()
        job='job_'+uuid.uuid4().hex;payload=adapt(raw,job_id=job)
        if not payload['documents'] and not payload['events']:raise ValueError()
    except (ValueError,TypeError,AttributeError,KeyError):raise HTTPException(400,'t/t_minus_1 문단이 있는 viewer_data.json을 선택하세요.')
    p=DATA/job;p.mkdir();write(p/'review.json',payload);status(p,'done','기존 결과 불러오기 완료')
    return {'job_id':job}
class Decision(BaseModel):
    state:str=Field(pattern='^(pending|reviewed|followup)$')
    note:str=Field(default='',max_length=4000)

class ReportRequest(BaseModel):
    title:str=Field(default='재무제표 최종 검토 보고서',max_length=120)
    reviewer:str=Field(default='',max_length=120)
    conclusion:str=Field(default='',max_length=6000)
    decisions:dict[str,Decision]=Field(default_factory=dict,max_length=2000)

@app.get('/api/jobs/{job}/explanations')
def explanations(job):
    p=folder(job)/'explanations.json'
    cache=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    return {key:value for key,value in cache.items() if value.get('version')==EXPLANATION_VERSION}

def check_links(payload,event_id,index):
    event=next((e for e in payload['events'] if e['id']==event_id),None)
    if event is None:raise HTTPException(404,'검토 위치가 없습니다.')
    if index<0 or index>=len(event['checks']):raise HTTPException(404,'확인사항이 없습니다.')
    return {'check':event['checks'][index],**resolve(payload,event,event['checks'][index])}

@app.get('/api/jobs/{job}/checks/{event_id}/{index}')
def saved_check_links(job,event_id,index:int):return check_links(result(job),event_id,index)

@app.get('/api/demo/checks/{event_id}/{index}')
def demo_check_links(event_id,index:int):return check_links(demo(),event_id,index)

def generate_explanation(payload,event_id):
    event=next((e for e in payload['events'] if e['id']==event_id),None)
    if event is None:raise HTTPException(404,'근거 ID가 없습니다.')
    if not configuration()['enabled']:raise HTTPException(503,configuration()['message'])
    if not LLM_LOCK.acquire(blocking=False):raise HTTPException(429,'다른 설명을 생성 중입니다. 완료 후 다시 시도하세요.')
    try:return explain(payload,event)
    except ValueError:raise HTTPException(422,'생성 설명의 출처·인용문·수치 검사를 통과하지 못했습니다. 원문 근거를 확인하세요.')
    except Exception as error:
        code=getattr(error,'code',None)
        if code==400:raise HTTPException(502,'LLM 설명 생성 실패 (HTTP 400): '+error_hint(error))
        message={400:'요청 형식 또는 API 키 설정',401:'인증',403:'접근 권한',404:'모델 이름·사용 권한',429:'무료 호출 한도·요청 빈도'}.get(code,'네트워크 연결·응답 처리')
        raise HTTPException(502,f'LLM 설명 생성 실패: {message} 확인이 필요합니다 (HTTP {code or "미수신"}). 규칙 근거는 계속 검토할 수 있습니다.')
    finally:LLM_LOCK.release()

@app.post('/api/jobs/{job}/explanations/{event_id}')
def generate_saved_explanation(job,event_id):
    payload=result(job)
    event=next((e for e in payload['events'] if e['id']==event_id),None)
    if event is None:raise HTTPException(404,'근거 ID가 없습니다.')
    saved=explanations(job).get(event_id)
    if saved and saved.get('evidence_hash')==evidence_hash(evidence_pack(payload,event)):return saved
    generated=generate_explanation(payload,event_id)
    with LOCK:
        cache=explanations(job);cache[event_id]=generated;write(folder(job)/'explanations.json',cache)
    return generated

DEMO_EXPLANATIONS={}
@app.post('/api/demo/explanations/{event_id}')
def generate_demo_explanation(event_id):
    if event_id in DEMO_EXPLANATIONS:return DEMO_EXPLANATIONS[event_id]
    generated=generate_explanation(demo(),event_id)
    DEMO_EXPLANATIONS[event_id]=generated
    return generated

@app.post('/api/jobs/{job}/report',response_class=HTMLResponse)
def report(job,request:ReportRequest):
    with LOCK:
        payload=result(job);saved=decisions(job);generated=explanations(job)
    # Actual job reports always use server records, never client-supplied decisions.
    return HTMLResponse(build_report(payload,saved,generated,request.reviewer,request.conclusion,request.title))

@app.post('/api/demo/report',response_class=HTMLResponse)
def demo_report(request:ReportRequest):
    payload=demo();valid={e['id'] for e in payload['events']}
    if not request.decisions.keys()<=valid:raise HTTPException(400,'알 수 없는 검토 위치입니다.')
    saved={key:value.model_dump() for key,value in request.decisions.items()}
    return HTMLResponse(build_report(payload,saved,DEMO_EXPLANATIONS,request.reviewer,request.conclusion,request.title))
@app.get('/api/jobs/{job}/decisions')
def decisions(job):
    p=folder(job)/'decisions.json';return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
@app.put('/api/jobs/{job}/decisions/{event}')
def save_decision(job,event:str,decision:Decision):
    p=folder(job)
    payload=result(job)
    if event not in {x['id'] for x in payload['events']}:raise HTTPException(404,'근거 ID가 없습니다.')
    with LOCK:
        data=decisions(job);data[event]=decision.model_dump();write(p/'decisions.json',data)
    return {'ok':True}
def run_job(p,industry):
    try:
        status(p,'running','추출 및 규칙 분석 준비')
        env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
        with (p/'worker.log').open('w',encoding='utf-8') as log:
            proc=subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/worker.py'),str(p),industry],cwd=p,env=env,stdout=log,stderr=log,timeout=1800)
        if proc.returncode!=0:status(p,'failed','분석 중단','OCR 설정·의존성 또는 문서 처리를 확인하세요. 로컬 worker.log에 상세 기록이 있습니다.');return
        payload=adapt(json.loads((p/'viewer_data/viewer_data.json').read_text(encoding='utf-8')),job_id=p.name)
        write(p/'review.json',payload);status(p,'done','검토 준비 완료')
    except subprocess.TimeoutExpired:status(p,'failed','시간 제한 초과','30분 제한으로 작업을 중단했습니다.')
    except Exception:status(p,'failed','결과 구성 실패','로컬 작업 폴더와 입력 형식을 확인하세요.')
@app.post('/api/analyze',status_code=202)
async def analyze(pdf_t:UploadFile=File(...),pdf_t_minus_1:UploadFile=File(...),industry:str=Form(...)):
    if os.getenv('REVIEW_ENABLE_OCR')!='1':raise HTTPException(503,'OCR 연결이 비활성화돼 있습니다. 합성 데모 또는 기존 결과 불러오기를 이용하세요.')
    if industry not in ['제조업','건설업','금융업','정보통신업','기타']:raise HTTPException(422,'업종을 선택하세요.')
    files=[]
    for upload in [pdf_t_minus_1,pdf_t]:
        data=await upload.read(30*1024*1024+1)
        if len(data)>30*1024*1024:raise HTTPException(413,'PDF는 각각 30MB 이하만 지원합니다.')
        if not data.startswith(b'%PDF-'):raise HTTPException(415,'PDF 파일을 선택하세요.')
        files.append(data)
    p=DATA/('job_'+uuid.uuid4().hex);p.mkdir()
    for name,data in zip(['t_minus_1.pdf','t.pdf'],files):(p/name).write_bytes(data)
    status(p,'queued','순서 대기');POOL.submit(run_job,p,industry)
    return {'job_id':p.name}
# On restart, do not show abandoned queued/running jobs as active.
for path in DATA.glob('job_*/status.json'):
    try:
        d=json.loads(path.read_text(encoding='utf-8'))
        if d.get('state') in ['running','queued']:status(path.parent,'interrupted','이전 서버에서 중단됨','새 분석을 시작하세요.')
    except (ValueError,OSError):pass
