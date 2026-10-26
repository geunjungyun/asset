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

import candle_store
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

# 섹터 ▸ 산업(Finviz 기준) ▸ 대표 종목. 편집 가능 구성의 기본 시드(SECTOR_MEMBERS/INDUSTRY는 여기서 파생).
TAXONOMY = {
    "xlk": [
        ("반도체", ["NVDA", "AVGO", "AMD", "TXN", "QCOM", "MU", "INTC", "ADI", "MRVL", "MCHP", "NXPI", "MPWR", "ON", "SWKS"]),
        ("소프트웨어-인프라", ["MSFT", "ORCL", "PLTR", "CRWD", "PANW", "FTNT", "SNPS", "CDNS", "VRSN", "FFIV"]),
        ("소프트웨어-응용", ["CRM", "NOW", "INTU", "ADBE", "UBER", "ADP", "WDAY", "DDOG", "ROP"]),
        ("IT서비스", ["IBM", "ACN", "FIS", "GPN", "CTSH"]),
        ("컴퓨터 하드웨어", ["DELL", "HPQ", "WDC", "STX", "ANET", "SNDK", "NTAP"]),
        ("통신장비", ["CSCO", "MSI", "HPE", "CIEN"]),
        ("반도체장비", ["AMAT", "LRCX", "KLAC", "TER", "QRVO"]),
        ("전자부품", ["APH", "GLW", "TEL"]),
        ("과학기술기기", ["KEYS", "TDY", "GRMN", "COHR", "FTV", "TRMB"]),
        ("소비자가전", ["AAPL"]),
    ],
    "xlc": [
        ("인터넷 컨텐츠", ["GOOGL", "META"]),
        ("통신서비스", ["TMUS", "VZ", "T", "CMCSA"]),
        ("엔터테인먼트", ["NFLX", "DIS", "WBD", "LYV", "FOXA"]),
        ("전자게임", ["EA", "TTWO"]),
        ("광고·마케팅", ["APP", "OMC", "IPG"]),
    ],
    "xlv": [
        ("제약-일반", ["LLY", "JNJ", "ABBV", "MRK", "PFE", "BMY", "GILD"]),
        ("바이오테크", ["AMGN", "VRTX", "REGN", "BIIB", "INCY"]),
        ("의료기기", ["ABT", "SYK", "BSX", "MDT", "ISRG", "EW", "DXCM", "BDX", "ZBH", "STE"]),
        ("진단·연구", ["TMO", "DHR", "A", "IQV", "DGX", "LH", "RVTY", "WAT"]),
        ("헬스케어플랜", ["UNH", "ELV", "CI", "CVS", "HUM", "CNC"]),
        ("의료유통", ["MCK", "COR", "CAH"]),
        ("의료서비스", ["HCA", "DVA", "UHS"]),
    ],
    "xlf": [
        ("은행-다각화", ["JPM", "BAC", "WFC", "C"]),
        ("자본시장", ["MS", "GS", "SCHW", "BX", "BLK", "KKR", "APO", "BK", "STT", "TROW", "AMP", "NTRS", "RJF", "IBKR"]),
        ("신용서비스", ["V", "MA", "AXP", "COF", "SYF", "PYPL"]),
        ("보험-다각화", ["BRK-B", "CB", "TRV", "ALL", "PGR", "AIG"]),
        ("금융데이터", ["SPGI", "ICE", "CME", "MCO", "MSCI", "NDAQ", "FDS"]),
        ("은행-지역", ["USB", "PNC", "TFC", "MTB", "FITB", "HBAN", "RF", "KEY", "CFG"]),
        ("보험-생명", ["MET", "PRU", "AFL"]),
    ],
    "xly": [
        ("인터넷소매", ["AMZN"]),
        ("자동차제조", ["TSLA", "GM", "F", "RIVN"]),
        ("음식점", ["MCD", "SBUX", "CMG", "YUM", "DRI", "DPZ"]),
        ("여행서비스", ["BKNG", "ABNB", "EXPE", "RCL", "CCL", "NCLH"]),
        ("주택개선소매", ["HD", "LOW"]),
        ("의류소매", ["TJX", "ROST"]),
        ("자동차부품", ["ORLY", "AZO", "APTV", "GPC"]),
        ("숙박", ["MAR", "HLT", "H"]),
        ("신발·의류", ["NKE", "DECK", "LULU"]),
        ("전문소매", ["BBY", "ULTA"]),
        ("도박·리조트", ["LVS", "DKNG", "CZR"]),
    ],
    "xlp": [
        ("할인점", ["WMT", "COST", "TGT", "DG", "DLTR"]),
        ("가정·개인용품", ["PG", "CL", "KMB", "CHD", "CLX", "EL", "KVUE"]),
        ("담배", ["PM", "MO", "BTI"]),
        ("음료-비알콜", ["KO", "PEP", "MNST", "KDP"]),
        ("음료-주류", ["STZ"]),
        ("포장식품", ["MDLZ", "GIS", "KHC", "HSY", "HRL", "CAG", "SJM"]),
        ("식품유통", ["SYY", "KR"]),
        ("농산물", ["ADM", "BG"]),
    ],
    "xli": [
        ("항공우주·방산", ["GE", "RTX", "BA", "LMT", "GD", "NOC", "HWM", "LHX", "AXON", "TDG", "GEV"]),
        ("특수산업기계", ["ETN", "PH", "EMR", "ITW", "CMI", "ROK", "IR", "DOV", "AME", "OTIS"]),
        ("농기계·중장비", ["CAT", "DE", "PCAR"]),
        ("철도", ["UNP", "CSX", "NSC"]),
        ("통합물류", ["UPS", "FDX"]),
        ("건물자재·설비", ["TT", "JCI", "CARR", "FAST"]),
        ("엔지니어링·건설", ["PWR", "FIX", "J"]),
        ("항공사", ["DAL", "UAL", "LUV", "AAL"]),
        ("전기장비", ["VRT", "BE", "ENPH"]),
        ("폐기물관리", ["WM", "RSG"]),
        ("복합기업", ["MMM", "HON"]),
        ("전문서비스", ["CTAS", "FERG", "URI", "VRSK", "PAYX"]),
    ],
    "xlu": [
        ("전력(규제)", ["NEE", "SO", "DUK", "AEP", "D", "EXC", "XEL", "ED", "PEG", "ETR", "WEC", "ES", "PPL", "CNP", "EIX", "LNT", "FE", "AEE", "DTE", "CMS"]),
        ("복합·독립발전", ["CEG", "VST", "NRG"]),
        ("가스·복합", ["SRE", "ATO"]),
        ("수도", ["AWK"]),
    ],
    "xlre": [
        ("통신·데이터리츠", ["AMT", "EQIX", "DLR", "CCI", "SBAC"]),
        ("산업리츠", ["PLD"]),
        ("리테일리츠", ["SPG", "O", "REG", "KIM"]),
        ("주거리츠", ["WELL", "AVB", "EQR", "INVH", "VICI"]),
        ("오피스·기타리츠", ["PSA", "EXR", "CBRE", "CSGP", "ARE", "VTR"]),
    ],
    "xle": [
        ("석유·가스 통합", ["XOM", "CVX"]),
        ("E&P(탐사·생산)", ["COP", "EOG", "OXY", "DVN", "FANG", "EQT", "CTRA"]),
        ("미드스트림", ["WMB", "KMI", "OKE", "TRGP", "LNG", "TPL"]),
        ("정제·마케팅", ["MPC", "PSX", "VLO"]),
        ("장비·서비스", ["SLB", "HAL", "BKR"]),
    ],
    "xlb": [
        ("특수화학", ["LIN", "SHW", "APD", "ECL", "IFF", "ALB", "DD", "DOW", "LYB"]),
        ("철강", ["NUE", "STLD"]),
        ("금속·광업", ["FCX", "NEM"]),
        ("건축자재", ["VMC", "MLM"]),
        ("포장", ["AMCR", "PKG", "IP", "BALL"]),
        ("농업투입", ["CF", "MOS", "CTVA"]),
    ],
}

