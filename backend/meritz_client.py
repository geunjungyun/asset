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

import json
import time
import uuid
from datetime import datetime

import httpx

from config import BASE_DIR, meritz_settings

TOKEN_CACHE_FILE = BASE_DIR / ".meritz_token_cache.json"
TOKEN_SAFETY_MARGIN = 60  # 만료 60초 전 재발급

HOLDINGS_PATH = "/accounts/v1/me/overseas/holdings"
CURRENCIES_PATH = "/accounts/v1/me/overseas/currencies"
DOMESTIC_HOLDINGS_PATH = "/accounts/v1/me/holdings"
DOMESTIC_DEPOSIT_PATH = "/accounts/v1/me/deposit"


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


async def get_access_token() -> str:
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
async def _get(path: str) -> dict:
    """GET 호출 후 JSON 반환. data 가 없고 오류 응답이면 MeritzError."""
    token = await get_access_token()
    headers = {
        "authorization": f"Bearer {token}",
        "mac_address": _mac_address(),
    }
    url = f"{meritz_settings.base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
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
    raise MeritzError(f"조회 오류({path}) rsp_cd={rsp_cd}: {msg}")


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
        code = (row.get("ovrs_stck_symb_iscd") or row.get("iscd", "").split(".")[0] or "").strip()
        purchase = _to_float(row.get("byov_camt"))        # 매수금액(외화)
        evalu = _to_float(row.get("frcr_vltn_amt"))       # 외화평가금액
        pnl = _to_float(row.get("vltn_prls_camt"))        # 평가손익(외화)
        currency = row.get("crcd") or currency
        purchase_total += purchase
        eval_total += evalu
        pnl_total += pnl
        holdings.append({
            "code": code,
            "name": row.get("isnm", ""),
            "quantity": _norm_qty(qty),
            "avg_price": _to_float(row.get("avrg_unpr")),
            "current_price": _to_float(row.get("psnt_prc")),
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
