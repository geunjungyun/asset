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
→ `http://localhost:8000` / `http://<서버IP>:8000` (LAN·폰 접속). 안정 모드(`--reload` 미사용), 코드 반영은 `update.ps1`이 자동 재시작으로 처리.

## 3. 평소 업데이트 흐름 (push만 하면 자동 반영)

```
[개발 PC] 수정 → git commit → git push
[서버]    (2분마다 자동) git pull
          ├ 프론트(js/css/html) 변경 → 그대로 반영(브라우저 새로고침)
          └ 백엔드(.py)/requirements 변경 → update.ps1 이 서버 자동 재시작
```

- **자동(권장)**: 한 번만 등록해 두면 N분마다 pull + 변경 시 서버 자동 재시작
  ```powershell
  # 관리자 PowerShell
  cd asset\backend
  .\register-autoupdate.ps1 -Minutes 2
  ```
  해제: `Unregister-ScheduledTask -TaskName "AssetPro-AutoUpdate" -Confirm:$false`
- **수동**: 서버에서 `cd asset\backend; .\update.ps1` (pull + 의존성 + 백엔드 변경 시 재시작)

> 자동 재시작은 `update.ps1`이 `AssetPro-Server` 작업을 Stop/Start 한다. 권한 때문에 재시작이 실패하면,
> `register-autoupdate.ps1`을 **관리자 권한으로 재등록**(작업이 Highest로 돌도록)하거나 수동 재시작한다.

## 4. 부팅 시 자동 시작

서버에서 **관리자 PowerShell** 로 1회 실행하면, 부팅 시 자동으로 서버가 뜬다
(로그인 안 해도 실행 · 꺼지면 1분 내 재시작):

```powershell
cd asset\backend
.\register-autostart.ps1
# SYSTEM 계정으로 돌리려면:  .\register-autostart.ps1 -System
```

- 지금 바로 시작: `Start-ScheduledTask -TaskName "AssetPro-Server"`
- 상태 확인: `Get-ScheduledTask -TaskName "AssetPro-Server" | Get-ScheduledTaskInfo`
- 해제: `Unregister-ScheduledTask -TaskName "AssetPro-Server" -Confirm:$false`

> 기본은 현재 사용자 + S4U(로그인 불필요, 암호 저장 안 함, 로컬 파일 접근 가능).
> 저장소가 `C:\Users\...` 가 아닌 경로(예: `D:\...`)라 SYSTEM 쓰기 권한이 걱정되면 기본값(현재 사용자)을 쓰면 된다.
> 안정화 후 여러 대 배포 단계에서는 nssm 서비스/Docker 전환 고려.

### 자동 시작 + 자동 업데이트 조합
- `register-autostart.ps1` : 부팅 시 서버 실행(`run-server.ps1`, `--reload`)
- `register-autoupdate.ps1` : N분마다 `git pull`(`update.ps1`)
→ 둘 다 등록하면 **PC만 켜두면 서버 자동 기동 + push 하면 자동 반영**.

## 참고
- `.venv` 는 복사하지 말 것(OS·경로 종속). 서버에서 새로 생성.
- `.env`, `.candles.db`, `.meritz_token_cache.json`, `.market_cache.json` 은 git 무시 → pull 해도 안 덮어쓰임.
- 5분봉은 서버가 켜져 있는 동안만 누적되므로, 장중 데이터를 모으려면 서버를 상시 가동.
- 안정화(환경 고정·여러 대 배포) 단계에서는 Docker 전환 고려.
