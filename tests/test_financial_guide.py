import unittest
from review.financial_guide import guide

class FinancialGuideTests(unittest.TestCase):
    def test_exact_summary_and_account_specific_checks(self):
        summary,checks=guide({'word':'매출채권','table':'재무상태표','previous':'1000',
            'current':'500','delta':'-500','change_percent':'-50.00','amount_unit':'천원'})
        self.assertIn('전기 1,000,000원 → 당기 500,000원',summary)
        self.assertIn('500,000원 감소',summary)
        self.assertIn('-50.00%',summary)
        self.assertTrue(any('회수 지연' in check for check in checks))
    def test_zero_and_missing_unit(self):
        summary,_=guide({'word':'차입금','previous':'0','current':'100','delta':'100',
                         'change_percent':None,'amount_unit':None})
        self.assertIn('단위 미확인',summary)
        self.assertIn('증감률 계산 불가',summary)
        self.assertNotIn('100원',summary)
