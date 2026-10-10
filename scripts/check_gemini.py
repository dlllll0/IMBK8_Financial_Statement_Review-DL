"""No documents or credentials are printed or saved by this diagnostic."""
import os
import sys
import re
from pathlib import Path
from google import genai
from google.genai import types

key = os.getenv('GEMINI_API_KEY', '').strip()
model = os.getenv('GEMINI_MODEL_NAME', 'gemini-3.5-flash-lite')
print('Key present:', bool(key), '| Character count:', len(key))
print('Model:', model)
if not key:
    raise SystemExit('FAIL: missing key')
stage = 'API request / SDK processing'
try:
    if '--explanation' in sys.argv:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from review.adapter import demo
        from review.explanation import evidence_pack, call_vertex, validate_response
        os.environ.update(REVIEW_ENABLE_LLM='1',REVIEW_AI_PROVIDER='gemini',REVIEW_LLM_MODEL=model)
        payload=demo()
        event=next(e for e in payload['events'] if e['word']=='자산손상')
        sources=evidence_pack(payload,event)
        print('STEP 1: sending synthetic structured explanation request')
        raw=call_vertex(sources)
        print('STEP 2: response received and JSON decoded; authentication succeeded')
        stage='local grounding validation (response already received)'
        result=validate_response(raw,sources)
        print('PASS: synthetic explanation generated and grounding validation passed.')
        print('Paragraph count:',len(result['paragraphs']))
        raise SystemExit(0)
    with genai.Client(vertexai=False, api_key=key,
                      http_options=types.HttpOptions(timeout=20000)) as client:
        response = client.models.generate_content(
            model=model, contents='Reply with OK only.',
            config=types.GenerateContentConfig(max_output_tokens=64))
    print('PASS: Gemini returned a response.' if response.text else 'FAIL: empty response')
except Exception as error:
    code = getattr(error, 'code', None)
    print('FAIL:', type(error).__name__, '| HTTP code:', code)
    print('Failed stage:',stage)
    if '--explanation' in sys.argv:
        # Only synthetic sources are used in this mode. Strip credentials and URLs.
        detail=str(getattr(error,'message','') or str(error) or type(error).__name__)
        detail=detail.replace(key,'[REDACTED]')
        detail=re.sub(r'AIza[\w-]+|https?://\S+','[REDACTED]',detail)
        print('Synthetic diagnostic detail:',detail[:1500])
    if isinstance(error,ValueError) and code is None:
        print('Local SDK/request/validation error; this is not evidence of a network failure or invalid key.')
    elif code == 400:
        print('Check API key validity and request configuration.')
    elif code in (401, 403):
        print('Authentication/access rejected. Check key restrictions and project access.')
    elif code == 404:
        print('Model not found or unavailable to this project.')
    elif code == 429:
        print('Quota/rate limit reached. Check AI Studio limits.')
    else:
        print('Network/SDK failure. Check proxy, TLS and security software; key validity is unconfirmed.')
    # Do not print raw exceptions: they may contain request URLs/credentials.
    raise SystemExit(1)
