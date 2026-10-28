"""보유종목 차트 서비스.

우선순위: 로컬 SQLite 누적본 → (오래되면) 메리츠 라이브 조회·병합 → 그래도 없으면 Yahoo 폴백.
- 1D~5D : 5분봉(분봉 누적본). 메리츠는 한 번에 40봉(≈3.3h)이라 폴러로 쌓은 로컬본을 사용.
- 1W~5Y : 일봉(메리츠 from/to 로 완전 적재된 누적본).
프론트가 쓰는 형식({series, times, change, change_pct, value})으로 반환(market._period 재사용).
"""
from __future__ import annotations

import bisect
import time
from datetime import datetime, timedelta

import market
import candle_store as store
import meritz_client as meritz
from config import meritz_settings
from meritz_client import KST

# period -> (interval, lookback_days, 시각 라벨 포맷)
PERIOD_PLAN = {
    "1D": ("5m", 1, "%H:%M"),
    "2D": ("5m", 2, "%m/%d"),
    "3D": ("5m", 3, "%m/%d"),
    "4D": ("5m", 4, "%m/%d"),
    "5D": ("5m", 5, "%m/%d"),
    "1W": ("1d", 8, "%m/%d"),
    "2W": ("1d", 16, "%m/%d"),
    "3W": ("1d", 23, "%m/%d"),
    "4W": ("1d", 30, "%m/%d"),
    "1M": ("1d", 33, "%m/%d"),
    "3M": ("1d", 95, "%m/%d"),
    "6M": ("1d", 190, "%m/%d"),
    "1Y": ("1d", 372, "%y/%m"),
    "5Y": ("1d", 1850, "%y/%m"),
}

# 스토어가 이보다 오래됐으면 라이브로 갱신
FRESH_5M = 900       # 15분
FRESH_1D = 2 * 86400  # 2일


async def _refresh(symbol: str, exch: str, mkt: str, interval: str, start: int) -> None:
    """필요 시 메리츠 라이브로 받아 스토어에 병합."""
    if not meritz_settings.enabled:
        return
    now = int(time.time())
    try:
        if interval in ("5m", "1m"):
            sec = 60 if interval == "1m" else 300
            fresh = 90 if interval == "1m" else FRESH_5M   # 1분봉은 90초 내면 생략
            rows = store.get_candles(symbol, interval, start_ts=now - fresh)
            if rows:
                return  # 최근 데이터 있으면 생략(폴러가 관리)
            bars = (await meritz.overseas_candles(symbol, exch, "minutes", sec)
                    if mkt == "overseas"
                    else await meritz.domestic_candles(symbol, "minutes", sec))
            store.upsert_candles(symbol, interval, bars)
        else:  # 1d
            latest = store.get_candles(symbol, "1d", start_ts=now - FRESH_1D)
            have = store.has_candles(symbol, "1d", start_ts=start)
            if latest and have >= 2:
                return  # 최신 + 창 데이터 충분
            frm = datetime.fromtimestamp(start, KST).strftime("%Y%m%d")
            to = datetime.now(KST).strftime("%Y%m%d")
            bars = (await meritz.overseas_candles(symbol, exch, "days", from_=frm, to=to)
                    if mkt == "overseas"
                    else await meritz.domestic_candles(symbol, "days", from_=frm, to=to))
            store.upsert_candles(symbol, "1d", bars)
    except meritz.MeritzError:
        pass  # 라이브 실패 → 스토어/야후로 진행


def _recent_session_start(mkt: str) -> int:
    """가장 최근 '장중 세션 시작'(KST)의 epoch. 그 시각 이전이면 전날.

    국내(NXT 포함)는 08:00, 해외(주간거래)는 09:00 시작.
    """
    hour = 8 if mkt == "domestic" else 9
    now = datetime.now(KST)
    anchor = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor -= timedelta(days=1)
    return int(anchor.timestamp())