# 파생: 섹터→종목(산업 순서대로), 종목→산업
SECTOR_MEMBERS = {k: [t for _ind, ts in v for t in ts] for k, v in TAXONOMY.items()}

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

# 종목 → 하위 산업(Industry) — TAXONOMY에서 파생
INDUSTRY = {t: ind for v in TAXONOMY.values() for ind, ts in v for t in ts}


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
# 섹터 보드 (히트맵/표) — 계층 + 다기간 수익률·상대강도·Δ순위·상태
# --------------------------------------------------------------------------- #
# 선택/스트립용 다기간 수익률(거래일 기준)
PERIOD_DAYS = {
    "1D": 1, "2D": 2, "3D": 3, "4D": 4, "5D": 5, "6D": 6,
    "1W": 5, "2W": 10, "3W": 15,
    "1M": 21, "2M": 42, "3M": 63, "5M": 105, "6M": 126,
    "7M": 147, "8M": 168, "10M": 210, "1Y": 252,
}
STRIP_PERIODS = ["1D", "1W", "2W", "1M", "3M", "6M", "1Y"]  # 프로파일 스트립 순서

_board_cache: dict | None = None
BOARD_TTL = 60  # 초 (로컬 DB 읽기라 저렴)


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g, avg_l = gains / n, losses / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _state(closes: list[float]) -> dict:
    """건전/과열/약세/중립 + 근거(RSI·52주 고가 대비·200일선)."""
    if len(closes) < 30:
        return {"state": "-", "rsi": None, "from_high": None, "vs_ma200": None}
    price = closes[-1]
    hi = max(closes[-252:]) if len(closes) >= 2 else price
    from_high = round((price / hi - 1) * 100, 1) if hi else 0.0
    ma_n = min(len(closes), 200)
    ma200 = sum(closes[-ma_n:]) / ma_n
    vs_ma200 = round((price / ma200 - 1) * 100, 1) if ma200 else 0.0
    rsi = _rsi(closes)
    if price < ma200:
        st = "약세"
    elif (rsi is not None and rsi > 70) or from_high > -3:
        st = "과열"
    elif rsi is not None and 40 <= rsi <= 70:
        st = "건전"
    else:
        st = "중립"
    return {"state": st, "rsi": rsi, "from_high": from_high, "vs_ma200": vs_ma200}


