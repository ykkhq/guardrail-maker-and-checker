# Guardrail Studio

## 목적

프롬프트 가드레일 흐름을 그래프로 설계하고 Kong에서 실행합니다.

배포 대상:

- **Konnect AI Gateway** (기본) — 로컬 `kong/kong-ai-gateway` 데이터 플레인이 Konnect AI Gateway에 연결됩니다.
- **Kong Gateway Enterprise** — Service / Route / `ai-proxy-advanced`와 DataKit 등 플러그인을 자체 운영 하이브리드 Gateway Admin API에 배포합니다.

## 적용 방식

### Konnect AI Gateway

```bash
cd studio-api
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.deploy ../samples/jp-support.json --create --region us
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.bootstrap
cd .. && scripts/up.sh
```

파이프라인 경로: `/pipelines/{slug}/chat/completions`, body의 `"model": "gs-{slug}"`.

항상 `scripts/up.sh`로 기동합니다. 일반 `docker compose up`은 시크릿이 비어 있습니다.

### Kong Gateway Enterprise + LM Studio

Enterprise 하이브리드(Admin `8001`, Proxy `8000`)와 호스트 LM Studio(`1234`, 채팅 모델 + `llama-guard-3-8b-imat`)가 필요합니다.

```bash
GUARDRAIL_DEPLOY_TARGET=gateway scripts/up.sh
```

UI에서 `lm-studio-support`(영문) 또는 `lm-studio-support-kr`(한국어)를 열어 Deploy합니다.

기본 Kong workspace는 `guardrail`입니다 (`KONG_WORKSPACE` 또는 UI 상태 표시의 `ws:…` 클릭으로 변경). Delete 시 해당 workspace의 Service/Route/플러그인도 제거합니다.

| 플러그인 | 역할 |
|----------|------|
| `datakit` | 커스텀 노드(PII, Llama Guard, 감성, 조건)를 `guardrail-engine`으로 실행 |
| `ai-prompt-guard` | deny 패턴 |
| `ai-proxy-advanced` | LM Studio로 chat completions (`provider: openai` + `upstream_url`) |

DataKit은 `http://host.docker.internal:18080`로 엔진에 연결합니다. Llama Guard는 LM Studio `POST /v1/completions`를 사용합니다.

## 출력 예시

| 서비스 | URL |
|--------|-----|
| Web UI | http://localhost:13000 |
| studio-api | http://localhost:18200 |
| guardrail-engine | http://localhost:18080 |
| Kong Proxy (Enterprise) | http://localhost:8000 |
| Kong Proxy (Konnect DP) | http://localhost:18000 |

```bash
curl -s localhost:8000/pipelines/lm-studio-support-kr/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"gs-lm-studio-support-kr","messages":[{"role":"user","content":"제 이메일은 user@example.com 입니다"}]}'
```

## 옵션 설명

| 이름 | 의미 |
|------|------|
| `GUARDRAIL_DEPLOY_TARGET` | `konnect`(기본) 또는 `gateway` |
| `KONG_ADMIN_URL` / `KONG_ADMIN_TOKEN` | Gateway Admin API |
| `KONG_PROXY_URL` / `KONG_PUBLIC_URL` | Playground / UI용 프록시 URL |
| `GUARDRAIL_ENGINE_URL` | DataKit에 기록되는 엔진 URL (Kong DP에서 도달 가능해야 함) |
| `LLAMAGUARD_BACKEND` | `ollama` 또는 `openai_completions` |
| `LLAMAGUARD_BASE_URL` / `LLAMAGUARD_MODEL` | LM Studio base URL과 모델 ID |

샘플: `jp-support.json`(Konnect/OpenAI), `lm-studio-support.json`(영문), `lm-studio-support-kr.json`(한국어).
