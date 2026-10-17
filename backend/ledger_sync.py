"""원장 수집: 메리츠 거래내역 → .ledger.db (멱등).

Slice 1: transactions 전체를 받아 정규화 후 upsert. 건수가 적어(계좌 단위 수십~수백)
매 주기 전체(00000000) 재조회해도 저렴하고 항상 일관적이다. (추후 증분 최적화 여지)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import ledger_store
import meritz_client
from meritz_client import KST

LEDGER_INTERVAL = 1800     # 초. 거래는 자주 변하지 않으므로 30분 주기 + 기동 시 1회
EXEC_BACKFILL_DAYS = 7     # 국내 체결 소급(기동 시 1회). 해외는 당일만 제공.
_backfilled = False


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


def _recent_days(today_str: str, n: int) -> list[str]:
    d0 = datetime.strptime(today_str, "%Y%m%d")
    return [(d0 - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _normalize_ovs_exec(r: dict) -> dict | None:
    qty = _num(r.get("cntg_qty"))
    if qty <= 0:
        return None
    date = r.get("oder_date") or ""
    oder_no = str(r.get("oder_no") or "")
    buy = str(r.get("seln_byov_clcd")) == "2"
    price = _num(r.get("cntg_crnc_prc"))
    frcr = _num(r.get("cntg_camt")) or round(price * qty, 4)
    iscd = r.get("iscd") or ""
    return {
        "exec_id": f"OVS:{date}:{oder_no}", "oder_date": date, "market": "overseas",
        "iscd": iscd, "name": r.get("ovrs_stck_symb_iscd") or iscd.split(".")[0],
        "side": "buy" if buy else "sell", "qty": qty, "price": price,
        "amount": 0, "frcr": frcr, "currency": "USD",
        "status": str(r.get("ovrs_stck_oder_stat_cls_code") or ""),
        "exec_time": r.get("oder_exec_ymdh") or "", "raw": json.dumps(r, ensure_ascii=False),
    }


def _normalize_dom_exec(r: dict, date: str) -> dict | None:
    qty = _num(r.get("total_cntg_qty"))
    if qty <= 0:
        return None
    oder_no = str(r.get("oder_no") or "")
    buy = str(r.get("deal_cls_code")) == "2"
    price = _num(r.get("cntg_unpr"))
    return {
        "exec_id": f"DOM:{date}:{oder_no}", "oder_date": date, "market": "domestic",
        "iscd": r.get("iscd") or "", "name": r.get("kor_issu_abwr_name") or "",
        "side": "buy" if buy else "sell", "qty": qty, "price": price,
        "amount": round(price * qty, 2), "frcr": 0, "currency": "KRW",
        "status": str(r.get("oder_pros_stat_cls_code") or ""),
        "exec_time": r.get("oder_pros_sta_time") or "", "raw": json.dumps(r, ensure_ascii=False),
    }


async def sync_executions() -> int:
    """당일 체결(해외 당일 + 국내 당일, 기동 시 국내 N일 소급) 수집·upsert."""
    global _backfilled
    rows: list[dict] = []
    try:
        for r in await meritz_client.get_overseas_orders_today():
            n = _normalize_ovs_exec(r)
            if n:
                rows.append(n)
    except meritz_client.MeritzError as exc:
        print(f"[ledger] overseas orders failed: {exc}")

    today = datetime.now(KST).strftime("%Y%m%d")
    dom_dates = _recent_days(today, EXEC_BACKFILL_DAYS) if not _backfilled else [today]
    _backfilled = True
    for d in dom_dates:
        try:
            for r in await meritz_client.get_domestic_orders_history(d):
                n = _normalize_dom_exec(r, d)
                if n:
                    rows.append(n)
        except meritz_client.MeritzError as exc:
            print(f"[ledger] domestic orders {d} failed: {exc}")
        await asyncio.sleep(0.1)

    if rows:
        await asyncio.to_thread(ledger_store.upsert_exec, rows)
    return len(rows)


async def run_ledger_sync() -> None:
    """기동 시 1회 + 주기적으로 거래내역·체결내역을 수집한다."""
    while True:
        n = await sync_transactions()
        ne = await sync_executions()
        if n >= 0:
            st = await asyncio.to_thread(ledger_store.stats)
            est = await asyncio.to_thread(ledger_store.exec_stats)
            print(f"[ledger] tx={n}(total {st['rows']}) exec+={ne}(total {est['rows']})")
        await asyncio.sleep(LEDGER_INTERVAL)
