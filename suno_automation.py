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

# 패치 빌드 식별자 — 소스/런타임 검증용. /me 기반 곡 감지 + 쿠키 자동수락 + 크레딧 사전점검.
BUILD = "me-detect-2026-06-09"

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_suno_profile"
# MP3 저장 디렉터리는 호출 시점 CWD 기준 — generate_songs() 안에서 동적으로 계산
SUNO_CREATE_URL = "https://suno.com/create"
SUNO_HOME_URL = "https://suno.com"
SUNO_ME_URL = "https://suno.com/me"

LOGIN_WAIT_TIMEOUT_MS = 300_000  # 5분
GENERATION_TIMEOUT_SEC = 300     # 5분 (전체 대기)
REQUIRED_CREDITS = 10            # create_song(2곡) 1회 소비량 (무료 v4.5 관측치: 30→20→10)


class SunoError(Exception):
    """Suno 자동화 도중 발생한 오류."""


@dataclass
class GenerateResult:
    files: list[str] = field(default_factory=list)
    song_ids: list[str] = field(default_factory=list)
    durations: dict[str, float] = field(default_factory=dict)
    credits_before: int | None = None  # 생성 직전 잔여 크레딧 (판독 실패 시 None)
    credits_after: int | None = None   # 생성 후 잔여 추정 (credits_before - REQUIRED_CREDITS)


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


def _dismiss_cookie_banner(page: Page) -> None:
    """OneTrust 쿠키 동의 배너를 수락해 닫는다.

    무료 계정/새 프로필 상태에서는 'Accept All Cookies' 배너가 페이지 하단에 떠
    Create 버튼(생성 패널 하단) 클릭을 가로채 생성이 시작되지 않는다. (Pro 프로필은
    과거에 쿠키를 수락해둬 배너가 없었음.) 배너가 있으면 수락 버튼을 눌러 제거한다.
    """
    for sel in (
        "#onetrust-accept-btn-handler",
        'button:has-text("Accept All Cookies")',
        'button:has-text("Accept All")',
        "#onetrust-banner-sdk button",
    ):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(800)
                return
        except Exception:
            pass


def _read_credits(page: Page) -> int | None:
    """상단바의 남은 크레딧 수를 읽는다. 패턴 미발견/오류 시 None (안전: 차단 안 함)."""
    try:
        return page.evaluate(
            r"""
            () => {
              const body = document.body.innerText || '';
              const m = body.match(/([\d,]+)\s*Credits/i);
              return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
            }
            """
        )
    except Exception:
        return None


def _ensure_enough_credits(page: Page) -> int | None:
    """남은 크레딧이 부족하면 SunoError 로 즉시 차단(낭비 방지). 판독 불가면 통과.

    Returns: 읽은 크레딧 수 (None 이면 판독 실패).
    """
    credits = _read_credits(page)
    if credits is not None and credits < REQUIRED_CREDITS:
        raise SunoError(
            f"Suno 크레딧 부족: 현재 {credits} 크레딧 (생성 1회에 약 {REQUIRED_CREDITS} 필요). "
            f"무료 크레딧은 매일 리셋됩니다 — 내일 다시 시도하거나 Suno 구독/충전이 필요합니다."
        )
    return credits


def _fill_form_and_submit(page: Page, lyrics: str, styles: str, title: str) -> int | None:
    page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)

    # 쿠키 동의 배너 수락 (Create 버튼 클릭 가로채기 방지)
    _dismiss_cookie_banner(page)

    # 크레딧 사전 점검 — 부족하면 폼 입력 전에 명확한 메시지로 즉시 중단
    credits_before = _ensure_enough_credits(page)

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
        raise SunoError(
            "Create 버튼이 비활성 상태입니다. 입력값 또는 크레딧 잔액을 확인하세요."
        )
    create_btn.click()
    return credits_before


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


# ---------- /me 페이지 기반 곡 감지 (현재 Suno UI) ----------
# 현재 Suno UI 에서 create 페이지 워크스페이스에는 생성된 곡이 a[href*="/song/"] 로
# 노출되지 않는다(워크스페이스가 /studio 로 이동). 생성된 곡은 /me 페이지에
# /song/<uuid> 링크로 나타나므로, 새 곡 감지/렌더대기는 /me 에서 수행한다.

