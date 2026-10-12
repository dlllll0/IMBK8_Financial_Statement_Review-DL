# 기술 스택 변화와 RAG·API 연동 상세

원본 `DL_final` 코드와 현재 실행 경로를 비교합니다. 발표 당시 설계, 원본 구현, 후속 개선의 차이는 아래와 같습니다.

## 1. 무엇을 유지했고 무엇을 바꿨는가?

| 영역 | 원본 구현 | 현재 공개 정리본 | 변경 이유 |
|---|---|---|---|
| 백엔드 | FastAPI, Uvicorn, Jinja2 템플릿·정적 파일 | FastAPI, Uvicorn, HTML/CSS/JavaScript와 JSON API | 기존 Python 분석을 유지하면서 화면 상태와 분석 실행을 분리 |
| 표 추출 SDK | `vertexai.generative_models.GenerativeModel`, `Part` | `google-genai`의 `genai.Client`·`types.Part` | 표 추출과 설명 생성의 SDK를 통합하고 인증 공급자를 설정으로 선택 |
| 설명 SDK | 기존 LLM 모듈에서 이미 `google-genai` 사용 | 동일 SDK를 사용하되 `review/explanation.py`로 설명 경계를 분리 | 기존 SDK를 유지하면서 요청·검증 책임을 분리 |
| 인증 | Vertex AI 프로젝트·리전, 서비스 계정/ADC 기반 | Gemini API 키 경로 추가, Vertex 경로도 보존 | 로컬에서 기존 Cloud 인증 환경 없이 API 키로 시험 가능하도록 개선 |
| 텍스트 추출 | PyMuPDF, Kiwi, 자체 사전·카테고리 | 기존 추출·분석 유지, 화면에서 비중첩 문자열 위치를 별도 계산 | 기존 분석 집계와 실제 하이라이트 위치·횟수를 구분 |
| RAG | Text/Financial RAG item 생성 → 기존 요약 모듈 | 기존 분석 근거 유지 + 선택 항목 중심 `evidence_pack` 구성 | RM이 보고 있는 항목에 관련 문맥과 계정 후보를 연결 |
| 설명 실행 | 기존 앱에서 분석 흐름에 RAG item·요약 생성 단계 연결 | Worker는 추출·규칙 분석, 설명은 별도 요청 API | 설명 실패와 규칙 결과를 분리하고 불필요한 설명 호출 감소 |
| 출력 검증 | 기존 요약 모듈의 프롬프트·응답 처리 | JSON 응답 스키마 + Pydantic + 인용문·출처·숫자 검사 | 출처 불일치·없는 숫자 등을 형식적으로 차단 |
| 금액 설명 | 재무 RAG item과 생성 요약 모듈 | 명시된 전기·당기 값의 코드 계산·문장 생성 우선 | 산술 결과를 생성 모델에 맡기지 않고 재현 가능하게 표시 |
| 실행·관측 | 원본 앱에서 `process_job` 등 분석 함수 연결 | 작업별 Worker 프로세스, 단계 상태·로그·30분 작업 제한 | 오래 걸리는 처리의 단계와 실패 위치를 추적 |
| 저장 | 업로드·분석 산출물 파일, 발표자료에 DB 설계 포함 | `.local` 작업별 JSON·PDF·로그 | 별도 DB 없이 로컬 파일로 검토 상태 저장 |
| 결과 활용 | 기존 viewer 데이터·웹 출력 | 검토 상태·메모, 근거 탐색, 최종 HTML 보고서 | 발견한 표현을 담당자의 확인·기록까지 연결 |

**유지한 핵심은 직접 구축한 사전, 카테고리 규칙, 전기·당기 비교, RAG를 통한 근거 기반 설명입니다.** 개선의 중심은 API 접속 경로, 설명 검증, 작업 격리, 검토 UX입니다. 새로운 딥러닝 모델 학습이나 벡터 검색 도입은 하지 않았습니다.

## 2. 기존 RAG 연결

원본 발표 슬라이드 30과 원본 분석 모듈은 다음 두 경로를 구분합니다.

