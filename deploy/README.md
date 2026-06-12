# ASLAN API — `chat-api.irisid.com` 배포 (InMotion VPS)

irisid.com WordPress와 **같은 VPS** (`173.231.221.180`, InMotion)에 FastAPI를 띄우고, 서브도메인으로 HTTPS 프록시합니다.

## 현재 상태 (확인됨)

| 항목 | 상태 |
|------|------|
| DNS `chat-api.irisid.com` → `173.231.221.180` | OK |
| SSL 인증서 | **불일치** — `vps84184.inmotionhosting.com`용 (서브도메인 미포함) |
| ASLAN API 프로세스 | **미배포** |

브라우저 `NET::ERR_CERT_COMMON_NAME_INVALID` = DNS는 맞지만 **서브도메인 SSL + API 서버**가 아직 없음.

---

## 전체 순서 (요약)

1. cPanel에서 `chat-api.irisid.com` 서브도메인 + **AutoSSL**
2. SSH로 VPS 접속 → ASLAN 코드 + Python venv + `.env`
3. Qdrant (Docker 로컬 **또는** Qdrant Cloud)
4. `systemd`로 uvicorn 상시 실행 (`127.0.0.1:8010`)
5. Apache가 `chat-api.irisid.com` → `8010`으로 **리버스 프록시**
6. `https://chat-api.irisid.com/health` 확인 → WP 설정 유지

---

## 1. cPanel — 서브도메인 + SSL

1. **cPanel** 로그인 (InMotion AMP → cPanel)
2. **Domains → Create A New Domain** (또는 Subdomains)
   - Domain: `chat-api.irisid.com`
   - Document root: 예) `/home/USER/chat-api` (WP `public_html`과 분리 권장)
   - “Share document root” **끄기**
3. **SSL/TLS Status** (또는 **SSL/TLS → Manage AutoSSL**)
   - `chat-api.irisid.com` 선택 → **Run AutoSSL** / **Issue**
   - 몇 분 후 인증서가 `chat-api.irisid.com` 이름으로 발급돼야 함
4. 브라우저에서 다시 열기 (아직 API 없으면 502/404 가능 — SSL 경고만 사라지면 OK)

DNS는 이미 `173.231.221.180`이면 **추가 DNS 작업 불필요**.

---

## 2. SSH — 코드 배포

```bash
# VPS SSH (InMotion: AMP → cPanel → SSH Access)
ssh USER@173.231.221.180

# 앱 전용 디렉터리 (root 권한 불필요)
mkdir -p ~/apps && cd ~/apps

# GitHub (deploy key 또는 HTTPS + token)
git clone git@github-irisid:hoyong-irisid/aslan.git
cd aslan

# Python 3.11+ (cPanel: Software → Setup Python App 에서 버전 확인)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 프로덕션 env (로컬 .env 내용을 복사하되 URL/키만 조정)
cp deploy/env.production.example .env
nano .env   # GOOGLE_API_KEY, QDRANT_*, RESEND_* 등
```

**`.env` 필수 항목 (프로덕션):**

- `GOOGLE_API_KEY` (또는 OpenAI)
- `QDRANT_URL` — 로컬 Docker `http://127.0.0.1:6333` 또는 Qdrant Cloud HTTPS URL
- `QDRANT_API_KEY` — Cloud 사용 시
- `PARTNER_CODES` — 파트너 코드
- `RESEND_API_KEY` + `RESEND_FROM` — 종료 시 transcript 메일 (선택)

로컬에서 이미 Qdrant Cloud를 쓰면 **같은 `QDRANT_URL` / API key**를 서버 `.env`에 넣으면 ingest를 다시 안 해도 됩니다.

---

## 3. Qdrant

### A) VPS에서 Docker (같은 서버)

```bash
cd ~/apps/aslan
docker compose up -d
curl -s http://127.0.0.1:6333/ | head
```

`.env`: `QDRANT_URL=http://127.0.0.1:6333`

처음이면 서버에서 ingest (corpus 경로는 서버에 업로드):

