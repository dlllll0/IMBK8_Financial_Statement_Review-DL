import unittest
from review.connections import resolve
from review.explanation import evidence_pack

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.event={'id':'selected','word':'특수관계자','category':'거래 검토','kind':'notes',
          'period':'t','document_id':'a','evidence':'특수관계자 거래를 기재합니다.',
          'reason':'사전 일치','count_basis':'추출 문단','checks':['매출채권, 대여금 변화를 비교'],
          'previous':1,'current':2,'change':'increased'}
        self.payload={'events':[self.event], 'documents':[
          {'id':'a','kind':'notes','period':'t','heading':'선택 문단',
           'text':self.event['evidence'],'marks':[{'word':'특수관계자','start':0,'end':5}]},
          {'id':'b','kind':'notes','period':'t','heading':'다른 페이지',
           'text':'계열사 대여금에 대한 관련 설명입니다.','marks':[],'source_pages':[9]}],
          'tables':{'t':[{'name':'재무상태표','columns':['과목','당기 금액','전기 금액'],
           'amount_unit':'원','rows':[{'과목':'대여금','당기 금액':100,'전기 금액':80}]}]}}
    def test_other_page_and_account_link_without_claiming_same_transaction(self):
        result=resolve(self.payload,self.event)
        self.assertEqual(result['documents'][0]['document_id'],'b')
        self.assertEqual(result['documents'][0]['pages'],[9])
        self.assertEqual(result['accounts'][0]['account'],'대여금')
        self.assertIn('확인된 관계가 아닙니다',result['basis'])
        sources=evidence_pack(self.payload,self.event)
        self.assertTrue(any('계열사 대여금' in s['text'] for s in sources))
    def test_bank_data_missing_never_marked_confirmed(self):
        result=resolve(self.payload,self.event,'은행데이터의 대출잔액, 입출금흐름 변화와 함께 확인')
        self.assertEqual(result['status'],'additional_data')
        self.assertEqual(result['documents'],[])
        self.assertEqual(result['accounts'],[])
