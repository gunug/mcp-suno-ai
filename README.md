# mcp_suno_ai

Suno AI 곡 생성 자동화를 MCP(Model Context Protocol) 서버로 노출하여 Claude Code 등에서 호출 가능하게 만든 프로젝트.

## 제공 도구 (5개)

### 생성 + 다운로드 일괄

| 도구 | 설명 |
|------|------|
| `create_song(lyrics, styles, title?)` | Advanced 모드 — 가사·스타일 입력, 생성부터 mp3 다운로드까지 한 번에 |
| `create_song_simple(description, title?, instrumental?)` | Simple 모드 — 자연어 설명 한 줄로 생성부터 mp3 다운로드까지 한 번에 |

### 생성 요청만 (빠른 반환)

| 도구 | 설명 |
|------|------|
| `request_song(lyrics, styles, title?)` | Advanced 모드 — 폼 제출 후 song ID만 즉시 반환 (다운로드 없음) |
| `request_song_simple(description, title?, instrumental?)` | Simple 모드 — 폼 제출 후 song ID만 즉시 반환 (다운로드 없음) |

### 다운로드만

| 도구 | 설명 |
|------|------|
| `download_songs(song_ids, title_hint?)` | song ID 목록을 받아 렌더링 완료 후 mp3 다운로드 |

---

## 저장 위치 정책

- **mp3 파일**: 호출 시점의 CWD(현재 작업 디렉터리) 하단 `./mp3/`. Claude Code가 어느 프로젝트에서 호출하느냐에 따라 그 프로젝트 안에 저장됨.
- **프롬프트 로그**: 호출 시점 CWD 하단 `./prompt/`. 생성 직전 입력 내용을 `YYYYMMDD_HHMMSS_<title>.md` 형식으로 기록.
- **Chrome 프로필**: 스크립트 위치(`<프로젝트경로>/chrome_suno_profile/`) 고정. CWD가 어디든 항상 같은 프로필을 사용 — 재로그인 불필요.
- **반환 경로**: CWD 기준 상대 경로 (예: `"mp3/곡제목.mp3"`).

> 본 README의 `<프로젝트경로>`는 본인이 clone 받은 디렉터리(예: `D:\projects\mcp-suno-ai`)로 치환해서 읽으세요.

---

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

---

## 설치 (상세)

```powershell
cd <프로젝트경로>
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 최초 1회 로그인

전용 Chrome 프로필이 `<프로젝트경로>/chrome_suno_profile/`에 저장됩니다. 최초 호출 시 자동으로 브라우저 창이 열리고, 로그인되어 있지 않으면 그 창에서 직접 로그인하면 됩니다 (최대 5분 대기). 이후 호출부터는 자동 인증됩니다.

---

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

등록 후 Claude Code 재시작.

### 등록 제거

```powershell
claude mcp remove suno-ai
```

### 트러블슈팅 (등록 단계)

- **경로에서 백슬래시가 사라짐** — PowerShell이 백슬래시를 잘못 해석한 경우. `claude mcp remove suno-ai`로 제거 후 위 권장 형식(따옴표 + `\\`)으로 재등록.
- **`✗ Failed to connect`** — `python`이 PATH에 없거나 `requirements.txt`의 패키지(`mcp`, `playwright` 등)가 설치되지 않은 경우. 설치 단계를 다시 확인.

---

## 사용 예 (Claude Code 안에서)

### 일괄 방식 — Advanced 모드

```
create_song 도구 호출:
- lyrics: "[Verse]\n코드 한 줄 한 줄\n..."
- styles: "Acoustic ballad, gentle, female vocal"
- title: "Midnight Compile"
```

### 일괄 방식 — Simple 모드

```
create_song_simple 도구 호출:
- description: "upbeat jazz cafe background music with piano and saxophone"
- title: "Cafe Jazz"
- instrumental: true
```

### 분리 방식 — 요청 후 나중에 다운로드

```
# Step 1: 생성 요청 (빠르게 반환)
request_song_simple 도구 호출:
- description: "chill lo-fi hip hop, rainy night, nostalgic"

# → song_ids: ["abc123", "def456"] 반환됨

