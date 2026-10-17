"""원장(거래내역) 로컬 저장소 — SQLite (.ledger.db).

메리츠 /accounts/v1/me/transactions 를 누적 보관한다(멱등 upsert).
- ledger_tx(srno PK, ...) : tran_srno(계좌 단위 일련번호)를 PK로 중복 자동 제거.

candle_store 와 동일한 패턴(동기 sqlite3 + WAL). 비동기에서는 asyncio.to_thread 로 감싼다.
"""
from __future__ import annotations

import re
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

EXEC_COLUMNS = [
    "exec_id", "oder_date", "market", "iscd", "name", "side", "qty",
    "price", "amount", "frcr", "currency", "status", "exec_time", "raw",
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
        c.execute(
            """CREATE TABLE IF NOT EXISTS ledger_exec(
                exec_id    TEXT PRIMARY KEY,   -- market:oder_date:oder_no
                oder_date  TEXT,
                market     TEXT,               -- 'overseas' | 'domestic'
                iscd       TEXT,
                name       TEXT,
                side       TEXT,               -- 'buy' | 'sell'
                qty        REAL,               -- 체결수량
                price      REAL,               -- 체결단가
                amount     REAL,               -- 원화 금액(국내) 또는 0
                frcr       REAL,               -- 외화 금액(해외)
                currency   TEXT,
                status     TEXT,
                exec_time  TEXT,
                raw        TEXT
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_date ON ledger_exec(oder_date)")


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


# --------------------------------------------------------------------------- #
# 체결내역(ledger_exec) + 정산/체결 병합
# --------------------------------------------------------------------------- #
def upsert_exec(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = ", ".join(EXEC_COLUMNS)
    ph = ", ".join(["?"] * len(EXEC_COLUMNS))
    with _lock, _db() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO ledger_exec({cols}) VALUES({ph})",
            [tuple(r.get(k) for k in EXEC_COLUMNS) for r in rows],
        )
    return len(rows)


def get_exec(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    where, params = [], []
    if date_from:
        where.append("oder_date >= ?"); params.append(date_from)
    if date_to:
        where.append("oder_date <= ?"); params.append(date_to)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _db() as c:
        return [dict(r) for r in c.execute(
            f"SELECT {', '.join(EXEC_COLUMNS)} FROM ledger_exec{clause} ORDER BY oder_date DESC",
            params).fetchall()]


_TRADE_RE = re.compile(r"주식.*(매수|매도)")


def _is_trade_name(nm: str | None) -> bool:
    return bool(_TRADE_RE.search(nm or ""))


def _sym_key(iscd: str | None) -> str:
    s = (iscd or "").strip().upper()
    return s if "." in s else "".join(ch for ch in s if ch.isdigit())


def _trade_key(date: str, iscd: str | None, side: str, qty) -> tuple:
    try:
        q = int(round(float(qty or 0)))
    except (TypeError, ValueError):
        q = 0
    return (date, _sym_key(iscd), side, q)


def _exec_to_display(e: dict) -> dict:
    buy = e.get("side") == "buy"
    ovs = e.get("market") == "overseas"
    smcd = ("해외주식 " + ("매수" if buy else "매도")) if ovs else ("주식매수" if buy else "주식매도")
    return {
        "srno": e.get("exec_id"), "tr_date": e.get("oder_date"),
        "io_cls_code": "2" if buy else "1", "tr_type_code": "",
        "smcd": "", "smcd_name": smcd, "iscd": e.get("iscd"), "name": e.get("name"),
        "qty": e.get("qty"), "amount": e.get("amount") or 0, "byng_unpr": e.get("price") or 0,
        "fee": 0, "tax": 0, "frcr_amt": e.get("frcr") or 0,
        "currency": e.get("currency"), "cncl_yn": "", "raw": e.get("raw") or "",
        "source": "exec",
    }


def _qint(qty) -> int:
    try:
        return int(round(float(qty or 0)))
    except (TypeError, ValueError):
        return 0


def _days_between(d1: str, d2: str):
    """d2 - d1 (일). YYYYMMDD. 파싱 실패 시 None."""
    try:
        return (datetime.strptime(d2, "%Y%m%d") - datetime.strptime(d1, "%Y%m%d")).days
    except (TypeError, ValueError):
        return None


SETTLE_LAG_DAYS = 6   # 체결→결제(정산) 지연 허용창(T+2 국내 ~ T+3 해외 + 주말)


def get_merged(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """정산(거래내역)+체결(주문내역)을 병합.

    매매(주식 매수/매도)는 '체결=거래일'을 정본으로 쓴다. 거래내역(정산)은 결제일
    기준이라 같은 매매가 결제일로 다시 찍히므로, 체결과 매칭되는 정산행(결제일)은
    숨긴다. 매칭=(종목·매수/매도·수량) + 정산일이 체결일+[0..LAG]일 창 안.
    매칭되는 체결이 없는 과거 매매(체결 수집창 밖)는 정산행(결제일)으로 남는다.
    현금/입출금/환전/이관 등 비매매 정산행은 그대로 둔다.
    """
    settle = get_tx(1000000, 0, date_from, date_to)
    execs = get_exec(date_from, date_to)

    # 체결 버킷: (종목, 매수매도, 수량) -> [체결들] (거래일 오름차순), 1:1 소비
    buckets: dict[tuple, list[dict]] = {}
    for e in execs:
        key = (_sym_key(e.get("iscd")), e.get("side"), _qint(e.get("qty")))
        buckets.setdefault(key, []).append(e)
    for lst in buckets.values():
        lst.sort(key=lambda e: e.get("oder_date") or "")
    consumed: set = set()

    rows: list[dict] = []
    for r in settle:
        if _is_trade_name(r["smcd_name"]):
            key = (_sym_key(r["iscd"]),
                   "buy" if r["io_cls_code"] == "2" else "sell", _qint(r["qty"]))
            matched = None
            for e in buckets.get(key, []):
                if id(e) in consumed:
                    continue
                gap = _days_between(e.get("oder_date") or "", r["tr_date"] or "")
                if gap is not None and 0 <= gap <= SETTLE_LAG_DAYS:
                    matched = e
                    break
            if matched is not None:
                consumed.add(id(matched))   # 체결이 대표 → 결제일 정산행 숨김
                continue
        r["source"] = "settle"
        rows.append(r)

    rows.extend(_exec_to_display(e) for e in execs)

    def _sortk(r):
        try:
            seq = int(r.get("srno"))
        except (TypeError, ValueError):
            seq = 0
        return (r.get("tr_date") or "", seq)

    rows.sort(key=_sortk, reverse=True)
    return rows


def exec_stats() -> dict:
    with _db() as c:
        row = c.execute("SELECT COUNT(*) n, MIN(oder_date) mn, MAX(oder_date) mx FROM ledger_exec").fetchone()
    return {"rows": row["n"], "min_date": row["mn"], "max_date": row["mx"]}
