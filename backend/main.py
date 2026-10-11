"""자산 관리 앱 백엔드 (FastAPI).

- /api/account/status : KIS 계좌 요약 + 보유종목 (mock 또는 실계좌)
- /api/transactions   : 최근 거래 내역 (현재는 mock)
- /api/health         : 상태 확인
- 그 외 경로          : 프론트 정적 파일 서빙 (../index.html 등)

실행:
    cd backend
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
브라우저에서 http://localhost:8000 접속.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import kis_client
import market
from config import settings
from kis_ws import engine

FRONTEND_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()      # KIS 실시간(또는 시뮬레이터) 엔진 기동
    yield
    await engine.stop()


app = FastAPI(title="Asset Manager API", version="1.0.0", lifespan=lifespan)

# 개인용 로컬이지만, 프론트를 다른 포트에서 띄우는 경우를 대비해 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache(request, call_next):
    """로컬 개발용: 브라우저가 옛 프론트(app.js/styles.css)를 캐시하지 않도록."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": "mock" if settings.use_mock else "live",
        "env": settings.env,
    }


@app.get("/api/account/status")
async def account_status() -> JSONResponse:
    """계좌 요약 + 보유종목. KIS 호출 실패 시 mock으로 폴백하고 경고를 붙인다."""
    try:
        data = await kis_client.inquire_balance()
        return JSONResponse(data)
    except kis_client.KisError as exc:
        fallback = kis_client.mock_balance()
        fallback["mode"] = "mock"
        fallback["warning"] = f"KIS 연동 실패로 mock 데이터를 표시합니다: {exc}"
        return JSONResponse(fallback)


@app.get("/api/transactions")
async def transactions() -> dict:
    return {"transactions": kis_client.mock_transactions()}


@app.get("/api/account/holding-chart")
async def holding_chart(code: str, period: str = "realtime") -> dict:
    """보유종목 1종목의 기간별 차트 (period: realtime|daily|monthly)."""
    return await market.get_stock_chart(code, period)


@app.get("/api/account/holdings-charts")
async def holdings_charts(period: str = "realtime") -> dict:
    """보유 계좌 전체 종목의 차트를 한 번에 (동시 조회)."""
    try:
        bal = await kis_client.inquire_balance()
    except kis_client.KisError:
        bal = kis_client.mock_balance()
    holdings = bal.get("holdings", [])
    charts = await asyncio.gather(
        *[market.get_stock_chart(h["code"], period) for h in holdings]
    )
    for ch, h in zip(charts, holdings):
        ch["name"] = h.get("name", ch.get("code"))
    return {"period": period, "currency": bal.get("currency", "KRW"), "charts": charts}


@app.get("/api/market")
async def market_status() -> dict:
    """시장 지표 스냅샷. 금리는 Yahoo 실데이터, 유가/환율은 KIS 실시간이 덮어씀."""
    return await market.get_market()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/api/market/stream")
async def market_stream(request: Request) -> StreamingResponse:
    """KIS 실시간 tick 을 SSE 로 push (유가/환율). 금리는 /api/market 폴링 유지."""

    async def gen():
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        engine.subscribers.add(q)
        try:
            # 접속 즉시 현재 상태 + 스냅샷 전송
            yield _sse({"type": "meta", "mode": engine.mode, "source": engine.source})
            for key, info in engine.snapshot().items():
                yield _sse({"type": "tick", "key": key, **info})
            # 이후 실시간 tick 스트리밍 (15초마다 ping 으로 연결 유지)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield _sse({"type": "tick", **item})
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            engine.subscribers.discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 정적 프론트엔드 서빙 (API 경로 뒤에 마운트해야 API가 가려지지 않음)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
