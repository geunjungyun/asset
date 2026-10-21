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
import meritz_client
import candle_store
import chart_service
import collector
import ledger_store
import ledger_sync
import ledger_pnl
import sector_config
from config import settings, meritz_settings
from kis_ws import engine

FRONTEND_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()      # KIS 실시간(또는 시뮬레이터) 엔진 기동
    candle_store.init_db()    # 캔들 저장소 준비
    ledger_store.init_db()    # 원장(거래내역) 저장소 준비
    tasks = []
    tasks.append(asyncio.create_task(collector.run_sector_collector()))  # 섹터 보드 일봉(Yahoo)
    if meritz_settings.enabled:
        tasks.append(asyncio.create_task(collector.run_collector()))     # 캔들 누적 수집
        tasks.append(asyncio.create_task(ledger_sync.run_ledger_sync())) # 거래내역 수집
    yield
    for t in tasks:
        t.cancel()
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
    live = meritz_settings.enabled or (not settings.use_mock)
    return {
        "status": "ok",
        "mode": "live" if live else "mock",
        "broker": "meritz" if meritz_settings.enabled else ("kis" if not settings.use_mock else "none"),
        "env": settings.env,
    }


def _enrich_prdy(data: dict, market: str) -> dict:
    """각 보유종목에 '전날 등락율'(prdy_pct) 추가.

    라이브 현재가 대비가 아니라, 직전 거래일 자체의 등락율
    = (전일종가 - 전전일종가)/전전일종가. 일봉 종가-종가 기준이라 장중에 변하지 않는다.
    """
    for h in data.get("holdings", []):
        mkt = h.get("market", market)
        pct, prev_close = chart_service._prev_day_change(h.get("code", ""), mkt)
        if pct is not None:
            h["prdy_pct"] = pct
            h["prdy_close"] = prev_close
    return data


@app.get("/api/account/status")
async def account_status() -> JSONResponse:
    """계좌 요약 + 보유종목. 메리츠 우선, 실패 시 mock 폴백(경고 포함)."""
    # 1) 메리츠 실계좌(앱키 있으면)
    if meritz_settings.enabled:
        try:
            return JSONResponse(_enrich_prdy(await meritz_client.get_balance(), "overseas"))
        except meritz_client.MeritzError as exc:
            fallback = kis_client.mock_balance()
            fallback["warning"] = f"메리츠 연동 실패로 예시 데이터를 표시합니다: {exc}"
            return JSONResponse(fallback)

    # 2) KIS(설정된 경우) 또는 mock
    try:
        return JSONResponse(await kis_client.inquire_balance())
    except kis_client.KisError as exc:
        fallback = kis_client.mock_balance()
        fallback["mode"] = "mock"
        fallback["warning"] = f"KIS 연동 실패로 mock 데이터를 표시합니다: {exc}"
        return JSONResponse(fallback)


@app.get("/api/account/domestic")
async def account_domestic() -> JSONResponse:
    """국내(원화) 보유종목만. 메리츠 미설정/실패 시 빈 목록."""
    if not meritz_settings.enabled:
        return JSONResponse({"mode": "mock", "currency": "KRW", "holdings": [], "summary": {}})
    try:
        return JSONResponse(_enrich_prdy(await meritz_client.get_domestic_balance(), "domestic"))
    except meritz_client.MeritzError as exc:
        return JSONResponse({"mode": "mock", "currency": "KRW", "holdings": [],
                             "summary": {}, "warning": f"국내 계좌 조회 실패: {exc}"})


@app.get("/api/transactions")
async def transactions() -> dict:
    return {"transactions": kis_client.mock_transactions()}


@app.get("/api/account/holding-chart")
async def holding_chart(code: str, period: str = "realtime") -> dict:
    """보유종목 1종목의 기간별 차트 (period: realtime|daily|monthly)."""
    return await market.get_stock_chart(code, period)


