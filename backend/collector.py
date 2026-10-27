"""캔들 수집기 — 백그라운드로 메리츠 분봉/일봉을 받아 SQLite에 누적.

- 수집 대상: watch 레지스트리(보유종목은 자동 등록, 관심/섹터는 추후 추가)
- 분봉(5m): 주기적으로 최근 40봉을 받아 병합 → 2D~5D 인트라데이 히스토리 축적
- 일봉(1d): 최초 5년 백필(from/to) 후 주기적 갱신 → 1W~5Y 완전한 뼈대 + 장기 분석
서버가 켜진 동안만 분봉이 쌓인다(오프라인 구간은 공백). 일봉은 from/to로 언제든 완전 복구.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import httpx

import candle_store as store
import meritz_client as meritz
import market
import sector_config
from config import meritz_settings
from meritz_client import KST

POLL_INTERVAL = 600        # 분봉 수집 주기(초) = 10분
DAILY_REFRESH = 3600       # 일봉 갱신 주기(초) = 1시간
BACKFILL_DAYS = 1850       # 최초 일봉 백필(≈5년)
STEP_SLEEP = 0.25          # 종목 간 호출 간격(TPS 여유)

# 섹터 보드 universe(종목+ETF) 일봉 수집 — Yahoo, 분산(레이트리밋 회피)
SECTOR_REFRESH = 21600     # 6시간마다 증분 갱신
SECTOR_STEP = 0.3          # 종목 간 간격


async def chartable_holdings() -> list[dict]:
    """계좌의 차트 대상 종목(해외 + 국내)."""
    out: list[dict] = []
    if not meritz_settings.enabled:
        return out
    try:
        bal = await meritz.get_balance()
        for h in bal.get("holdings", []):
            out.append({"symbol": h["code"], "exch": h.get("exch", ""),
                        "market": "overseas", "name": h.get("name", ""),
                        "current_price": h.get("current_price"),
                        "prdy_close": h.get("prdy_close")})
    except meritz.MeritzError:
        pass
    try:
        dom = await meritz.get_domestic_balance()
        for h in dom.get("holdings", []):
            out.append({"symbol": h["code"], "exch": "", "market": "domestic",
                        "name": h.get("name", ""),
                        "current_price": h.get("current_price"),
                        "prdy_close": h.get("prdy_close")})
    except meritz.MeritzError:
        pass
    return out


async def sync_watch() -> None:
    """보유종목을 watch 레지스트리(role='held')에 등록/갱신."""
    for h in await chartable_holdings():
        store.upsert_watch(h["symbol"], h["market"], h["exch"], h["name"], "held", ["5m", "1d"])


async def _collect_minutes(sym: str, exch: str, mkt: str) -> None:
    try:
        # 1분봉(1h~1D 차트용) + 5분봉(2D~5D 멀티데이용) 둘 다 누적. held+watch만.
        if mkt == "overseas":
            store.upsert_candles(sym, "1m", await meritz.overseas_candles(sym, exch, "minutes", 60))
            bars = await meritz.overseas_candles(sym, exch, "minutes", 300)
        else:
            store.upsert_candles(sym, "1m", await meritz.domestic_candles(sym, "minutes", 60))
            bars = await meritz.domestic_candles(sym, "minutes", 300)
        store.upsert_candles(sym, "5m", bars)
    except meritz.MeritzError:
        pass


async def _collect_daily(sym: str, exch: str, mkt: str) -> None:
    try:
        # 다중 소스(Yahoo) 오염으로 같은 날 중복이면 "1d"를 비우고 메리츠로 깨끗이 재백필
        if store.has_dup_dates(sym, "1d"):
            store.delete_candles(sym, "1d")
        have = store.has_candles(sym, "1d")
        lookback = BACKFILL_DAYS if have < 100 else 40  # 최초면 5년, 이후 최근 40일
        frm = datetime.fromtimestamp(time.time() - lookback * 86400, KST).strftime("%Y%m%d")
        to = datetime.now(KST).strftime("%Y%m%d")
        bars = (await meritz.overseas_candles(sym, exch, "days", from_=frm, to=to)
                if mkt == "overseas"
                else await meritz.domestic_candles(sym, "days", from_=frm, to=to))
        store.upsert_candles(sym, "1d", bars)
    except meritz.MeritzError:
        pass


def sector_symbols() -> list[str]:
    """섹터 보드 universe: SPY + 섹터 ETF + 전 구성 종목."""
    cfg = sector_config.get_config()
    syms = ["SPY"]
    for s in cfg.get("sectors", []):
        if s.get("etf"):
            syms.append(s["etf"])
        for ind in s.get("industries", []):
            syms.extend(ind.get("tickers", []))
    return list(dict.fromkeys(syms))


async def _collect_yahoo_daily(client, sym: str, rng: str) -> int:
    try:
        meta, pts = await market._fetch_chart(client, sym, "1d", rng)
        name = (meta or {}).get("longName") or (meta or {}).get("shortName")
        if name:
            store.set_name(sym, name)
        bars = [(ts, None, None, None, c, None) for ts, c in (pts or []) if c is not None]
        return store.upsert_candles(sym, "1dv", bars)
    except Exception:  # noqa: BLE001
        return 0


async def _sync_sector_universe() -> int:
    """universe 일봉을 Yahoo에서 분산 수집(없으면 1년 백필, 있으면 최근 1개월 갱신)."""
    syms = sector_symbols()
    sem = asyncio.Semaphore(3)

    async def _one(client, sym):
        async with sem:
            rng = "1y" if store.has_candles(sym, "1dv") < 200 else "1mo"
            n = await _collect_yahoo_daily(client, sym, rng)
            await asyncio.sleep(SECTOR_STEP)
            return n

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await asyncio.gather(*[_one(client, s) for s in syms])
    got = sum(res)

    # Yahoo에서 계속 실패하는 종목은 메리츠로 보완(거래소 OQ→NY 순, 소수라 부하 미미)
    if meritz_settings.enabled:
        frm = (datetime.now(KST) - timedelta(days=400)).strftime("%Y%m%d")
        to = datetime.now(KST).strftime("%Y%m%d")
        for sym in syms:
            if store.has_candles(sym, "1dv") >= 200:
                continue
            for exch in ("OQ", "NY"):
                try:
                    bars = await meritz.overseas_candles(sym, exch, "days", from_=frm, to=to)
                except meritz.MeritzError:
                    bars = None
                if bars:
                    store.upsert_candles(sym, "1dv", bars)
                    break
            await asyncio.sleep(SECTOR_STEP)
    return got


async def build_sector_report_now() -> dict:
    """현재 섹터 보드 + 실제 보유 종목으로 리포트 생성."""
    board = await market.get_sector_board()
    held: set[str] = set()
    if meritz_settings.enabled:
        try:
            for h in await chartable_holdings():
                if h.get("symbol"):
                    held.add(h["symbol"])
        except Exception:  # noqa: BLE001
            pass
    return market.build_sector_report(board, held)


async def _save_daily_report() -> None:
    """오늘 리포트 스냅샷 저장(날짜별 조회용)."""
    try:
        rep = await build_sector_report_now()
        today = datetime.now(KST).strftime("%Y%m%d")
        store.save_report(today, rep)
    except Exception:  # noqa: BLE001
        pass


def _backfill_compute(series: dict, names: dict, cfg: dict, held: set, weeks: int) -> int:
    """소급: 저장된 일봉을 거래일마다 잘라 보드 재계산→리포트 저장. (동기, 스레드에서 호출)"""
    spy = series.get("SPY", [])
    start = time.time() - weeks * 7 * 86400
    dates = sorted({datetime.fromtimestamp(ts, KST).strftime("%Y%m%d")
                    for ts, _c in spy if ts >= start})
    cnt = 0
    for d in dates:
        dt = datetime.strptime(d, "%Y%m%d").replace(tzinfo=KST, hour=23, minute=59, second=59)
        label = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        board = market.board_asof(series, names, cfg, int(dt.timestamp()), label)
        rep = market.build_sector_report(board, held)
        store.save_report(d, rep)
        cnt += 1
    return cnt


async def backfill_reports(weeks: int = 6) -> int:
    """최근 weeks주간 거래일마다 섹터 리포트를 소급 생성·저장."""
    store.init_db()
    cfg = sector_config.get_config()
    allsyms, uniq = market._sector_universe(cfg)
    series = await asyncio.to_thread(store.series_bulk, allsyms, "1dv")
    names = await asyncio.to_thread(store.names_bulk, uniq)
    held: set[str] = set()
    if meritz_settings.enabled:
        try:
            for h in await chartable_holdings():
                if h.get("symbol"):
                    held.add(h["symbol"])
        except Exception:  # noqa: BLE001
            pass
    return await asyncio.to_thread(_backfill_compute, series, names, cfg, held, weeks)


async def run_sector_collector() -> None:
    """섹터 보드 universe 일봉을 백그라운드로 수집(Yahoo). 메리츠와 무관."""
    store.init_db()
    try:
        await _sync_sector_universe()
        await _save_daily_report()
    except Exception:  # noqa: BLE001
        pass
    while True:
        try:
            await asyncio.sleep(SECTOR_REFRESH)
            await _sync_sector_universe()
            await _save_daily_report()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            pass


async def run_collector() -> None:
    if not meritz_settings.enabled:
        return
    store.init_db()
    try:
        await sync_watch()
        # 최초 1회: 일봉 백필 + 분봉 수집
        for w in store.list_watch():
            await _collect_daily(w["symbol"], w["exch"], w["market"])
            await _collect_minutes(w["symbol"], w["exch"], w["market"])
            await asyncio.sleep(STEP_SLEEP)
    except Exception:  # noqa: BLE001 — 수집 실패가 서버를 죽이지 않도록
        pass

    last_daily = time.time()
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            do_daily = (time.time() - last_daily) >= DAILY_REFRESH
            await sync_watch()
            for w in store.list_watch():
                await _collect_minutes(w["symbol"], w["exch"], w["market"])
                if do_daily:
                    await _collect_daily(w["symbol"], w["exch"], w["market"])
                await asyncio.sleep(STEP_SLEEP)
            if do_daily:
                last_daily = time.time()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            pass
