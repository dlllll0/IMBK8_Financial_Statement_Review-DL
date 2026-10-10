"""Deterministic review adapter. Never treats generated prose as a rule verdict."""
import ast, hashlib, json, re
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
def literal(name,file):
    for n in ast.parse((ROOT/'legacy'/file).read_text(encoding='utf-8')).body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets):return ast.literal_eval(n.value)
VOCAB=literal('CATEGORY_VOCAB','category_vocab.py')
RULES=literal('CATEGORY_ANALYSIS_RULES','src/category_analysis_rules.py')
def stable(*parts):return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:18]
def spans(text):
    matches=[]
    for word,category in VOCAB.items():
        if len(word)<2:continue
        for m in re.finditer(re.escape(word),text):matches.append((m.start(),m.end(),word,category))
    chosen=[];end=-1
    for a,b,w,c in sorted(matches,key=lambda x:(x[0],-(x[1]-x[0]))):
        if a>=end:chosen.append((a,b,w,c));end=b
    return chosen

def adapt(viewer,changes=None,job_id='demo',synthetic=False):
    docs=[];occ=[];counts={'t':Counter(),'t_minus_1':Counter()}
    for period in counts:
        data=viewer.get(period,{})
        for kind,doc in data.get('documents',{}).items():
            for si,section in enumerate(doc.get('sections',[])):
                for ci,chunk in enumerate(section.get('chunks',[])):
                    text=str(chunk.get('text',''));did=stable(period,kind,si,ci)
                    item={'id':did,'period':period,'kind':kind,'heading':section.get('heading') or doc.get('title') or kind,'text':text,'page':chunk.get('page') or chunk.get('page_number'),'marks':[]}
                    for a,b,word,category in spans(text):
                        eid=stable(did,a,b,word);mark={'id':eid,'start':a,'end':b,'word':word};item['marks'].append(mark)
                        counts[period][word]+=1;occ.append((eid,did,period,kind,word,category,text,max(0,a-70),min(len(text),b+100)))
                    docs.append(item)
    events=[]
    for eid,did,period,kind,word,category,text,a,b in occ:
        previous,current=counts['t_minus_1'][word],counts['t'][word]
        change='new' if previous==0 else 'removed' if current==0 else 'increased' if current>previous else 'decreased' if current<previous else 'persistent'
        rule=RULES.get(category,{})
        methods=rule.get('analysis_methods',rule.get('methods',[]))
        if not isinstance(methods,list):methods=[str(methods)]
        events.append({'id':eid,'document_id':did,'period':period,'kind':kind,'word':word,'category':category,'change':change,'previous':previous,'current':current,'rule_id':'DICT-'+stable(word,category)[:8],'reason':'자체 구축 검토 사전에 포함된 표현이 이 문단에서 확인되었습니다. 표현 자체가 회계이상 또는 부정을 의미하지 않습니다.','checks':methods or ['표현의 문맥과 실제 거래·주석 내용을 확인하세요.'],'evidence':text[a:b],'count_basis':'화면에 표시된 추출 문단의 겹치지 않는 문자열 일치 횟수. 원문 전체 빈도 또는 기존 명사 행 수와 다를 수 있습니다.'})
    # Preserve exact rule-linked financial evidence; do not display generated interpretation as fact.
    seen=set()
    for period in ['t','t_minus_1']:
        fin=viewer.get(period,{}).get('financial',{})
        for i,item in enumerate(fin.get('highlight_items',[])):
            table=item.get('table_name','');account=item.get('account_name',item.get('highlight_account',''))
            if not account:continue
            key=(table,account,i)
            if key in seen:continue
            seen.add(key)
            eid='fin-'+stable(table,account,i)
            evidence=next((x for x in fin.get('financial_rag_items',[]) if x.get('table_name')==table and x.get('account_name')==account),{})
            # Explicitly unverified values: never infer unit or percentage.
            events.append({'id':eid,'document_id':None,'period':'t','kind':'financial','word':account,'category':item.get('risk_category','재무 항목 검토'),'change':'financial','previous':evidence.get('previous_value'),'current':evidence.get('current_value'),'rule_id':'FIN-'+stable(table,account)[:8],'reason':item.get('analysis_method') or '기존 재무 규칙에 연결된 계정입니다.','checks':[str(item.get('check_points','계정·단위·기간을 원문과 대조하세요.'))],'evidence':str(evidence.get('financial_evidence','')),'count_basis':'기존 재무 비교 결과. 금액 단위·추출 열·전년 대응을 원문에서 확인하기 전에는 증감률이나 위험 판단에 사용하지 마세요.','table':table})
    tables={period:[{'name':table.get('name','재무 표'),'columns':table.get('columns',[]),'rows':table.get('rows',[])} for table in viewer.get(period,{}).get('financial',{}).get('tables',[])] for period in counts}
    return {'job_id':job_id,'synthetic':synthetic,'documents':docs,'tables':tables,'events':events,'warnings':['규칙 일치는 검토의 시작점이며 최종 판단은 담당자에게 있습니다.','빈도는 추출된 화면 문단 기준입니다. 누락·중복·OCR 오류와 문맥을 확인하세요.'],'summary':{k:len({(e.get('table',''),e['word']) for e in events if e['change']==k}) for k in ['new','removed','increased','decreased','persistent','financial']}}