```text
텍스트 → 사전 키워드 → 위험 카테고리 → 분석방법·기간별 변화
      → Text RAG item → 텍스트 LLM 요약
재무 표 → 계정 구조화 → 전기·당기 비교 → 규칙·카테고리
      → Financial RAG item → 재무 LLM 요약
```

- `text_rag_item_builder.py`: 텍스트 분석 결과를 설명에 사용할 항목으로 구조화합니다.
- `financial_comparator.py`, `financial_rag_builder.py`: 계정 비교와 재무 근거 항목을 구성합니다.
- `text_llm_summary_builder.py`, `llm_summary_builder.py`: 기존 RAG 항목을 입력으로 생성 요약을 구성합니다.
- `viewer_builder.py`: 문서·표·근거를 화면용 데이터로 연결합니다.

이 RAG는 **규칙으로 선별한 분석 근거를 LLM에 공급하는 구조**입니다. 임베딩 유사도 검색이나 벡터 DB를 사용하는 것으로 설명하지 않습니다. 원본 코드가 존재한다는 사실과 그 당시 모든 문서에서 성공적으로 실행됐다는 사실은 구분해야 합니다.

## 3. 현재 RAG 연결: 분석 근거를 선택한 항목에 맞춰 구성

```text
Worker: 기존 추출·사전 분석·변화 탐지·규칙·근거·계정 비교
  → viewer_data.json
  → adapter: 문단·검토 항목·문자열 위치
  → enrichment: 페이지 후보·금액 비교 보충
  → 화면에서 항목 선택
  → connections.resolve: 관련 문맥·계정 후보 검색
  → explanation.evidence_pack: 출처 ID가 있는 근거 묶음
  → Gemini API / Vertex AI
  → 로컬 검증 통과한 설명만 표시·저장
```

현재 Worker는 `run_rag_builder` 등 기존 분석 단계를 실행하지만, 이전 Text/Financial 요약 생성기를 일괄 실행하지 않습니다. 텍스트 웹 항목은 `adapter.py`가 viewer 문단에서 사전 문자열을 찾아 다시 구성합니다. 현재 텍스트 설명은 과거 `text_rag_items.json`을 직접 읽지 않습니다. 기존 재무 하이라이트는 `financial_rag_items`에서 표명·계정명에 맞는 근거를 연결합니다.

### 검색과 근거 묶음

`connections.py`는 선택한 표현·담당자 확인사항에 포함된 사전 용어, 명시적 연관 키워드 묶음, 필요 시 동일 카테고리의 용어를 이용합니다. 관련 문단과 계정명에서 키워드 일치를 찾고 기간·문서 종류·페이지 후보를 함께 반환합니다. 검색 결과는 정교한 의미 검색 순위나 동일 거래의 증명이 아닙니다.

`evidence_pack`에는 다음이 포함됩니다.

| 근거 | 전달 내용 | 목적 |
|---|---|---|
| E1 | 선택한 하이라이트 주변 원문 | 설명 첫 문장의 직접 근거 |
| E2 | 표현·전기 당기 집계·집계 한계 | 변화와 관측 범위를 구분 |
| E3 | 표시 이유·미수행 확인사항 | 검토 질문과 확인 완료를 구분 |
| 추가 문맥 | 같은 표현이 있는 기간별 문단의 일부 | 앞뒤 문맥과 전기·당기 설명 참고 |
| 계산·용어 | 계산값이 있는 경우 비교 정보, 사전에 있는 일반 개념과 출처 | 기업 사실과 일반 용어 정의를 구분 |
| 관련 후보 | 다른 문맥 최대 3개, 관련 계정 최대 3개 | 텍스트와 수치의 연결 후보 제공 |
| 한계 | 키워드 연결의 한계·은행 거래 데이터 미입력 | 없는 근거를 이용한 확장 방지 |

다른 문맥·계정은 모든 보고서를 무제한 전달하는 방식이 아니라 일부 후보를 잘라 제공합니다. 관련 자료가 입력되지 않았거나 검색되지 않으면 미확인으로 남깁니다. 은행데이터가 필요한 확인사항은 `additional_data`로 반환합니다.

## 4. Gemini·Vertex API와 인증