def _intraday_interval(span_sec: int) -> int:
    """세션 시작~현재를 메리츠 40봉 한도로 덮는 가장 촘촘한 간격(초).

    각 간격이 40봉으로 담을 수 있는 최대 구간(간격×40)에 맞춰 선택한다.
    국내는 08:00 시작이라 5분봉 한도(3시간20분)를 조금만 넘겨도 15분봉으로
    떨어지곤 했는데, 정확히 40봉 기준으로 계산해 최대한 5분봉을 유지한다.
    """
    for iv in (300, 900, 1800, 3600):   # 5·15·30·60분
        if span_sec <= 40 * iv:
            return iv
    return 3600


# 시간 단위(Nh) 기간 → 시간 수
HOUR_MAP = {"1h": 1, "2h": 2, "3h": 3, "4h": 4, "6h": 6, "8h": 8, "10h": 10, "12h": 12}
# 멀티데이(2D~5D) 기간 → 일 수
MULTIDAY_DAYS = {"2D": 2, "3D": 3, "4D": 4, "5D": 5, "1W": 7, "2W": 14, "3W": 21, "4W": 28}


async def _chart_intraday(symbol: str, exch: str, mkt: str, anchor: int, period_label: str,
                          live_price: float | None = None) -> dict | None:
    """장중(분봉) 차트. anchor(시작 epoch)부터 현재까지. 간격 자동 조절."""
    span = int(time.time()) - anchor
    interval_sec = _intraday_interval(span)
    try:
        bars = (await meritz.overseas_candles(symbol, exch, "minutes", interval_sec)
                if mkt == "overseas"
                else await meritz.domestic_candles(symbol, "minutes", interval_sec))
    except meritz.MeritzError:
        return None
    # 메리츠 분봉은 '봉 종료시각' 기준(09:00~09:15 → "09:15"). '봉 시작시각'으로 당겨
    # 표기하면 첫 봉이 정각(예: 09:00)으로 시작한다.
    pts = [(ts - interval_sec, c) for ts, o, h, l, c, v in bars
           if c and (ts - interval_sec) >= anchor]
    if len(pts) < 2:
        return None
    # 현재가는 잔고의 공식 현재가(psnt_prc)를 우선 사용한다. 마지막 분봉이 거래 공백으로
    # 전일종가로 캐리포워드되면 수익율이 0으로 튀는데, 공식 현재가는 그 흔들림이 없다.
    if live_price and live_price > 0:
        price = float(live_price)
        now_ts = int(time.time())
        if pts[-1][0] is not None and now_ts > pts[-1][0]:
            pts = pts + [(now_ts, price)]   # 선이 실제 현재가까지 닿도록 꼬리 추가
        else:
            pts[-1] = (pts[-1][0], price)
    else:
        price = pts[-1][1]
    p = market._period(pts, price, "%H:%M")
    return {
        "mode": "live", "code": symbol, "period": period_label,
        "currency": "USD" if mkt == "overseas" else "KRW",
        "value": round(price, 2), "source": "meritz",
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), **p,
    }


def _prev_session_close(symbol: str, mkt: str = "overseas") -> float | None:
    """현재 장중 세션 시작의 '이전 거래일' 일봉 종가 = 전일 종가.

    KST 자정이 아니라 세션 시작(국내 08:00/해외 09:00) '날짜'를 기준으로 비교한다.
    미국 야간장이 KST 자정을 넘겨 진행돼도 세션 도중에 기준이 바뀌지 않고,
    매일 세션 시작 시각에만 리셋된다. (일봉=정규장 종가 기준)
    """
    anchor_date = datetime.fromtimestamp(_recent_session_start(mkt), KST).date()
    for ts, close, *_ in reversed(store.get_candles(symbol, "1d")):
        if close and datetime.fromtimestamp(ts, KST).date() < anchor_date:
            return close
    return None


