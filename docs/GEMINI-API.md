# 실제 PDF·LLM 실행

README가 있는 폴더를 VS Code에서 열고 새 터미널에서 실행합니다. 개인 키와 실제 문서는 저장소에 넣지 않습니다. 공개·합성 문서로 먼저 검증하세요.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

Google AI Studio의 [API 키 화면](https://aistudio.google.com/apikey)에서 본인 프로젝트의 키를 발급합니다. 키는 채팅·GitHub·소스에 붙여넣지 않습니다. 사용 모델과 요금·한도는 계정의 현재 설정을 확인하세요. 모델을 자동으로 유료 대안으로 바꾸지 않습니다.

아래 명령을 터미널에 먼저 붙여넣고, **Enter를 누르기 전에 AI Studio에서 키 전체를 복사**한 다음 터미널로 돌아와 Enter만 누릅니다. 명령을 나중에 복사하면 클립보드의 키가 덮어써집니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-gemini.ps1 -UseClipboard -Python .\.venv\Scripts\python.exe
```

클립보드 키는 서버 프로세스의 환경변수로 전달하며 파일에 기록하지 않습니다. 키가 짧거나 공백이 있으면 호출 전 차단합니다. 이 PowerShell 프로세스에만 실행 정책 옵션을 적용하며 시스템 실행 정책을 변경하지 않습니다. 서버는 `127.0.0.1:8765`에서 실행합니다. 코드 변경 후에는 Ctrl+C로 서버를 종료하고 재실행해야 합니다.

기본 모델은 `gemini-3.5-flash-lite`입니다. 모델 사용 가능 여부는 계정에 따라 확인해야 하며 `-Model`로 지정할 수 있습니다. 본 프로젝트의 사용자 환경에서 해당 모델의 짧은 요청·합성 설명 생성과 로컬 검증 성공을 확인했습니다. 동일 결과를 모든 계정에서 보장하지 않습니다.

## 합성 설명 진단

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\check-gemini.ps1 -UseClipboard -Explanation -Python .\.venv\Scripts\python.exe
```

이 명령도 준비한 뒤 키를 복사하고 Enter를 누릅니다. `STEP 2`는 응답·JSON 수신, 마지막 `PASS`는 인용·출처·숫자 검증까지 성공했다는 뜻입니다. 이 진단은 합성 문장만 사용하고 키 값은 출력하지 않습니다.

## 데이터와 호출 범위

- PDF 분석에서는 추출할 재무 표 구간을 외부 Gemini API에 전송합니다.
- 텍스트 설명에서는 선택 문맥과 연결된 근거 후보를 전송합니다.
- 금액 비교 요약은 코드 계산이며 해당 요약에 LLM 호출이 필요하지 않습니다.
- `.local/`에는 입력 PDF와 분석 결과·담당자 메모가 생깁니다. 공개 저장소에 포함하지 않습니다.
- 서비스 계정 JSON은 Gemini API 키 방식의 필수 파일이 아닙니다.
- 현재 서버는 사용자 인증이 없는 로컬 도구입니다. 그대로 인터넷에 공개 배포하지 않습니다.

요금과 데이터 이용 조건은 변경될 수 있으므로 실행 전에 [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing), [API 키 안내](https://ai.google.dev/gemini-api/docs/api-key), [이용약관](https://ai.google.dev/gemini-api/terms)을 확인하세요. 무료 사용을 무조건 보장하지 않습니다.
