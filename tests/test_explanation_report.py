import json
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_review import fixture
from app import app
from review.adapter import demo
from review.explanation import validate_response
from review.report import build_report

class ExplanationReportTests(unittest.TestCase):
    def test_gemini_key_configuration_without_vertex(self):
        from review.explanation import configuration
        with patch.dict(os.environ,{'REVIEW_AI_PROVIDER':'gemini','REVIEW_ENABLE_LLM':'1','REVIEW_LLM_MODEL':'gemini-3.5-flash-lite','GEMINI_API_KEY':'synthetic-not-a-real-key','VERTEX_PROJECT_ID':''}):
            self.assertTrue(configuration()['enabled'])
            self.assertEqual(configuration()['provider'],'gemini')
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post('/api/import',files={'file':('viewer.json',json.dumps(fixture()))})
        self.job=response.json()['job_id']
        self.payload=self.client.get(f'/api/jobs/{self.job}/result').json()
        self.event=self.payload['events'][0]['id']

    def test_all_four_demo_tabs(self):
        data=demo()
        self.assertEqual({e['kind'] for e in data['events']},{'financial','notes','audit','correction'})
        self.assertTrue(data['tables']['t'])

    def test_grounding_rejects_unknown_quote_or_number(self):
        sources=[{'id':'E1','text':'전기 추출값 100, 당기 추출값 120. 단위 확인 필요.'}]
        raw={'paragraphs':[{'text':'당기 추출값은 120입니다.','source_ids':['E1'],'quotes':[{'source_id':'E1','quote':'당기 추출값 120'}]}]}
        self.assertEqual(len(validate_response(raw,sources)['paragraphs']),1)
        for text,quote,source in [('당기 추출값은 999입니다.','당기 추출값 120','E1'),('근거 확인','없는 인용 문장','E1'),('근거 확인','당기 추출값 120','E9')]:
            with self.subTest(text=text,quote=quote,source=source):
                bad={'paragraphs':[{'text':text,'source_ids':[source],'quotes':[{'source_id':source,'quote':quote}]}]}
                with self.assertRaises(ValueError):validate_response(bad,sources)

    def test_llm_unconfigured_is_explicit(self):
        with patch.dict(os.environ,{'REVIEW_ENABLE_LLM':'0'}):
            r=self.client.post(f'/api/jobs/{self.job}/explanations/{self.event}')
        self.assertEqual(r.status_code,503)
        self.assertEqual(self.client.get(f'/api/jobs/{self.job}/explanations').json(),{})

    def test_generated_explanation_cached_after_validation(self):
        def provider(sources):
            quote=sources[0]['text']
            return {'paragraphs':[{'text':'추출 문단에 검토할 표현이 포함되어 있습니다.','source_ids':['E1'],'quotes':[{'source_id':'E1','quote':quote}]}]}
        with patch.dict(os.environ,{'REVIEW_ENABLE_LLM':'1','VERTEX_PROJECT_ID':'synthetic-project','REVIEW_LLM_MODEL':'mock-model'}),patch('review.explanation.call_vertex',side_effect=provider) as call:
            endpoint=f'/api/jobs/{self.job}/explanations/{self.event}'
            first=self.client.post(endpoint);second=self.client.post(endpoint)
            self.assertEqual(first.status_code,200)
            self.assertEqual(first.json(),second.json())
            self.assertEqual(call.call_count,1)
        self.assertIn(self.event,self.client.get(f'/api/jobs/{self.job}/explanations').json())

    def test_invalid_llm_never_persisted(self):
        with patch.dict(os.environ,{'REVIEW_ENABLE_LLM':'1','VERTEX_PROJECT_ID':'synthetic','REVIEW_LLM_MODEL':'mock-model'}),patch('review.explanation.call_vertex',return_value={'paragraphs':[]}):
            r=self.client.post(f'/api/jobs/{self.job}/explanations/{self.event}')
        self.assertEqual(r.status_code,422)
        self.assertEqual(self.client.get(f'/api/jobs/{self.job}/explanations').json(),{})

    def test_report_escapes_text_and_retains_pending(self):
        html=build_report(demo(),{}, {},'<script>alert(1)</script>','후속 확인')
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('합성 예시',html)
        self.assertIn('검토 미완료',html)
        for title in ['재무제표','재무제표 주석','감사보고서','정정신고']:self.assertIn(title,html)

    def test_report_uses_saved_server_records(self):
        endpoint=f'/api/jobs/{self.job}/decisions/{self.event}'
        self.client.put(endpoint,json={'state':'followup','note':'기간 대조 필요'})
        response=self.client.post(f'/api/jobs/{self.job}/report',json={'decisions':{self.event:{'state':'reviewed','note':'FORGED'}}})
        self.assertEqual(response.status_code,200)
        self.assertIn('기간 대조 필요',response.text)
        self.assertNotIn('FORGED',response.text)
        self.assertIn('추가 확인 필요',response.text)

    def test_upload_queue_and_invalid_pdf(self):
        with patch.dict(os.environ,{'REVIEW_ENABLE_OCR':'1'}),patch('app.POOL.submit') as submit:
            response=self.client.post('/api/analyze',data={'industry':'제조업'},files={'pdf_t':('t.pdf',b'%PDF-synthetic'),'pdf_t_minus_1':('prior.pdf',b'%PDF-synthetic')})
            self.assertEqual(response.status_code,202)
            self.assertEqual(submit.call_count,1)
            job=response.json()['job_id']
            self.assertEqual(self.client.get(f'/api/jobs/{job}/status').json()['state'],'queued')
            bad=self.client.post('/api/analyze',data={'industry':'제조업'},files={'pdf_t':('fake.pdf',b'not a pdf'),'pdf_t_minus_1':('prior.pdf',b'%PDF-synthetic')})
            self.assertEqual(bad.status_code,415)

if __name__=='__main__':unittest.main()
