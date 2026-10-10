"""Exact arithmetic summary and review questions, never findings of a problem."""
from decimal import Decimal

def guide(event):
    account=event['word']
    checks=['원문에서 '+account+'의 전기·당기 금액, 단위와 비교 기간을 확인하세요.']
    groups=[
      (('매출채권','대여금','미수금','대손'), '매출채권·대여금의 회수 지연 또는 손상 가능성을 검토하려면 채권 연령, 대손충당금 및 특수관계자 관련 주석을 확인하세요.'),
      (('재고',), '재고자산의 장기 체화·평가손실 가능성을 검토하려면 재고 구성, 평가충당금 및 매출원가 관련 주석을 확인하세요.'),
      (('차입','사채','부채','유동성'), '차입금·부채의 상환 부담을 검토하려면 만기, 이자비용, 담보·약정 및 현금흐름 관련 내용을 확인하세요.'),
      (('보증','충당','우발'), '지급보증·충당부채·우발채무의 추가 의무 가능성을 검토하려면 보증 대상, 조건, 추정 근거와 관련 주석을 확인하세요.'),
      (('유형자산','무형자산','영업권','손상'), '자산손상 또는 처분에 따른 변동인지 검토하려면 손상차손, 회수가능액, 취득·처분 및 감가상각 관련 주석을 확인하세요.'),
      (('매출','수익','이익','손실','비용'), '매출·이익 변동의 지속성을 검토하려면 수익인식, 일회성 손익 및 관련 비용의 변화 내용을 확인하세요.'),
      (('현금','예금'), '현금 감소가 유동성 부담으로 이어지는지 검토하려면 영업현금흐름, 사용제한 예금 및 단기 상환 일정 관련 내용을 확인하세요.')]
    for words,question in groups:
        if any(word in account for word in words):checks.append(question)
    checks.append(account+'의 변동 원인이 일회성 거래·재분류·회계정책 변경인지 관련 주석과 대조하세요.')
    unit=event.get('amount_unit');multiplier={'원':1,'천원':1000,'백만원':1000000}.get(unit)
    def amount(value):
        number=Decimal(str(value))
        return f'{number*multiplier:,.0f}원' if multiplier else f'{number:,} (단위 미확인)'
    if event.get('delta') is None:
        summary=f'{account}: 전기·당기 대응 금액이 확인되지 않아 증감액·증감률을 계산하지 않았습니다.'
    else:
        delta=Decimal(event['delta']);direction='증가' if delta>0 else '감소' if delta<0 else '변동 없음'
        rate=event.get('change_percent')
        rate_text=f"증감률 {rate}%" if rate is not None else '전기 금액이 0이므로 증감률 계산 불가'
        summary=f"{event.get('table','재무표')}의 {account}: 전기 {amount(event['previous'])} → 당기 {amount(event['current'])}. 전기 대비 {amount(abs(delta))} {direction}, {rate_text}."
    return summary,checks
