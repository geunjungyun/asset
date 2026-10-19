"""캔들(시세) 로컬 저장소 — SQLite.

메리츠에서 받은 분봉/일봉을 누적 저장한다. 보관 무제한(분석용 데이터셋).
- candles(symbol, interval, ts, OHLCV)  : 중복은 PK(INSERT OR REPLACE)로 자동 제거
- watch(symbol, ...)                    : 수집 대상 레지스트리(보유/관심/섹터)

sqlite3는 동기 API라, 비동기(FastAPI) 컨텍스트에서는 asyncio.to_thread로 감싸 호출한다.
WAL 모드로 폴러(쓰기)와 차트요청(읽기)의 동시성을 확보한다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager

from config import BASE_DIR

DB_PATH = BASE_DIR / ".candles.db"
_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
    global _initialized
    with _lock, _db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS candles(
                symbol   TEXT NOT NULL,
                interval TEXT NOT NULL,
                ts       INTEGER NOT NULL,
                open     REAL, high REAL, low REAL, close REAL, vol REAL,
                PRIMARY KEY(symbol, interval, ts)
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_candles_sym_int_ts ON candles(symbol, interval, ts)")
        c.execute("CREATE TABLE IF NOT EXISTS names(symbol TEXT PRIMARY KEY, name TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, val TEXT)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS watch(
                symbol    TEXT PRIMARY KEY,
                market    TEXT,            -- 'overseas' | 'domestic'
                exch      TEXT,            -- 거래소코드(OQ/NY...) 해외만
                name      TEXT,
                role      TEXT,            -- 'held' | 'watch' | 'sector'
                intervals TEXT,            -- JSON 배열 예: ["5m","1d"]
                enabled   INTEGER DEFAULT 1
            )"""
        )
    _initialized = True


# --------------------------------------------------------------------------- #
# candles
# --------------------------------------------------------------------------- #
def upsert_candles(symbol: str, interval: str, bars) -> int:
    """bars: iterable of (ts, open, high, low, close, vol). 중복은 덮어씀."""
    rows = [
        (symbol, interval, int(ts), o, h, l, cl, v)
        for (ts, o, h, l, cl, v) in bars
        if ts is not None
    ]
    if not rows:
        return 0
    with _lock, _db() as c:
        c.executemany(
            "INSERT OR REPLACE INTO candles(symbol,interval,ts,open,high,low,close,vol)"
            " VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def get_candles(symbol: str, interval: str, start_ts: int | None = None,
                end_ts: int | None = None, limit: int | None = None) -> list[tuple]:
    """(ts, close, open, high, low, vol) 시간오름차순."""
    q = "SELECT ts, close, open, high, low, vol FROM candles WHERE symbol=? AND interval=?"
    args: list = [symbol, interval]
    if start_ts is not None:
        q += " AND ts>=?"; args.append(int(start_ts))
    if end_ts is not None:
        q += " AND ts<=?"; args.append(int(end_ts))
    q += " ORDER BY ts ASC"
    if limit:
        q += " LIMIT ?"; args.append(int(limit))
    with _db() as c:
        return c.execute(q, args).fetchall()


def closes_bulk(symbols: list[str], interval: str = "1d") -> dict[str, list[float]]:
    """여러 종목의 종가 시계열(오름차순)을 한 번에. {symbol: [close,...]}."""
    out: dict[str, list[float]] = {s: [] for s in symbols}
    if not symbols:
        return out
    qmarks = ",".join(["?"] * len(symbols))
    with _db() as c:
        rows = c.execute(
            f"SELECT symbol, close FROM candles WHERE interval=? AND symbol IN ({qmarks}) ORDER BY symbol, ts ASC",
            [interval, *symbols],
        ).fetchall()
    for sym, close in rows:
        if close is not None and sym in out:
            out[sym].append(close)
    return out


def delete_candles(symbol: str, interval: str) -> None:
    with _lock, _db() as c:
        c.execute("DELETE FROM candles WHERE symbol=? AND interval=?", (symbol, interval))


def has_dup_dates(symbol: str, interval: str) -> bool:
    """같은 날짜(KST)에 2개 이상 봉이 있으면 True(다중 소스 오염 감지)."""
    with _db() as c:
        rows = c.execute("SELECT ts FROM candles WHERE symbol=? AND interval=?", (symbol, interval)).fetchall()
    if not rows:
        return False
    dates = {(int(t[0]) + 32400) // 86400 for t in rows}  # KST 일 단위 버킷
    return len(rows) > len(dates)


def meta_get(key: str) -> str | None:
    with _db() as c:
        row = c.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(key: str, val: str) -> None:
    with _lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, val) VALUES(?,?)", (key, val))


def set_name(symbol: str, name: str) -> None:
    if not name:
        return
    with _lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO names(symbol, name) VALUES(?,?)", (symbol, name))


def names_bulk(symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    qmarks = ",".join(["?"] * len(symbols))
    with _db() as c:
        rows = c.execute(f"SELECT symbol, name FROM names WHERE symbol IN ({qmarks})", symbols).fetchall()
    return {s: n for s, n in rows}


def has_candles(symbol: str, interval: str, start_ts: int | None = None) -> int:
    q = "SELECT COUNT(*) FROM candles WHERE symbol=? AND interval=?"
    args: list = [symbol, interval]
    if start_ts is not None:
        q += " AND ts>=?"; args.append(int(start_ts))
    with _db() as c:
        return c.execute(q, args).fetchone()[0]


# --------------------------------------------------------------------------- #
# watch 레지스트리
# --------------------------------------------------------------------------- #
def upsert_watch(symbol: str, market: str, exch: str, name: str,
                 role: str, intervals: list[str]) -> None:
    with _lock, _db() as c:
        c.execute(
            "INSERT INTO watch(symbol,market,exch,name,role,intervals,enabled)"
            " VALUES(?,?,?,?,?,?,1)"
            " ON CONFLICT(symbol) DO UPDATE SET market=excluded.market, exch=excluded.exch,"
            " name=excluded.name, role=excluded.role, intervals=excluded.intervals",
            (symbol, market, exch, name, role, json.dumps(intervals)),
        )


def list_watch(enabled_only: bool = True) -> list[dict]:
    q = "SELECT symbol, market, exch, name, role, intervals, enabled FROM watch"
    if enabled_only:
        q += " WHERE enabled=1"
    with _db() as c:
        rows = c.execute(q).fetchall()
    return [
        {"symbol": r[0], "market": r[1], "exch": r[2], "name": r[3],
         "role": r[4], "intervals": json.loads(r[5] or "[]"), "enabled": r[6]}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# 통계/진단
# --------------------------------------------------------------------------- #
def stats() -> dict:
    with _db() as c:
        total = c.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        per = c.execute(
            "SELECT symbol, interval, COUNT(*), MIN(ts), MAX(ts) FROM candles"
            " GROUP BY symbol, interval ORDER BY symbol, interval"
        ).fetchall()
    return {
        "total_rows": total,
        "series": [
            {"symbol": r[0], "interval": r[1], "rows": r[2], "min_ts": r[3], "max_ts": r[4]}
            for r in per
        ],
    }