def _prev_day_change(symbol: str, mkt: str = "overseas") -> tuple[float | None, float | None]:
    """직전 거래일(전날) '그 자체'의 등락율 = (전일종가 - 전전일종가) / 전전일종가 * 100.

    오늘 세션(해외 09:00/국내 08:00 KST) 시작 이전의 일봉 종가 2개(최근 2거래일)를
    종가-종가로 비교한다. 라이브 현재가와 무관한 '전날의 등락율'. 일봉 2개가 없으면 (None, None).
    반환: (등락율%, 전일종가)
    """
    anchor_date = datetime.fromtimestamp(_recent_session_start(mkt), KST).date()
    closes: list[float] = []
    seen: set = set()   # 같은 날짜 중복(다중 소스 ts) 제거 → 거래일 단위 2개
    for ts, close, *_ in reversed(store.get_candles(symbol, "1d")):
        if not close:
            continue
        d = datetime.fromtimestamp(ts, KST).date()
        if d >= anchor_date or d in seen:
            continue
        seen.add(d)
        closes.append(close)
        if len(closes) == 2:
            break
    if len(closes) < 2 or not closes[1]:
        return None, None
    prev_close, prev_prev = closes[0], closes[1]
    pct = round((prev_close - prev_prev) / prev_prev * 100, 2)
    return pct, round(prev_close, 4)


def _apply_prev_close(res: dict, symbol: str, mkt: str = "overseas",
                      prev_close: float | None = None) -> dict:
    """장중(1h~1D) 등락폭을 전일 정규장 마감 종가 기준으로 덮어쓴다.

    prev_close(잔고의 공식 전일종가)가 주어지면 그것을 쓴다. 메리츠 일봉 저장소의
    종가가 공식 전일종가와 어긋나는 경우가 있어(앱과 불일치), 잔고값을 우선한다.
    """
    pc = prev_close if (prev_close and prev_close > 0) else _prev_session_close(symbol, mkt)
    price = res.get("value")
    if pc and price:
        res["change"] = round(price - pc, 4)
        res["change_pct"] = round((price - pc) / pc * 100, 2)
        res["prev_close"] = round(pc, 4)
        res["baseline"] = "prev_close"
    return res


BAR_SEC = {"1m": 60, "5m": 300, "15m": 900}