def demo():
    texts={'t_minus_1':['당사는 사채보증 약정을 유지하고 있습니다. 대손충당금 산정 근거를 검토하였습니다.','우발채무와 관련한 공시를 검토하였습니다.'], 't':['당기 자산손상 징후를 검토하였습니다. 대손충당금 산정 가정을 변경하였습니다.','대손충당금 추정의 불확실성과 우발채무에 관한 추가 설명입니다. 자산손상 평가 근거를 확인합니다.']}
    v={p:{'documents':{'notes':{'title':'재무제표 주석','sections':[{'heading':'주요 회계 추정 · 합성 예시','chunks':[{'text':t,'page':i+1} for i,t in enumerate(ts)]}]}}} for p,ts in texts.items()}
    for period,amount in [('t',120),('t_minus_1',100)]:
        v[period]['documents']['audit']={'title':'감사보고서 · 합성 예시','sections':[{'heading':'검토할 표현 예시','chunks':[{'text':'감사 문서에 대손충당금 추정에 관한 설명이 포함되어 있습니다. 이 문장은 기능 시연용 합성 문장입니다.','page':3}]}]}
        v[period]['documents']['correction']={'title':'정정신고 · 합성 예시','sections':[{'heading':'정정 내역 예시','chunks':[{'text':'우발채무 관련 설명의 기재 위치를 정정하였습니다. 실제 기업 공시가 아닌 합성 예시입니다.','page':4}]}]}
        v[period]['financial']={'tables':[{'name':'재무상태표 · 합성 예시','columns':['과목','당기 예시값','전기 예시값'], 'rows':[{'과목':'매출채권','당기 예시값':amount,'전기 예시값':80 if period=='t_minus_1' else 100},{'과목':'현금및현금성자산','당기 예시값':60,'전기 예시값':55}]}]}
    v['t']['financial']['highlight_items']=[{'table_name':'재무상태표 · 합성 예시','account_name':'매출채권','risk_category':'합성 재무 계정 검토','analysis_method':'전기와 당기 계정의 기간·단위를 대조한 후 변화를 확인합니다.','check_points':'채권 회수 내역과 관련 주석을 원문에서 확인하세요.'}]
    v['t']['financial']['financial_rag_items']=[{'table_name':'재무상태표 · 합성 예시','account_name':'매출채권','previous_value':100,'current_value':120,'financial_evidence':'합성 재무상태표: 전기 예시값 100, 당기 예시값 120. 실제 기업의 금액이 아니며 단위는 부여하지 않았습니다.'}]
    return adapt(v,job_id='demo',synthetic=True)
