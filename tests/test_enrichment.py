import tempfile
import unittest
from unittest.mock import patch, MagicMock
from review.enrichment import enrich, number

class EnrichmentTests(unittest.TestCase):
    def test_zero_negative_and_missing_unit(self):
        payload={'documents':[],'events':[],'summary':{'financial':0},'tables':{'t':[{
            'name':'재무상태표','columns':['과목','당기 금액','전기 금액'],
            'rows':[{'과목':'A','당기 금액':'1,000','전기 금액':'0'},
                    {'과목':'B','당기 금액':'(200)','전기 금액':'(100)'}]}]}}
        with tempfile.TemporaryDirectory() as directory:
            result=enrich(payload,directory)
        first,second=result['events']
        self.assertIsNone(first['change_percent'])
        self.assertIsNone(first['amount_unit'])
        self.assertEqual(second['delta'],'-100')
        self.assertEqual(second['change_percent'],'-100.00')
        self.assertIsNone(number('NaN'))
        self.assertEqual(result['summary']['financial'],2)

    def test_context_window_covers_late_highlight(self):
        from review.explanation import evidence_pack
        text='앞부분 '*900+'대손충당금 뒤의 검토 문맥'
        position=text.index('대손충당금')
        event={'word':'대손충당금','kind':'notes','reason':'검토','checks':[],
               'count_basis':'문단','evidence':'대손충당금','period':'t','document_id':'selected','category':'검토'}
        payload={'events':[],'documents':[{'id':'selected','kind':'notes','period':'t','heading':'주석','text':text,
                 'marks':[{'word':'대손충당금','start':position,'end':position+5}]}]}
        sources=evidence_pack(payload,event)
        self.assertTrue(any('뒤의 검토 문맥' in source['text'] for source in sources))
