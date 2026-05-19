# mcp_suno_ai

Suno AI 곡 생성 자동화를 MCP(Model Context Protocol) 서버로 노출하여 Claude Code 등에서 호출 가능하게 만든 프로젝트.

## 제공 도구

- `create_song(lyrics, styles, title?)` — Suno Create 페이지에서 Advanced 모드로 곡 2개를 생성하고 **호출 측 CWD/mp3/** 폴더에 mp3로 다운로드.

## 저장 위치 정책

- **mp3 파일**: 호출 시점의 CWD(현재 작업 디렉터리) 하단 `./mp3/`. Claude Code가 어느 프로젝트에서 호출하느냐에 따라 그 프로젝트 안에 저장됨.
- **Chrome 프로필**: 스크립트 위치(`<프로젝트경로>/chrome_suno_profile/`) 고정. CWD가 어디든 항상 같은 프로필을 사용 — 재로그인 불필요.
- **반환 경로**: CWD 기준 상대 경로 (예: `"mp3/곡제목.mp3"`).

> 본 README의 `<프로젝트경로>`는 본인이 clone 받은 디렉터리(예: `D:\projects\mcp-suno-ai`)로 치환해서 읽으세요.

## 빠른 시작 — 다른 사용자가 clone 받았을 때

```powershell
# 1. clone & 진입
git clone https://github.com/gunug/mcp-suno-ai.git
cd mcp-suno-ai

# 2. 의존성 설치
python -m pip install -r requirements.txt
python -m playwright install chromium

# 3. Claude Code에 MCP 등록 (자기 경로로 치환!)
#    PowerShell은 백슬래시를 두 번 써야 함
claude mcp add suno-ai python "<프로젝트경로>\\server.py"
# 예: claude mcp add suno-ai python "D:\\projects\\mcp-suno-ai\\server.py"

# 4. 등록 확인
claude mcp list

# 5. Claude Code 재시작 후 첫 호출 → 브라우저가 자동으로 열림
#    본인 Suno 계정으로 로그인 (최대 5분 대기, 1회만)
```

이후 호출부터는 자동 인증되며, mp3는 호출 시점 CWD 하단 `./mp3/`에 저장됩니다.

별도 셋업이나 환경변수, 절대 경로 일치 같은 건 필요 없습니다. Chrome 로그인 세션과 다운로드된 mp3는 모두 `.gitignore` 처리되어 있어서 본인 계정으로 처음부터 시작합니다.

## 설치 (상세)

```powershell
cd <프로젝트경로>
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 최초 1회 로그인

전용 Chrome 프로필이 `<프로젝트경로>/chrome_suno_profile/`에 저장됩니다. 최초 호출 시 자동으로 브라우저 창이 열리고, 로그인되어 있지 않으면 그 창에서 직접 로그인하면 됩니다 (최대 5분 대기). 이후 호출부터는 자동 인증됩니다.

## Claude Code에 MCP 등록

### 방법 1: `claude mcp add` 명령 (권장)

PowerShell에서는 백슬래시가 이스케이프 처리되어 사라질 수 있으므로 **경로를 따옴표로 감싸고 백슬래시를 두 번** 써야 합니다.

```powershell
claude mcp add suno-ai python "<프로젝트경로>\\server.py"
```

기본 스코프는 `local`(현재 프로젝트 전용). 다른 스코프가 필요하면 `--scope user`(전역) 또는 `--scope project`(`.mcp.json` 공유) 옵션을 추가:

```powershell
claude mcp add suno-ai --scope user python "<프로젝트경로>\\server.py"
```

### 방법 2: 수동으로 `~/.claude.json` 편집

`C:\Users\<사용자>\.claude.json`을 열어 stdio 서버로 등록:

```json
{
  "mcpServers": {
    "suno-ai": {
      "command": "python",
      "args": ["<프로젝트경로>\\server.py"]
    }
  }
}
```

### 등록 확인

```powershell
claude mcp list
```

다음과 같이 `✓ Connected`로 표시되면 정상:

```
suno-ai: python <프로젝트경로>\server.py - ✓ Connected
```

등록 후 Claude Code 재시작. Claude Code 안에서는 `mcp__suno-ai__create_song` 도구로 호출됩니다.

### 등록 제거

```powershell
claude mcp remove suno-ai
```

### 트러블슈팅 (등록 단계)

- **경로에서 백슬래시가 사라짐** (예: `python conethelabprojectmcp_suno_aiserver.py` 로 등록됨) — PowerShell이 백슬래시를 잘못 해석한 경우. `claude mcp remove suno-ai`로 제거 후 위 권장 형식(따옴표 + `\\`)으로 재등록.
- **`✗ Failed to connect`** — `python`이 PATH에 없거나 `requirements.txt`의 패키지(`mcp`, `playwright` 등)가 설치되지 않은 경우. 설치 단계를 다시 확인.

## 사용 예 (Claude Code 안에서)

```
suno-ai의 create_song 도구를 호출해줘:
- lyrics: "..."
- styles: "Acoustic ballad, gentle, female vocal"
- title: "Midnight Compile"
```

## 반환 형식

```json
{
  "status": "success",
  "files": ["mp3/Midnight Compile.mp3", "mp3/Midnight Compile_2.mp3"],
  "song_ids": ["<id1>", "<id2>"],
  "durations": {"<id1>": 159.0, "<id2>": 162.0},
  "message": "2곡 다운로드 완료."
}
```

`files`의 경로는 **호출 측 CWD 기준 상대 경로**. 실제 파일은 `<CWD>/mp3/...`에 저장됨.

실패 시 `{"status": "error", "message": "..."}` 반환.

## 동작 흐름

1. Persistent Chromium 컨텍스트로 브라우저 열기 (`headless=False`, 봇 감지 우회 플래그)
2. `https://suno.com` 접속 → 아바타 요소로 로그인 상태 확인
3. 미로그인 시 사용자 로그인 대기 (최대 5분)
4. `https://suno.com/create` 이동 → Advanced 클릭 → Lyrics Mode를 Manual로
5. Lyrics, Styles, Title을 React 호환 방식으로 입력 (clipboard paste + native setter)
6. Create 클릭 → 새 `/song/<id>` 링크 2개 출현 대기 (최대 90초)
7. 곡 카드의 duration 텍스트(`X:XX`)와 spinner 부재로 렌더링 완료 감지 (최대 5분)
8. CDN 안정화 15초 대기 후, 각 곡 페이지의 `audio_url` → `cdn1.suno.ai` → `audiopipe` 순으로 다운로드 시도

## 폴더 구조

```
<프로젝트경로>/                # MCP 서버 코드 (clone 받은 위치)
├── goal.md                    # 요구사항
├── server.py                  # MCP 서버 엔트리
├── suno_automation.py         # Playwright 자동화 로직
├── requirements.txt
├── README.md
└── chrome_suno_profile/       # (자동 생성, gitignored) Suno 전용 Chrome 프로필

<호출 측 CWD>/                 # Claude Code가 실행 중인 디렉터리
└── mp3/                       # (자동 생성, gitignored) 다운로드된 mp3
```

> 참고: 예전에는 `mp3/`도 스크립트 위치 하단에 저장되었으나, 호출하는 프로젝트별로 결과물을 분리하기 위해 **CWD 기준**으로 변경되었습니다.

## 트러블슈팅

- `Create 버튼이 비활성 상태입니다` — Lyrics/Styles가 제대로 입력되지 않은 경우. UI 변경 가능성 있음.
- `Suno 로그인이 감지되지 않았습니다` — 5분 안에 로그인 미완료. 브라우저 창에서 로그인 후 재시도.
- `새로 생성된 곡 ID가 감지되지 않았습니다` — Suno 측 큐 적체 또는 계정 크레딧 부족.
- 다운로드된 mp3가 짧게 잘림 — `cdn1.suno.ai`가 아직 준비 안 된 경우. 시간을 두고 재시도하거나 `audiopipe` 대체.
- **mp3 파일이 예상한 위치에 없음** — Claude Code의 CWD를 확인하세요. 응답의 `files` 경로(예: `mp3/...`)는 CWD 기준 상대 경로이므로, `<현재 작업 디렉터리>/mp3/` 안에 저장됩니다. CWD를 모르겠다면 응답을 받기 전후로 Claude Code에서 `pwd` 또는 `Get-Location`을 실행해 확인 가능.
