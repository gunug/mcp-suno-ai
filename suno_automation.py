"""Suno AI 곡 생성 자동화 코어 로직.

기존 91_make_mp3/suno_create.py의 검증된 셀렉터/대기 로직을 MCP 호출용으로 정리.
- 입력: lyrics, styles, title (직접 인자)
- 저장 위치: 호출 시점의 CWD 하단 ./mp3/
- 출력: 다운로드된 mp3 파일 경로 리스트 (CWD 기준 상대 경로, 예: "mp3/곡제목.mp3")
- 입력 기록: 생성 직전 ./prompt/YYYYMMDD_HHMMSS_<title>.md 작성
- 로그인 미감지 시 브라우저를 띄워둔 채 사용자 로그인 대기 (최대 5분)
- Chrome 프로필(PROFILE_DIR)은 스크립트 위치 기준 — 세션 영속성 보장
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mutagen.mp3 import MP3
from playwright.sync_api import sync_playwright, Page

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_suno_profile"
# MP3 저장 디렉터리는 호출 시점 CWD 기준 — generate_songs() 안에서 동적으로 계산
SUNO_CREATE_URL = "https://suno.com/create"
SUNO_HOME_URL = "https://suno.com"

LOGIN_WAIT_TIMEOUT_MS = 300_000  # 5분
GENERATION_TIMEOUT_SEC = 300     # 5분 (전체 대기)


class SunoError(Exception):
    """Suno 자동화 도중 발생한 오류."""


@dataclass
class GenerateResult:
    files: list[str] = field(default_factory=list)
    song_ids: list[str] = field(default_factory=list)
    durations: dict[str, float] = field(default_factory=dict)


# ---------- 유틸 ----------

_AVATAR_SELECTOR = (
    'button[aria-label="Open user menu"], '
    '[data-testid="user-menu"], '
    'img[alt*="avatar" i], '
    'button:has(img[class*="avatar" i]), '
    'div[class*="avatar" i]'
)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "song"


def _parse_duration_str(s: str) -> int:
    try:
        parts = s.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        pass
    return 0


def _mp3_duration(path: str) -> float:
    try:
        return MP3(path).info.length
    except Exception:
        return 0.0


def _write_prompt_log(prompt_dir: Path, lyrics: str, styles: str, title: str) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = _sanitize_filename(title) if title.strip() else "untitled"
    md_path = prompt_dir / f"{ts}_{name}.md"
    content = (
        f"## Title\n{title}\n\n"
        f"## Style of Music\n{styles}\n\n"
        f"## Lyrics\n{lyrics}\n"
    )
    md_path.write_text(content, encoding="utf-8")
    return md_path


# ---------- 로그인 ----------

def _ensure_logged_in(page: Page) -> bool:
    page.goto(SUNO_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)
    try:
        page.wait_for_selector(_AVATAR_SELECTOR, timeout=10_000)
        return True
    except Exception:
        pass

    # 미로그인 → 브라우저 띄워둔 채로 수동 로그인 대기
    try:
        page.wait_for_selector(_AVATAR_SELECTOR, timeout=LOGIN_WAIT_TIMEOUT_MS)
        return True
    except Exception:
        return False


# ---------- 곡 생성 ----------

def _paste_into(page: Page, element, text: str) -> None:
    element.click()
    page.wait_for_timeout(300)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(200)
    page.evaluate(
        """
        (text) => {
            const el = document.activeElement;
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const evt = new ClipboardEvent('paste', {
                clipboardData: dt, bubbles: true, cancelable: true
            });
            el.dispatchEvent(evt);
        }
        """,
        text,
    )
    page.wait_for_timeout(400)
    actual = element.input_value()
    if len(actual) < len(text) // 2:
        page.evaluate(
            """
            (text) => {
                const el = document.activeElement;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                setter.call(el, text);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            text,
        )
        page.wait_for_timeout(300)