# Step 2: 렌더링 완료 후 다운로드
download_songs 도구 호출:
- song_ids: ["abc123", "def456"]
```

---

## 반환 형식

### create_song / create_song_simple / download_songs

```json
{
  "status": "success",
  "files": ["mp3/Midnight Compile.mp3", "mp3/Midnight Compile_2.mp3"],
  "song_ids": ["<id1>", "<id2>"],
  "durations": {"<id1>": 159.0, "<id2>": 162.0},
  "message": "2곡 다운로드 완료."
}
```

### request_song / request_song_simple

```json
{
  "status": "success",
  "song_ids": ["<id1>", "<id2>"],
  "message": "생성 요청 완료. song_ids=[...]"
}
```

실패 시 공통: `{"status": "error", "message": "..."}` 반환.

`files`의 경로는 **호출 측 CWD 기준 상대 경로**. 실제 파일은 `<CWD>/mp3/...`에 저장됨.

---

## 동작 흐름

### 일괄 방식 (create_song / create_song_simple)

1. Persistent Chromium 컨텍스트로 브라우저 열기 (`headless=False`, 봇 감지 우회)
2. `https://suno.com` 접속 → 아바타 요소로 로그인 상태 확인
3. 미로그인 시 사용자 로그인 대기 (최대 5분)
4. `https://suno.com/create` 이동
   - **Advanced**: Advanced 클릭 → Lyrics Mode를 Manual로 → Lyrics / Styles / Title 입력
   - **Simple**: Simple 모드 유지 → Description / Title / Instrumental 입력
5. Create 클릭 → 새 `/song/<id>` 링크 2개 출현 대기 (최대 90초)
6. 곡 카드의 duration 텍스트(`X:XX`)와 spinner 부재로 렌더링 완료 감지 (최대 5분)
7. CDN 안정화 15초 대기 후, `audio_url` → `cdn1.suno.ai` → `audiopipe` 순으로 다운로드

### 분리 방식 (request_song* + download_songs)

**request_song / request_song_simple**
1~5번 동일, song ID 2개가 확인되면 **즉시 브라우저 종료 후 반환** (렌더링·다운로드 없음)

**download_songs**
1. 브라우저 열기 + 로그인 확인
2. `https://suno.com/create` 이동 → 피드에서 해당 song ID의 duration 표시 대기 (최대 5분)
3. CDN 안정화 15초 대기 후 다운로드

---

## 폴더 구조

```
<프로젝트경로>/                # MCP 서버 코드 (clone 받은 위치)
├── server.py                  # MCP 서버 엔트리
├── suno_automation.py         # Playwright 자동화 로직
├── requirements.txt
├── README.md
└── chrome_suno_profile/       # (자동 생성, gitignored) Suno 전용 Chrome 프로필

<호출 측 CWD>/                 # Claude Code가 실행 중인 디렉터리
├── mp3/                       # (자동 생성) 다운로드된 mp3
└── prompt/                    # (자동 생성) 생성 요청 로그 (.md)
```

---

## 트러블슈팅

- `Create 버튼이 비활성 상태입니다` — Lyrics/Styles 또는 Description이 제대로 입력되지 않은 경우. UI 변경 가능성 있음.
- `Suno 로그인이 감지되지 않았습니다` — 5분 안에 로그인 미완료. 브라우저 창에서 로그인 후 재시도.
- `새로 생성된 곡 ID가 감지되지 않았습니다` — Suno 측 큐 적체 또는 계정 크레딧 부족.
- `Simple 모드에서 Description 입력 필드를 찾지 못했습니다` — Suno UI 변경으로 textarea 셀렉터가 달라진 경우. `suno_automation.py`의 `_fill_form_simple_and_submit` 안 셀렉터 목록을 수정.
- 다운로드된 mp3가 짧게 잘림 — CDN이 아직 준비 안 된 경우. 시간을 두고 재시도하거나 `audiopipe` 대체.
- **mp3 파일이 예상한 위치에 없음** — Claude Code의 CWD를 확인하세요. 응답의 `files` 경로는 CWD 기준 상대 경로이므로, `<현재 작업 디렉터리>/mp3/` 안에 저장됩니다. CWD를 모르겠다면 Claude Code에서 `Get-Location`을 실행해 확인.
