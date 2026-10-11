# AssetPro — 자산 관리 대시보드 (+ 한국투자증권 연동)

개인용 자산 관리 대시보드입니다. 프론트엔드(정적 HTML/CSS/JS)와 FastAPI 백엔드로 구성되며,
백엔드가 **한국투자증권 KIS Open API**를 호출해 계좌 잔고·보유종목·평가손익을 보여줍니다.

> 자격증명(`.env`)이 없으면 **예시(mock) 데이터**로 그대로 동작합니다.
> 실제 키를 넣으면 자동으로 **실계좌 조회**로 전환됩니다.

## 구조

```
asset/
├── backend/
│   ├── main.py            # FastAPI 앱 (API + 정적 프론트 서빙)
│   ├── kis_client.py      # KIS 토큰 발급 + 잔고조회 + mock 폴백
│   ├── config.py          # .env 설정 로드
│   ├── requirements.txt
│   ├── .env.example       # → 복사해서 .env 로 사용
│   └── .gitignore
├── index.html             # 프론트엔드
├── app.js
├── styles.css
└── README.md
```

## 실행 방법

Windows PowerShell 기준. **활성화(Activate.ps1) 없이 venv 파이썬을 직접 호출**하는 방식이라
실행정책(ExecutionPolicy) 문제를 피할 수 있습니다.

```powershell
cd D:\workspace3\asset\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

브라우저에서 **http://localhost:8000** 접속. (프론트엔드도 백엔드가 함께 서빙합니다.)
종료는 터미널에서 `Ctrl + C`.

> 참고: `Activate.ps1`로 가상환경을 켜려다 "이 시스템에서 스크립트를 실행할 수 없으므로..."
> 오류가 나면, 위처럼 `.\.venv\Scripts\python.exe -m ...` 로 직접 실행하면 됩니다.
> (또는 한 번만 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 실행)

## 한국투자증권 실계좌 연동하기

### 1. KIS Developers 앱키 발급
1. [KIS Developers 포털](https://apiportal.koreainvestment.com) 접속 후 로그인
2. 계좌가 있어야 하며, HTS/MTS에서 **API 신청(사용 동의)** 을 먼저 완료
3. "KIS Developers → 앱 등록"에서 앱을 만들면 **App Key / App Secret** 발급
4. 모의투자로 먼저 테스트하려면 모의투자용 앱키를 별도로 발급

### 2. 자격증명 설정
`backend/.env.example` 을 복사해 `backend/.env` 로 저장하고 값을 채웁니다.

```env
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_ACCOUNT_NO=12345678-01     # 계좌번호 (앞8자리-뒤2자리)
KIS_ENV=real                   # real=실전, paper=모의투자
```

### 3. 서버 재시작
`uvicorn` 을 다시 실행하면 우측 상단 배지가 **"실계좌 연동"** 으로 바뀌고 실제 잔고가 표시됩니다.

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/health` | 서버 상태 / 현재 모드(mock·live) |
| GET | `/api/account/status` | 계좌 요약 + 보유종목 (KIS 잔고조회) |
| GET | `/api/transactions` | 최근 거래 내역 (현재 mock) |

## 시장 지표 데이터 (하이브리드 실시간)

지표는 **성격에 맞는 최적 소스**로 나눠 받습니다.

| 지표 | 소스 | 방식 |
|------|------|------|
| 美 국채 10Y (`^TNX`) | Yahoo Finance | 스냅샷 폴링(60초) |
| 美 국채 30Y (`^TYX`) | Yahoo Finance | 스냅샷 폴링(60초) |
| WTI | **KIS 실시간** | WebSocket → SSE push |
| 브렌트유 | **KIS 실시간** | WebSocket → SSE push |
| USD/KRW | **KIS 실시간** | WebSocket → SSE push |

- **왜 하이브리드인가:** KIS 실시간(WebSocket)은 주식·해외선물·통화선물 *체결가*는 주지만
  국채 *수익률(%)* 지수는 제공하지 않습니다. 그래서 금리는 Yahoo(수익률), 유가/환율은 KIS 실시간으로 나눴습니다.

### 실시간 파이프라인
```
KIS WebSocket ──▶ backend/kis_ws.py (RealtimeEngine) ──▶ /api/market/stream (SSE) ──▶ 브라우저(EventSource)
                                                                              └▶ 카드 값 + 1일 스파크라인 실시간 갱신
```
- `/api/market` : 스냅샷(금리 + 일봉 창). 프론트가 60초마다 폴링.
- `/api/market/stream` : KIS 유가/환율 tick 을 SSE 로 push. 헤더의 초록 점(●)이 스트림 수신 상태.
- 키가 없으면 엔진이 **시뮬레이터**로 동작해 실시간 화면을 그대로 확인할 수 있습니다.

### 실제 KIS 실시간으로 전환하기
1. KIS 앱키 발급 + **'해외선물옵션' API 사용신청/권한** 획득 (해외선물 계좌 서비스 필요)
2. `backend/.env` 에 아래를 설정:
   ```env
   KIS_APP_KEY=...
   KIS_APP_SECRET=...
   KIS_ENV=real                 # 또는 paper
   KIS_ENABLE_WS=1              # 실시간 WebSocket 켜기
   KIS_WS_WTI_KEY=<WTI 해외선물 종목코드>
   KIS_WS_BRENT_KEY=<브렌트 종목코드>
   KIS_WS_USDKRW_KEY=<원/달러 종목코드>
   # KIS_WS_PRICE_INDEX : 체결 프레임의 '현재가' 필드 위치(문서/수신 로그로 확인)
   ```
3. 서버 재시작 → 헤더 출처가 "KIS 실시간(실전/모의)"으로 바뀝니다.
   연결/구독 실패 시 자동으로 시뮬레이터로 폴백합니다.

> **주의:** 종목코드(tr_key)와 체결 프레임의 현재가 필드 위치(`KIS_WS_PRICE_INDEX`)는
> KIS 해외선물 상품/문서에 따라 다릅니다. 최초 연동 시 수신 원문(raw)을 로그로 찍어
> 필드 위치를 맞춰 주세요. (`kis_ws.py` 의 `_handle_real_msg` 참고)

## 참고 / 주의

- **App Secret은 절대 프론트엔드에 두지 마세요.** 이 앱은 백엔드(`.env`)에만 보관합니다.
- KIS 접근토큰은 발급 빈도 제한이 있어 `backend/.token_cache.json` 에 캐싱해 재사용합니다.
- 사용한 KIS API: `POST /oauth2/tokenP`(토큰), `GET /uapi/domestic-stock/v1/trading/inquire-balance`(주식잔고조회).
- 실전 tr_id `TTTC8434R`, 모의 tr_id `VTTC8434R`.
- 거래내역/입출금(`/api/transactions`)은 현재 mock입니다. KIS `일별주문체결조회` 등으로 확장 가능합니다.
