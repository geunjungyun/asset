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
        if interval == "5m":
            rows = store.get_candles(symbol, "5m", start_ts=now - FRESH_5M)
            if rows:
                return  # 최근 15분 내 데이터 있으면 생략(폴러가 관리)
            bars = (await meritz.overseas_candles(symbol, exch, "minutes", 300)
                    if mkt == "overseas"
                    else await meritz.domestic_candles(symbol, "minutes", 300))
            store.upsert_candles(symbol, "5m", bars)
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


async def _chart_1d_store(symbol: str, exch: str, mkt: str,
                          live_price: float | None = None) -> dict | None:
    """오늘 세션 전체를 5분봉 누적본으로 그린다(메리츠 40봉 한도에 묶이지 않음).

    국내 세션(08:00~15:30)은 5분봉 90개라 라이브 40봉으론 다 못 담아 15분봉으로
    성겼는데, 로컬 5분봉 저장소를 쓰면 하루 종일 5분 해상도를 유지한다.
    저장소가 부족하면(서버가 세션 내내 안 떠 있었음) None → 라이브 40봉으로 폴백.
    """
    anchor = _recent_session_start(mkt)
    now = int(time.time())
    await _refresh(symbol, exch, mkt, "5m", anchor)
    pts = [(ts, c) for ts, c, *_ in store.get_candles(symbol, "5m", start_ts=anchor) if c]
    if len(pts) < 3:
        return None
    price = pts[-1][1]
    if live_price and live_price > 0:   # 현재가는 공식값으로, 선 끝을 현재가까지
        price = float(live_price)
        if pts[-1][0] < now:
            pts = pts + [(now, price)]
        else:
            pts[-1] = (pts[-1][0], price)
    if len(pts) > 400:
        pts = pts[:: (len(pts) // 400) or 1]
    p = market._period(pts, price, "%H:%M")
    return {
        "mode": "live", "code": symbol, "period": "1D",
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

    combined = daily + pts_5m
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
                        prev_close: float | None = None) -> dict:
    is_intraday = period == "1D" or period in HOUR_MAP  # 1h~1D 는 전일종가 기준 등락폭

    if meritz_settings.enabled:
        # 1D: 세션 시작 고정(국내 08:00/해외 09:00)
        if period == "1D":
            # 오늘 세션 전체를 5분봉 누적본으로(해상도 균일). 부족하면 라이브 40봉 폴백.
            res = await _chart_1d_store(symbol, exch, mkt, live_price)
            if res is None:
                res = await _chart_intraday(symbol, exch, mkt, _recent_session_start(mkt), "1D", live_price)
            if res is not None:
                return _attach_ma200(_apply_prev_close(res, symbol, mkt, prev_close), symbol)
        # Nh: 최근 N시간 롤링 윈도우
        elif period in HOUR_MAP:
            anchor = int(time.time()) - HOUR_MAP[period] * 3600
            res = await _chart_intraday(symbol, exch, mkt, anchor, period, live_price)
            if res is not None:
                return _attach_ma200(_apply_prev_close(res, symbol, mkt, prev_close), symbol)

        # 2D~5D: 일봉(완전) + 오늘 5분봉 결합
        elif period in MULTIDAY_DAYS:
            res = await _chart_multiday(symbol, exch, mkt, period)
            if res is not None:
                return _attach_ma200(res, symbol)

    interval, days, fmt = PERIOD_PLAN.get(period, PERIOD_PLAN["1D"])
    now = int(time.time())
    start = now - days * 86400

    await _refresh(symbol, exch, mkt, interval, start)

    rows = store.get_candles(symbol, interval, start_ts=start)
    pts = [(ts, close) for ts, close, *_ in rows if close]

    # 스토어에 충분치 않으면 Yahoo 폴백(해외=티커, 국내=6자리→.KS)
    if len(pts) < 2:
        res = await market.get_stock_chart(symbol, period)
        res = _apply_prev_close(res, symbol, mkt, prev_close) if is_intraday else res
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
    res = _apply_prev_close(res, symbol, mkt, prev_close) if is_intraday else res
    return _attach_ma200(res, symbol, period, pts)