원본 표 추출은 `vertexai.init(project, location)`와 `GenerativeModel`을 사용했습니다. 원본 LLM 요약은 이미 `genai.Client(vertexai=True, ...)`를 사용했으므로, **기존에도 Gemini 모델을 Vertex AI를 통해 호출한 구조**입니다. 별도 Cloud Vision OCR로 전환했다는 의미가 아닙니다.

현재 표 추출과 설명은 `google-genai` SDK를 사용합니다.

| 설정 | Gemini API 경로 | Vertex AI 경로 |
|---|---|---|
| 공급자 | `REVIEW_AI_PROVIDER=gemini` | `REVIEW_AI_PROVIDER=vertex` |
| 클라이언트 | `genai.Client(vertexai=False, api_key=...)` | `genai.Client(vertexai=True, project=..., location=...)` |
| 인증 설정 | `GEMINI_API_KEY` | `VERTEX_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` 등 |
| 표 추출 모델 | `GEMINI_MODEL_NAME` | 동일 환경변수 |
| 설명 모델 | `REVIEW_LLM_MODEL` | 동일 환경변수 |

`start-gemini.ps1`은 입력받은 키를 프로세스 환경변수에 두고 공급자·모델·OCR/LLM 활성화 설정 후 로컬 Uvicorn을 실행합니다. 키를 코드나 공개 문서에 저장하지 않습니다. 모델명은 설정값이며 비용·사용 가능 여부·호출 한도는 서비스 조건에 따라 확인해야 합니다. 무료 사용을 영구 보장하지 않습니다.

### 외부로 전달하는 데이터

- **재무 표 추출:** 대상 PDF 구간과 추출 프롬프트를 Gemini에 전달합니다. 응답을 표·계정 구조화에 사용하므로 추출값 대조가 필요합니다.
- **텍스트 추출:** PyMuPDF 텍스트 추출, Kiwi·사전 매칭 자체는 로컬에서 처리합니다. 모든 이미지형 문서의 텍스트 OCR을 보장하지 않습니다.
- **설명 생성:** 선택한 항목의 근거 묶음을 JSON으로 전달합니다. 이 단계에서도 원문 일부가 외부 API에 전달됩니다.

실데이터를 GitHub에 넣지 않는 것과 외부 API로 문서를 전송할 권한은 별개입니다. 공개·합성 문서 또는 전송 허용 자료로 실행해야 합니다. API 키는 브라우저가 아닌 Python 서버에서 사용합니다.

## 5. 구조화 응답과 검증

`review/explanation.py`는 `system_instruction`과 JSON 근거를 `models.generate_content`에 전달합니다. `response_mime_type='application/json'`, 간소화한 `response_json_schema`, 출력 토큰 제한을 적용하고 자동 함수 호출을 비활성화합니다. 도구를 자율 호출하는 에이전트 구조는 아닙니다.

설명은 **선택 문단 요약 → 관련 문맥·계정 → RM 확인 관점**으로 유도합니다. 모델에는 문단별 `text`와 `quotes(source_id, quote)`를 요청합니다. 중복된 출처 목록을 모델이 따로 만들지 않도록 서버가 인용문에서 `source_ids`를 구성합니다.

검증 단계는 다음과 같습니다.

1. Pydantic으로 필수 필드, 문단·인용 개수, 길이, 허용하지 않은 필드를 검사합니다.
2. 출처 ID가 입력 근거에 실제 존재하고 인용된 출처와 일치하는지 검사합니다.
3. 인용문이 해당 근거의 연속 문자열에 실제 포함되는지 검사합니다.
4. 설명의 아라비아 숫자가 인용 근거의 숫자 집합에 포함되는지 검사합니다.
5. 실패하면 생성 설명을 채택하지 않고 오류와 기존 규칙 근거를 제공합니다.

이 검사는 의미 정확성·인과관계·회계 판단의 보증이 아닙니다. 숫자 집합 검사는 숫자가 올바른 계정에 연결됐는지까지 증명하지 않으므로 원문 검토가 필요합니다. 문서 내부 지시를 따르지 않도록 프롬프트에 명시했으나 모든 프롬프트 공격을 차단했다고 주장하지 않습니다.

