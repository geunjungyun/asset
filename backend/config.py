"""애플리케이션 설정 로드.

.env 파일에서 KIS(한국투자증권) 자격증명과 실행 옵션을 읽어온다.
앱키가 비어 있으면 자동으로 mock 모드로 동작한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# backend/.env 우선 로드
load_dotenv(BASE_DIR / ".env")

# 실전투자 / 모의투자 도메인
REAL_BASE = "https://openapi.koreainvestment.com:9443"
PAPER_BASE = "https://openapivts.koreainvestment.com:29443"

# 주식잔고조회 tr_id (실전/모의)
TR_INQUIRE_BALANCE_REAL = "TTTC8434R"
TR_INQUIRE_BALANCE_PAPER = "VTTC8434R"


def _clean(value: str | None) -> str:
    return (value or "").strip()


@dataclass
class Settings:
    app_key: str
    app_secret: str
    account_no: str          # 예: "12345678-01"
    env: str                 # "real" | "paper"

    @property
    def base_url(self) -> str:
        return PAPER_BASE if self.env == "paper" else REAL_BASE

    @property
    def tr_balance(self) -> str:
        return TR_INQUIRE_BALANCE_PAPER if self.env == "paper" else TR_INQUIRE_BALANCE_REAL

    @property
    def cano(self) -> str:
        """계좌번호 앞 8자리 (종합계좌번호)."""
        return self.account_no.replace("-", "")[:8]

    @property
    def acnt_prdt_cd(self) -> str:
        """계좌 상품코드 뒤 2자리 (보통 01)."""
        digits = self.account_no.replace("-", "")
        return digits[8:10] if len(digits) >= 10 else "01"

    @property
    def use_mock(self) -> bool:
        """자격증명이 없으면 mock 모드."""
        return not (self.app_key and self.app_secret and self.account_no)


def get_settings() -> Settings:
    env = _clean(os.getenv("KIS_ENV")).lower() or "real"
    if env not in ("real", "paper"):
        env = "real"
    return Settings(
        app_key=_clean(os.getenv("KIS_APP_KEY")),
        app_secret=_clean(os.getenv("KIS_APP_SECRET")),
        account_no=_clean(os.getenv("KIS_ACCOUNT_NO")),
        env=env,
    )


settings = get_settings()


# --------------------------------------------------------------------------- #
# 메리츠증권 Open API 설정 (해외주식 계좌)
# 공식 저장소: github.com/meritz-securities/open-api
# 운영 서버만 존재(모의계좌 없음). 앱키가 계좌에 바인딩되어 계좌번호 불필요.
# --------------------------------------------------------------------------- #
MERITZ_BASE = "https://openapi.imeritz.com:9443"


@dataclass
class MeritzSettings:
    app_key: str
    app_secret: str
    base_url: str = MERITZ_BASE

    @property
    def enabled(self) -> bool:
        """앱키·시크릿이 모두 있으면 실연동."""
        return bool(self.app_key and self.app_secret)


def get_meritz() -> MeritzSettings:
    return MeritzSettings(
        app_key=_clean(os.getenv("MERITZ_APP_KEY")),
        app_secret=_clean(os.getenv("MERITZ_APP_SECRET")),
        base_url=_clean(os.getenv("MERITZ_BASE_URL")) or MERITZ_BASE,
    )


meritz_settings = get_meritz()
