"""시장 지표 실시간(장중) 연동.

미국 국채 10Y/30Y 금리, WTI, 브렌트유, 환율(USD/KRW)을 Yahoo Finance chart API에서
가져온다. **API 키 불필요.** 장중(intraday) 시세라 FRED(전일 종가)보다 훨씬 현재에 가깝다.

주의: Yahoo는 거래소 라이선스가 아닌 무료 소스라 통상 '약 15분 지연'이다.
진짜 틱 단위 실시간은 유료 피드(ICE/Refinitiv 등)가 필요하다.

- 소스: https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>
- 심볼: ^TNX(10Y), ^TYX(30Y), CL=F(WTI), BZ=F(Brent), KRW=X(USD/KRW)
- 짧은 캐시(기본 15초) + 실패 시 마지막 시세 → mock 폴백
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timezone, timedelta

import httpx

from config import BASE_DIR

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KST = timezone(timedelta(hours=9))

# key, 표시명, 그룹, 단위, Yahoo 심볼
INDICATORS = [
    ("ust10y", "美 국채 10Y", "금리", "%", "^TNX"),
    ("ust30y", "美 국채 30Y", "금리", "%", "^TYX"),
    ("wti", "WTI", "유가", "$", "CL=F"),
    ("brent", "브렌트유", "유가", "$", "BZ=F"),
    ("usdkrw", "USD/KRW", "환율", "₩", "KRW=X"),
]

GROUPS = ["금리", "유가", "환율"]

# 장중(5분봉) 기간 → 최근 N일
INTRA_DAYS = {"1D": 1, "2D": 2, "3D": 3, "4D": 4, "5D": 5}
# 일봉 기반 기간 → 최근 표본 개수(거래일)
DAILY_WINDOWS = {"1W": 5, "1M": 22, "3M": 66, "6M": 132, "1Y": 252}

CACHE_FILE = BASE_DIR / ".market_cache.json"
CACHE_TTL = 15  # 초 (자동 갱신 대비 짧게, Yahoo 과호출 방지)

_mem: dict | None = None       # 최신(신선) 캐시
_last_good: dict | None = None  # 실패 시 폴백용 마지막 성공 데이터


# --------------------------------------------------------------------------- #
# Yahoo 다운로드
# --------------------------------------------------------------------------- #
async def _fetch_chart(client: httpx.AsyncClient, sym: str, interval: str, rng: str):
    """(meta, [(ts, close), ...]) 반환. 실패 시 (None, [])."""
    try:
        r = await client.get(
            YF_URL.format(sym=sym),
            params={"interval": interval, "range": rng},
            headers=YF_HEADERS,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        meta = res.get("meta", {})
        times = res.get("timestamp", []) or []
        closes = (res.get("indicators", {}).get("quote", [{}])[0] or {}).get("close", []) or []
        pts = [(t, c) for t, c in zip(times, closes) if c is not None]
        return meta, pts
    except (httpx.HTTPError, KeyError, IndexError, ValueError, Exception):
        return None, []


# 기간별 시간축 라벨 포맷
PERIOD_FMT = {"1D": "%H:%M", "2D": "%m/%d", "3D": "%m/%d", "4D": "%m/%d", "5D": "%m/%d",
              "1W": "%m/%d", "1M": "%m/%d",
              "3M": "%m/%d", "6M": "%m/%d", "1Y": "%y/%m", "5Y": "%y/%m"}


def _fmt_ts(ts, fmt: str) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST).strftime(fmt)
    except (OSError, OverflowError, ValueError):
        return ""


def _period(pts: list[tuple], price: float, fmt: str) -> dict:
    """(시각, 값) 시계열 + 현재가로 변동/변동률/스파크라인/시간축 계산."""
    if not pts:
        pts = [(None, price)]
    series = [v for _t, v in pts]
    times = [_fmt_ts(t, fmt) for t, _v in pts]
    start = series[0]
    change = round(price - start, 4)
    change_pct = round((change / start * 100), 2) if start else 0.0
    return {
        "series": [round(v, 4) for v in series],
        "times": times,
        "change": change,
        "change_pct": change_pct,
    }


async def _build(client: httpx.AsyncClient, meta_row: tuple) -> dict | None:
    key, name, group, unit, sym = meta_row
    (intra_meta, intra), (daily_meta, daily) = await asyncio.gather(
        _fetch_chart(client, sym, "5m", "5d"),   # 1~5일 장중 5분봉
        _fetch_chart(client, sym, "1d", "5y"),   # 1주~5년 윈도우용 일봉(5년)
    )
    meta = intra_meta or daily_meta
    if meta is None:
        return None

    price = meta.get("regularMarketPrice")
    if price is None:
        price = (intra[-1][1] if intra else (daily[-1][1] if daily else None))
    if price is None:
        return None
    price = float(price)

    ts = meta.get("regularMarketTime")
    as_of = (
        datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
        if ts else datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    )

    periods: dict[str, dict] = {}

    # 1~5일: (가용 데이터의) 최근 N일 장중 5분봉 — 지연 데이터 대비 최신 봉 기준
    ref_ts = intra[-1][0] if intra else datetime.now(timezone.utc).timestamp()
    for label, days in INTRA_DAYS.items():
        cut = ref_ts - days * 86400
        pts = [(t, c) for t, c in intra if t is not None and t >= cut]
        if len(pts) < 2:
            pts = intra or [(None, price)]
        if len(pts) > 300:          # 과다 포인트는 다운샘플
            pts = pts[:: (len(pts) // 300) or 1]
        pts = pts[:]
        pts[-1] = (pts[-1][0], price)   # 끝점 현재가
        periods[label] = _period(pts, price, PERIOD_FMT[label])

    # 1주~1년: 일봉 창. 마지막 점은 현재가로 갱신.
    for label, n in DAILY_WINDOWS.items():
        window = daily[-n:] if len(daily) >= n else daily[:]
        if window:
            window = window[:]
            window[-1] = (window[-1][0], price)  # 끝점 값을 현재가로
        periods[label] = _period(window or [(None, price)], price, PERIOD_FMT[label])

    # 5년: 전체 일봉(5y)을 약 250포인트로 다운샘플(≈주봉)
    if daily:
        step = max(1, len(daily) // 250)
        full = daily[::step]
        full = full[:]
        full[-1] = (full[-1][0], price)
        periods["5Y"] = _period(full, price, PERIOD_FMT["5Y"])
    else:
        periods["5Y"] = _period([(None, price)], price, PERIOD_FMT["5Y"])

    return {
        "key": key, "name": name, "group": group, "unit": unit,
        "value": round(price, 4), "as_of": as_of, "periods": periods,
    }


# --------------------------------------------------------------------------- #
# 캐시
# --------------------------------------------------------------------------- #
def _load_files() -> None:
    """프로세스 시작 시 파일 캐시를 메모리로."""
    global _mem, _last_good
    if _mem is None and CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _mem = raw
            _last_good = raw
        except (json.JSONDecodeError, OSError):
            pass


def _write_cache(data: dict) -> None:
    global _mem, _last_good
    payload = {"_ts": time.time(), "data": data}
    _mem = payload
    _last_good = payload
    try:
        CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 공개 진입점
# --------------------------------------------------------------------------- #
async def get_market() -> dict:
    _load_files()
    if _mem and time.time() - _mem.get("_ts", 0) < CACHE_TTL:
        return _mem["data"]

    async with httpx.AsyncClient(timeout=12.0) as client:
        built = await asyncio.gather(*[_build(client, row) for row in INDICATORS])

    indicators, fell_back = [], []
    for row, item in zip(INDICATORS, built):
        if item is None:
            item = _mock_indicator(row)
            fell_back.append(row[1])
        indicators.append(item)

    live_count = len(INDICATORS) - len(fell_back)

    # 전부 실패 → 마지막 성공 데이터가 있으면 그걸(오래됐어도) 반환
    if live_count == 0 and _last_good:
        stale = dict(_last_good["data"])
        stale["warning"] = "실시간 갱신에 실패해 마지막 수신 시세를 표시합니다."
        return stale

    result = {
        "mode": "live" if live_count else "mock",
        "source": "Yahoo Finance · 약 15분 지연",
        "updated_at": max((i["as_of"] for i in indicators), default=""),
        "groups": GROUPS,
        "indicators": indicators,
    }
    if fell_back:
        result["warning"] = f"일부 지표 연동 실패로 예시 데이터 사용: {', '.join(fell_back)}"
    if live_count:
        _write_cache(result)
    return result


# --------------------------------------------------------------------------- #
# Mock 폴백
# --------------------------------------------------------------------------- #
_MOCK_BASE = {"ust10y": 4.28, "ust30y": 4.51, "wti": 78.4, "brent": 82.1, "usdkrw": 1342.0}
_ALL_WINDOWS = {"1D": 78, "2D": 120, "3D": 160, "4D": 200, "5D": 240,
                **DAILY_WINDOWS, "5Y": 260}


# 기간별 표본 간격(초)
_STEP_SEC = {"1D": 300, "2D": 1440, "3D": 1620, "4D": 1728, "5D": 1800,
             "1W": 86400, "1M": 86400, "3M": 86400,
             "6M": 86400, "1Y": 86400, "5Y": 604800}


def _mock_indicator(meta_row: tuple) -> dict:
    key, name, group, unit, _sym = meta_row
    current = _MOCK_BASE.get(key, 100.0)
    rng = random.Random(key)
    now = datetime.now(KST)
    periods = {}
    for label, n in _ALL_WINDOWS.items():
        vals = [current]
        vol = 0.004 + 0.02 * (n / 252)
        for _ in range(n - 1):
            vals.append(vals[-1] * (1 + rng.uniform(-vol, vol)))
        vals.reverse()
        digits = 2 if current >= 100 else 4
        series = [round(p, digits) for p in vals]
        # 과거→현재 시각 라벨
        step = _STEP_SEC[label]
        times = [
            (now - timedelta(seconds=step * (n - 1 - i))).strftime(PERIOD_FMT[label])
            for i in range(n)
        ]
        start = series[0]
        change = round(current - start, 4)
        periods[label] = {
            "series": series, "times": times, "change": change,
            "change_pct": round(change / start * 100, 2) if start else 0.0,
        }
    return {
        "key": key, "name": name, "group": group, "unit": unit,
        "value": current, "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "periods": periods,
    }


# --------------------------------------------------------------------------- #
# 보유종목 차트 (실시간 / 일 / 월) — 한국 주식
# --------------------------------------------------------------------------- #
# period -> (interval, range, 시각 포맷)
STOCK_PERIODS = {
    "1D": ("5m", "5d", "%H:%M"),      # 1일(장중 5분봉, 최근 1일 필터)
    "2D": ("5m", "5d", "%m/%d"),      # 2일
    "3D": ("5m", "5d", "%m/%d"),      # 3일
    "4D": ("5m", "5d", "%m/%d"),      # 4일
    "5D": ("5m", "5d", "%m/%d"),      # 5일
    "1W": ("30m", "5d", "%m/%d"),     # 1주(30분봉, ~5거래일)
    "1M": ("1d", "1mo", "%m/%d"),     # 1개월(일봉)
    "3M": ("1d", "3mo", "%m/%d"),     # 3개월(일봉)
    "6M": ("1d", "6mo", "%m/%d"),     # 6개월(일봉)
    "1Y": ("1d", "1y", "%y/%m"),      # 1년(일봉)
    "5Y": ("1wk", "5y", "%y/%m"),     # 5년(주봉)
}
# 장중 기간은 최근 N일로 필터
STOCK_DAY_FILTER = {"1D": 1, "2D": 2, "3D": 3, "4D": 4, "5D": 5}


async def get_stock_chart(code: str, period: str) -> dict:
    """한국 주식 1종목의 기간별 차트. Yahoo <code>.KS → 없으면 .KQ. 실패 시 mock."""
    interval, rng, fmt = STOCK_PERIODS.get(period, STOCK_PERIODS["1D"])
    code = (code or "").strip()

    meta, pts, symbol = None, [], ""
    if code:
        # 숫자코드=한국주식(.KS→.KQ), 영문=미국 등 해외 티커(그대로)
        candidates = ([code + ".KS", code + ".KQ"] if code.isdigit() else [code])
        async with httpx.AsyncClient(timeout=12.0) as client:
            for sym in candidates:
                m, p = await _fetch_chart(client, sym, interval, rng)
                if p:
                    meta, pts, symbol = m, p, sym
                    break

    if not pts:
        return _mock_stock_chart(code, period, fmt)

    # 장중(1~5일)은 (가용 데이터의) 최근 N일로 필터 + 다운샘플 — 최신 봉 기준
    if period in STOCK_DAY_FILTER:
        ref = pts[-1][0] if pts and pts[-1][0] else None
        if ref:
            cut = ref - STOCK_DAY_FILTER[period] * 86400
            f = [(t, c) for t, c in pts if t is not None and t >= cut]
            if len(f) >= 2:
                pts = f
        if len(pts) > 300:
            pts = pts[:: (len(pts) // 300) or 1]

    price = float(meta.get("regularMarketPrice") or pts[-1][1])
    p = _period(pts, price, fmt)
    return {
        "mode": "live",
        "code": code,
        "symbol": symbol,
        "period": period,
        "currency": meta.get("currency", "KRW"),
        "value": round(price, 2),
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        **p,  # series, times, change, change_pct
    }


def _mock_stock_chart(code: str, period: str, fmt: str) -> dict:
    n = {"1D": 78, "2D": 120, "3D": 160, "4D": 200, "5D": 240,
         "1W": 65, "1M": 22, "3M": 66, "6M": 132, "1Y": 252, "5Y": 260}.get(period, 66)
    step = _STEP_SEC.get(period, 86400)
    is_us = not code.isdigit()  # 영문 티커=미국(USD), 숫자=한국(KRW)
    currency = "USD" if is_us else "KRW"
    base = (100 + sum(ord(c) for c in code) % 400) if is_us else 50000 + (sum(ord(c) for c in code) % 50) * 2000
    rng = random.Random(f"{code}:{period}")
    vals = [float(base)]
    vol = 0.004 + 0.02 * (n / 252)
    for _ in range(n - 1):
        vals.append(vals[-1] * (1 + rng.uniform(-vol, vol)))
    vals.reverse()
    now = datetime.now(KST)
    pts = [
        (int((now - timedelta(seconds=step * (n - 1 - i))).timestamp()), vals[i])
        for i in range(n)
    ]
    price = vals[-1]
    p = _period(pts, price, fmt)
    return {
        "mode": "mock", "code": code, "symbol": code, "period": period,
        "currency": currency, "value": round(price, 2),
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"), **p,
    }
