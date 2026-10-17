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

import sector_config
from config import BASE_DIR

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KST = timezone(timedelta(hours=9))

# key, 표시명, 그룹, 단위, Yahoo 심볼
INDICATORS = [
    ("sp500", "S&P 500", "지수", "pt", "^GSPC"),
    ("ndx100", "나스닥 100", "지수", "pt", "^NDX"),
    ("ust10y", "美 국채 10Y", "금리", "%", "^TNX"),
    ("ust30y", "美 국채 30Y", "금리", "%", "^TYX"),
    ("wti", "WTI", "유가", "$", "CL=F"),
    ("brent", "브렌트유", "유가", "$", "BZ=F"),
    ("usdkrw", "USD/KRW", "환율", "₩", "KRW=X"),
]

GROUPS = ["지수", "금리", "유가", "환율"]

# SPDR 섹터 ETF (11개 GICS 섹터). 섹터별 주/월 단위 추이 모니터링용.
# key, 표시명(섹터), 그룹, 단위, Yahoo 심볼
SECTOR_ETFS = [
    ("xlk",  "기술",        "섹터", "$", "XLK"),
    ("xlc",  "커뮤니케이션", "섹터", "$", "XLC"),
    ("xlv",  "헬스케어",     "섹터", "$", "XLV"),
    ("xlf",  "금융",        "섹터", "$", "XLF"),
    ("xly",  "경기소비재",   "섹터", "$", "XLY"),
    ("xlp",  "필수소비재",   "섹터", "$", "XLP"),
    ("xle",  "에너지",      "섹터", "$", "XLE"),
    ("xli",  "산업재",      "섹터", "$", "XLI"),
    ("xlb",  "소재",        "섹터", "$", "XLB"),
    ("xlre", "부동산",      "섹터", "$", "XLRE"),
    ("xlu",  "유틸리티",     "섹터", "$", "XLU"),
]

# 섹터별 대표(시총 상위) 구성 종목 — 드릴다운용. (Yahoo 티커)
SECTOR_MEMBERS = {
    "xlk":  ["NVDA", "MSFT", "AAPL", "AVGO", "ORCL", "CRM", "AMD", "MU", "MRVL"],
    "xlc":  ["GOOGL", "META", "NFLX", "DIS", "TMUS", "T", "VZ", "CMCSA"],
    "xlv":  ["LLY", "JNJ", "ABBV", "MRK", "UNH", "ABT", "AMGN", "TMO"],
    "xlf":  ["JPM", "BRK-B", "V", "MA", "BAC", "WFC", "GS", "MS"],
    "xly":  ["AMZN", "TSLA", "HD", "MCD", "BKNG", "LOW", "NKE", "SBUX"],
    "xlp":  ["WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ"],
    "xle":  ["XOM", "CVX", "COP", "WMB", "EOG", "SLB", "OKE", "PSX"],
    "xli":  ["GE", "RTX", "CAT", "UBER", "BA", "HON", "UNP", "DE"],
    "xlb":  ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DOW", "NUE"],
    "xlre": ["PLD", "AMT", "EQIX", "WELL", "SPG", "O", "DLR", "CCI"],
    "xlu":  ["NEE", "SO", "DUK", "CEG", "AEP", "D", "EXC", "SRE"],
}

# 사용자 관심그룹(앱의 관심 목록 기반). 그룹명 → Yahoo 티커 목록.
# GICS 섹터와 별개로, 섹터 추이에서 '관심그룹' 모멘텀으로 조회.
WATCH_GROUPS: dict[str, list[str]] = {
    "빅테크":     ["GOOGL", "NVDA", "AAPL", "AMZN", "MSFT", "META", "TSLA"],
    "반도체":     ["NVDA", "MRVL", "SNDK", "MU", "AMD", "INTC", "WDC", "STX"],
    "ai인프라":   ["AMAT", "ASML", "LITE", "CBRS", "IREN", "CRWV"],
    "양자":       ["QBTS", "RGTI", "IONQ"],
    "소프트웨어": ["CRWD", "PANW", "ORCL", "PLTR"],
    "바이오":     ["AMGN", "LLY", "NVO", "MRK", "JNJ", "PFE", "MRNA"],
    "에너지":     ["XOM", "CVX", "COP", "EOG"],
    "서버":       ["IBM", "ORCL", "DELL"],
}

