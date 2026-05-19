"""Suno AI MCP 서버.

Claude Code 등 MCP 클라이언트에서 호출 가능한 도구:
    create_song(lyrics, styles, title?) → 곡 2개 생성 후 호출 측 CWD/mp3/ 에 저장

브라우저 자동화는 별도 스레드에서 실행 (sync_playwright 호환).
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from suno_automation import GenerateResult, SunoError, generate_songs

mcp = FastMCP("suno-ai")


@mcp.tool()
async def create_song(lyrics: str, styles: str, title: str = "") -> dict:
    """Suno AI Advanced 모드로 곡 2개를 생성하고 mp3로 다운로드한다.

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
            "message": f"{len(result.files)}곡 다운로드 완료.",
        }
    except SunoError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"예상치 못한 오류: {e!r}"}


if __name__ == "__main__":
    mcp.run()
