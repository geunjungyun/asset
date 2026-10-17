"""원장(거래내역) 로컬 저장소 — SQLite (.ledger.db).

메리츠 /accounts/v1/me/transactions 를 누적 보관한다(멱등 upsert).
- ledger_tx(srno PK, ...) : tran_srno(계좌 단위 일련번호)를 PK로 중복 자동 제거.

candle_store 와 동일한 패턴(동기 sqlite3 + WAL). 비동기에서는 asyncio.to_thread 로 감싼다.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from config import BASE_DIR

DB_PATH = BASE_DIR / ".ledger.db"
_lock = threading.Lock()

COLUMNS = [
    "srno", "tr_date", "io_cls_code", "tr_type_code", "smcd", "smcd_name",
    "iscd", "name", "qty", "amount", "byng_unpr", "fee", "tax",
    "frcr_amt", "currency", "cncl_yn", "raw",
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _lock, _db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS ledger_tx(
                srno         INTEGER PRIMARY KEY,   -- tran_srno(계좌 일련번호)
                tr_date      TEXT,                  -- YYYYMMDD
                io_cls_code  TEXT,                  -- 1=증가(입고/입금) 2=감소(출고/출금)
                tr_type_code TEXT,                  -- 거래유형코드
                smcd         TEXT,
                smcd_name    TEXT,                  -- 거래구분명(예: 타인출고입고)
                iscd         TEXT,
                name         TEXT,
                qty          REAL,
                amount       REAL,                  -- 원화 거래금액
                byng_unpr    REAL,                  -- 매입단가
                fee          REAL,                  -- 수수료(국내+해외 합)
                tax          REAL,                  -- 제세금 합(intx+rstx+srtx+trtx)
                frcr_amt     REAL,                  -- 외화 거래금액
                currency     TEXT,                  -- 'KRW' | 'USD'
                cncl_yn      TEXT,
                raw          TEXT                   -- 원본 JSON
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date ON ledger_tx(tr_date)")


def upsert_tx(rows: list[dict]) -> int:
    """정규화된 거래행들을 멱등 저장(srno 기준 교체). 반환: 처리 건수."""
    if not rows:
        return 0
    cols = ", ".join(COLUMNS)
    ph = ", ".join(["?"] * len(COLUMNS))
    with _lock, _db() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO ledger_tx({cols}) VALUES({ph})",
            [tuple(r.get(k) for k in COLUMNS) for r in rows],
        )
    return len(rows)


def get_tx(limit: int = 200, offset: int = 0,
           date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """최신(srno 내림차순)부터. 날짜(YYYYMMDD) 범위 필터 옵션."""
    where, params = [], []
    if date_from:
        where.append("tr_date >= ?"); params.append(date_from)
    if date_to:
        where.append("tr_date <= ?"); params.append(date_to)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT {', '.join(COLUMNS)} FROM ledger_tx{clause} "
           f"ORDER BY srno DESC LIMIT ? OFFSET ?")
    params += [int(limit), int(offset)]
    with _db() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def stats() -> dict:
    with _db() as c:
        row = c.execute(
            "SELECT COUNT(*) n, MIN(tr_date) mn, MAX(tr_date) mx FROM ledger_tx"
        ).fetchone()
    return {"rows": row["n"], "min_date": row["mn"], "max_date": row["mx"]}


def max_srno() -> int:
    with _db() as c:
        row = c.execute("SELECT MAX(srno) m FROM ledger_tx").fetchone()
    return int(row["m"]) if row and row["m"] is not None else 0