# 사용자 보유 종목(섹터 드릴다운에서 강조 표시)
USER_HELD = {"MU", "SNDK", "GOOGL", "MRVL", "AMD", "AMGN", "LLY", "NVDA"}

# 종목 → 주요 하위 산업(Industry) 분류
INDUSTRY = {
    # 기술
    "NVDA": "반도체", "AVGO": "반도체", "AMD": "반도체", "MU": "반도체", "MRVL": "반도체",
    "MSFT": "소프트웨어", "ORCL": "소프트웨어", "CRM": "소프트웨어",
    "AAPL": "하드웨어·전자",
    # 커뮤니케이션
    "GOOGL": "인터넷·미디어", "META": "인터넷·미디어", "NFLX": "인터넷·미디어", "DIS": "인터넷·미디어",
    "TMUS": "통신서비스", "T": "통신서비스", "VZ": "통신서비스", "CMCSA": "통신서비스",
    # 헬스케어
    "LLY": "제약", "ABBV": "제약", "MRK": "제약",
    "AMGN": "바이오테크",
    "JNJ": "의료기기·서비스", "UNH": "의료기기·서비스", "ABT": "의료기기·서비스", "TMO": "의료기기·서비스",
    # 금융
    "JPM": "은행", "BAC": "은행", "WFC": "은행",
    "V": "카드·결제", "MA": "카드·결제",
    "GS": "자본시장·보험", "MS": "자본시장·보험", "BRK-B": "자본시장·보험",
    # 경기소비재
    "AMZN": "인터넷소매", "TSLA": "자동차",
    "HD": "소매·외식·여행", "MCD": "소매·외식·여행", "LOW": "소매·외식·여행",
    "BKNG": "소매·외식·여행", "SBUX": "소매·외식·여행", "NKE": "소매·외식·여행",
    # 필수소비재
    "WMT": "유통", "COST": "유통",
    "KO": "음식료·담배", "PEP": "음식료·담배", "MO": "음식료·담배", "MDLZ": "음식료·담배",
    "PG": "생활용품",
    # 에너지
    "XOM": "종합·E&P", "CVX": "종합·E&P", "COP": "종합·E&P", "EOG": "종합·E&P",
    "SLB": "서비스·중류", "WMB": "서비스·중류", "OKE": "서비스·중류", "PSX": "서비스·중류",
    # 산업재
    "GE": "자본재", "RTX": "자본재", "CAT": "자본재", "BA": "자본재", "HON": "자본재", "DE": "자본재",
    "UBER": "운송", "UNP": "운송",
    # 소재
    "LIN": "화학", "SHW": "화학", "APD": "화학", "ECL": "화학", "DOW": "화학",
    "FCX": "금속·광업", "NEM": "금속·광업", "NUE": "금속·광업",
    # 부동산
    "AMT": "인프라리츠", "CCI": "인프라리츠", "EQIX": "데이터센터리츠", "DLR": "데이터센터리츠",
    "PLD": "물류리츠", "SPG": "기타리츠", "O": "기타리츠", "WELL": "기타리츠",
    # 유틸리티
    "NEE": "전력", "SO": "전력", "DUK": "전력", "AEP": "전력", "D": "전력", "EXC": "전력",
    "CEG": "신재생·복합", "SRE": "신재생·복합",
}


