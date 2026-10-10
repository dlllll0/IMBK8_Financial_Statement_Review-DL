"""Transparent keyword links, not proof of a causal relationship."""
from review.adapter import VOCAB

TOPICS=[('특수관계자','계열사','내부거래','지급보증','매출채권','대여금'),
        ('손상','손상차손','자산손상','회수가능액','유형자산','영업권'),
        ('우발채무','우발부채','충당부채','보증','소송'),
        ('대손충당금','대손','매출채권','연체','회수')]

def terms_for(event,check=None):
    text=check if check is not None else event['word']
    terms={word for word in VOCAB if word in text}
    for topic in TOPICS:
        if any(word in text for word in topic):terms.update(topic)
    if not terms:
        terms={word for word,category in VOCAB.items() if category==event['category']}
    return sorted(terms,key=lambda x:(-len(x),x))

def resolve(payload,event,check=None):
    if check is not None and ('은행데이터' in check or '입출금흐름' in check):
        return {'status':'additional_data','basis':'은행 고객 거래 데이터는 현재 보고서 입력에 포함되지 않았습니다.','documents':[],'accounts':[],'events':[]}
    terms=terms_for(event,check)
    documents=[]
    for doc in payload['documents']:
        if check is None and doc['id']==event.get('document_id'):continue
        matches=[word for word in terms if word in doc['text']]
        if not matches:continue
        start=doc['text'].find(matches[0]);snippet=doc['text'][max(0,start-160):start+500]
        linked=next((e for e in payload['events'] if e.get('document_id')==doc['id'] and e['word'] in matches),None)
        documents.append({'document_id':doc['id'],'event_id':linked['id'] if linked else None,
            'period':doc['period'],'kind':doc['kind'],'heading':doc['heading'],'text':snippet,
            'pages':doc.get('source_pages') or ([doc['page']] if doc.get('page') else []),'matched_terms':matches})
    documents.sort(key=lambda d:(d['period']!=event['period'],not bool(d['pages']),d['document_id']))
    accounts=[]
    for period,tables in payload.get('tables',{}).items():
        for table in tables:
            if not table['columns']:continue
            for row in table['rows']:
                account=str(row.get(table['columns'][0],''))
                if not any(word in account for word in terms):continue
                linked=next((e for e in payload['events'] if e['kind']=='financial' and e['word']==account and e.get('table')==table['name'] and e['period']==period),None)
                accounts.append({'period':period,'table':table['name'],'account':account,'row':row,
                    'amount_unit':table.get('amount_unit'),'event_id':linked['id'] if linked else None,
                    'comparison':linked.get('evidence') if linked else '명시된 전기·당기 열을 원문과 대조하세요.'})
    events=[];seen=set()
    for candidate in payload['events']:
        if candidate['kind']=='financial' or candidate['word'] not in terms or candidate['word'] in seen:continue
        seen.add(candidate['word'])
        events.append({k:candidate[k] for k in ('id','word','previous','current','change','count_basis')})
    return {'status':'linked' if documents or accounts or events else 'not_found',
        'basis':'규칙·계정명에 따른 키워드 연결 후보입니다. 동일 거래나 원인으로 확인된 관계가 아닙니다. 검색 결과 부재는 원문 내용 부재를 뜻하지 않습니다.',
        'terms':terms,'documents':documents[:10],'accounts':accounts[:12],'events':events[:15],
        'document_total':len(documents),'account_total':len(accounts),'event_total':len(events)}
