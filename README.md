# 🚄 KTX 빈자리 예약 도우미 (개발 초안)

매진된 KTX 열차를 일정 주기로 다시 조회하다가 **빈자리가 생기면 바로 예약(좌석 홀드)** 하고 알려주는 개인용 웹앱입니다.
결제는 자동으로 하지 않습니다. 예약 후 **결제 기한(보통 10~20분) 안에 코레일톡/홈페이지에서 직접 결제**해야 확정됩니다.

![screenshot](docs/screenshot.png)

## 빠른 시작

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 기본값 PROVIDER=mock (가짜 데이터)
uvicorn app.main:app --reload
# → http://localhost:8000
pytest -q                     # 단위 테스트
```

## 동작 흐름

```
[웹 UI] ──조회──▶ POST /api/search ──▶ Provider.search()
   │  열차 체크(우선순위) + 좌석 옵션
   └──감시 시작──▶ POST /api/watches ──▶ WatchManager가 asyncio Task 생성
                                            │
              ┌─────────────────────────────┘
              ▼  (20초 ± 20% 지터, 오류 시 지수 백오프)
        Provider.search() → 지정 열차 중 조건 맞는 자리 있음?
              │ 아니오 → 대기 후 반복
              ▼ 예 (우선순위 순)
        Provider.reserve() ── 매진(SoldOut)이면 다음 후보 열차 시도
              ▼ 성공
        status=reserved → 카카오톡/Gmail/텔레그램 + 브라우저 알림(UI 3초 폴링)
