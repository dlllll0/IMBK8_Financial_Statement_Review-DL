"""Local, deterministic page lookup and financial review candidates; no AI verdicts."""
import re
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from review.adapter import stable
from review.glossary import GLOSSARY
from review.financial_guide import guide

def number(value):
    text = str(value).strip().replace(',', '').replace(' ', '')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        result = Decimal(text)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None

def normalized(text):
    return re.sub(r'\s+', '', str(text))

def enrich(payload, directory):
    import pymupdf
    directory = Path(directory)
    pages = {}
    for period in ('t', 't_minus_1'):
        path = directory / (period + '.pdf')
        if path.exists():
            with pymupdf.open(path) as pdf:
                pages[period] = [page.get_text() for page in pdf]
    page_texts={period:[normalized(page) for page in texts] for period,texts in pages.items()}
    def locate(period, text):
        needle = normalized(text)
        return [i + 1 for i, page in enumerate(page_texts.get(period, []))
                if needle and needle in page]
    for event in payload['events']:
        if event['word'] in GLOSSARY:
            event['term_meaning'],event['term_source']=GLOSSARY[event['word']]
        event['source_pages'] = locate(event['period'], event['word'])
        event['page_basis'] = 'PDF 텍스트에서 해당 표현이 일치한 페이지 후보입니다. 문맥을 원문에서 확인하세요.'
    for doc in payload['documents']:
        # A phrase can occur on several pages; do not assert a unique page without evidence.
        needle = doc['text'].strip()[:100]
        doc['source_pages'] = locate(doc['period'], needle)
    for period, tables in payload.get('tables', {}).items():
        financial_pages=set()
        for metadata in (directory/period/'financial').glob('*financial_value_data.csv'):
            with metadata.open(encoding='utf-8-sig',newline='') as stream:
                for record in csv.DictReader(stream):
                    try:
                        start,end=int(float(record['pdf_start_page'])),int(float(record['pdf_end_page']))
                        financial_pages.update(range(start,end+1))
                    except (KeyError,ValueError,TypeError):
                        pass
        def financial_locations(account):
            return [page for page in locate(period,account) if page in financial_pages] if financial_pages else []
        section_units=set()
        for page in financial_pages:
            if 0<page<=len(pages.get(period,[])):
                section_units.update(re.findall(r'단위\s*[:：]\s*(백만원|천원|원)',pages[period][page-1]))
        for table in tables:
            table_units = set()
            for row in table['rows']:
                for page in financial_locations(row.get(table['columns'][0], '')):
                    table_units.update(re.findall(r'단위\s*[:：]\s*(백만원|천원|원)', pages[period][page-1]))
            table['amount_unit'] = next(iter(table_units)) if len(table_units)==1 else next(iter(section_units)) if not table_units and len(section_units)==1 else None
            current = next((c for c in table['columns'] if '당' in c and '금액' in c), None)
            previous = next((c for c in table['columns'] if '전' in c and '금액' in c), None)
            if not current or not previous:
                continue
            account_column = table['columns'][0]
            for row in table['rows']:
                account = str(row.get(account_column, '')).strip()
                before, after = number(row.get(previous)), number(row.get(current))
                if not account or before is None or after is None:
                    continue
                locations = financial_locations(account)
                units = set()
                for page in locations:
                    units.update(re.findall(r'단위\s*[:：]\s*(백만원|천원|원)', pages[period][page-1]))
                unit = next(iter(units)) if len(units) == 1 else table['amount_unit'] if not units else None
                delta = after - before
                rate = delta / abs(before) * 100 if before else None
                reasons = []
                if rate is not None and abs(rate) >= 50:
                    reasons.append('전기 대비 절대 증감률 50% 이상')
                if before == 0 and after != 0:
                    reasons.append('전기 0에서 당기 금액 발생: 증감률 계산 불가')
                if before * after < 0:
                    reasons.append('전기와 당기 부호가 바뀜')
                total = next((number(r.get(current)) for r in table['rows']
                              if normalized(r.get(account_column)) in ('자산총계', '총자산')), None)
                share = abs(after / total * 100) if total else None
                if share is not None and share >= 50 and normalized(account) not in ('자산총계','총자산'):
                    reasons.append('당기 총자산 대비 절대 금액 비중 50% 이상')
                if share is not None and 0 < share <= Decimal('0.1') and rate is not None and abs(rate) >= 50:
                    reasons.append('총자산 대비 0.1% 이하 소액이지만 증감률이 큼: 기저효과 확인')
                existing = next((e for e in payload['events'] if e['kind']=='financial'
                                 and e.get('table')==table['name'] and e['word']==account), None) if period=='t' else None
                if not reasons and not existing:
                    continue
                event = existing or {'id':'fin-auto-'+stable(period, table['name'], account),
                    'document_id':None,'period':period,'kind':'financial','word':account,
                    'category':'금액 변동 검토 후보','change':'financial','table':table['name'],
                    'rule_id':'FIN-REVIEW-50','checks':['원문 금액·단위·기간을 확인하세요.',
                    '관련 주석에서 변동 원인과 일회성 거래 여부를 확인하세요.']}
                event.update(previous=str(before),current=str(after),source_pages=locations,
                    page_basis='계정명이 일치한 PDF 페이지 후보. 표 위치를 직접 확인하세요.',
                    amount_unit=unit, unit_multiplier={'원':1,'천원':1000,'백만원':1000000}.get(unit),
                    delta=str(delta),change_percent=f'{rate:.2f}' if rate is not None else None,
                    asset_share_percent=f'{share:.2f}' if share is not None else None,
                    count_basis='같은 업로드 보고서의 명시된 전기·당기 금액 열 비교. 음수 기준은 전기 절댓값을 분모로 사용. 원문 검증 전 추출값입니다.',
                    evidence=f"{table['name']} / {account}: {previous}={before}, {current}={after}. 단위: {unit or '미확인'}. 증감={delta}, 증감률={f'{rate:.2f}%' if rate is not None else '전기 0으로 계산 불가'}.")
                if reasons:
                    event['reason']=' · '.join(reasons)+'. 검토 후보 선정 기준이며 회계상 문제를 의미하지 않습니다.'
                if not existing:
                    payload['events'].append(event)
    for event in payload['events']:
        if event['kind']=='financial':
            event['comparison_summary'],event['checks']=guide(event)
    payload['summary'] = {key:len({(e.get('table',''),e['word']) for e in payload['events'] if e['change']==key})
                          for key in payload['summary']}
    return payload
