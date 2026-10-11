"""한국투자증권(KIS) Open API 클라이언트.

- OAuth 접근토큰 발급 및 파일 캐싱 (KIS는 토큰 발급 빈도 제한이 있어 재사용 필수)
- 주식잔고조회(inquire-balance) 호출
- 자격증명이 없으면(settings.use_mock) 실제 호출 대신 mock 데이터 반환

응답은 프론트가 쓰기 쉬운 형태로 정규화해서 돌려준다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from config import BASE_DIR, settings

TOKEN_CACHE_FILE = BASE_DIR / ".token_cache.json"
# 만료 60초 전에는 새로 발급
TOKEN_SAFETY_MARGIN = 60


class KisError(Exception):
    """KIS API 호출 실패."""


# --------------------------------------------------------------------------- #
# 토큰 관리
# --------------------------------------------------------------------------- #
def _read_cached_token() -> str | None:
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # 도메인이 바뀌면(실전<->모의) 토큰 무효
    if data.get("base_url") != settings.base_url:
        return None
    if data.get("expires_at", 0) - TOKEN_SAFETY_MARGIN <= time.time():
        return None
    return data.get("access_token")


def _write_cached_token(access_token: str, expires_in: int) -> None:
    payload = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in),
        "base_url": settings.base_url,
        "issued_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        TOKEN_CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # 캐시 실패는 치명적이지 않음


async def get_access_token() -> str:
    """유효한 접근토큰을 반환한다 (캐시 우선)."""
    cached = _read_cached_token()
    if cached:
        return cached

    url = f"{settings.base_url}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=body)
    if resp.status_code != 200:
        raise KisError(f"토큰 발급 실패 ({resp.status_code}): {resp.text}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise KisError(f"토큰 응답에 access_token 없음: {data}")
    _write_cached_token(token, data.get("expires_in", 86400))
    return token


# --------------------------------------------------------------------------- #
# 잔고 조회
# --------------------------------------------------------------------------- #
def _to_float(value: str | float | None) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def inquire_balance() -> dict:
    """주식잔고를 조회해 정규화된 dict로 반환한다.

    mock 모드면 mock 데이터를, 실계좌면 KIS 응답을 정규화해 돌려준다.
    """
    if settings.use_mock:
        return mock_balance()

    token = await get_access_token()
    url = f"{settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
        "tr_id": settings.tr_balance,
        "custtype": "P",  # 개인
    }
    params = {
        "CANO": settings.cano,
        "ACNT_PRDT_CD": settings.acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",      # 시간외단일가 여부
        "OFL_YN": "",             # 오프라인 여부
        "INQR_DVSN": "02",        # 조회구분: 종목별
        "UNPR_DVSN": "01",        # 단가구분
        "FUND_STTL_ICLD_YN": "N", # 펀드결제분 포함 여부
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",        # 전일매매포함
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise KisError(f"잔고조회 실패 ({resp.status_code}): {resp.text}")

    data = resp.json()
    if data.get("rt_cd") not in ("0", 0):
        raise KisError(f"잔고조회 오류: {data.get('msg1', data)}")

    return _normalize(data.get("output1", []), data.get("output2", []))


def _normalize(output1: list[dict], output2: list[dict]) -> dict:
    """KIS 원응답(output1: 종목, output2: 계좌요약)을 정규화한다."""
    holdings = []
    for row in output1:
        qty = _to_float(row.get("hldg_qty"))
        if qty <= 0:
            continue
        holdings.append(
            {
                "code": row.get("pdno", ""),
                "name": row.get("prdt_name", ""),
                "quantity": int(qty),
                "avg_price": _to_float(row.get("pchs_avg_pric")),
                "current_price": _to_float(row.get("prpr")),
                "purchase_amount": _to_float(row.get("pchs_amt")),
                "eval_amount": _to_float(row.get("evlu_amt")),
                "eval_pnl": _to_float(row.get("evlu_pfls_amt")),
                "pnl_rate": _to_float(row.get("evlu_pfls_rt")),
            }
        )

    summary_raw = output2[0] if output2 else {}
    cash = _to_float(summary_raw.get("dnca_tot_amt"))           # 예수금총금액
    eval_amount = _to_float(summary_raw.get("scts_evlu_amt"))   # 유가증권 평가금액
    purchase_amount = _to_float(summary_raw.get("pchs_amt_smtl_amt"))
    eval_pnl = _to_float(summary_raw.get("evlu_pfls_smtl_amt")) # 평가손익 합계
    total_eval = _to_float(summary_raw.get("tot_evlu_amt"))     # 총평가금액(예수금 포함)

    pnl_rate = (eval_pnl / purchase_amount * 100) if purchase_amount else 0.0

    return {
        "mode": "live",
        "env": settings.env,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_asset": total_eval or (cash + eval_amount),
            "cash": cash,
            "invest_eval": eval_amount,
            "purchase_amount": purchase_amount,
            "eval_pnl": eval_pnl,
            "pnl_rate": round(pnl_rate, 2),
        },
        "holdings": holdings,
    }


# --------------------------------------------------------------------------- #
# Mock 데이터 (자격증명 없을 때)
# --------------------------------------------------------------------------- #
def mock_balance() -> dict:
    # 사용자의 실제 보유 미국 주식 (code=Yahoo 티커, 금액 USD)
    # code, 종목명, 수량, 원금(매입금액), 평가금액
    raw = [
        ("MU", "마이크론 테크놀로지", 59, 61608.55, 60711.00),
        ("SNDK", "샌디스크", 35, 59764.30, 55363.70),
        ("GOOGL", "알파벳 A", 139, 48348.31, 48880.74),
        ("MRVL", "마벨 테크놀로지 그룹", 170, 42840.00, 46797.60),
        ("AMD", "AMD", 40, 25625.00, 24324.00),
        ("AMGN", "암젠", 33, 14595.09, 13690.71),
        ("LLY", "일라이 릴리", 8, 9303.92, 9434.16),
        ("NVDA", "엔비디아", 40, 9504.00, 9171.20),
    ]
    holdings = []
    purchase_total = 0.0
    eval_total = 0.0
    for code, name, qty, purchase, evalu in raw:
        pnl = evalu - purchase
        purchase_total += purchase
        eval_total += evalu
        holdings.append(
            {
                "code": code,
                "name": name,
                "quantity": qty,
                "avg_price": round(purchase / qty, 2) if qty else 0.0,
                "current_price": round(evalu / qty, 2) if qty else 0.0,
                "purchase_amount": round(purchase, 2),
                "eval_amount": round(evalu, 2),
                "eval_pnl": round(pnl, 2),
                "pnl_rate": round(pnl / purchase * 100, 2) if purchase else 0.0,
            }
        )

    cash = 0.0  # 예수금 정보 미입력 (실제 KIS 연동 시 자동 반영)
    eval_pnl = eval_total - purchase_total
    return {
        "mode": "mock",
        "env": settings.env,
        "currency": "USD",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_asset": round(cash + eval_total, 2),
            "cash": cash,
            "invest_eval": round(eval_total, 2),
            "purchase_amount": round(purchase_total, 2),
            "eval_pnl": round(eval_pnl, 2),
            "pnl_rate": round(eval_pnl / purchase_total * 100, 2) if purchase_total else 0.0,
        },
        "holdings": holdings,
    }


def mock_transactions() -> list[dict]:
    return [
        {"id": 1, "title": "삼성전자 매수", "category": "투자", "date": "오늘", "amount": -1490000, "type": "expense", "icon": "fa-cart-shopping"},
        {"id": 2, "title": "배당금 입금", "category": "수입", "date": "어제", "amount": 82000, "type": "income", "icon": "fa-sack-dollar"},
        {"id": 3, "title": "SK하이닉스 매도", "category": "투자", "date": "8월 27일", "amount": 890000, "type": "income", "icon": "fa-arrow-trend-up"},
        {"id": 4, "title": "예수금 이체", "category": "입금", "date": "8월 25일", "amount": 2000000, "type": "income", "icon": "fa-building-columns"},
        {"id": 5, "title": "카카오 매수", "category": "투자", "date": "8월 22일", "amount": -2200000, "type": "expense", "icon": "fa-cart-shopping"},
    ]
