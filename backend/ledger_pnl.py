"""손익 요약 계산 — 실현(네이티브) + 미실현(보유) = 총수익.

메리츠 네이티브 실현손익(profit-loss)과 현재 보유(미실현)를 종목별로 합산한다.
라이브 조회(요청 시점). 종목 원통화 + 고시환율 KRW 환산.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import meritz_client

KST = timezone(timedelta(hours=9))


def _f(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _digits(s: str | None) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _osym(s: str | None) -> str:
    return (s or "").upper().split(".")[0].strip()


def _ok(x):
    return None if isinstance(x, BaseException) else x


async def build_pnl() -> dict:
    ovs_bal, dom_bal, dom_real, ovs_real, fx = await asyncio.gather(
        meritz_client.get_balance(),
        meritz_client.get_domestic_balance(),
        meritz_client.get_domestic_pnl_symbols(),
        meritz_client.get_overseas_pnl(),
        meritz_client.get_fx_rate("USD"),
        return_exceptions=True,
    )
    ovs_bal = _ok(ovs_bal) or {}
    dom_bal = _ok(dom_bal) or {}
    dom_real = _ok(dom_real) or []
    ovs_real = _ok(ovs_real) or []
    fx = _ok(fx) or 0.0

    syms: dict[str, dict] = {}

    def rec(key: str, market: str, name: str) -> dict:
        if key not in syms:
            syms[key] = {
                "key": key, "market": market, "code": "", "name": name or "",
                "currency": "USD" if market == "overseas" else "KRW",
                "qty": 0, "avg": 0.0, "price": 0.0,
                "unreal": 0.0, "unreal_pct": None,
                "real": 0.0, "real_pct": None, "held": False,
            }
        return syms[key]

    # 미실현(보유)
    for h in (ovs_bal.get("holdings") or []):
        r = rec("O:" + _osym(h.get("code")), "overseas", h.get("name") or h.get("code"))
        r.update(code=h.get("code") or r["code"], name=h.get("name") or r["name"],
                 qty=h.get("quantity") or 0, avg=_f(h.get("avg_price")), price=_f(h.get("current_price")),
                 unreal=_f(h.get("eval_pnl")), unreal_pct=h.get("pnl_rate"),
                 currency=h.get("currency") or "USD", held=True)
    for h in (dom_bal.get("holdings") or []):
        r = rec("D:" + _digits(h.get("code")), "domestic", h.get("name") or h.get("code"))
        r.update(code=h.get("code") or r["code"], name=h.get("name") or r["name"],
                 qty=h.get("quantity") or 0, avg=_f(h.get("avg_price")), price=_f(h.get("current_price")),
                 unreal=_f(h.get("eval_pnl")), unreal_pct=h.get("pnl_rate"),
                 currency="KRW", held=True)

    # 실현(국내 종목별)
    for p in dom_real:
        r = rec("D:" + _digits(p.get("iscd")), "domestic", p.get("isnm") or "")
        if not r["name"]:
            r["name"] = p.get("isnm") or ""
        r["real"] = _f(p.get("prls_amt"))
        r["real_pct"] = _f(p.get("plrt")) if str(p.get("plrt") or "") != "" else None
        r["currency"] = "KRW"

    # 실현(해외) — 통화 단위는 데이터 확인 전까지 USD 가정(×환율). 데이터 생기면 검증 필요.
    for p in ovs_real:
        sym = p.get("iscd") or p.get("ovrs_stck_symb_iscd") or ""
        r = rec("O:" + _osym(sym), "overseas", p.get("isnm") or p.get("kor_isnm") or "")
        if not r["name"]:
            r["name"] = p.get("isnm") or p.get("kor_isnm") or ""
        r["real"] = _f(p.get("prls_amt"))
        r["real_pct"] = _f(p.get("plrt")) if str(p.get("plrt") or "") != "" else None

    out, real_krw, unreal_krw = [], 0.0, 0.0
    for r in syms.values():
        mul = fx if r["market"] == "overseas" else 1.0
        r["unreal_krw"] = round(r["unreal"] * mul)
        r["real_krw"] = round(r["real"] * mul)
        r["total"] = round(r["unreal"] + r["real"], 2)
        r["total_krw"] = round(r["unreal_krw"] + r["real_krw"])
        real_krw += r["real_krw"]
        unreal_krw += r["unreal_krw"]
        out.append(r)

    out.sort(key=lambda r: (not r["held"], -abs(r["total_krw"])))

    return {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "fx": fx,
        "summary": {
            "total_krw": round(real_krw + unreal_krw),
            "real_krw": round(real_krw),
            "unreal_krw": round(unreal_krw),
        },
        "symbols": out,
    }