@app.get("/api/account/holdings-charts")
async def holdings_charts(period: str = "realtime") -> dict:
    """보유 계좌 전체 종목의 차트를 한 번에.

    메리츠 연동 시: 해외+국내 보유종목을 메리츠 캔들(로컬 누적본, 24h)로. 실패 시 Yahoo 폴백.
    미연동 시: 기존 mock 보유종목을 Yahoo 로.
    """
    if meritz_settings.enabled:
        holdings = await collector.chartable_holdings()
        if holdings:
            # 동시 호출을 4개로 제한해 분봉 API 초당 한도(TPS 10/s)를 넘지 않게 한다.
            sem = asyncio.Semaphore(4)

            async def _one(h):
                async with sem:
                    return await chart_service.holding_chart(
                        h["symbol"], h["exch"], h["market"], period,
                        live_price=h.get("current_price"),
                        prev_close=h.get("prdy_close"))

            charts = await asyncio.gather(*[_one(h) for h in holdings])
            for ch, h in zip(charts, holdings):
                ch["name"] = h.get("name", ch.get("code"))
                ch["kind"] = "held"
            charts = list(charts) + await _watch_charts(period)
            return {"period": period, "currency": "USD", "charts": charts}

    # 폴백: mock(또는 KIS) 보유종목 → Yahoo
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
        ch["kind"] = "held"
    return {"period": period, "currency": bal.get("currency", "KRW"), "charts": charts}


async def _watch_charts(period: str) -> list[dict]:
    """관심종목(watch) 차트. 잔고가 없어 현재가·전일종가는 메리츠 캔들에서 산출."""
    if not meritz_settings.enabled:
        return []
    watch = candle_store.list_watch(role="watch")
    if not watch:
        return []
    sem = asyncio.Semaphore(4)

    async def _one(w):
        async with sem:
            q = await meritz_client.day_quote(w["symbol"], w["exch"], w["market"])
            ch = await chart_service.holding_chart(
                w["symbol"], w["exch"], w["market"], period,
                live_price=q.get("price"), prev_close=q.get("prev_close"))
            ch["name"] = w.get("name") or ch.get("code")
            ch["kind"] = "watch"
            return ch

    return list(await asyncio.gather(*[_one(w) for w in watch]))


@app.get("/api/candles/stats")
async def candles_stats() -> dict:
    """캔들 누적 저장소 진단(종목·간격별 행수/기간)."""
    return candle_store.stats()


# --------------------------------------------------------------------------- #
# 관심종목(watch) — 보유종목 차트에 함께 표시
# --------------------------------------------------------------------------- #
async def _resolve_watch(code: str) -> dict | None:
    """티커/종목코드 → {symbol, market, exch, name}. 해외 거래소(OQ/NY)·이름 자동 해석."""
    code = (code or "").strip().upper()
    if not code:
        return None
    is_domestic = code.isdigit() and len(code) == 6
    name = ""
    # 이름 해석(Yahoo longName; 실패해도 치명적 아님)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12.0) as client:
            cands = ([code + ".KS", code + ".KQ"] if is_domestic else [code])
            for sym in cands:
                meta, pts = await market._fetch_chart(client, sym, "1d", "5d")
                if meta or pts:
                    name = (meta or {}).get("longName") or (meta or {}).get("shortName") or ""
                    break
    except Exception:  # noqa: BLE001
        pass

    if is_domestic:
        return {"symbol": code, "market": "domestic", "exch": "", "name": name or code}
    # 해외: 거래소 자동 탐지(OQ→NY), 당일 전일종가가 나오는 거래소 채택
    exch = ""
    if meritz_settings.enabled:
        for ex in ("OQ", "NY"):
            q = await meritz_client.day_quote(code, ex, "overseas")
            if q.get("prev_close"):
                exch = ex
                break
    return {"symbol": code, "market": "overseas", "exch": exch or "OQ", "name": name or code}


@app.get("/api/watch")
async def watch_list() -> dict:
    """관심종목 목록."""
    return {"items": candle_store.list_watch(role="watch")}