def _fill_form_and_submit(page: Page, lyrics: str, styles: str, title: str) -> None:
    page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)

    # 오버레이 닫기
    if page.query_selector('div[class*="overlay"], div[class*="modal"]'):
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)

    # Advanced
    advanced = page.query_selector('button:has-text("Advanced")')
    if advanced:
        advanced.click()
        page.wait_for_timeout(800)

    # Lyrics Mode → Manual
    manual = page.query_selector('button:has-text("Manual")')
    if manual:
        manual.click(force=True)
        page.wait_for_timeout(500)

    page.wait_for_timeout(800)
    textareas = page.query_selector_all('textarea')
    if len(textareas) < 2:
        raise SunoError("Suno UI에서 Lyrics/Styles 입력 필드를 찾지 못했습니다.")

    _paste_into(page, textareas[0], lyrics)
    _paste_into(page, textareas[1], styles)

    if title:
        page.evaluate(
            """
            (title) => {
                const inputs = document.querySelectorAll(
                    'input[placeholder="Song Title (Optional)"]'
                );
                for (const inp of inputs) {
                    if (window.getComputedStyle(inp).visibility === 'visible') {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(inp, title);
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        return;
                    }
                }
            }
            """,
            title,
        )

    page.wait_for_timeout(800)

    create_btn = page.query_selector('button:has-text("Create")')
    if not create_btn:
        raise SunoError("Create 버튼을 찾지 못했습니다.")
    if create_btn.is_disabled():
        raise SunoError("Create 버튼이 비활성 상태입니다. 입력값을 확인하세요.")
    create_btn.click()


# ---------- 생성 완료 대기 ----------

def _existing_song_ids(page: Page) -> set[str]:
    ids: set[str] = set()
    for a in page.query_selector_all('a[href*="/song/"]'):
        href = a.get_attribute("href") or ""
        if "/song/" in href:
            ids.add(href.split("/song/")[-1].split("?")[0])
    return ids


def _wait_new_song_ids(page: Page, before: set[str], timeout_sec: int) -> list[str]:
    new_ids: list[str] = []
    start = time.time()
    while time.time() - start < timeout_sec:
        page.wait_for_timeout(5_000)
        for a in page.query_selector_all('a[href*="/song/"]'):
            href = a.get_attribute("href") or ""
            if "/song/" in href:
                sid = href.split("/song/")[-1].split("?")[0]
                if sid and sid not in before and sid not in new_ids:
                    new_ids.append(sid)
        if len(new_ids) >= 2:
            return new_ids
    return new_ids


def _check_completed(page: Page, song_ids: list[str]):
    """곡 카드에 duration 텍스트가 있고 spinner가 없으면 완료."""
    result = page.evaluate(
        """
        (songIds) => {
            const out = { allComplete: true, details: {} };
            for (const sid of songIds) {
                const link = document.querySelector(`a[href*="/song/${sid}"]`);
                if (!link) {
                    out.allComplete = false;
                    out.details[sid] = { complete: false, duration: null, title: null };
                    continue;
                }
                let card = link.closest('[data-testid="clip-row"]');
                if (!card) {
                    card = link;
                    for (let i = 0; i < 8 && card.parentElement; i++) {
                        card = card.parentElement;
                    }
                }
                const text = card.innerText || '';
                const m = text.match(/(\\d{1,2}:\\d{2})/);
                const hasDuration = !!m;
                const hasSpinner = !!card.querySelector('.animate-spin');
                const complete = hasDuration && !hasSpinner;
                if (!complete) out.allComplete = false;
                let title = null;
                const t = link.innerText && link.innerText.trim();
                if (t) title = t;
                out.details[sid] = {
                    complete, duration: m ? m[1] : null, title
                };
            }
            return out;
        }
        """,
        song_ids,
    )
    return result


def _wait_until_rendered(page: Page, song_ids: list[str], timeout_sec: int):
    start = time.time()
    while time.time() - start < timeout_sec:
        page.wait_for_timeout(5_000)
        r = _check_completed(page, song_ids)
        if r.get("allComplete"):
            return r
    return _check_completed(page, song_ids)


# ---------- 다운로드 ----------

