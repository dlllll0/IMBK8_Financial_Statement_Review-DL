import json
import os
import tempfile
import unittest

TEMP = tempfile.TemporaryDirectory()
os.environ['REVIEW_DATA_DIR'] = TEMP.name
os.environ.pop('REVIEW_ENABLE_OCR', None)
from fastapi.testclient import TestClient
from app import app
from review.adapter import adapt, demo, spans

def fixture():
    return {p: {'documents': {'notes': {'sections': [{'chunks': [{'text': t}]}]}}}
            for p, t in [('t', '😀 자산손상 대손충당금 대손충당금'),
                         ('t_minus_1', '사채보증 대손충당금')]}

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_changes_and_unicode_positions(self):
        payload = adapt(fixture())
        self.assertEqual(payload['summary']['new'], 1)
        self.assertEqual(payload['summary']['removed'], 1)
        self.assertEqual(payload['summary']['increased'], 1)
        for doc in payload['documents']:
            for mark in doc['marks']:
                self.assertEqual(doc['text'][mark['start']:mark['end']], mark['word'])
        self.assertEqual(payload, adapt(fixture()))

    def test_nonoverlap(self):
        matches = spans('대손충당금 자산손상 사채보증')
        self.assertTrue(matches)
        for left, right in zip(matches, matches[1:]):
            self.assertLessEqual(left[1], right[0])

    def test_demo_explicitly_synthetic(self):
        self.assertTrue(self.client.get('/api/demo').json()['synthetic'])
        self.assertTrue(all(e['checks'] for e in demo()['events']))

    def test_financial_evidence_preserved_without_generated_verdict(self):
        source = fixture()
        source['t']['financial'] = {
            'tables': [{'name': '합성 표', 'columns': ['과목', '금액'], 'rows': [{'과목': '합성계정', '금액': '100'}]}],
            'highlight_items': [{'table_name': '합성 표', 'account_name': '합성계정', 'analysis_method': '기간과 단위를 확인', 'interpretation': 'UNVERIFIED_GENERATED_VERDICT'}],
            'financial_rag_items': [{'table_name': '합성 표', 'account_name': '합성계정', 'previous_value': 80, 'current_value': 100, 'financial_evidence': '합성 비교 근거'}]}
        payload = adapt(source)
        item = next(e for e in payload['events'] if e['kind'] == 'financial')
        self.assertEqual((item['previous'], item['current']), (80, 100))
        self.assertEqual(payload['tables']['t'][0]['rows'][0]['금액'], '100')
        self.assertNotIn('UNVERIFIED_GENERATED_VERDICT', json.dumps(payload))

    def test_import_save_reload(self):
        response = self.client.post('/api/import', files={'file': ('viewer.json', json.dumps(fixture()), 'application/json')})
        self.assertEqual(response.status_code, 201)
        job = response.json()['job_id']
        result = self.client.get(f'/api/jobs/{job}/result').json()
        event = result['events'][0]['id']
        url = f'/api/jobs/{job}/decisions/{event}'
        record = {'state': 'followup', 'note': '원문 확인 필요'}
        self.assertEqual(self.client.put(url, json=record).status_code, 200)
        with TestClient(app) as new_client:
            self.assertEqual(new_client.get(f'/api/jobs/{job}/decisions').json()[event], record)
            self.assertEqual(new_client.get(f'/result/{job}').status_code, 200)
        self.assertEqual(self.client.put(url, json={'state': 'fraud'}).status_code, 422)
        self.assertEqual(self.client.put(f'/api/jobs/{job}/decisions/missing', json=record).status_code, 404)

    def test_bad_import_and_job(self):
        for raw in ['not json', '{}', '{"t":{},"t_minus_1":{}}']:
            self.assertEqual(self.client.post('/api/import', files={'file': ('bad.json', raw)}).status_code, 400)
        self.assertEqual(self.client.get('/api/jobs/invalid/result').status_code, 400)
        self.assertEqual(self.client.get('/.local/status.json').status_code, 404)

    def test_ocr_disabled_without_external_requests(self):
        result = self.client.post('/api/analyze', data={'industry': '제조업'}, files={
            'pdf_t': ('t.pdf', b'%PDF-test'), 'pdf_t_minus_1': ('prior.pdf', b'%PDF-test')})
        self.assertEqual(result.status_code, 503)

if __name__ == '__main__':
    unittest.main()