```bash
source .venv/bin/activate
python -m rag.ingest /path/to/corpus --prefix manuals --product iA1000 --language en
python -m rag.ingest --partner --product iA1000 --language en
```

### B) Qdrant Cloud (권장 — WP와 리소스 분리)

1. [cloud.qdrant.io](https://cloud.qdrant.io) 클러스터 URL을 `.env`의 `QDRANT_URL`에 설정
2. `QDRANT_API_KEY` 설정
3. 로컬에서 이미 ingest 했다면 **추가 ingest 불필요**

---

## 4. systemd — API 상시 실행

```bash
# 경로 수정: USER → 실제 cPanel 사용자명
sudo cp deploy/aslan-api.service /etc/systemd/system/aslan-api.service
sudo nano /etc/systemd/system/aslan-api.service
#   User=USER
#   WorkingDirectory=/home/USER/apps/aslan

sudo systemctl daemon-reload
sudo systemctl enable aslan-api
sudo systemctl start aslan-api
sudo systemctl status aslan-api

# 로컬에서 health 확인 (VPS 안에서)
curl -s http://127.0.0.1:8010/health
# → {"status":"ok"}
```

---

## 5. Apache — HTTPS → uvicorn 프록시

cPanel VPS는 Apache가 443을 받습니다. `chat-api.irisid.com` vhost에 프록시를 넣습니다.

### 방법 A — cPanel “Include” (권장)

1. cPanel → **Domains** → `chat-api.irisid.com` → **Manage** (또는 **Apache Configuration**)
2. **Include** / **Predefined Apache Configuration** 에 아래 추가  
   (파일: `deploy/apache-chat-api.conf.example` 내용 복사)
3. **mod_proxy** 활성화 필요 시 InMotion 티켓 또는 WHM에서 `proxy`, `proxy_http` 모듈 확인
4. Apache 재시작: `sudo systemctl restart httpd` (또는 cPanel UI)

### 방법 B — 수동 vhost (root/WHM 접근 시)

`/etc/apache2/conf.d/chat-api.conf` 등에 `apache-chat-api.conf.example` 내용 배치.

### 확인

```bash
curl -s https://chat-api.irisid.com/health
curl -s https://chat-api.irisid.com/health/config
```

브라우저: SSL 자물쇠 정상 + JSON 응답.

---

## 6. WordPress

**Settings → ASLAN Chat**

- API base URL: `https://chat-api.irisid.com` (끝에 `/` 없음)
- Page slugs: `aslan`

`irisid.com/aslan`에서 메시지 전송 테스트.

---

## 트러블슈팅

| 증상 | 원인 / 조치 |
|------|-------------|
| SSL `COMMON_NAME_INVALID` | AutoSSL을 `chat-api.irisid.com`에 재발급 |
| 502 Bad Gateway | `systemctl status aslan-api` — uvicorn 다운 또는 포트 불일치 |
| 404 on `/health` | Apache 프록시 미적용 — Include 확인 |
| Chat “Internal error” / Qdrant | `.env`의 `QDRANT_URL`, 방화벽, collection ingest |
| `GOOGLE_API_KEY is not set` | 서버 `.env` 경로 = `~/apps/aslan/.env`, restart |
| Partner admin / signup shows old UI after deploy | Apache/LiteSpeed cached GET HTML. App auto-redirects to `?v=<partner_ui_version>`. Always open pages with `?v=` in the URL; hard refresh alone may not bypass cache. |

---

## 빠른 로컬 연동 테스트 (SSL 전)

WP는 HTTPS라서 `http://127.0.0.1:8010` 직접 호출 불가. 임시로:

```bash
ngrok http 8010
```

WP API URL에 `https://xxxx.ngrok-free.app` 입력 → SSL/API 배포 전 UI 테스트용.

---

## 파일

| 파일 | 용도 |
|------|------|
| `aslan-api.service` | systemd 유닛 |
| `apache-chat-api.conf.example` | Apache 리버스 프록시 |
| `env.production.example` | 프로덕션 `.env` 템플릿 |