def _industry(sym: str) -> str:
    # 동적 구성(사용자 편집) 우선, 없으면 기본 INDUSTRY
    return sector_config.industry_of(sym) or INDUSTRY.get(sym, "기타")

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

    # 1주: 일봉 5점 대신 5일치 장중 5분봉으로 상세 표현(override)
    if intra and len(intra) >= 2:
        wk = intra[:]
        wk[-1] = (wk[-1][0], price)
        if len(wk) > 300:          # 과다 포인트 다운샘플
            wk = wk[:: (len(wk) // 300) or 1]
        periods["1W"] = _period(wk, price, PERIOD_FMT["1W"])

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
# 섹터별 추이 (SPDR 섹터 ETF 11개) — 주/월 단위 모니터링
# --------------------------------------------------------------------------- #
_sec_mem: dict | None = None
_sec_last_good: dict | None = None
SECTOR_CACHE_TTL = 60  # 초 (보조 지표라 조금 길게)


async def get_sectors() -> dict:
    """11개 GICS 섹터(SPDR ETF)의 기간별 추이. market과 동일한 periods 구조."""
    global _sec_mem, _sec_last_good
    if _sec_mem and time.time() - _sec_mem.get("_ts", 0) < SECTOR_CACHE_TTL:
        return _sec_mem["data"]

    async with httpx.AsyncClient(timeout=12.0) as client:
        built = await asyncio.gather(*[_build(client, row) for row in SECTOR_ETFS])

    sectors, fell_back = [], []
    for row, item in zip(SECTOR_ETFS, built):
        if item is None:
            item = _mock_indicator(row)
            fell_back.append(row[1])
        sectors.append(item)

    live_count = len(SECTOR_ETFS) - len(fell_back)
    if live_count == 0 and _sec_last_good:
        stale = dict(_sec_last_good["data"])
        stale["warning"] = "실시간 갱신에 실패해 마지막 수신 시세를 표시합니다."
        return stale

    result = {
        "mode": "live" if live_count else "mock",
        "source": "Yahoo Finance · 약 15분 지연",
        "updated_at": max((s["as_of"] for s in sectors), default=""),
        "sectors": sectors,
    }
    if fell_back:
        result["warning"] = f"일부 섹터 연동 실패로 예시 데이터 사용: {', '.join(fell_back)}"
    if live_count:
        payload = {"_ts": time.time(), "data": result}
        _sec_mem = payload
        _sec_last_good = payload
    return result


# 섹터 구성종목 드릴다운 — (key|period) 캐시
_member_cache: dict[str, dict] = {}
MEMBER_CACHE_TTL = 60  # 초


async def get_sector_members(key: str, period: str) -> dict:
    """특정 섹터의 대표 구성종목들을 선택 기간으로 조회(등락률 내림차순)."""
    syms = sector_config.members(key)
    if not syms:
        return {"key": key, "period": period, "members": []}

    cache_key = f"{key}|{period}"
    hit = _member_cache.get(cache_key)
    if hit and time.time() - hit["_ts"] < MEMBER_CACHE_TTL:
        return hit["data"]

    results = await asyncio.gather(*[get_stock_chart(s, period) for s in syms])
    members = []
    for sym, r in zip(syms, results):
        members.append({
            "code": sym,
            "held": sym in USER_HELD,
            "industry": _industry(sym),
            "value": r.get("value"),
            "currency": r.get("currency", "USD"),
            "change_pct": r.get("change_pct", 0.0),
            "series": r.get("series", []),
            "times": r.get("times", []),
        })
    members.sort(key=lambda m: m["change_pct"], reverse=True)
    data = {"key": key, "period": period, "members": members}
    _member_cache[cache_key] = {"_ts": time.time(), "data": data}
    return data


# --------------------------------------------------------------------------- #
# 섹터 모멘텀 (갈아타기 판단) — 1주/1개월/3개월 수익률 + 가속
# --------------------------------------------------------------------------- #
# 주간 환산 상수(거래일 기준): 1개월≈4.345주, 3개월≈13.04주
_WK_M = 4.345
_WK_3M = 13.04
# 수익률 기준점(거래일): 1주=5, 1개월=21, 3개월=63
_MOM_BACK = {"r1w": 5, "r1m": 21, "r3m": 63}

_mom_cache: dict[str, dict] = {}
MOM_CACHE_TTL = 60  # 초


def _ret(closes: list[float], nback: int) -> float:
    if len(closes) < 2:
        return 0.0
    idx = len(closes) - 1 - nback
    base = closes[idx] if idx >= 0 else closes[0]
    last = closes[-1]
    return round((last / base - 1) * 100, 2) if base else 0.0


def _momentum(r1w: float, r1m: float, r3m: float) -> dict:
    p1w, p1m, p3m = r1w, r1m / _WK_M, r3m / _WK_3M
    score = round(0.5 * p1w + 0.3 * p1m + 0.2 * p3m, 3)
    accel = round(p1w - p1m, 3)
    return {"score": score, "accel": accel}


async def get_sector_momentum(key: str) -> dict:
    """섹터 구성종목의 1주/1개월/3개월 수익률 + 모멘텀 점수/가속(점수 내림차순)."""
    syms = sector_config.members(key)
    if not syms:
        return {"key": key, "members": []}

    hit = _mom_cache.get(key)
    if hit and time.time() - hit["_ts"] < MOM_CACHE_TTL:
        return hit["data"]

    async with httpx.AsyncClient(timeout=12.0) as client:
        fetched = await asyncio.gather(
            *[_fetch_chart(client, s, "1d", "6mo") for s in syms]
        )

    members = []
    for sym, (meta, pts) in zip(syms, fetched):
        closes = [c for _t, c in pts if c is not None]
        if len(closes) < 2:
            members.append({
                "code": sym, "held": sym in USER_HELD, "industry": _industry(sym),
                "value": None, "currency": "USD", "r1w": 0.0, "r1m": 0.0, "r3m": 0.0,
                "score": -999, "accel": 0.0, "series": [], "no_data": True,
            })
            continue
        r1w = _ret(closes, _MOM_BACK["r1w"])
        r1m = _ret(closes, _MOM_BACK["r1m"])
        r3m = _ret(closes, _MOM_BACK["r3m"])
        mom = _momentum(r1w, r1m, r3m)
        spark = closes[-63:] if len(closes) >= 63 else closes
        if len(spark) > 40:
            spark = spark[:: (len(spark) // 40) or 1]
        price = (meta or {}).get("regularMarketPrice") or closes[-1]
        members.append({
            "code": sym, "held": sym in USER_HELD, "industry": _industry(sym),
            "value": round(float(price), 2), "currency": (meta or {}).get("currency", "USD"),
            "r1w": r1w, "r1m": r1m, "r3m": r3m,
            "score": mom["score"], "accel": mom["accel"],
            "series": [round(c, 4) for c in spark],
        })

    members.sort(key=lambda m: m["score"], reverse=True)
    for i, m in enumerate(members):
        m["rank"] = i + 1

    # 산업별 집계(갈아타기: 어느 하위 산업이 강한가)
    groups: dict[str, list] = {}
    for m in members:
        if m.get("no_data"):
            continue
        groups.setdefault(m["industry"], []).append(m)
    industries = []
    for name, ms in groups.items():
        n = len(ms)
        r1w = round(sum(x["r1w"] for x in ms) / n, 2)
        r1m = round(sum(x["r1m"] for x in ms) / n, 2)
        r3m = round(sum(x["r3m"] for x in ms) / n, 2)
        mom = _momentum(r1w, r1m, r3m)
        industries.append({
            "name": name, "count": n, "held": any(x["held"] for x in ms),
            "r1w": r1w, "r1m": r1m, "r3m": r3m,
            "score": mom["score"], "accel": mom["accel"],
        })
    industries.sort(key=lambda x: x["score"], reverse=True)

    data = {"key": key, "members": members, "industries": industries}
    _mom_cache[key] = {"_ts": time.time(), "data": data}
    return data


# --------------------------------------------------------------------------- #
# 관심그룹 모멘텀 (사용자 정의 그룹)
# --------------------------------------------------------------------------- #
_wgroups_cache: dict | None = None
WGROUPS_TTL = 60  # 초


def _member_momentum(sym: str, meta: dict | None, pts: list) -> dict:
    """일봉 시계열 → 종목 모멘텀 dict(섹터 구성종목과 동일 포맷)."""
    closes = [c for _t, c in pts if c is not None]
    if len(closes) < 2:
        return {"code": sym, "held": sym in USER_HELD, "industry": _industry(sym),
                "value": None, "currency": "USD", "r1w": 0.0, "r1m": 0.0, "r3m": 0.0,
                "score": -999, "accel": 0.0, "series": [], "no_data": True}
    r1w = _ret(closes, _MOM_BACK["r1w"])
    r1m = _ret(closes, _MOM_BACK["r1m"])
    r3m = _ret(closes, _MOM_BACK["r3m"])
    mom = _momentum(r1w, r1m, r3m)
    spark = closes[-63:] if len(closes) >= 63 else closes
    if len(spark) > 40:
        spark = spark[:: (len(spark) // 40) or 1]
    price = (meta or {}).get("regularMarketPrice") or closes[-1]
    return {"code": sym, "held": sym in USER_HELD, "industry": _industry(sym),
            "value": round(float(price), 2), "currency": (meta or {}).get("currency", "USD"),
            "r1w": r1w, "r1m": r1m, "r3m": r3m,
            "score": mom["score"], "accel": mom["accel"],
            "series": [round(c, 4) for c in spark]}


async def get_watch_groups_momentum() -> dict:
    """관심그룹별 모멘텀(구성종목 동일가중 평균) + 구성종목 모멘텀. 점수 내림차순."""
    global _wgroups_cache
    if _wgroups_cache and time.time() - _wgroups_cache["_ts"] < WGROUPS_TTL:
        return _wgroups_cache["data"]

    uniq = list(dict.fromkeys(s for syms in WATCH_GROUPS.values() for s in syms))
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=12.0) as client:
        async def _one(s):
            async with sem:
                return await _fetch_chart(client, s, "1d", "6mo")
        fetched = await asyncio.gather(*[_one(s) for s in uniq])
    mmap = {sym: _member_momentum(sym, meta, pts) for sym, (meta, pts) in zip(uniq, fetched)}

    groups = []
    for name, syms in WATCH_GROUPS.items():
        members = [dict(mmap[s]) for s in syms]
        members.sort(key=lambda m: m["score"], reverse=True)
        for i, m in enumerate(members):
            m["rank"] = i + 1
        valid = [m for m in members if not m.get("no_data")]
        if valid:
            r1w = round(sum(m["r1w"] for m in valid) / len(valid), 2)
            r1m = round(sum(m["r1m"] for m in valid) / len(valid), 2)
            r3m = round(sum(m["r3m"] for m in valid) / len(valid), 2)
            mom = _momentum(r1w, r1m, r3m)
            score, accel = mom["score"], mom["accel"]
        else:
            r1w = r1m = r3m = 0.0
            score, accel = -999, 0.0
        groups.append({
            "name": name, "count": len(valid), "total": len(members),
            "held": any(m["held"] for m in valid),
            "r1w": r1w, "r1m": r1m, "r3m": r3m, "score": score, "accel": accel,
            "members": members,
        })
    groups.sort(key=lambda g: g["score"], reverse=True)

    data = {
        "groups": groups,
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "Yahoo Finance · 약 15분 지연",
    }
    _wgroups_cache = {"_ts": time.time(), "data": data}
    return data


# --------------------------------------------------------------------------- #
# Mock 폴백
# --------------------------------------------------------------------------- #
_MOCK_BASE = {"sp500": 5200.0, "ndx100": 18500.0,
              "ust10y": 4.28, "ust30y": 4.51, "wti": 78.4, "brent": 82.1, "usdkrw": 1342.0,
              # 섹터 ETF(대략적 시세) — mock 폴백용
              "xlk": 230.0, "xlc": 105.0, "xlv": 150.0, "xlf": 52.0, "xly": 215.0,
              "xlp": 82.0, "xle": 92.0, "xli": 140.0, "xlb": 92.0, "xlre": 42.0, "xlu": 82.0}
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
