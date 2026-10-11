"""KIS 실시간 시세 엔진 (해외선물/환율) — 하이브리드용.

WTI / 브렌트유 / USD-KRW 의 '현재가'를 실시간으로 유지하고, 구독자(SSE 핸들러)에게
tick 을 push 한다. 두 가지 모드로 동작한다.

- REAL : KIS 자격증명이 있고 KIS_ENABLE_WS=1 이면 KIS WebSocket 에 접속해 실제 체결가 수신.
         (해외선물옵션 API 사용신청/권한 필요. 종목코드는 .env 로 지정.)
- SIM  : 그 외에는 시뮬레이터로 1초마다 현실적인 랜덤워크 tick 생성.
         키가 없어도 실시간 파이프라인(엔진→SSE→브라우저)이 그대로 동작/검증 가능.

REAL 파싱 필드 인덱스 등은 KIS 문서에 맞춰 .env 로 조정할 수 있게 열어 두었다.
(이 환경에서 실계좌 검증이 불가하므로, 실전 적용 시 raw 로그로 인덱스 확인 권장.)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timezone, timedelta

from config import settings

KST = timezone(timedelta(hours=9))

# KIS 실시간이 담당하는 지표: key, 표시명, 단위, 시뮬레이터 기준가
KIS_INSTRUMENTS = [
    ("wti", "WTI", "$", 91.8),
    ("brent", "브렌트유", "$", 97.7),
    ("usdkrw", "USD/KRW", "₩", 1367.0),
]

# KIS WebSocket 도메인 (실전/모의)
WS_REAL = "ws://ops.koreainvestment.com:21000"
WS_PAPER = "ws://ops.koreainvestment.com:31000"


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


class RealtimeEngine:
    """실시간 가격 보관 + pub/sub."""

    def __init__(self) -> None:
        self.latest: dict[str, dict] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.mode = "sim"
        self.source = "KIS 시뮬레이터"
        self._task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------- #
    async def start(self) -> None:
        use_real = (not settings.use_mock) and os.getenv("KIS_ENABLE_WS", "").strip() == "1"
        if use_real:
            self.mode = "live"
            self.source = f"KIS 실시간 ({'모의' if settings.env == 'paper' else '실전'})"
            self._task = asyncio.create_task(self._run_real())
        else:
            self.mode = "sim"
            self.source = "KIS 시뮬레이터 (키 미설정)" if settings.use_mock else "KIS 시뮬레이터 (WS 비활성)"
            self._task = asyncio.create_task(self._run_sim())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # -- pub/sub ---------------------------------------------------------- #
    def snapshot(self) -> dict[str, dict]:
        return dict(self.latest)

    async def _publish(self, key: str, value: float) -> None:
        info = {"value": round(value, 4), "as_of": _now()}
        self.latest[key] = info
        for q in list(self.subscribers):
            try:
                q.put_nowait({"key": key, **info})
            except asyncio.QueueFull:
                pass

    # -- simulator -------------------------------------------------------- #
    async def _run_sim(self) -> None:
        rng = random.Random()
        prices = {k: base for k, _n, _u, base in KIS_INSTRUMENTS}
        for k, v in prices.items():
            await self._publish(k, v)
        while True:
            await asyncio.sleep(2.0)  # 갱신 주기(차분하게)
            for k in prices:
                vol = 0.00015 if k == "usdkrw" else 0.0005  # 변동성 축소
                prices[k] *= 1 + rng.uniform(-vol, vol)
                await self._publish(k, prices[k])

    # -- real KIS WebSocket ---------------------------------------------- #
    async def _approval_key(self) -> str:
        import httpx

        url = f"{settings.base_url}/oauth2/Approval"
        body = {
            "grant_type": "client_credentials",
            "appkey": settings.app_key,
            "secretkey": settings.app_secret,  # 이 엔드포인트는 secretkey 필드
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=body)
        r.raise_for_status()
        return r.json()["approval_key"]

    def _real_subs(self) -> list[tuple[str, str, str, int]]:
        """(key, tr_id, tr_key, price_index). .env 로 종목코드/필드 지정."""
        default_trid = os.getenv("KIS_WS_FUT_TRID", "HDFFF020")  # 해외선물 실시간체결가
        # price_index: 체결 프레임(^ 구분)에서 현재가 위치. 문서에 맞춰 조정.
        pidx = int(os.getenv("KIS_WS_PRICE_INDEX", "11"))
        subs = []
        for key in ("wti", "brent", "usdkrw"):
            tr_key = os.getenv(f"KIS_WS_{key.upper()}_KEY", "").strip()
            tr_id = os.getenv(f"KIS_WS_{key.upper()}_TRID", default_trid).strip()
            if tr_key:
                subs.append((key, tr_id, tr_key, pidx))
        return subs

    async def _run_real(self) -> None:
        try:
            import websockets

            subs = self._real_subs()
            if not subs:
                raise RuntimeError("KIS_WS_*_KEY(종목코드) 미설정")

            approval = await self._approval_key()
            ws_url = WS_PAPER if settings.env == "paper" else WS_REAL
            key_by_trkey = {trk: (k, pidx) for k, _t, trk, pidx in subs}

            async with websockets.connect(ws_url, ping_interval=None, max_size=None) as ws:
                for _k, tr_id, tr_key, _p in subs:
                    await ws.send(json.dumps({
                        "header": {"approval_key": approval, "custtype": "P",
                                   "tr_type": "1", "content-type": "utf-8"},
                        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
                    }))

                async for msg in ws:
                    await self._handle_real_msg(ws, msg, key_by_trkey)
        except Exception as exc:  # 연결/파싱 실패 → 시뮬레이터로 폴백
            self.mode = "sim"
            self.source = f"KIS 연결 실패 → 시뮬레이터 ({type(exc).__name__})"
            await self._run_sim()

    async def _handle_real_msg(self, ws, msg: str, key_by_trkey: dict) -> None:
        # 제어 프레임(JSON): 구독 응답 / PINGPONG
        if msg and msg[0] == "{":
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                return
            if data.get("header", {}).get("tr_id") == "PINGPONG":
                await ws.send(msg)  # 그대로 되돌려줌
            return

        # 실시간 데이터 프레임: "0|TR_ID|CNT|body"
        parts = msg.split("|")
        if len(parts) < 4:
            return
        tr_key_field = parts[3].split("^")
        # tr_key(종목코드)는 보통 body 첫 필드
        symbol = tr_key_field[0] if tr_key_field else ""
        match = key_by_trkey.get(symbol)
        if not match:
            return
        key, pidx = match
        try:
            value = float(tr_key_field[pidx])
        except (IndexError, ValueError):
            return
        await self._publish(key, value)


engine = RealtimeEngine()
