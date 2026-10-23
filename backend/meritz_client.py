"""메리츠증권 Open API 클라이언트 (해외주식 계좌).

공식 저장소(github.com/meritz-securities/open-api)의 스펙을 그대로 구현한다.
- OAuth2 토큰: POST /oauth2/token (form, grant_type=client_credentials, scope=login) + 파일 캐시
- 필수 헤더: authorization(Bearer), mac_address(호출 단말 MAC 12자리) — 없으면 요청 거부
- 해외주식 잔고: GET /accounts/v1/me/overseas/holdings  (파라미터 없음, 앱키가 계좌 바인딩)
- 해외 외화예수금: GET /accounts/v1/me/overseas/currencies

응답(data[])을 프론트가 쓰는 형태(kis_client.mock_balance와 동일)로 정규화한다.
자격증명이 없으면 호출하지 않는다(상위에서 mock 폴백).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone, timedelta

import httpx

from config import BASE_DIR, meritz_settings

KST = timezone(timedelta(hours=9))

TOKEN_CACHE_FILE = BASE_DIR / ".meritz_token_cache.json"
TOKEN_SAFETY_MARGIN = 60  # 만료 60초 전 재발급

HOLDINGS_PATH = "/accounts/v1/me/overseas/holdings"
CURRENCIES_PATH = "/accounts/v1/me/overseas/currencies"
DOMESTIC_HOLDINGS_PATH = "/accounts/v1/me/holdings"
DOMESTIC_DEPOSIT_PATH = "/accounts/v1/me/deposit"
TRANSACTIONS_PATH = "/accounts/v1/me/transactions"


class MeritzError(Exception):
    """메리츠 API 호출 실패."""


def _mac_address() -> str:
    """호출 단말의 MAC 주소(구분자 없는 12자리 16진수). 메리츠 게이트웨이 필수 헤더."""
    return f"{uuid.getnode():012x}"


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# 토큰 관리 (파일 캐시)
# --------------------------------------------------------------------------- #
def _read_cached_token() -> str | None:
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("base_url") != meritz_settings.base_url:
        return None
    if data.get("expires_at", 0) - TOKEN_SAFETY_MARGIN <= time.time():
        return None
    return data.get("access_token")


def _write_cached_token(access_token: str, expires_in: int) -> None:
    payload = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in),
        "base_url": meritz_settings.base_url,
        "issued_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        TOKEN_CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _invalidate_token() -> None:
    """캐시 토큰을 폐기(메리츠가 EGW00121로 거부 → 강제 재발급 유도)."""
    try:
        TOKEN_CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


async def get_access_token(force: bool = False) -> str:
    if not force:
        cached = _read_cached_token()
        if cached:
            return cached

    url = f"{meritz_settings.base_url}/oauth2/token"
    form = {
        "grant_type": "client_credentials",
        "client_id": meritz_settings.app_key,
        "client_secret": meritz_settings.app_secret,
        "scope": "login",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=form)
    except httpx.HTTPError as exc:
        raise MeritzError(f"토큰 요청 네트워크 오류: {exc}") from exc
    if resp.status_code != 200:
        raise MeritzError(f"토큰 발급 실패 ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise MeritzError(f"토큰 응답에 access_token 없음: {data}")
    _write_cached_token(token, data.get("expires_in", 43200))
    return token


# --------------------------------------------------------------------------- #
# API 호출
# --------------------------------------------------------------------------- #
async def _get(path: str, params: dict | None = None) -> dict:
    """GET 호출 후 JSON 반환. data 가 없고 오류 응답이면 MeritzError."""
    token = await get_access_token()
    headers = {
        "authorization": f"Bearer {token}",
        "mac_address": _mac_address(),
    }
    url = f"{meritz_settings.base_url}{path}"
    # 초당 호출 한도(EGW00200)는 재시도 가능 → 최대 2회
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise MeritzError(f"요청 네트워크 오류({path}): {exc}") from exc

        # 메리츠는 업무오류도 HTTP 500으로 내려오므로 상태코드만으로 판단하지 않는다.
        try:
            body = resp.json()
        except ValueError:
            raise MeritzError(f"응답 파싱 실패({path}, HTTP {resp.status_code}): {resp.text[:300]}")

        if isinstance(body.get("data"), (list, dict)):
            return body  # 정상(list=목록, dict=단건, 빈 리스트=자료 없음)
        rsp_cd = body.get("rsp_cd")
        msg = body.get("rsp_msg") or body.get("rsp_sub_msg") or body.get("error") or resp.text[:200]
        rate_limited = "EGW00200" in str(rsp_cd) or "EGW00200" in str(body) or resp.status_code == 429
        # 토큰 무효(EGW00121 등): 캐시 폐기 후 강제 재발급하고 1회 재시도
        token_bad = "EGW00121" in str(rsp_cd) or "token" in str(msg).lower() or "토큰" in str(msg)
        if attempt == 0 and token_bad:
            _invalidate_token()
            token = await get_access_token(force=True)
            headers["authorization"] = f"Bearer {token}"
            continue
        if attempt == 0 and rate_limited:
            await asyncio.sleep(0.4)  # 잠깐 쉬고 1회 재시도
            continue
        raise MeritzError(f"조회 오류({path}) rsp_cd={rsp_cd}: {msg}")
    raise MeritzError(f"조회 오류({path}) 재시도 실패")


# --------------------------------------------------------------------------- #
# 잔고 정규화
# --------------------------------------------------------------------------- #
def _norm_qty(qty: float):
    return int(qty) if qty == int(qty) else round(qty, 4)


async def get_balance() -> dict:
    """해외주식 잔고 + 외화예수금을 정규화해 반환(kis_client.mock_balance와 동일 포맷)."""
    hold_body = await _get(HOLDINGS_PATH)

    holdings = []
    purchase_total = eval_total = pnl_total = 0.0
    currency = "USD"
    for row in hold_body.get("data", []):
        qty = _to_float(row.get("rmnd_dqty"))
        if qty <= 0:
            continue
        raw_iscd = (row.get("iscd") or "").strip()         # 예: "AMD.OQ"
        code = (row.get("ovrs_stck_symb_iscd") or raw_iscd.split(".")[0] or "").strip()
        exch = raw_iscd.split(".")[1] if "." in raw_iscd else ""  # OQ/NY/AX...
        purchase = _to_float(row.get("byov_camt"))        # 매수금액(외화)
        evalu = _to_float(row.get("frcr_vltn_amt"))       # 외화평가금액
        pnl = _to_float(row.get("vltn_prls_camt"))        # 평가손익(외화)
        currency = row.get("crcd") or currency
        purchase_total += purchase
        eval_total += evalu
        pnl_total += pnl
        # 공식 전일종가 = 현재가 − 전일대비(부호 포함). 일봉 저장소보다 신뢰도 높음(앱과 동일).
        psnt = _to_float(row.get("psnt_prc"))
        vrss = _to_float(row.get("prdy_vrss_rsfl_crnc_prc"))
        prdy_close = round(psnt - vrss, 4) if (psnt and vrss is not None) else None
        holdings.append({
            "code": code,
            "name": row.get("isnm", ""),
            "market": "overseas",
            "exch": exch,
            "quantity": _norm_qty(qty),
            "avg_price": _to_float(row.get("avrg_unpr")),
            "current_price": psnt,
            "prdy_close": prdy_close,
            "purchase_amount": round(purchase, 2),
            "eval_amount": round(evalu, 2),
            "eval_pnl": round(pnl, 2),
            "pnl_rate": _to_float(row.get("vltn_ert")),
            "currency": currency,
        })

    # 외화예수금(해당 통화의 현재 예수금 frcr_amt 합)
    cash = 0.0
    try:
        cur_body = await _get(CURRENCIES_PATH)
        for c in cur_body.get("data", []):
            if (c.get("crcd") or "") == currency:
                cash += _to_float(c.get("frcr_amt"))
    except MeritzError:
        cash = 0.0  # 예수금 조회 실패는 치명적이지 않음(잔고는 유지)

    pnl_rate = (pnl_total / purchase_total * 100) if purchase_total else 0.0
    return {
        "mode": "live",
        "env": "real",
        "broker": "meritz",
        "currency": currency,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_asset": round(cash + eval_total, 2),
            "cash": round(cash, 2),
            "invest_eval": round(eval_total, 2),
            "purchase_amount": round(purchase_total, 2),
            "eval_pnl": round(pnl_total, 2),
            "pnl_rate": round(pnl_rate, 2),
        },
        "holdings": holdings,
    }


# --------------------------------------------------------------------------- #
# 캔들(시세추이) — 보유종목 차트/누적 저장용
# --------------------------------------------------------------------------- #
OVS_CANDLES_DAYS = "/market/v1/overseas/candles/days"
OVS_CANDLES_MIN = "/market/v1/overseas/candles/minutes"
KRX_CANDLES_DAYS = "/market/v1/candles/days"
KRX_CANDLES_MIN = "/market/v1/candles/minutes"


def _ts_from_date(yyyymmdd: str) -> int | None:
    try:
        return int(datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=KST).timestamp())
    except (TypeError, ValueError):
        return None


def _ts_from_dt(yyyymmdd: str, hhmmss: str) -> int | None:
    try:
        hhmmss = (hhmmss or "").zfill(6)[:6]
        return int(datetime.strptime(yyyymmdd + hhmmss, "%Y%m%d%H%M%S").replace(tzinfo=KST).timestamp())
    except (TypeError, ValueError):
        return None


async def overseas_candles(symbol: str, exch: str, kind: str,
                           interval_sec: int | None = None,
                           from_: str | None = None, to: str | None = None) -> list[tuple]:
    """해외주식 캔들 → [(ts, open, high, low, close, vol)] 시간오름차순.

    kind='days'  : 일봉(from/to YYYYMMDD, 미지정 시 최근 40). dely=0(실시간), mod=1
    kind='minutes': 분봉(interval_sec 간격, 최근 40봉 고정)
    """
    if kind == "days":
        params = {"mrkt_div_code": "OV", "mrkt_cls_code": exch, "iscd": symbol,
                  "dely_rltm_cls_code": "0", "mod_stpr_cls_code": "1"}
        if from_ and to:
            params["from"] = from_; params["to"] = to
        body = await _get(OVS_CANDLES_DAYS, params)
        out = []
        for r in body.get("data", []):
            ts = _ts_from_date(r.get("date"))
            out.append((ts, _to_float(r.get("oprc")), _to_float(r.get("hprc")),
                        _to_float(r.get("lprc")), _to_float(r.get("prpr")), _to_float(r.get("acml_vol"))))
    else:
        params = {"mrkt_div_code": "OV", "mrkt_cls_code": exch, "iscd": symbol,
                  "dely_rltm_cls_code": "0", "hour_cls_code": str(interval_sec or 300)}
        body = await _get(OVS_CANDLES_MIN, params)
        out = []
        for r in body.get("data", []):
            ts = _ts_from_dt(r.get("korea_date"), r.get("korea_hour"))
            out.append((ts, _to_float(r.get("oprc")), _to_float(r.get("hprc")),
                        _to_float(r.get("lprc")), _to_float(r.get("prpr")),
                        _to_float(r.get("cntg_vol") or r.get("acml_vol"))))
    out = [b for b in out if b[0] is not None]
    out.sort(key=lambda b: b[0])
    return out


async def domestic_candles(code: str, kind: str, interval_sec: int | None = None,
                           from_: str | None = None, to: str | None = None) -> list[tuple]:
    """국내주식 캔들 → [(ts, open, high, low, close, vol)]. iscd 는 A+6자리."""
    iscd = code if code[:1] == "A" else "A" + code
    if kind == "days":
        params = {"mrkt_div_code": "J", "iscd": iscd, "mod_stpr_cls_code": "0"}
        if from_ and to:
            params["from"] = from_; params["to"] = to
        body = await _get(KRX_CANDLES_DAYS, params)
        out = []
        for r in body.get("data", []):
            ts = _ts_from_date(r.get("date"))
            out.append((ts, _to_float(r.get("oprc")), _to_float(r.get("hprc")),
                        _to_float(r.get("lprc")), _to_float(r.get("stck_clpr")), _to_float(r.get("acml_vol"))))
    else:
        # TJ = KRX+NXT(넥스트레이드) 통합 → 장시간(08:00~20:00) 체결 포함
        params = {"mrkt_div_code": "TJ", "iscd": iscd, "hour_cls_code": str(interval_sec or 300)}
        body = await _get(KRX_CANDLES_MIN, params)
        today = datetime.now(KST).strftime("%Y%m%d")
        out = []
        for r in body.get("data", []):
            ts = _ts_from_dt(r.get("cntg_date") or today, r.get("cntg_hour"))
            out.append((ts, _to_float(r.get("oprc")), _to_float(r.get("hprc")),
                        _to_float(r.get("lprc")), _to_float(r.get("stck_prpr")), _to_float(r.get("cntg_vol"))))
    out = [b for b in out if b[0] is not None]
    out.sort(key=lambda b: b[0])
    return out


async def day_quote(symbol: str, exch: str, mkt: str) -> dict:
    """현재가·공식 전일종가를 일봉에서 산출(보유·관심 공용).

    메리츠 잔고(psnt_prc/전일대비)는 US 마감 직후 전환 시간에 서로 다른 세션 값이
    섞여 깨지는 경우가 있어, 정합성 높은 일봉을 단일 기준으로 쓴다.

    ※ 해외(미국): 일봉의 날짜는 '미국 날짜'라 KST '오늘'과 하루 어긋난다(정규장은
      KST 새벽에 진행). 따라서 date==오늘 비교를 쓰지 않고, 가장 최근 '실세션' 봉
      (플랫 placeholder 제외)을 현재/직전 세션으로 본다. 그 봉의 prdy_ctrt가 곧 금일 등락.
      단 한국 주간거래 시간(KST 10:00~17:40)엔 일봉에 그 세션이 없어, 현재가는 분봉에
      맡기고(price=None) 전일만 최신 실세션 종가로 준다.
    ※ 국내: 같은 KST 날짜라 date==오늘 비교가 유효.
    반환: {"price": float|None, "prev_close": float|None}
    """
    close_f = "stck_clpr" if mkt == "domestic" else "prpr"
    try:
        if mkt == "domestic":
            iscd = symbol if symbol[:1] == "A" else "A" + symbol
            params = {"mrkt_div_code": "J", "iscd": iscd, "mod_stpr_cls_code": "0"}
            body = await _get(KRX_CANDLES_DAYS, params)
        else:
            params = {"mrkt_div_code": "OV", "mrkt_cls_code": exch, "iscd": symbol,
                      "dely_rltm_cls_code": "0", "mod_stpr_cls_code": "1"}
            body = await _get(OVS_CANDLES_DAYS, params)
        rows = body.get("data", [])  # 최신순
        if not rows:
            return {"price": None, "prev_close": None}
        # 가장 최근 '실세션' 봉(플랫 placeholder=prdy_vrss 0 은 건너뜀)
        real = next((r for r in rows if _to_float(r.get("prdy_vrss")) != 0), rows[0])
        r_px = _to_float(real.get(close_f))
        r_prev = round(r_px - _to_float(real.get("prdy_vrss")), 4)

        if mkt == "domestic":
            now = datetime.now(KST)
            today = now.strftime("%Y%m%d")
            top = rows[0]
            top_today = (top.get("date") or "") == today
            if top_today and _to_float(top.get("prdy_vrss")) != 0:
                px = _to_float(top.get(close_f))        # 오늘 거래 발생(장중/마감 후) → 금일 등락
                return {"price": px, "prev_close": round(px - _to_float(top.get("prdy_vrss")), 4)}
            if 9 <= now.hour < 16:                      # 장중인데 당일 봉 아직 플랫 → 분봉이 현재가
                return {"price": None, "prev_close": round(_to_float(top.get(close_f)), 4)}
            if top_today:                               # 개장 전 플랫 placeholder → 전일종가 기준 0%
                px = _to_float(top.get(close_f))
                return {"price": px, "prev_close": px}
            return {"price": r_px, "prev_close": r_prev}  # 오늘 봉 없음(마감 후 밤) → 최신 완료세션

        # 해외: 주간거래 시간대면 현재가는 분봉, 전일=최신 실세션 종가
        if 9 <= datetime.now(KST).hour < 18:   # 주간거래(약 09:00~17:40): 현재가=분봉
            return {"price": None, "prev_close": r_px}
        # 그 외(미국 정규장/공백/마감): 최신 실세션 봉의 공식 전일대비
        return {"price": r_px, "prev_close": r_prev}
    except MeritzError:
        return {"price": None, "prev_close": None}


def _is_domestic(iscd: str) -> bool:
    """국내(원화) 종목 여부. 해외는 'AAPL.OQ'처럼 '.'로 거래소 접미사가 붙는다."""
    return bool(iscd) and "." not in iscd


async def get_domestic_balance() -> dict:
    """국내(원화) 보유종목만 정규화. 종합계좌의 해외분(.OQ/.NY 등)은 제외.

    통합 /holdings 는 해외주식도 원화 환산해 함께 내려주므로, 순수 국내 종목만 추린다.
    """
    hold_body = await _get(DOMESTIC_HOLDINGS_PATH)

    holdings = []
    purchase_total = eval_total = pnl_total = 0.0
    for row in hold_body.get("data", []):
        iscd = (row.get("iscd") or "").strip()
        if not _is_domestic(iscd):
            continue  # 해외분 제외
        qty = _to_float(row.get("rmnd_qty") or row.get("rmnd_dqty"))
        if qty <= 0:
            continue
        code = iscd[1:] if iscd[:1] == "A" and iscd[1:].isdigit() else iscd  # A000660 → 000660
        purchase = _to_float(row.get("byng_amt"))        # 매입금액(원)
        evalu = _to_float(row.get("vltn_amt"))            # 평가금액(원)
        pnl = _to_float(row.get("vltn_prls_amt"))         # 평가손익(원)
        purchase_total += purchase
        eval_total += evalu
        pnl_total += pnl
        holdings.append({
            "code": code,
            "name": row.get("isnm", ""),
            "quantity": _norm_qty(qty),
            "avg_price": _to_float(row.get("byng_unpr")),  # 매입단가
            "current_price": _to_float(row.get("psnt_prc")),
            "prdy_close": _to_float(row.get("prdy_last_prc")),  # 공식 전일종가(앱과 동일)
            "purchase_amount": round(purchase, 2),
            "eval_amount": round(evalu, 2),
            "eval_pnl": round(pnl, 2),
            "pnl_rate": _to_float(row.get("ert")),
            "currency": "KRW",
        })

    # 원화 예수금(주문가능현금)
    cash = 0.0
    try:
        dep_body = await _get(DOMESTIC_DEPOSIT_PATH)
        dep = dep_body.get("data") or {}
        if isinstance(dep, dict):
            cash = _to_float(dep.get("oder_able_cash_amt") or dep.get("tfam"))
    except MeritzError:
        cash = 0.0

    pnl_rate = (pnl_total / purchase_total * 100) if purchase_total else 0.0
    return {
        "mode": "live",
        "env": "real",
        "broker": "meritz",
        "currency": "KRW",
        "scope": "domestic",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_asset": round(cash + eval_total, 2),
            "cash": round(cash, 2),
            "invest_eval": round(eval_total, 2),
            "purchase_amount": round(purchase_total, 2),
            "eval_pnl": round(pnl_total, 2),
            "pnl_rate": round(pnl_rate, 2),
        },
        "holdings": holdings,
    }


# --------------------------------------------------------------------------- #
# 거래·입출금 내역 (원장용)
# --------------------------------------------------------------------------- #
async def get_transactions(from_date: str = "00000000", to_date: str | None = None,
                           sort: str = "ASC", max_pages: int = 200) -> list[dict]:
    """계좌 거래·입출금 내역 전체(연속조회). 원본 data 행 리스트를 반환.

    from_date='00000000' 이면 계좌 개설 시점부터. 응답 tr_cont='1' 이면 다음 페이지가
    있어 tr_cont_key 로 이어 조회한다. tr_cont_key 는 뒤쪽 공백이 포함될 수 있으므로
    가공하지 않고 그대로 전달한다(메리츠 규약).
    """
    to_date = to_date or datetime.now(KST).strftime("%Y%m%d")
    out: list[dict] = []
    tr_cont_key: str | None = None
    for _ in range(max_pages):
        params = {"from": from_date, "to": to_date, "sort": sort}
        if tr_cont_key is not None:
            params["tr_cont"] = "1"
            params["tr_cont_key"] = tr_cont_key
        body = await _get(TRANSACTIONS_PATH, params)
        data = body.get("data")
        if isinstance(data, list):
            out.extend(data)
        if str(body.get("tr_cont")) == "1" and body.get("tr_cont_key"):
            tr_cont_key = body["tr_cont_key"]
            await asyncio.sleep(0.15)
            continue
        break
    return out


# --------------------------------------------------------------------------- #
# 실현손익 / 고시환율 (손익 요약용) — 네이티브 값 사용
# --------------------------------------------------------------------------- #
async def get_domestic_pnl_symbols(from_date: str = "00000000", to_date: str | None = None) -> list[dict]:
    """국내 종목별 실현손익(개설시점~). rsp 5820이어도 rows 있을 수 있어 길이로 판단."""
    to_date = to_date or datetime.now(KST).strftime("%Y%m%d")
    try:
        body = await _get(PNL_SYMBOLS_PATH, {"iqry_sta_date": from_date, "iqry_fin_date": to_date})
    except MeritzError:
        return []
    data = body.get("data")
    return data if isinstance(data, list) else []


async def get_overseas_pnl(ctcd: str = "840", from_date: str = "00000000",
                           to_date: str | None = None) -> list[dict]:
    """해외 실현손익(국가코드 ctcd=840 미국, 매도 건별, 개설시점~).

    rlzt_prls_amt=원화 실현손익(환차 포함), frcr_rlzt_ernn_amt=외화(USD) 실현손익.
    """
    to_date = to_date or datetime.now(KST).strftime("%Y%m%d")
    try:
        body = await _get(OVS_PNL_PATH, {"ctcd": ctcd, "oder_sta_date": from_date, "oder_fin_date": to_date})
    except MeritzError:
        return []
    data = body.get("data")
    if isinstance(data, list):
        return data
    return [data] if isinstance(data, dict) else []


async def get_fx_rate(crcd: str = "USD", stnd_date: str | None = None) -> float | None:
    """고시환율(기준환율). 실패 시 None."""
    stnd_date = stnd_date or datetime.now(KST).strftime("%Y%m%d")
    try:
        body = await _get(FX_RATES_PATH, {"stnd_date": stnd_date, "crcd": crcd})
    except MeritzError:
        return None
    data = body.get("data")
    row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    if not row:
        return None
    raw = row.get("stnd_exrt") or row.get("deal_stnd_exrt") or row.get("tltr_aply_byrt")
    try:
        return float(str(raw).replace(",", "")) or None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 체결내역(주문내역) — 거래내역(정산)보다 먼저 체결 당일 반영
# --------------------------------------------------------------------------- #
OVS_ORDERS_TODAY_PATH = "/trading/v1/overseas/orders/today"
ORDERS_HISTORY_PATH = "/trading/v1/orders/history"
PNL_SYMBOLS_PATH = "/accounts/v1/me/profit-loss/symbols"
OVS_PNL_PATH = "/accounts/v1/me/overseas/profit-loss"
FX_RATES_PATH = "/forex/v1/rates"


async def get_overseas_orders_today() -> list[dict]:
    """해외주식 당일 주문·체결 내역(체결분만). 당일만 제공(날짜 지정 불가)."""
    try:
        body = await _get(OVS_ORDERS_TODAY_PATH, {"cntg_cls_code": "1", "seln_byov_clcd": "0"})
    except MeritzError:
        return []
    data = body.get("data")
    return data if isinstance(data, list) else []


async def get_domestic_orders_history(oder_date: str) -> list[dict]:
    """국내주식 주문·체결 내역(특정일, 체결분만). 연속조회 지원."""
    out: list[dict] = []
    tr_cont_key: str | None = None
    for _ in range(50):
        params = {"oder_date": oder_date, "deal_cls_code": "0", "cntg_cls_code": "1"}
        if tr_cont_key is not None:
            params["tr_cont"] = "1"
            params["tr_cont_key"] = tr_cont_key
        try:
            body = await _get(ORDERS_HISTORY_PATH, params)
        except MeritzError:
            return out
        data = body.get("data")
        if isinstance(data, list):
            out.extend(data)
        if str(body.get("tr_cont")) == "1" and body.get("tr_cont_key"):
            tr_cont_key = body["tr_cont_key"]
            await asyncio.sleep(0.15)
            continue
        break
    return out
