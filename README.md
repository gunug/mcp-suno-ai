# mcp_suno_ai

Suno AI 곡 생성 자동화를 MCP(Model Context Protocol) 서버로 노출하여 Claude Code 등에서 호출 가능하게 만든 프로젝트.

## 제공 도구

- `create_song(lyrics, styles, title?)` — Suno Create 페이지에서 Advanced 모드로 곡 2개를 생성하고 `./mp3/`에 mp3로 다운로드.

## 설치

```powershell
cd c:\onethelab\project\mcp_suno_ai
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 최초 1회 로그인

전용 Chrome 프로필이 `./chrome_suno_profile/`에 저장됩니다. 최초 호출 시 자동으로 브라우저 창이 열리고, 로그인되어 있지 않으면 그 창에서 직접 로그인하면 됩니다 (최대 5분 대기). 이후 호출부터는 자동 인증됩니다.

## Claude Code에 MCP 등록

프로젝트 루트(또는 워크스페이스)에서:

```powershell
claude mcp add suno-ai python c:\onethelab\project\mcp_suno_ai\server.py
```

또는 수동으로 `~/.claude.json` 등에 stdio 서버로 등록:

```json
{
  "mcpServers": {
    "suno-ai": {
      "command": "python",
      "args": ["c:\\onethelab\\project\\mcp_suno_ai\\server.py"]
    }
  }
}
```

등록 후 Claude Code 재시작.

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
  "files": ["c:/.../mp3/Midnight Compile.mp3", "c:/.../mp3/Midnight Compile_2.mp3"],
  "song_ids": ["<id1>", "<id2>"],
  "durations": {"<id1>": 159.0, "<id2>": 162.0},
  "message": "2곡 다운로드 완료."
}
```

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
mcp_suno_ai/
├── goal.md                 # 요구사항
├── server.py               # MCP 서버 엔트리
├── suno_automation.py      # Playwright 자동화 로직
├── requirements.txt
├── README.md
├── chrome_suno_profile/    # (자동 생성) Suno 전용 Chrome 프로필
└── mp3/                    # (자동 생성) 다운로드된 mp3
```

## 트러블슈팅

- `Create 버튼이 비활성 상태입니다` — Lyrics/Styles가 제대로 입력되지 않은 경우. UI 변경 가능성 있음.
- `Suno 로그인이 감지되지 않았습니다` — 5분 안에 로그인 미완료. 브라우저 창에서 로그인 후 재시도.
- `새로 생성된 곡 ID가 감지되지 않았습니다` — Suno 측 큐 적체 또는 계정 크레딧 부족.
- 다운로드된 mp3가 짧게 잘림 — `cdn1.suno.ai`가 아직 준비 안 된 경우. 시간을 두고 재시도하거나 `audiopipe` 대체.