## 6. 웹 API·작업 실행·캐시

| API | 역할 |
|---|---|
| `POST /api/analyze` | 두 PDF 업로드·분석 작업 등록, 202 응답 후 상태 조회 |
| `GET /api/config` | OCR/LLM 설정 상태; 실제 연결 성공의 증거와 구분 |
| `GET /api/jobs`, `GET /api/jobs/{job}/status` | 작업 목록·처리 단계·실패 상태 |
| `GET /api/jobs/{job}/result` | 검토용 데이터와 보충 근거 |
| `POST /api/import` | 기존 viewer JSON을 검토 화면으로 변환 |
| `POST /api/jobs/{job}/explanations/{event_id}` | 선택한 항목 설명 생성·캐시 |
| `GET /api/jobs/{job}/checks/{event_id}/{index}` | 확인사항별 관련 문맥·계정 탐색 |
| `GET/PUT .../decisions` 관련 경로 | 담당자 확인 상태·메모 조회와 항목별 저장 |
| `POST /api/jobs/{job}/report` | 서버 기록 기반 최종 HTML 보고서 |

정확한 전체 경로는 `app.py`를 기준으로 합니다. 데모는 별도 `/api/demo` 경로를 사용합니다.

PDF 분석은 작업별 별도 프로세스로 실행하고 단계 상태와 `worker.log`를 남깁니다. 전체 작업 제한은 30분, 표 추출 클라이언트 타임아웃은 120초, 설명은 60초입니다. 이것은 완료 예상 시간이거나 무조건 성공하는 시간 보장이 아닙니다.

설명 생성은 잠금으로 동시 호출을 제한합니다. 기존 설명의 버전과 근거 SHA-256 해시가 현재 근거와 일치하면 재사용하고, 변경됐다면 다시 생성합니다. JSON 저장에는 파일 잠금 상황을 고려한 처리도 포함합니다. 인증 실패·요청 거부·모델 접근·한도·출처 검증 실패를 구분해 안내하되 오류에서 키와 원문을 노출하지 않도록 범위를 제한했습니다.

## 7. 기술적 성과와 아직 없는 기능

현재 확인 가능한 성과는 **추출 → 규칙·비교 → 근거 연결 → 검증된 생성 설명 → 사람의 기록**을 웹 흐름으로 연결한 구현입니다. 테스트 26개는 합성·단위·API 동작 범위를 검증하며 전체 실문서 정확도나 업무 절감률을 측정한 것은 아닙니다.

벡터 DB·임베딩 검색·재순위 모델, 자체 모델 학습·파인튜닝, 운영용 DB·인증·권한관리·분산 작업 큐·상용 배포는 미구현 상태입니다. 현재 앱은 로컬 검토용이며 그대로 인터넷에 노출하기 전에 별도 운영 설계가 필요합니다.

## 코드 근거

| 구분 | 확인 파일 |
|---|---|
| 원본 SDK·인증 | 원본 `DL_final/src/financial_statement_ocr.py`, `src/llm_summary_builder.py` |
| 원본 웹 연결 | 원본 `DL_final/app.py` |
| 기존 근거 구조 | 공개본 `legacy/src/text_rag_item_builder.py`, `financial_rag_builder.py`, `viewer_builder.py` |
| 현재 OCR 공급자 | 공개본 `legacy/src/financial_statement_ocr.py` |
| 현재 작업 실행 | `scripts/worker.py`, `app.py` |
| 현재 항목·연결 | `review/adapter.py`, `review/connections.py`, `review/enrichment.py` |
| 현재 설명 API·검증 | `review/explanation.py`, `app.py` |
| 현재 금액 요약 | `review/financial_guide.py` |
| 실행·진단 | `start-gemini.ps1`, `check-gemini.ps1`, `scripts/check_gemini.py` |

원본 파일은 수정하지 않았습니다. 공개본의 `legacy`는 호환을 위해 일부 경로와 API 호출을 정리한 복사본이므로 원본과 완전히 동일한 스냅샷으로 간주하지 않습니다.