def _collect_me_songs(page: Page) -> list[dict]:
    """현재 /me 페이지의 곡 목록을 (id, title, dur, spin) 순서대로 수집 (최신순)."""
    return page.evaluate(
        r"""
        () => {
          const idRe = /\/song\/([0-9a-f-]{36})/i;
          const out = []; const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/song/"]')) {
            const m = (a.getAttribute('href')||'').match(idRe);
            if (!m) continue;
            const id = m[1];
            if (seen.has(id)) continue; seen.add(id);
            let card = a;
            for (let i=0;i<6 && card.parentElement;i++) card = card.parentElement;
            const text = (card.innerText||'').replace(/\s+/g,' ').trim();
            const dur = (text.match(/\b(\d{1,2}:\d{2})\b/)||[])[1] || null;
            const spin = !!card.querySelector('.animate-spin');
            const title = (a.innerText||'').trim() || null;
            out.push({id, title, dur, spin});
          }
          return out.slice(0, 40);
        }
        """
    )


def _goto_me(page: Page) -> None:
    page.goto(SUNO_ME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)
    _dismiss_cookie_banner(page)
    page.wait_for_timeout(1_500)


def _me_ids(page: Page) -> set[str]:
    """제출 전 /me 의 기존 song id 집합 (새 곡 식별 기준선)."""
    _goto_me(page)
    return {s["id"] for s in _collect_me_songs(page)}


def _wait_new_on_me(page: Page, before: set[str], timeout_sec: int) -> list[str]:
    """submit 후 /me 를 새로고침하며 before 에 없는 새 song id 2개를 기다린다."""
    start = time.time()
    new_ids: list[str] = []
    while time.time() - start < timeout_sec:
        _goto_me(page)
        new_ids = [s["id"] for s in _collect_me_songs(page) if s["id"] not in before]
        if len(new_ids) >= 2:
            return new_ids[:2]
        page.wait_for_timeout(6_000)
    return new_ids[:2]


def _wait_rendered_on_me(page: Page, ids: list[str], timeout_sec: int) -> dict:
    """ids 가 /me 에서 duration 표시 & spinner 없음(렌더 완료)이 될 때까지 대기.

    Returns: {id: {"duration": "m:ss"|None, "title": str|None}}
    """
    start = time.time()
    details: dict = {sid: {"duration": None, "title": None} for sid in ids}
    while time.time() - start < timeout_sec:
        _goto_me(page)
        songs = {s["id"]: s for s in _collect_me_songs(page)}
        all_done = True
        for sid in ids:
            s = songs.get(sid)
            if not s:
                all_done = False
                continue
            details[sid] = {"duration": s.get("dur"), "title": s.get("title")}
            if not s.get("dur") or s.get("spin"):
                all_done = False
        if all_done:
            return details
        page.wait_for_timeout(6_000)
    return details


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


# ---------- Simple 모드 전용 ----------

def _write_prompt_log_simple(prompt_dir: Path, description: str, title: str, instrumental: bool) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = _sanitize_filename(title) if title.strip() else "untitled"
    md_path = prompt_dir / f"{ts}_{name}_simple.md"
    content = (
        f"## Title\n{title}\n\n"
        f"## Instrumental\n{'Yes' if instrumental else 'No'}\n\n"
        f"## Description\n{description}\n"
    )
    md_path.write_text(content, encoding="utf-8")
    return md_path


def _first_visible(page: Page, selectors: list[str]):
    """셀렉터 목록을 순서대로 시도해 첫 번째 visible 요소를 반환."""
    for sel in selectors:
        el = page.query_selector(sel)
        if el and el.is_visible():
            return el
    return None


