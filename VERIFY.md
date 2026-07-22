# MCP 패치 적용 확인 가이드 (Suno AI 연결 없이)

이 패치(`/me` 기반 곡 감지 + 쿠키 자동수락 + 크레딧 사전점검)가 **실행 중 MCP 서버에 반영됐는지**를,
브라우저/크레딧 소모 없이 확인하는 순서.

빌드 식별자: **`me-detect-2026-06-09`**

---

## 1단계 — 소스 파일 확인 (연결 0, 가장 먼저)

파일이 패치돼 있는지 grep:

```
grep -n "BUILD = \"me-detect" suno_automation.py
grep -n "_wait_new_on_me\|_wait_rendered_on_me\|_dismiss_cookie_banner\|_ensure_enough_credits" suno_automation.py
grep -n "BUILD: me-detect" server.py
```

- 위가 다 잡히면 → **소스는 패치됨**(정상). 안 잡히면 패치 자체가 유실된 것.
- 단, 소스가 패치돼 있어도 **실행 중 서버가 그 코드를 로드했다는 보장은 아님** → 2단계로.

## 2단계 — 실행 중 서버 확인 (Suno 호출 없음, 핵심)

MCP 도구 **설명(description)** 은 서버가 시작될 때 클라이언트로 전달된다(= 브라우저/크레딧과 무관).
따라서 `create_song` 도구 설명에 빌드 마커가 보이는지로 런타임 코드 버전을 판별한다.

- Claude: `ToolSearch` 로 `select:mcp__suno-ai__create_song` 조회 → 반환된 description 첫 줄 확인.
- description에 **`[BUILD: me-detect-2026-06-09]`** 이 있으면 → **재시작된 서버가 패치 코드 로드함 = OK.**
- 없으면(옛 설명만 보이면) → **옛 코드 실행 중** → MCP 서버 또는 Claude Code **완전 재시작** 필요.
  ("리커넥트"만으로는 서버 프로세스가 안 바뀌어 반영 안 될 수 있음 — 실측으로 확인된 사례 2026-06-09.)

## 3단계 — (필요 시) 기능 probe (크레딧 0, 곡 생성 없음)

확실히 하려면 `download_songs` 로 **이미 존재하는** song_id 를 받아본다(생성 아님, 크레딧 0):

- 새 코드: `/me` 에서 길이를 읽어 결과 `durations` 에 **실제 초**(예: 250.0)가 들어옴 → OK.
- 옛 코드: create 페이지를 기다리다 타임아웃 → `durations` 가 **0.0** (그래도 파일은 받아짐).

예) `download_songs(["bdf046c2-3240-410d-8174-6d44d48a492c"])` → duration 250.0 이면 OK, 0.0 이면 옛 코드.
(이 id 는 과거 '파문' 곡. 없으면 /me 의 아무 최신 곡 id 사용.)

---

## 재시작 방법 (옛 코드일 때)

1. **Claude Code 완전 종료 후 재실행** (권장 — 모든 MCP 서버 재생성)
2. 또는 /mcp 설정에서 `suno-ai` 서버 제거 후 재추가
3. 또는 실행 중인 `mcp-suno-ai` 파이썬 프로세스 종료 후 재연결

> YouTube MCP 도 동일 원리. 새 `token.json` 은 이미 발급돼 있으니, 완전 재시작하면 `upload_video` 가 새 토큰으로 동작.
