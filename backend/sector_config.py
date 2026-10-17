"""섹터 추이 동적 구성 — 사용자가 편집 가능한 중분류(산업)·종목 계층.

대분류(11 GICS 섹터)는 고정(SPDR ETF 벤치마크). 그 안의 산업·종목은 서버의
.sector_config.json 에 저장되어 편집·영구 유지된다. 파일이 없으면 market.py의
기본값(SECTOR_ETFS/SECTOR_MEMBERS/INDUSTRY)으로 시드한다.

구조:
  {"sectors":[{"key","name","etf","industries":[{"name","tickers":[...]}]}]}
"""
from __future__ import annotations

import json
import threading

from config import BASE_DIR

CONFIG_PATH = BASE_DIR / ".sector_config.json"
_lock = threading.Lock()
_cache: dict | None = None


def _seed() -> dict:
    """market.py 기본값에서 섹터▸산업▸종목 계층을 만든다(지연 import로 순환 회피)."""
    import market  # noqa: WPS433 (lazy to avoid circular import)
    sectors = []
    for key, name, _grp, _unit, etf in market.SECTOR_ETFS:
        order: list[str] = []
        by_ind: dict[str, list[str]] = {}
        for sym in market.SECTOR_MEMBERS.get(key, []):
            ind = market.INDUSTRY.get(sym, "기타")
            if ind not in by_ind:
                by_ind[ind] = []
                order.append(ind)
            by_ind[ind].append(sym)
        sectors.append({
            "key": key, "name": name, "etf": etf,
            "industries": [{"name": ind, "tickers": by_ind[ind]} for ind in order],
        })
    return {"sectors": sectors}


def _write(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if CONFIG_PATH.exists():
        try:
            _cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return _cache
        except (json.JSONDecodeError, OSError):
            pass
    _cache = _seed()
    _write(_cache)
    return _cache


def _validate(cfg: dict) -> dict:
    """구조 검증·정규화(티커 대문자·중복 제거). 잘못되면 ValueError."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("sectors"), list):
        raise ValueError("sectors 리스트가 필요합니다")
    out = []
    for s in cfg["sectors"]:
        inds = []
        for ind in (s.get("industries") or []):
            seen, tickers = set(), []
            for t in (ind.get("tickers") or []):
                t = str(t).strip().upper()
                if t and t not in seen:
                    seen.add(t)
                    tickers.append(t)
            inds.append({"name": str(ind.get("name") or "기타").strip() or "기타", "tickers": tickers})
        out.append({
            "key": s.get("key"), "name": s.get("name"),
            "etf": s.get("etf", ""), "industries": inds,
        })
    return {"sectors": out}


# --------------------------------------------------------------------------- #
# 공개 API
# --------------------------------------------------------------------------- #
def get_config() -> dict:
    with _lock:
        return _load()


def save_config(cfg: dict) -> dict:
    cfg = _validate(cfg)
    with _lock:
        global _cache
        _cache = cfg
        _write(cfg)
    return cfg


def reset_config() -> dict:
    """구성을 코드 기본값(TAXONOMY)으로 재시드(파일 덮어씀)."""
    with _lock:
        global _cache
        _cache = _seed()
        _write(_cache)
    return _cache


def sectors() -> list[tuple]:
    """[(key, name, etf)] — 고정 대분류."""
    return [(s.get("key"), s.get("name"), s.get("etf", "")) for s in _load()["sectors"]]


def members(key: str) -> list[str]:
    """해당 섹터의 전체 종목(모든 산업 합)."""
    for s in _load()["sectors"]:
        if s.get("key") == key:
            out: list[str] = []
            for ind in s.get("industries", []):
                out.extend(ind.get("tickers", []))
            return out
    return []


def industry_of(sym: str) -> str:
    for s in _load()["sectors"]:
        for ind in s.get("industries", []):
            if sym in ind.get("tickers", []):
                return ind.get("name", "기타")
    return "기타"