def _score_asof(closes: list[float], offset: int = 0) -> float:
    """offset 거래일 전 시점의 모멘텀 점수(Δ순위용). 0=현재, 5=1주 전."""
    cl = closes[:len(closes) - offset] if offset else closes
    if len(cl) < 2:
        return -999.0
    return _momentum(_ret(cl, _MOM_BACK["r1w"]), _ret(cl, _MOM_BACK["r1m"]),
                     _ret(cl, _MOM_BACK["r3m"]))["score"]


def _sector_universe(cfg: dict):
    etfs = [s.get("etf") for s in cfg["sectors"] if s.get("etf")]
    stocks: list[str] = []
    for s in cfg["sectors"]:
        for ind in s.get("industries", []):
            stocks.extend(ind.get("tickers", []))
    uniq_stocks = list(dict.fromkeys(stocks))
    allsyms = list(dict.fromkeys(["SPY", *etfs, *uniq_stocks]))
    return allsyms, uniq_stocks


def _compute_board(series_map: dict, names: dict, cfg: dict, updated_at: str, source: str) -> dict:
    """closes_map(종가 시계열) → 섹터 보드 데이터. 라이브/소급 공용."""
    spy_score = _score_asof(series_map.get("SPY", []))

    def _sig(sym: str) -> dict | None:
        cl = series_map.get(sym, [])
        if len(cl) < 2:
            return None
        per = {p: _ret(cl, d) for p, d in PERIOD_DAYS.items()}
        mom = _momentum(per["1W"], per["1M"], per["3M"])
        st = _state(cl)
        spark = cl[-63:]
        if len(spark) > 40:
            spark = spark[:: (len(spark) // 40) or 1]
        return {
            "periods": per, "r1w": per["1W"], "r1m": per["1M"], "r3m": per["3M"],
            "score": mom["score"], "accel": mom["accel"],
            "rs": round(mom["score"] - spy_score, 3),
            "state": st["state"], "rsi": st["rsi"],
            "from_high": st["from_high"], "vs_ma200": st["vs_ma200"],
            "_score_1w": _score_asof(cl, 5),
            "price": round(float(cl[-1]), 2), "currency": "USD",
            "series": [round(c, 4) for c in spark],
        }

    sectors_out = []
    for s in cfg["sectors"]:
        esig = _sig(s.get("etf")) if s.get("etf") else None
        inds_out = []
        for ind in s.get("industries", []):
            toks = []
            for t in ind.get("tickers", []):
                g = _sig(t)
                toks.append({"code": t, "name": names.get(t, ""), "held": t in USER_HELD, **(g or {"no_data": True})})
            inds_out.append({"name": ind.get("name", "기타"), "stocks": toks})
        sectors_out.append({"key": s["key"], "name": s["name"], "etf": s.get("etf"),
                            "sig": esig, "industries": inds_out})

    valid = [(so["key"], so["sig"]) for so in sectors_out if so["sig"]]
    now_r = {k: i + 1 for i, (k, g) in enumerate(sorted(valid, key=lambda x: x[1]["score"], reverse=True))}
    old_r = {k: i + 1 for i, (k, g) in enumerate(sorted(valid, key=lambda x: x[1]["_score_1w"], reverse=True))}
    for so in sectors_out:
        if so["sig"]:
            so["rank"] = now_r.get(so["key"])
            so["drank"] = old_r.get(so["key"], 0) - now_r.get(so["key"], 0)

    for so in sectors_out:
        toks = [t for ind in so["industries"] for t in ind["stocks"] if "score" in t]
        nr = {t["code"]: i + 1 for i, t in enumerate(sorted(toks, key=lambda t: t["score"], reverse=True))}
        orr = {t["code"]: i + 1 for i, t in enumerate(sorted(toks, key=lambda t: t.get("_score_1w", -999), reverse=True))}
        for ind in so["industries"]:
            for t in ind["stocks"]:
                if "score" in t:
                    t["rank"] = nr.get(t["code"])
                    t["drank"] = orr.get(t["code"], 0) - nr.get(t["code"], 0)

    return {
        "sectors": sectors_out,
        "strip_periods": STRIP_PERIODS,
        "all_periods": list(PERIOD_DAYS.keys()),
        "updated_at": updated_at,
        "source": source,
    }


async def get_sector_board() -> dict:
    """전체 섹터▸산업▸종목 + 신호(다기간 수익률·상대강도·Δ순위·상태). 히트맵/표용."""
    global _board_cache
    if _board_cache and time.time() - _board_cache["_ts"] < BOARD_TTL:
        return _board_cache["data"]

    cfg = sector_config.get_config()
    allsyms, uniq_stocks = _sector_universe(cfg)
    series_map = await asyncio.to_thread(candle_store.closes_bulk, allsyms, "1dv")
    names = await asyncio.to_thread(candle_store.names_bulk, uniq_stocks)
    data = _compute_board(series_map, names, cfg,
                          datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), "Yahoo (약 15분 지연)")
    _board_cache = {"_ts": time.time(), "data": data}
    return data


def board_asof(series_ts_map: dict, names: dict, cfg: dict, cutoff_ts: int, label: str) -> dict:
    """cutoff_ts(해당 날짜 말) 이전 종가만으로 보드 재계산(소급용)."""
    closes = {sym: [c for ts, c in lst if ts <= cutoff_ts] for sym, lst in series_ts_map.items()}
    return _compute_board(closes, names, cfg, label, f"Yahoo 소급({label})")


# --------------------------------------------------------------------------- #
# 섹터 리포트 — 보드 신호로 '보유 추이 + 추천'을 자동 생성
# --------------------------------------------------------------------------- #
def build_sector_report(board: dict, held_codes: set[str]) -> dict:
    """섹터 보드 신호 → 섹터 리더보드·보유 종목 분류·추천 후보·요약.

    분류 규칙(모두 보드의 신호값 기준):
    - 추세훼손: 200일선 아래(vs200<0) 또는 상태 '약세'
    - 반등주도: 가속>0 & Δ순위>0 & 200일선 위
    - 과매도·관망: 그 외(추세는 유지, 반등 미확정)
    추천: 미보유 & RS>0 & 가속>0 & Δ순위≥0 & 상태 '건전'(과열·약세 제외).
    """
    # 한국 주식(숫자 코드)은 미국 섹터 유니버스 밖이라 신호 계산 불가 → 리포트에서 제외
    held_codes = {c for c in held_codes if not str(c).isdigit()}
    sectors = board.get("sectors", [])
    # 종목코드 → 신호(+섹터명)
    bycode: dict[str, dict] = {}
    for s in sectors:
        for ind in s.get("industries", []):
            for t in ind.get("stocks", []):
                bycode[t["code"]] = {**t, "sector": s.get("name"), "sector_key": s.get("key")}

    # 1) 섹터 리더보드(score순)
    secs = [s for s in sectors if (s.get("sig") or {}).get("score") is not None]
    secs.sort(key=lambda s: -(s["sig"].get("score") or -999))
    leaderboard = [{
        "name": s.get("name"), "key": s.get("key"), "rank": s.get("rank"), "drank": s.get("drank"),
        "rs": s["sig"].get("rs"), "accel": s["sig"].get("accel"), "state": s["sig"].get("state"),
        "rsi": s["sig"].get("rsi"), "r1w": s["sig"].get("r1w"), "r1m": s["sig"].get("r1m"),
    } for s in secs]

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # 2) 보유 종목 분류
    held_rows = []
    for code in sorted(held_codes):
        t = bycode.get(code)
        if not t or t.get("score") is None or t.get("no_data"):
            held_rows.append({"code": code, "name": (t or {}).get("name", ""),
                              "group": "no_data", "reason": "섹터 유니버스 밖(신호 없음)"})
            continue
        vs200, accel, drank = _f(t.get("vs_ma200")), _f(t.get("accel")), _f(t.get("drank"))
        rsi, state, rs = t.get("rsi"), t.get("state"), _f(t.get("rs"))
        if vs200 < 0 or state == "약세":
            grp = "broken"
            reason = f"200일선 {vs200:+.0f}%(이탈)·가속 {accel:+.1f}·{state} — 추세 훼손 경계"
        elif accel > 0 and drank > 0 and vs200 >= 0:
            grp = "turning"
            reason = f"가속 {accel:+.1f}·Δ순위 {int(drank):+d}·200일선 {vs200:+.0f}% — 돌아서는 중"
        else:
            grp = "oversold"
            reason = f"RSI {rsi}·Δ순위 {int(drank):+d}·200일선 {vs200:+.0f}% — 과매도/관망"
        held_rows.append({
            "code": code, "name": t.get("name", ""), "sector": t.get("sector"),
            "rs": t.get("rs"), "accel": t.get("accel"), "drank": t.get("drank"),
            "rsi": rsi, "state": state, "vs200": t.get("vs_ma200"), "from_high": t.get("from_high"),
            "r1w": t.get("r1w"), "r1m": t.get("r1m"), "r3m": t.get("r3m"),
            "group": grp, "reason": reason,
        })
    grp_order = {"turning": 0, "oversold": 1, "broken": 2, "no_data": 3}
    held_rows.sort(key=lambda r: grp_order.get(r["group"], 9))

    # 3) 추천 후보(미보유·RS>0·가속>0·Δ≥0·건전)
    cand = [t for code, t in bycode.items()
            if t.get("score") is not None and not t.get("no_data") and code not in held_codes
            and _f(t.get("rs")) > 0 and _f(t.get("accel")) > 0 and _f(t.get("drank")) >= 0
            and t.get("state") == "건전"]
    cand.sort(key=lambda t: -(_f(t.get("rs")) + _f(t.get("accel")) + 0.1 * _f(t.get("drank"))))
    recs = [{
        "code": t["code"], "name": t.get("name", ""), "sector": t.get("sector"),
        "rs": t.get("rs"), "accel": t.get("accel"), "drank": t.get("drank"),
        "rsi": t.get("rsi"), "r1w": t.get("r1w"), "r1m": t.get("r1m"),
    } for t in cand[:8]]

    # 4) 요약 노트
    notes = []
    xlk = next((s for s in sectors if s.get("key") == "xlk"), None)
    if xlk and (xlk.get("sig") or {}).get("score") is not None:
        g = xlk["sig"]
        if _f(g.get("accel")) > 0 and abs(_f(g.get("rs"))) < 1:
            notes.append(f"기술(XLK): RS {g.get('rs')} · 가속 {_f(g.get('accel')):+.1f} · Δ {xlk.get('drank')} "
                         f"— 과매도 반등 변곡 관찰. RS가 0을 넘어 양전환하면 섹터 리더십 복귀 트리거.")
    broken = [r["code"] for r in held_rows if r["group"] == "broken"]
    if broken:
        notes.append("추세 훼손 경계(보유): " + ", ".join(broken) + " — 200일선 아래/약세. 점검 1순위.")
    turning = [r["code"] for r in held_rows if r["group"] == "turning"]
    if turning:
        notes.append("돌아서는 중(보유): " + ", ".join(turning) + " — 가속·순위 상승.")
    # 보유 섹터 집중
    held_secs = [bycode[c]["sector"] for c in held_codes if c in bycode and bycode[c].get("sector")]
    if held_secs:
        from collections import Counter
        top_sec, cnt = Counter(held_secs).most_common(1)[0]
        if cnt >= max(2, len(held_secs) // 2):
            notes.append(f"집중도: 보유가 '{top_sec}'에 몰려 있음({cnt}종목) — 분산 관점 점검 권장.")

    return {
        "as_of": board.get("updated_at"), "source": board.get("source"),
        "leaderboard": leaderboard,
        "held": held_rows,
        "recommendations": recs,
        "notes": notes,
        "disclaimer": "기술적 신호 기반 참고용이며 매매 권유가 아닙니다. 투자 판단·책임은 본인에게 있습니다.",
    }


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
             "1W": 86400, "2W": 86400, "3W": 86400, "4W": 86400, "1M": 86400, "3M": 86400,
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
    "2W": ("1h", "1mo", "%m/%d"),     # 2주(60분봉, ~2주 필터)
    "3W": ("1h", "2mo", "%m/%d"),     # 3주(60분봉, ~3주 필터)
    "4W": ("1h", "2mo", "%m/%d"),     # 4주(60분봉, ~4주 필터)
    "1M": ("1d", "1mo", "%m/%d"),     # 1개월(일봉)
    "3M": ("1d", "3mo", "%m/%d"),     # 3개월(일봉)
    "6M": ("1d", "6mo", "%m/%d"),     # 6개월(일봉)
    "1Y": ("1d", "1y", "%y/%m"),      # 1년(일봉)
    "5Y": ("1wk", "5y", "%y/%m"),     # 5년(주봉)
}
# 장중 기간은 최근 N일로 필터
STOCK_DAY_FILTER = {"1D": 1, "2D": 2, "3D": 3, "4D": 4, "5D": 5, "2W": 14, "3W": 21, "4W": 28}


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
