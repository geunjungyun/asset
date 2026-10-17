"""P0 feasibility probe for the Meritz ledger plan (READ-ONLY).

Confirms that the endpoints the ledger needs actually exist, return data from
account inception, and expose the fields we expect. It NEVER places an order or
executes an FX conversion -- it only calls GET inquiry endpoints.

IMPORTANT: run this LOCALLY ONLY WHILE THE SERVER IS STOPPED.
  The Meritz token is 1-per-appkey; if the server is running, a fresh local
  token issue would invalidate the server's token. Stop AssetPro-Server first.
  After running, delete the server's .meritz_token_cache.json before restarting
  so the server re-issues a clean token.

Usage (from the backend folder, with the venv python):
  python verify_ledger_api.py
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta

import meritz_client as m
from config import meritz_settings

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y%m%d")
YEAR_START = TODAY[:4] + "0101"


async def probe(title: str, path: str, params: dict | None = None,
                note: str = "") -> dict | None:
    """Call one GET inquiry and print a compact summary."""
    print("\n==== " + title + " ====")
    print("  GET", path, params or "")
    if note:
        print("  note:", note)
    try:
        body = await m._get(path, params)
    except m.MeritzError as exc:
        print("  FAIL:", exc)
        return None

    print("  rsp_cd:", body.get("rsp_cd"), "| rsp_msg:", body.get("rsp_msg"))
    if body.get("tr_cont"):
        print("  tr_cont:", body.get("tr_cont"), "(more pages available via tr_cont_key)")

    data = body.get("data")
    if isinstance(data, list):
        print("  rows on first page:", len(data))
        if data:
            print("  fields:", ", ".join(data[0].keys()))
            for row in data[:3]:
                print("   -", json.dumps(row, ensure_ascii=False))
    elif isinstance(data, dict):
        print("  single-record fields:", ", ".join(data.keys()))
        print("   ", json.dumps(data, ensure_ascii=False)[:800])
    else:
        print("  (no 'data' array/object)")
    return body


async def main() -> None:
    print("Meritz ledger P0 probe (READ-ONLY). today =", TODAY)
    if not meritz_settings.enabled:
        print("ABORT: Meritz keys not set in .env (MERITZ_APP_KEY/SECRET).")
        return

    # Force a fresh token (server must be stopped). Avoids a stale/dead cached token.
    try:
        m.TOKEN_CACHE_FILE.unlink()
        print("Deleted local token cache -> will issue a fresh token.")
    except FileNotFoundError:
        pass

    # --- 1) Transactions: oldest-first to measure history depth --------------
    body = await probe(
        "transactions ASC (from=00000000) -> earliest record / history depth",
        "/accounts/v1/me/transactions",
        {"from": "00000000", "to": TODAY, "sort": "ASC"},
        note="earliest tr_date on page 1 shows how far back history goes",
    )
    if body and isinstance(body.get("data"), list) and body["data"]:
        rows = body["data"]
        print("  >> EARLIEST tr_date (page 1):", rows[0].get("tr_date"))
        io_vals = sorted({str(r.get("io_cls_code")) for r in rows})
        tt_vals = sorted({str(r.get("tr_type_code")) for r in rows})
        print("  >> io_cls_code values seen:", io_vals)
        print("  >> tr_type_code values seen:", tt_vals)
        has_fx = any(m._to_float(r.get("frcr_tr_amt")) for r in rows)
        print("  >> overseas rows present (frcr_tr_amt != 0):", has_fx)

    # --- 2) Transactions: newest-first for recent trade classification -------
    await probe(
        "transactions DES -> most recent activity + code values",
        "/accounts/v1/me/transactions",
        {"from": "00000000", "to": TODAY, "sort": "DES"},
    )

    # --- 3) Realized P&L (domestic, by date) --------------------------------
    await probe(
        "profit-loss domestic (by date, from=00000000)",
        "/accounts/v1/me/profit-loss",
        {"iqry_sta_date": "00000000", "iqry_fin_date": TODAY},
        note="prls_amt=realized P&L, plrt=rate; success 5766 / no-data 5820",
    )

    # --- 4) Realized P&L per symbol -----------------------------------------
    await probe(
        "profit-loss per symbol",
        "/accounts/v1/me/profit-loss/symbols",
        {"iqry_sta_date": "00000000", "iqry_fin_date": TODAY},
        note="may return rsp 5820 even with rows -> check row count",
    )

    # --- 5) Realized P&L (overseas) -----------------------------------------
    await probe(
        "overseas profit-loss",
        "/accounts/v1/me/overseas/profit-loss",
        {"iqry_sta_date": "00000000", "iqry_fin_date": TODAY},
    )

    # --- 6) Period summary (fin must not be 00000000) -----------------------
    await probe(
        "profit-loss summary (YTD)",
        "/accounts/v1/me/profit-loss/summary",
        {"iqry_sta_date": YEAR_START, "iqry_fin_date": TODAY},
    )

    # --- 7) FX quoted rates (historical by date) ----------------------------
    await probe(
        "forex quoted rates (stnd_date=today, USD)",
        "/forex/v1/rates",
        {"stnd_date": TODAY, "crcd": "USD"},
        note="for KRW cost-basis conversion; try a prior business day if empty",
    )

    # --- 8) FX conversion history -------------------------------------------
    await probe(
        "forex exchanges history (from=00000000)",
        "/forex/v1/exchanges/history",
        {"iqry_sta_date": "00000000", "iqry_fin_date": TODAY},
    )

    # --- 9) Orders history (daily; secondary) --------------------------------
    await probe(
        "orders history (today only; daily endpoint)",
        "/trading/v1/orders/history",
        {"oder_date": TODAY, "deal_cls_code": "0", "cntg_cls_code": "0"},
        note="daily; realized P&L + transactions already cover trades",
    )

    print("\nDONE (read-only; no orders or FX executed).")
    print("Reminder: on the server, delete .meritz_token_cache.json before restarting.")


if __name__ == "__main__":
    asyncio.run(main())
