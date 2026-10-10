import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from review.explanation import call_vertex, WIRE_SCHEMA, error_hint, validate_response

class RequestTests(unittest.TestCase):
    def test_citations_derived_only_from_verified_quotes(self):
        sources=[{'id':'E1','text':'합성 문장입니다.'}]
        raw={'paragraphs':[{'text':'합성 문장입니다.',
            'quotes':[{'source_id':'E1','quote':'합성 문장입니다.'}]}]}
        result=validate_response(raw,sources)
        self.assertEqual(result['paragraphs'][0]['source_ids'],['E1'])
        raw['paragraphs'][0]['quotes'][0]['source_id']='E999'
        with self.assertRaises(ValueError):validate_response(raw,sources)
        raw['paragraphs'][0]['quotes'][0]={'source_id':'E1','quote':'원문에 없는 문장입니다.'}
        with self.assertRaises(ValueError):validate_response(raw,sources)

    def test_portable_schema_and_no_temperature_override(self):
        client=MagicMock()
        client.__enter__.return_value=client
        client.models.generate_content.return_value=SimpleNamespace(parsed={'paragraphs':[]})
        with patch('review.explanation.configuration',return_value={'model':'synthetic-model'}), \
             patch.dict('os.environ',{'REVIEW_AI_PROVIDER':'gemini','GEMINI_API_KEY':'synthetic-key'}), \
             patch('google.genai.Client',return_value=client):
            call_vertex([{'id':'E1','text':'합성 자료'}])
        config=client.models.generate_content.call_args.kwargs['config']
        self.assertEqual(config.response_json_schema,WIRE_SCHEMA)
        self.assertIsNone(config.temperature)
        self.assertIsNone(config.response_schema)
        with self.assertRaises(ValueError):
            validate_response({'paragraphs':[]},[{'id':'E1','text':'합성 자료'}])

    def test_error_hint_does_not_echo_secrets(self):
        error=SimpleNamespace(message='Invalid response schema secret-value')
        hint=error_hint(error)
        self.assertIn('스키마',hint)
        self.assertNotIn('secret-value',hint)
