# 배포 가이드 (Windows 서버)

개발(수정)은 개발 PC에서 하고, **git push → 서버에서 git pull** 로 반영한다.
서버는 `--reload` 로 띄워 두면 백엔드는 자동 재시작, 프론트(html/js/css)는 브라우저 새로고침만 하면 된다.

## 1. 최초 1회 셋업 (Windows 서버)

사전: **Git**, **Python 3.11+** 설치.

```powershell
git clone https://github.com/geunjungyun/asset.git
cd asset\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 필수 파일 2개 (git 에 없음)

1. **`backend\.env`** — 메리츠 키 입력:
   ```
   MERITZ_APP_KEY=발급받은_앱키
   MERITZ_APP_SECRET=발급받은_시크릿
   ```
2. **`backend\.candles.db`** (선택, 권장) — 개발 PC에서 복사하면 누적된 5분봉 보존.
   서버 끈 상태에서 `.candles.db`(+ `-wal`, `-shm` 있으면 함께) 복사. 없으면 켤 때 일봉은 자동 재적재되고 5분봉은 새로 누적.

### 방화벽
8000 포트 인바운드 허용(LAN 접속용):
```powershell
New-NetFirewallRule -DisplayName "AssetPro 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

## 2. 서버 실행

```powershell
cd asset\backend
.\run-server.ps1
```
→ `http://localhost:8000` / `http://<서버IP>:8000` (LAN·폰 접속). `--reload` 로 코드 변경 자동 반영.

## 3. 평소 업데이트 흐름

```
[개발 PC] 수정 → git commit → git push
[서버]    git pull  (아래 중 택1)
```

- **수동**: 서버에서 `cd asset\backend; .\update.ps1`
  (git pull + requirements 바뀌면 pip install 자동)
- **자동**: 한 번만 등록해 두면 N분마다 알아서 pull
  ```powershell
  # 관리자 PowerShell
  cd asset\backend
  .\register-autoupdate.ps1 -Minutes 2
  ```
  해제: `Unregister-ScheduledTask -TaskName "AssetPro-AutoUpdate" -Confirm:$false`

## 4. (선택) 부팅 시 자동 시작

작업 스케줄러에 "시스템 시작 시 `run-server.ps1` 실행" 작업을 추가하거나,
안정화 후에는 nssm 으로 서비스 등록 권장.

## 참고
- `.venv` 는 복사하지 말 것(OS·경로 종속). 서버에서 새로 생성.
- `.env`, `.candles.db`, `.meritz_token_cache.json`, `.market_cache.json` 은 git 무시 → pull 해도 안 덮어쓰임.
- 5분봉은 서버가 켜져 있는 동안만 누적되므로, 장중 데이터를 모으려면 서버를 상시 가동.
- 안정화(환경 고정·여러 대 배포) 단계에서는 Docker 전환 고려.