def _get_audio_url(page: Page, song_id: str) -> str | None:
    try:
        page.goto(
            f"https://suno.com/song/{song_id}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(3_000)
        return page.evaluate(
            """
            () => {
                if (window.__NEXT_DATA__) {
                    const j = JSON.stringify(window.__NEXT_DATA__);
                    const m = j.match(/"audio_url":"(https?:\\/\\/[^"]+\\.mp3[^"]*)"/);
                    if (m) return m[1];
                }
                const audio = document.querySelector(
                    'audio source[type="audio/mpeg"], audio[src*=".mp3"]'
                );
                if (audio) return audio.src || audio.getAttribute('src');
                const body = document.body.innerHTML;
                const c = body.match(/(https?:\\/\\/cdn[^"'\\s]+\\.mp3[^"'\\s]*)/);
                if (c) return c[1];
                return null;
            }
            """
        )
    except Exception:
        return None


def _download_song(page: Page, song_id: str, target_path: str, expected_sec: float) -> bool:
    """전략 우선순위: audio_url → cdn1.suno.ai → audiopipe."""
    # 1) audio_url
    audio_url = _get_audio_url(page, song_id)
    if audio_url:
        try:
            resp = page.request.get(audio_url)
            if resp.status == 200 and len(resp.body()) > 10_000:
                with open(target_path, "wb") as f:
                    f.write(resp.body())
                actual = _mp3_duration(target_path)
                if expected_sec <= 0 or actual >= expected_sec * 0.9:
                    return True
        except Exception:
            pass

    # 2) cdn1.suno.ai
    try:
        resp = page.request.get(f"https://cdn1.suno.ai/{song_id}.mp3")
        if resp.status == 200 and len(resp.body()) > 10_000:
            with open(target_path, "wb") as f:
                f.write(resp.body())
            actual = _mp3_duration(target_path)
            if expected_sec <= 0 or actual >= expected_sec * 0.9:
                return True
    except Exception:
        pass

    # 3) audiopipe (재시도)
    for attempt in range(3):
        try:
            resp = page.request.get(f"https://audiopipe.suno.ai/?item_id={song_id}")
            with open(target_path, "wb") as f:
                f.write(resp.body())
            actual = _mp3_duration(target_path)
            if expected_sec <= 0 or actual >= expected_sec * 0.9:
                return True
        except Exception:
            pass
        if attempt < 2:
            time.sleep(20)

    p = Path(target_path)
    return p.exists() and p.stat().st_size > 10_000


def _resolve_filename(used: set[str], title_from_ui: str | None, fallback_title: str, song_id: str) -> str:
    base = title_from_ui or fallback_title or song_id[:12]
    base = _sanitize_filename(base)
    candidate = f"{base}.mp3"
    if candidate not in used:
        used.add(candidate)
        return candidate
    i = 2
    while True:
        candidate = f"{base}_{i}.mp3"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


# ---------- 엔트리포인트 ----------

def generate_songs(lyrics: str, styles: str, title: str = "") -> GenerateResult:
    """Suno에 곡 2개를 생성하고 ./mp3/에 다운로드.

    저장 위치: 호출 시점의 CWD 하단 ./mp3/
    반환 경로: CWD 기준 상대 경로 ("mp3/곡제목.mp3" 형식)

    생성 직전 ./prompt/YYYYMMDD_HHMMSS_<title>.md 에 입력 프롬프트(Title/Style/Lyrics)를 기록.
    """
    if not lyrics.strip():
        raise SunoError("lyrics가 비어 있습니다.")
    if not styles.strip():
        raise SunoError("styles가 비어 있습니다.")

    cwd = Path.cwd()
    mp3_dir = cwd / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    _write_prompt_log(cwd / "prompt", lyrics, styles, title)

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            if not _ensure_logged_in(page):
                raise SunoError("Suno 로그인이 감지되지 않았습니다 (5분 대기 초과).")

            before = _existing_song_ids(page) if page.url.startswith("https://suno.com") else set()
            page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            before |= _existing_song_ids(page)

            _fill_form_and_submit(page, lyrics, styles, title)

            # Phase 1: 새 song ID 출현 (최대 90초)
            new_ids = _wait_new_song_ids(page, before, timeout_sec=90)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다.")

            # 새 곡은 보통 2개; 더 많거나 적게 잡혀도 그대로 진행
            new_ids = new_ids[:2]

            # Phase 2: UI 렌더링 완료 대기 (duration 표시 & spinner 없음)
            status = _wait_until_rendered(page, new_ids, timeout_sec=GENERATION_TIMEOUT_SEC)
            details = status.get("details", {})

            # CDN 안정화 대기
            page.wait_for_timeout(15_000)

            durations: dict[str, float] = {}
            files: list[str] = []
            used_names: set[str] = set()

            for sid in new_ids:
                info = details.get(sid, {})
                dur_str = info.get("duration")
                ui_title = info.get("title")
                expected_sec = float(_parse_duration_str(dur_str)) if dur_str else 0.0
                durations[sid] = expected_sec

                filename = _resolve_filename(used_names, ui_title, title, sid)
                target = mp3_dir / filename

                ok = _download_song(page, sid, str(target), expected_sec)
                if ok and target.exists():
                    rel_path = target.relative_to(cwd).as_posix()
                    files.append(rel_path)

            if not files:
                raise SunoError(
                    f"곡 생성은 감지되었으나 다운로드에 실패했습니다 (song_ids={new_ids})."
                )

            return GenerateResult(files=files, song_ids=new_ids, durations=durations)
        finally:
            browser.close()