def _fill_form_simple_and_submit(page: Page, description: str, title: str, instrumental: bool) -> int | None:
    page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)

    # 쿠키 동의 배너 수락 (Create 버튼 클릭 가로채기 방지)
    _dismiss_cookie_banner(page)

    # 크레딧 사전 점검 — 부족하면 폼 입력 전에 즉시 중단
    credits_before = _ensure_enough_credits(page)

    # 오버레이 닫기
    overlay = page.query_selector('div[class*="overlay"], div[class*="modal"]')
    if overlay and overlay.is_visible():
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)

    # 혹시 Custom/Advanced 모드로 남아 있으면 Simple/Description으로 복귀
    # (visible 체크 필수 — hidden DOM 요소 클릭 방지)
    mode_btn = _first_visible(page, [
        'button:has-text("Simple")',
        'button:has-text("Description")',
    ])
    if mode_btn:
        mode_btn.click()
        page.wait_for_timeout(800)

    page.wait_for_timeout(800)

    # Description textarea: visible 요소만 사용
    desc_area = _first_visible(page, [
        'textarea[placeholder*="Describe" i]',
        'textarea[placeholder*="An Indie" i]',
        'textarea[placeholder*="description" i]',
        'textarea[placeholder*="song" i]',
    ])
    if not desc_area:
        for el in page.query_selector_all('textarea'):
            if el.is_visible():
                desc_area = el
                break
    if not desc_area:
        raise SunoError("Simple 모드에서 Description 입력 필드를 찾지 못했습니다.")

    _paste_into(page, desc_area, description)

    # Instrumental 토글 (요청 시, visible 체크)
    if instrumental:
        instr_btn = _first_visible(page, [
            'button:has-text("Instrumental")',
            'label:has-text("Instrumental")',
            '[data-testid*="instrumental" i]',
        ])
        if instr_btn:
            instr_btn.click()
            page.wait_for_timeout(500)

    # Title 입력 (선택)
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
        raise SunoError(
            "Create 버튼이 비활성 상태입니다. 입력값 또는 크레딧 잔액을 확인하세요."
        )
    create_btn.click()
    return credits_before


# ---------- 생성-only / 다운로드-only 분리 엔트리포인트 ----------

def request_songs(lyrics: str, styles: str, title: str = "") -> list[str]:
    """Advanced 모드로 생성 요청만 하고 song ID 목록을 반환 (다운로드 없음).

    폼 제출 후 곡 카드에 song ID가 나타날 때까지만 대기 (최대 90초).
    렌더링·다운로드는 하지 않으므로 빠르게 반환된다.
    반환된 song ID는 download_songs_by_ids()에 넘겨 나중에 다운로드할 수 있다.
    """
    if not lyrics.strip():
        raise SunoError("lyrics가 비어 있습니다.")
    if not styles.strip():
        raise SunoError("styles가 비어 있습니다.")

    cwd = Path.cwd()
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

            before = _me_ids(page)

            _fill_form_and_submit(page, lyrics, styles, title)
            page.wait_for_timeout(4_000)

            new_ids = _wait_new_on_me(page, before, timeout_sec=180)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다 (/me).")
            return new_ids[:2]
        finally:
            browser.close()


def request_songs_simple(description: str, title: str = "", instrumental: bool = False) -> list[str]:
    """Simple 모드로 생성 요청만 하고 song ID 목록을 반환 (다운로드 없음).

    폼 제출 후 곡 카드에 song ID가 나타날 때까지만 대기 (최대 90초).
    반환된 song ID는 download_songs_by_ids()에 넘겨 나중에 다운로드할 수 있다.
    """
    if not description.strip():
        raise SunoError("description이 비어 있습니다.")

    cwd = Path.cwd()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _write_prompt_log_simple(cwd / "prompt", description, title, instrumental)

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

            before = _me_ids(page)

            _fill_form_simple_and_submit(page, description, title, instrumental)
            page.wait_for_timeout(4_000)

            new_ids = _wait_new_on_me(page, before, timeout_sec=180)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다 (/me).")
            return new_ids[:2]
        finally:
            browser.close()