def _resample(pts: list, bar_sec: int) -> list:
    """(ts, close) 시계열을 bar_sec 버킷으로 리샘플(각 버킷의 마지막 종가)."""
    if bar_sec <= 60:
        return pts
    buckets: dict[int, tuple] = {}
    for ts, c in pts:
        buckets[ts // bar_sec] = (ts, c)   # 같은 버킷이면 마지막 봉이 대표
    return [buckets[b] for b in sorted(buckets)]


async def _chart_store_window(symbol: str, exch: str, mkt: str, anchor: int,
                              period_label: str, live_price: float | None = None,
                              bar: str = "5m") -> dict | None:
    """anchor~현재 구간을 로컬 분봉 누적본으로 그린다(40봉 한도에 안 묶임).

    1분봉은 추가/기동 이후로만 누적되므로, 이른 구간은 5분봉으로 채우고 1분봉이 있는
    최근 구간만 1분 해상도로 병합한다. 그 뒤 선택한 봉 간격(bar: 1m/5m/15m)으로 리샘플.
    둘 다 부족하면(3봉 미만) None → 상위에서 라이브 40봉으로 폴백.
    """
    now = int(time.time())
    await _refresh(symbol, exch, mkt, "1m", anchor)
    await _refresh(symbol, exch, mkt, "5m", anchor)
    p1 = [(ts, c) for ts, c, *_ in store.get_candles(symbol, "1m", start_ts=anchor) if c]
    p5 = [(ts, c) for ts, c, *_ in store.get_candles(symbol, "5m", start_ts=anchor) if c]
    if p1:
        cut = p1[0][0]
        pts = [(ts, c) for ts, c in p5 if ts < cut] + p1   # 5분봉(이른) + 1분봉(최근)
    else:
        pts = p5
    if len(pts) < 3:
        return None
    # 현재가(공식값)를 최신 점에 반영 — 고점/저점 판정에도 포함
    price = pts[-1][1]
    if live_price and live_price > 0:
        price = float(live_price)
        if pts[-1][0] < now:
            pts = pts + [(now, price)]
        else:
            pts[-1] = (pts[-1][0], price)
    # 리샘플 전 '최정밀' 시계열에서 진짜 고점·저점을 구해둔다(봉 크기와 무관하게 동일 시점)
    hi = max(pts, key=lambda x: x[1])
    lo = min(pts, key=lambda x: x[1])
    last = pts[-1]
    disp = _resample(pts, BAR_SEC.get(bar, 300))           # 선택 봉 간격으로
    if len(disp) > 400:
        disp = disp[:: (len(disp) // 400) or 1]
    # 리샘플·다운샘플로 사라졌을 수 있는 고점/저점/현재점을 같은 시점에 강제 포함
    present = {t for t, _ in disp}
    for ex in (hi, lo, last):
        if ex[0] not in present:
            disp.append(ex)
            present.add(ex[0])
    disp.sort(key=lambda x: x[0])
    if len(disp) < 2:
        return None
    p = market._period(disp, price, "%H:%M")
    return {
        "mode": "live", "code": symbol, "period": period_label,
        "currency": "USD" if mkt == "overseas" else "KRW",
        "value": round(price, 2), "source": "meritz",
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), **p,
    }


async def _chart_multiday(symbol: str, exch: str, mkt: str, period: str) -> dict | None:
    """2D~5D: 최근 N거래일 일봉(완전) + 오늘 누적 5분봉을 이어붙여 그린다.

    누적 초기엔 일봉으로 멀티데이 추세를 보여주고, 5분봉이 쌓일수록 최근 구간이 촘촘해진다.
    """
    days = MULTIDAY_DAYS[period]
    now = int(time.time())
    start = now - days * 86400

    # 최근 5분봉 신선화 + 일봉 보장(둘 다 대개 스토어 적중 → 추가 호출 없음)
    await _refresh(symbol, exch, mkt, "5m", start)
    await _refresh(symbol, exch, mkt, "1d", now - 40 * 86400)

    pts_5m = [(ts, c) for ts, c, *_ in store.get_candles(symbol, "5m", start_ts=start) if c]
    cutoff = pts_5m[0][0] if pts_5m else now
    # 5분봉이 시작되기 전까지의 일봉 중 최근 N개
    daily = [(ts, c) for ts, c, *_ in store.get_candles(symbol, "1d") if c and ts < cutoff][-days:]

    # 과거 구간이 일봉뿐(관심종목 신규 추가 등 누적본 부족)이면 Yahoo 5분봉으로 메움.
    # 보유종목은 이미 5분봉이 과거까지 쌓여 cutoff가 과거라 Yahoo 구간이 비어 영향 없음.
    pre = daily
    try:
        y = await market.get_intraday_pts(symbol)  # 5m/5d
        y_pre = [(t, c) for t, c in y if t is not None and start <= t < cutoff]
        if len(y_pre) > len(daily):
            pre = y_pre
    except Exception:
        pass

    combined = pre + pts_5m
    if len(combined) < 2:
        return None  # → 상위에서 Yahoo 폴백

    if len(combined) > 400:
        combined = combined[:: (len(combined) // 400) or 1]
    price = combined[-1][1]
    p = market._period(combined, price, "%m/%d")
    return {
        "mode": "live", "code": symbol, "period": period,
        "currency": "USD" if mkt == "overseas" else "KRW",
        "value": round(price, 2), "source": "meritz",
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), **p,
    }


# 200일선을 '곡선'으로 그릴 일봉 장기 기간(그 외는 수평 기준선=현재 MA 값)
MA_CURVE_PERIODS = {"1M", "3M", "6M", "1Y", "5Y"}


def _daily_closes(symbol: str) -> list[tuple]:
    return [(ts, c) for ts, c, *_ in store.get_candles(symbol, "1d") if c]


def _ma200_now(symbol: str) -> float | None:
    cl = [c for _t, c in _daily_closes(symbol)]
    if len(cl) < 20:
        return None
    w = cl[-200:]
    return round(sum(w) / len(w), 4)


def _ma200_series(symbol: str, ts_list: list[int]) -> list | None:
    """차트 각 지점(ts)에서의 200일 이동평균(일봉 종가 기준). 곡선용."""
    daily = _daily_closes(symbol)
    if len(daily) < 20:
        return None
    dts = [t for t, _ in daily]
    dcl = [c for _, c in daily]
    out = []
    for ts in ts_list:
        k = bisect.bisect_right(dts, ts)       # ts 이하 일봉 개수
        w = dcl[max(0, k - 200):k]
        out.append(round(sum(w) / len(w), 4) if w else None)
    return out


def _attach_ma200(res: dict, symbol: str, period: str | None = None,
                  pts: list | None = None) -> dict:
    """결과에 200일선 부착. 장기 일봉 기간은 곡선(ma200_series), 그 외는 수평값(ma200)."""
    res["ma200"] = _ma200_now(symbol)
    if pts and period in MA_CURVE_PERIODS:
        res["ma200_series"] = _ma200_series(symbol, [t for t, _ in pts])
    return res


async def holding_chart(symbol: str, exch: str, mkt: str, period: str,
                        live_price: float | None = None,
                        prev_close: float | None = None,
                        bar: str = "5m") -> dict:
    # 수익률 기준: 전 기간 전일 종가(_apply_prev_close) — 통합/개별 공통

    if meritz_settings.enabled:
        # 1D: 세션 시작 고정(국내 08:00/해외 09:00) — 1분봉 누적본(해상도 균일)
        if period == "1D":
            anc = _recent_session_start(mkt)
            res = await _chart_store_window(symbol, exch, mkt, anc, "1D", live_price, bar)
            if res is None:   # 저장소 부족 → 라이브 40봉
                res = await _chart_intraday(symbol, exch, mkt, anc, "1D", live_price)
            if res is not None:
                return _attach_ma200(_apply_prev_close(res, symbol, mkt, prev_close), symbol)
        # Nh: 최근 N시간 롤링 윈도우 — 1분봉 누적본(+5분봉 이른 구간), 부족하면 라이브 40봉
        elif period in HOUR_MAP:
            anchor = int(time.time()) - HOUR_MAP[period] * 3600
            res = await _chart_store_window(symbol, exch, mkt, anchor, period, live_price, bar)
            if res is None:
                res = await _chart_intraday(symbol, exch, mkt, anchor, period, live_price)
            if res is not None:
                return _attach_ma200(_apply_prev_close(res, symbol, mkt, prev_close), symbol)

        # 2D~5D: 일봉(완전) + 오늘 5분봉 결합
        elif period in MULTIDAY_DAYS:
            res = await _chart_multiday(symbol, exch, mkt, period)
            if res is not None:
                return _attach_ma200(_apply_prev_close(res, symbol, mkt, prev_close), symbol)

    interval, days, fmt = PERIOD_PLAN.get(period, PERIOD_PLAN["1D"])
    now = int(time.time())
    start = now - days * 86400

    await _refresh(symbol, exch, mkt, interval, start)

    rows = store.get_candles(symbol, interval, start_ts=start)
    pts = [(ts, close) for ts, close, *_ in rows if close]

    # 스토어에 충분치 않으면 Yahoo 폴백(해외=티커, 국내=6자리→.KS)
    if len(pts) < 2:
        res = await market.get_stock_chart(symbol, period)
        res = _apply_prev_close(res, symbol, mkt, prev_close)   # 전 기간 전일 종가 기준
        return _attach_ma200(res, symbol)

    if len(pts) > 400:
        pts = pts[:: (len(pts) // 400) or 1]

    price = pts[-1][1]
    p = market._period(pts, price, fmt)
    res = {
        "mode": "live",
        "code": symbol,
        "period": period,
        "currency": "USD" if mkt == "overseas" else "KRW",
        "value": round(price, 2),
        "source": "meritz",
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        **p,
    }
    res = _apply_prev_close(res, symbol, mkt, prev_close)   # 전 기간 전일 종가 기준
    return _attach_ma200(res, symbol, period, pts)