```

## 구조

```
app/
  main.py            FastAPI 라우트 (/api/*) + 정적 파일 서빙
  config.py          환경변수 설정 (.env)
  models.py          Pydantic 모델 (TrainInfo, Watch, ReservationInfo ...)
  watcher.py         감시/자동예약 루프 (핵심 로직)
  notifier.py        알림 채널 (카카오톡 / Gmail / 텔레그램)
  providers/
    base.py          TrainProvider 인터페이스 + 예외
    mock.py          개발용 가짜 데이터 (자리 날 확률 15%, 예약 직전 매진 20%)
    korail.py        실제 코레일 연동 (korail2 래핑)
static/              바닐라 JS 프론트엔드 (빌드 불필요)
tests/               WatchManager 단위 테스트
```

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/health` | 상태 / provider 종류 |
| GET | `/api/stations` | 역 목록 |
| POST | `/api/search?adults=1` | 열차 조회 `{dep, arr, date:"YYYYMMDD", time:"HHMMSS"}` |
| GET | `/api/watches` | 감시 목록 |
| POST | `/api/watches` | 감시 시작 `{dep, arr, date, train_nos[], search_time, seat_option, passengers}` |
| DELETE | `/api/watches/{id}` | 감시 중지 |
| GET | `/api/notify/status` | 알림 채널별 활성 상태 |
| POST | `/api/notify/test` | 모든 활성 채널로 테스트 알림 |
| GET | `/api/kakao/login` | 카카오 로그인 시작 (브라우저로 이동) |
| DELETE | `/api/kakao` | 카카오 연결 해제 |

`APP_TOKEN`을 설정하면 모든 API에 `X-App-Token` 헤더가 필요합니다. 내 코레일 계정으로 예약이 걸리는 앱이므로 외부에 띄울 땐 꼭 설정하세요.

## 알림 설정

예약이 되면 설정한 채널 **모두**로 알림이 갑니다. 쓰고 싶은 것만 `.env`에 채우세요.
설정 후 화면의 **[알림 설정] → 테스트 알림 보내기**로 확인할 수 있습니다.

### 카카오톡 '나에게 보내기' (추천)

내 카카오톡의 '나와의 채팅'으로 메시지가 옵니다. 무료이고 사업자 등록이 필요 없습니다.

1. https://developers.kakao.com 에 카카오 계정으로 로그인 → **내 애플리케이션 → 애플리케이션 추가** (앱 이름 예: `KTX알림`)
2. **앱 키 → REST API 키**에서 키를 복사해 `.env`의 `KAKAO_REST_API_KEY`에 붙여넣기
3. 같은 REST API 키 설정 화면에서 **카카오 로그인 리다이렉트 URI**에 `http://localhost:8000/api/kakao/callback` 등록
   - 2025년 콘솔 개편으로 이 항목이 '카카오 로그인' 메뉴에서 '앱 키' 쪽으로 옮겨졌습니다. 클라이언트 시크릿을 켰다면 그 값도 `KAKAO_CLIENT_SECRET`에 넣으세요.
4. **카카오 로그인 → 사용 설정 ON**
5. **카카오 로그인 → 동의항목 → '카카오톡 메시지 전송(talk_message)'** 을 '선택 동의'로 설정
6. **플랫폼 → Web 사이트 도메인**에 `http://localhost:8000` 등록 (메시지의 '앱 열기' 버튼 링크용)
7. 앱을 재시작하고 화면의 **카카오톡 연결** 버튼 → 카카오 로그인 → 메시지 전송 동의

- 토큰은 `data/kakao_token.json`에 저장되며 자동 갱신됩니다. 약 2개월 뒤 갱신 토큰이 만료되면 **카카오톡 연결**을 다시 눌러야 합니다.
- 서버 주소가 localhost가 아니면 3·6번과 `.env`의 `KAKAO_REDIRECT_URI`, `APP_URL`을 그 주소로 바꾸세요.
- `KOE006` 오류가 나면 리다이렉트 URI가 정확히 일치하지 않는 것입니다.

### Gmail

휴대폰 Gmail 앱 푸시로 받습니다. 받는 주소(`NOTIFY_EMAIL_TO`)를 비우면 나에게 보냅니다.

1. 구글 계정 → 보안 → **2단계 인증** 켜기
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 생성 (16자리)
3. `.env`에 `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` 입력

## 실제 코레일 연동 (PROVIDER=korail)

```bash
pip install korail2 pycryptodome
# .env: PROVIDER=korail, KORAIL_ID, KORAIL_PW
```

- 코레일은 공식 공개 API가 없어 **모바일 앱의 비공식 API**를 사용합니다. 코레일 쪽 변경으로 언제든 깨질 수 있습니다.
- PyPI `korail2` 0.4.0은 오래되어 로그인이 안 될 수 있습니다. 그럴 땐 최근 유지보수되는 구현(예: SRTgo의 ktx 모듈)을 참고해 `app/providers/korail.py`만 교체하면 됩니다. 나머지 코드는 `TrainProvider` 인터페이스에만 의존합니다.
- **실계정으로 쓰기 전에 반드시 직접 검증하세요.** 이 초안은 mock으로만 테스트했습니다.

## 책임 있는 사용 (설계에 반영된 제한)

- 조회 주기 **최소 10초** 강제, 기본 20초 + 지터. 동시 감시 **최대 3개**. 코레일 서버 부하를 줄이고 계정 차단을 피하기 위함입니다.
- 코레일 이용약관은 매크로/자동화 프로그램 사용을 제한하며, 위반 시 계정 이용이 제한될 수 있습니다. 승차권 **부정 판매(되팔이) 목적 사용은 법적 제재 대상**입니다. 본인 이동 목적의 개인 용도로만 사용하세요.
- 결제 자동화는 일부러 넣지 않았습니다 (카드정보 저장 위험 + 사용자 확인 단계 유지).

## 다음 단계 (TODO)

- [ ] **실제 연동 검증**: korail provider 로그인/조회/예약 실계정 테스트, 최신 API로 교체
- [ ] **영속화**: 감시 목록을 SQLite에 저장 → 서버 재시작 시 복구
- [ ] **만료 처리 정교화**: 감시 대상 열차가 모두 출발하면 즉시 종료 (지금은 날짜 기준)
- [ ] **웹 푸시**: Service Worker 로 브라우저를 닫아도 알림
- [ ] **예약 내역 화면**: 코레일 계정의 기존 예약 조회 및 취소 (`korail.reservations()`, `cancel()`)
- [ ] **실시간 갱신**: 폴링 → SSE/WebSocket
- [ ] **배포**: Dockerfile, 항상 켜진 서버(예: 소형 VPS)에서 실행 — PC가 꺼지면 감시도 멈춤
- [ ] SRT 지원 (provider 하나 추가로 확장 가능)