def download_songs_by_ids(song_ids: list[str], title_hint: str = "") -> GenerateResult:
    """song ID 목록을 받아 렌더링 완료 후 mp3를 다운로드.

    request_songs / request_songs_simple 이 반환한 song_ids를 넘겨 사용.
    create 페이지 피드에서 duration 표시를 감지해 완료를 확인한 뒤 다운로드.
    저장 위치: 호출 시점 CWD/mp3/
    """
    if not song_ids:
        raise SunoError("song_ids가 비어 있습니다.")

    cwd = Path.cwd()
    mp3_dir = cwd / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

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

            # /me 에서 렌더링 완료(duration 표시 & spinner 없음) 대기
            details = _wait_rendered_on_me(page, song_ids, timeout_sec=GENERATION_TIMEOUT_SEC)

            # CDN 안정화 대기
            page.wait_for_timeout(15_000)

            durations: dict[str, float] = {}
            files: list[str] = []
            used_names: set[str] = set()

            for sid in song_ids:
                info = details.get(sid, {})
                dur_str = info.get("duration")
                ui_title = info.get("title")
                expected_sec = float(_parse_duration_str(dur_str)) if dur_str else 0.0
                durations[sid] = expected_sec

                filename = _resolve_filename(used_names, ui_title, title_hint, sid)
                target = mp3_dir / filename

                ok = _download_song(page, sid, str(target), expected_sec)
                if ok and target.exists():
                    rel_path = target.relative_to(cwd).as_posix()
                    files.append(rel_path)

            if not files:
                raise SunoError(f"다운로드에 실패했습니다 (song_ids={song_ids}).")

            return GenerateResult(files=files, song_ids=song_ids, durations=durations)
        finally:
            browser.close()


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

            # 제출 전 /me 의 기존 곡 id 집합 (새 곡 식별 기준선)
            before = _me_ids(page)

            credits_before = _fill_form_and_submit(page, lyrics, styles, title)
            page.wait_for_timeout(4_000)  # 생성 요청 전송 안정화

            # Phase 1: /me 에서 새 song ID 출현 (최대 180초)
            #   현재 Suno UI 는 create 페이지에 곡을 노출하지 않으므로 /me 에서 감지한다.
            new_ids = _wait_new_on_me(page, before, timeout_sec=180)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다 (/me).")
            new_ids = new_ids[:2]

            # Phase 2: /me 에서 렌더링 완료 대기 (duration 표시 & spinner 없음)
            details = _wait_rendered_on_me(page, new_ids, timeout_sec=GENERATION_TIMEOUT_SEC)

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

            return GenerateResult(
                files=files, song_ids=new_ids, durations=durations,
                credits_before=credits_before,
                credits_after=(credits_before - REQUIRED_CREDITS)
                if credits_before is not None else None,
            )
        finally:
            browser.close()


def generate_songs_simple(description: str, title: str = "", instrumental: bool = False) -> GenerateResult:
    """Suno Simple 모드로 곡 2개를 생성하고 ./mp3/에 다운로드.

    저장 위치: 호출 시점의 CWD 하단 ./mp3/
    반환 경로: CWD 기준 상대 경로 ("mp3/곡제목.mp3" 형식)

    생성 직전 ./prompt/YYYYMMDD_HHMMSS_<title>_simple.md 에 입력 프롬프트를 기록.
    """
    if not description.strip():
        raise SunoError("description이 비어 있습니다.")

    cwd = Path.cwd()
    mp3_dir = cwd / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    _write_prompt_log_simple(cwd / "prompt", description, title, instrumental)

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

            # 제출 전 /me 의 기존 곡 id 집합 (새 곡 식별 기준선)
            before = _me_ids(page)

            credits_before = _fill_form_simple_and_submit(page, description, title, instrumental)
            page.wait_for_timeout(4_000)  # 생성 요청 전송 안정화

            # Phase 1: /me 에서 새 song ID 출현 (최대 180초)
            new_ids = _wait_new_on_me(page, before, timeout_sec=180)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다 (/me).")
            new_ids = new_ids[:2]

            # Phase 2: /me 에서 렌더링 완료 대기 (duration 표시 & spinner 없음)
            details = _wait_rendered_on_me(page, new_ids, timeout_sec=GENERATION_TIMEOUT_SEC)

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

            return GenerateResult(
                files=files, song_ids=new_ids, durations=durations,
                credits_before=credits_before,
                credits_after=(credits_before - REQUIRED_CREDITS)
                if credits_before is not None else None,
            )
        finally:
            browser.close()
