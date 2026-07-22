"""Suno AI MCP 서버.

Claude Code 등 MCP 클라이언트에서 호출 가능한 도구:
    create_song(lyrics, styles, title?) → 곡 2개 생성 후 호출 측 CWD/mp3/ 에 저장

브라우저 자동화는 별도 스레드에서 실행 (sync_playwright 호환).
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from suno_automation import (
    REQUIRED_CREDITS,
    GenerateResult,
    SunoError,
    generate_songs,
    generate_songs_simple,
    request_songs,
    request_songs_simple,
    download_songs_by_ids,
)
from suno_tuned import generate_songs_tuned

mcp = FastMCP("suno-ai")


def _credit_note(result: GenerateResult) -> str:
    """성공 결과에 붙일 잔여 크레딧 안내/경고 문구 (판독 실패 시 빈 문자열)."""
    if result.credits_after is None:
        return ""
    note = f" | 남은 크레딧 약 {result.credits_after}" \
           f" (생성 {result.credits_after // REQUIRED_CREDITS}회분)"
    if result.credits_after < REQUIRED_CREDITS:
        note += " ⚠️ 다음 생성 불가 — 무료 크레딧 소진. 매일 리셋됨."
    return note


@mcp.tool()
async def create_song(lyrics: str, styles: str, title: str = "") -> dict:
    """[BUILD: me-detect-2026-06-09] 곡 감지 /me 기반 + 쿠키 자동수락 + 크레딧 사전점검 적용본.

    (검증용 마커: 이 도구 설명에 위 BUILD 문자열이 보이면 = 실행 중 서버가 패치 코드를 로드함.
     안 보이면 = 옛 코드 → MCP 서버/Claude Code 완전 재시작 필요.)

    Suno AI Advanced 모드로 곡 2개를 생성하고 mp3로 다운로드한다.

    동작:
    - 호출 측 CWD/prompt/YYYYMMDD_HHMMSS_<title>.md 에 입력 프롬프트 기록
    - 전용 Chrome 프로필(스크립트 위치/chrome_suno_profile)로 브라우저를 띄움
    - 로그인 미감지 시 창을 열어둔 채 사용자 로그인 대기 (최대 5분)
    - https://suno.com/create → Advanced → Lyrics/Styles/Title 입력 → Create
    - 곡 카드의 duration 표시로 렌더링 완료를 감지 (최대 5분)
    - 호출 측 CWD/mp3/ 폴더에 mp3 다운로드 (파일명: Suno 곡 제목)
    - 반환 경로는 CWD 기준 상대 경로 ("mp3/곡제목.mp3")

    Args:
        lyrics: 가사 (필수)
        styles: 음악 스타일 설명 (필수)
        title: 곡 제목 (선택, 빈 문자열이면 Suno가 자동 생성)

    Returns:
        성공 시 {"status": "success", "files": [...], "song_ids": [...], "durations": {...}}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        result: GenerateResult = await asyncio.to_thread(
            generate_songs, lyrics, styles, title
        )
        return {
            "status": "success",
            "files": result.files,
            "song_ids": result.song_ids,
            "durations": result.durations,
            "credits_before": result.credits_before,
            "credits_after": result.credits_after,
            "message": f"{len(result.files)}곡 다운로드 완료.{_credit_note(result)}",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


@mcp.tool()
async def create_song_tuned(
    lyrics: str,
    styles: str,
    title: str = "",
    weirdness: int | None = None,
    style_influence: int | None = None,
) -> dict:
    """create_song 과 동일하되, Advanced > More Options 의 Weirdness / Style Influence
    슬라이더를 지정 값으로 설정한 뒤 곡 2개를 생성·다운로드한다.

    기존 create_song 은 그대로 두고 분리된 경로다. 슬라이더 외 동작(2곡 생성,
    mp3 다운로드, prompt 로그)은 create_song 과 동일.

    Args:
        lyrics: 가사 (필수). 보컬 미요청 시 "[Instrumental]".
        styles: 음악 스타일 설명 (필수).
        title: 곡 제목 (선택).
        weirdness: 0~100 정수. None(기본)이면 Suno 기본값 50% 유지.
            높을수록 실험적·예측 불가한 결과.
        style_influence: 0~100 정수. None(기본)이면 50% 유지.
            높을수록 styles 설명을 더 강하게 반영.

    Returns:
        성공 시 {"status": "success", "files": [...], "song_ids": [...],
                "durations": {...}, "applied": {weirdness, style_influence}}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        result: GenerateResult = await asyncio.to_thread(
            generate_songs_tuned, lyrics, styles, title, weirdness, style_influence
        )
        return {
            "status": "success",
            "files": result.files,
            "song_ids": result.song_ids,
            "durations": result.durations,
            "applied": {"weirdness": weirdness, "style_influence": style_influence},
            "message": f"{len(result.files)}곡 다운로드 완료.",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


@mcp.tool()
async def create_song_simple(description: str, title: str = "", instrumental: bool = False) -> dict:
    """Suno AI Simple 모드로 곡 2개를 생성하고 mp3로 다운로드한다.

    동작:
    - 호출 측 CWD/prompt/YYYYMMDD_HHMMSS_<title>_simple.md 에 입력 프롬프트 기록
    - 전용 Chrome 프로필(스크립트 위치/chrome_suno_profile)로 브라우저를 띄움
    - 로그인 미감지 시 창을 열어둔 채 사용자 로그인 대기 (최대 5분)
    - https://suno.com/create → Simple 모드 유지 → Description/Title 입력 → Create
    - 곡 카드의 duration 표시로 렌더링 완료를 감지 (최대 5분)
    - 호출 측 CWD/mp3/ 폴더에 mp3 다운로드 (파일명: Suno 곡 제목)
    - 반환 경로는 CWD 기준 상대 경로 ("mp3/곡제목.mp3")

    Args:
        description: 원하는 곡의 분위기·장르·감정을 자연어로 설명 (필수)
                     예: "upbeat jazz cafe background music with piano and saxophone"
        title: 곡 제목 (선택, 빈 문자열이면 Suno가 자동 생성)
        instrumental: True이면 가사 없는 연주곡으로 생성 (기본값 False)

    Returns:
        성공 시 {"status": "success", "files": [...], "song_ids": [...], "durations": {...}}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        result: GenerateResult = await asyncio.to_thread(
            generate_songs_simple, description, title, instrumental
        )
        return {
            "status": "success",
            "files": result.files,
            "song_ids": result.song_ids,
            "durations": result.durations,
            "credits_before": result.credits_before,
            "credits_after": result.credits_after,
            "message": f"{len(result.files)}곡 다운로드 완료.{_credit_note(result)}",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


@mcp.tool()
async def request_song(lyrics: str, styles: str, title: str = "") -> dict:
    """Suno AI Advanced 모드로 생성 요청만 하고 song ID를 즉시 반환 (다운로드 없음).

    동작:
    - 프롬프트 로그 기록 후 Advanced 모드로 폼 제출
    - 곡 카드에 song ID가 나타나면 즉시 반환 (렌더링·다운로드 대기 없음)
    - 반환된 song_ids는 download_songs 도구에 넘겨 나중에 다운로드 가능

    Args:
        lyrics: 가사 (필수)
        styles: 음악 스타일 설명 (필수)
        title: 곡 제목 (선택)

    Returns:
        성공 시 {"status": "success", "song_ids": [...]}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        song_ids = await asyncio.to_thread(request_songs, lyrics, styles, title)
        return {
            "status": "success",
            "song_ids": song_ids,
            "message": f"생성 요청 완료. song_ids={song_ids}",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


@mcp.tool()
async def request_song_simple(description: str, title: str = "", instrumental: bool = False) -> dict:
    """Suno AI Simple 모드로 생성 요청만 하고 song ID를 즉시 반환 (다운로드 없음).

    동작:
    - 프롬프트 로그 기록 후 Simple 모드로 폼 제출
    - 곡 카드에 song ID가 나타나면 즉시 반환 (렌더링·다운로드 대기 없음)
    - 반환된 song_ids는 download_songs 도구에 넘겨 나중에 다운로드 가능

    Args:
        description: 원하는 곡의 분위기·장르·감정을 자연어로 설명 (필수)
        title: 곡 제목 (선택)
        instrumental: True이면 연주곡으로 생성 (기본값 False)

    Returns:
        성공 시 {"status": "success", "song_ids": [...]}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        song_ids = await asyncio.to_thread(request_songs_simple, description, title, instrumental)
        return {
            "status": "success",
            "song_ids": song_ids,
            "message": f"생성 요청 완료. song_ids={song_ids}",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


@mcp.tool()
async def download_songs(song_ids: list[str], title_hint: str = "") -> dict:
    """song ID 목록을 받아 렌더링 완료 후 mp3를 다운로드.

    동작:
    - request_song / request_song_simple 이 반환한 song_ids를 입력으로 받음
    - Suno create 페이지 피드에서 duration 표시를 감지해 렌더링 완료 확인 (최대 5분)
    - 완료된 곡을 CWD/mp3/ 에 다운로드

    Args:
        song_ids: 다운로드할 song ID 목록 (request_song* 반환값)
        title_hint: 파일명 fallback용 제목 힌트 (선택)

    Returns:
        성공 시 {"status": "success", "files": [...], "song_ids": [...], "durations": {...}}
        실패 시 {"status": "error", "message": "..."}
    """
    try:
        result: GenerateResult = await asyncio.to_thread(
            download_songs_by_ids, song_ids, title_hint
        )
        return {
            "status": "success",
            "files": result.files,
            "song_ids": result.song_ids,
            "durations": result.durations,
            "message": f"{len(result.files)}곡 다운로드 완료.",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


if __name__ == "__main__":
    mcp.run()