@app.post("/api/watch")
async def watch_add(payload: dict) -> JSONResponse:
    """관심종목 추가. body: {code: "TSLA" | "005930"}."""
    info = await _resolve_watch(payload.get("code", ""))
    if not info:
        return JSONResponse({"error": "유효한 티커/종목코드가 아닙니다."}, status_code=400)
    candle_store.upsert_watch(info["symbol"], info["market"], info["exch"],
                              info["name"], "watch", ["5m", "1d"])
    if info["name"]:
        candle_store.set_name(info["symbol"], info["name"])
    return JSONResponse({"ok": True, "item": info})


@app.delete("/api/watch/{code}")
async def watch_remove(code: str) -> dict:
    """관심종목 제거."""
    candle_store.remove_watch(code.strip().upper())
    return {"ok": True}


@app.get("/api/ledger/transactions")
async def ledger_transactions(limit: int = 200, offset: int = 0,
                              date_from: str | None = None,
                              date_to: str | None = None) -> dict:
    """원장 거래·매매 내역(정산+체결 병합, 최신순). 날짜(YYYYMMDD) 범위 옵션."""
    merged = await asyncio.to_thread(ledger_store.get_merged, date_from, date_to)
    page = merged[offset:offset + limit]
    st = await asyncio.to_thread(ledger_store.stats)
    est = await asyncio.to_thread(ledger_store.exec_stats)
    return {"stats": st, "exec_stats": est, "total": len(merged),
            "count": len(page), "transactions": page}


@app.post("/api/ledger/sync")
async def ledger_sync_now() -> dict:
    """수동 수집 트리거(거래내역 + 체결내역 재조회·upsert)."""
    n = await ledger_sync.sync_transactions()
    ne = await ledger_sync.sync_executions()
    st = await asyncio.to_thread(ledger_store.stats)
    est = await asyncio.to_thread(ledger_store.exec_stats)
    return {"synced": n, "exec_synced": ne, "stats": st, "exec_stats": est}


@app.get("/api/ledger/pnl")
async def ledger_pnl_summary() -> dict:
    """손익 요약: 종목별 실현(네이티브)+미실현(보유)=총수익, 전체 누적. KRW 환산 병기."""
    if not meritz_settings.enabled:
        return {"summary": {}, "symbols": [], "fx": 0, "as_of": None}
    return await ledger_pnl.build_pnl()


@app.get("/api/market")
async def market_status() -> dict:
    """시장 지표 스냅샷. 금리는 Yahoo 실데이터, 유가/환율은 KIS 실시간이 덮어씀."""
    return await market.get_market()


@app.get("/api/sectors")
async def sectors_status() -> dict:
    """11개 GICS 섹터(SPDR ETF)의 기간별 추이. 섹터별 주/월 모니터링용."""
    return await market.get_sectors()


@app.get("/api/sectors/config")
async def sectors_config_get() -> dict:
    """섹터▸산업▸종목 동적 구성(사용자 편집 대상)."""
    return await asyncio.to_thread(sector_config.get_config)


@app.put("/api/sectors/config")
async def sectors_config_put(cfg: dict) -> JSONResponse:
    """구성 저장(산업·종목 편집). 대분류는 그대로, 산업·종목만 반영."""
    try:
        saved = await asyncio.to_thread(sector_config.save_config, cfg)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(saved)


@app.post("/api/sectors/config/reset")
async def sectors_config_reset() -> dict:
    """구성을 코드 기본값(Finviz TAXONOMY)으로 재시드."""
    return await asyncio.to_thread(sector_config.reset_config)


@app.get("/api/sectors/board")
async def sectors_board() -> dict:
    """섹터▸산업▸종목 + 신호(다기간 수익률·상대강도·Δ순위·상태). 히트맵/표용."""
    return await market.get_sector_board()


@app.get("/api/sectors/{key}/members")
async def sector_members(key: str, period: str = "1W") -> dict:
    """특정 섹터의 대표 구성종목 드릴다운(기간별 등락률). 펼칠 때만 호출."""
    return await market.get_sector_members(key, period)


@app.get("/api/sectors/{key}/momentum")
async def sector_momentum(key: str) -> dict:
    """섹터 구성종목의 1주/1개월/3개월 수익률 + 모멘텀 점수·가속. 갈아타기 판단용."""
    return await market.get_sector_momentum(key)


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
