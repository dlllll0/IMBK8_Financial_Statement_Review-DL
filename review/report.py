"""Self-contained, escaped HTML snapshot for download and browser PDF printing."""
from collections import Counter
from datetime import datetime, timezone
from html import escape

KINDS={'financial':'재무제표','notes':'재무제표 주석','audit':'감사보고서','correction':'정정신고'}
STATES={'pending':'미검토','reviewed':'확인 완료','followup':'추가 확인 필요'}
def h(value): return escape(str(value if value is not None else '미확인'))

def build_report(payload, decisions, explanations, reviewer='', conclusion='', title='재무제표 최종 검토 보고서'):
    counts=Counter(decisions.get(e['id'],{}).get('state','pending') for e in payload['events'])
    complete=bool(payload['events']) and counts['pending']==0 and counts['followup']==0
    status='모든 표시 항목 확인 완료 · 담당자 기록 기준' if complete else '검토 미완료 · 미검토/추가 확인 사항 포함'
    now=datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
    parts=[]
    for kind,label in KINDS.items():
        items=[e for e in payload['events'] if e['kind']==kind]
        present=bool(payload.get('tables',{}).get('t') or payload.get('tables',{}).get('t_minus_1')) if kind=='financial' else any(d['kind']==kind for d in payload['documents'])
        parts.append(f'<section><h2>{h(label)} <small>{len(items)}개 검토 위치</small></h2>')
        if not items: parts.append('<p>표시 항목이 없습니다. '+('추출 문서는 있으나 규칙 일치가 없는 상태이며 정상 판정을 뜻하지 않습니다.' if present else '이 유형의 추출 자료가 없어 검토할 수 없습니다.')+'</p>')
        for e in items:
            record=decisions.get(e['id'],{});state=record.get('state','pending')
            parts.append(f'''<article><h3>{h(e['word'])} <span class="tag">{h(STATES.get(state,state))}</span></h3>
<p class="meta">{h(e['category'])} · {h(e['period'])} · 근거 ID {h(e['id'])} · 규칙 {h(e['rule_id'])}</p>
<p><b>표시 이유</b> {h(e['reason'])}</p><p><b>전기 → 당기 추출값</b> {h(e.get('previous'))} → {h(e.get('current'))}</p>
<p class="meta">{h(e['count_basis'])}</p><blockquote>{h(e.get('evidence','근거 없음'))}</blockquote>
<h4>담당자 확인사항</h4><ul>{''.join('<li>'+h(c)+'</li>' for c in e['checks'])}</ul>
<h4>담당자 검토 기록</h4><p class="note">{h(record.get('note') or '기록 없음')}</p>''')
            llm=explanations.get(e['id'])
            if llm:
                parts.append(f'<h4>LLM 설명 · 담당자 검증 필요</h4><p class="meta">{h(llm.get("model"))} · {h(llm.get("generated_at"))}</p>')
                for paragraph in llm.get('paragraphs',[]):
                    parts.append('<p>'+h(paragraph['text'])+' <small>['+h(', '.join(paragraph['source_ids']))+']</small></p>')
                    for quote in paragraph['quotes']:parts.append('<blockquote>'+h(quote['source_id'])+': '+h(quote['quote'])+'</blockquote>')
            else: parts.append('<p class="meta">LLM 설명: 미생성</p>')
            parts.append('</article>')
        parts.append('</section>')
    synthetic='<p class="synthetic">합성 예시 · 실제 기업이나 실제 분석 성과가 아닙니다.</p>' if payload['synthetic'] else ''
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title>
<style>body{{font-family:"Malgun Gothic",sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#152744;font-size:13px;line-height:1.8}}h1{{font-size:29px;line-height:1.4}}h2{{border-bottom:2px solid #dce5f4;padding-bottom:10px;margin-top:36px}}h3{{font-size:17px}}h4{{margin-bottom:4px}}.meta,small{{font-size:11px;color:#526279;overflow-wrap:anywhere}}.banner,.synthetic{{padding:15px;background:#edf3ff;border-left:4px solid #355fe5}}.synthetic{{background:#fff4d4}}article{{padding:18px 0;border-bottom:1px solid #dce5f4}}blockquote{{margin:12px 0;padding:12px;background:#f4f6fa;border-left:3px solid #bccae4;white-space:pre-wrap;overflow-wrap:anywhere}}.note{{white-space:pre-wrap;overflow-wrap:anywhere}}.tag{{font-size:11px;border:1px solid #a9b9d8;border-radius:5px;padding:3px 8px}}.stats{{display:flex;gap:24px;flex-wrap:wrap}}footer{{border-top:1px solid #dce5f4;margin-top:28px;padding-top:14px;font-size:11px}}@page{{size:A4;margin:18mm}}@media print{{body{{margin:0;padding:0;max-width:none;font-size:10pt}}h2,h3,h4{{break-after:avoid}}blockquote,.note{{orphans:3;widows:3}}.banner{{print-color-adjust:exact}}}}</style></head><body>
<p class="meta">표독 · FINANCIAL REVIEW / 검토 기록 스냅샷</p><h1>{h(title)}</h1>{synthetic}<p class="banner">{h(status)}</p>
<p>작성자: {h(reviewer or '미기재')}<br>생성 시각: {h(now)}<br>작업: {h(payload['job_id'])}</p>
<div class="stats"><b>전체 {len(payload['events'])}개 위치</b><span>확인 완료 {counts['reviewed']}</span><span>미검토 {counts['pending']}</span><span>추가 확인 {counts['followup']}</span></div>
<h2>담당자 종합 의견</h2><p class="note">{h(conclusion or '종합 의견 미작성')}</p>
<p>이 보고서는 추출 자료의 규칙 일치와 담당자 기록을 모은 결과입니다. 원문 전체의 완전성, 회계부정 여부, 신용도 또는 여신 승인에 대한 판정이 아닙니다. 확인 완료는 담당자가 표시한 상태이며 외부 검증을 뜻하지 않습니다.</p>
{''.join(parts)}<footer>현재 저장된 기록을 기준으로 작성했습니다. LLM 설명의 출처·인용 검사는 의미의 정확성을 보장하지 않습니다. 원문·기간·단위를 확인하고 최종 결정은 담당자가 수행해야 합니다.</footer></body></html>'''
