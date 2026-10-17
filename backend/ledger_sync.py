"""원장 수집: 메리츠 거래내역 → .ledger.db (멱등).

Slice 1: transactions 전체를 받아 정규화 후 upsert. 건수가 적어(계좌 단위 수십~수백)
매 주기 전체(00000000) 재조회해도 저렴하고 항상 일관적이다. (추후 증분 최적화 여지)
"""
from __future__ import annotations

import asyncio
import json

import ledger_store
import meritz_client

LEDGER_INTERVAL = 1800   # 초. 거래는 자주 변하지 않으므로 30분 주기 + 기동 시 1회


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalize(row: dict) -> dict | None:
    srno = row.get("tran_srno")
    if srno is None:
        return None
    frcr = _num(row.get("frcr_tr_amt"))
    iscd = row.get("iscd") or ""
    currency = "USD" if (frcr or iscd == "USD") else "KRW"
    tax = (_num(row.get("intx")) + _num(row.get("rstx"))
           + _num(row.get("srtx")) + _num(row.get("trtx")))
    fee = _num(row.get("deal_fee")) + _num(row.get("ovsc_deal_fee")) + _num(row.get("fee"))
    return {
        "srno": int(srno),
        "tr_date": row.get("tr_date") or "",
        "io_cls_code": row.get("io_cls_code") or "",
        "tr_type_code": row.get("tr_type_code") or "",
        "smcd": row.get("smcd") or "",
        "smcd_name": row.get("smcd_name") or "",
        "iscd": iscd,
        "name": row.get("kor_isnm") or "",
        "qty": _num(row.get("tr_qty")),
        "amount": _num(row.get("tr_amt")),
        "byng_unpr": _num(row.get("byng_unpr")),
        "fee": fee,
        "tax": tax,
        "frcr_amt": frcr,
        "currency": currency,
        "cncl_yn": row.get("cncl_yn") or "",
        "raw": json.dumps(row, ensure_ascii=False),
    }


async def sync_transactions() -> int:
    """거래내역 전체를 받아 정규화·upsert. 반환: 저장 건수(실패 시 -1)."""
    try:
        rows = await meritz_client.get_transactions("00000000", sort="ASC")
    except meritz_client.MeritzError as exc:
        print(f"[ledger] transactions sync failed: {exc}")
        return -1
    norm = [n for r in rows if (n := _normalize(r)) is not None]
    await asyncio.to_thread(ledger_store.upsert_tx, norm)
    return len(norm)


async def run_ledger_sync() -> None:
    """기동 시 1회 + 주기적으로 거래내역을 수집한다."""
    while True:
        n = await sync_transactions()
        if n >= 0:
            st = await asyncio.to_thread(ledger_store.stats)
            print(f"[ledger] synced {n} tx; total={st['rows']} "
                  f"({st['min_date']}~{st['max_date']})")
        await asyncio.sleep(LEDGER_INTERVAL)
