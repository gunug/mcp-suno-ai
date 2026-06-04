"""Suno Advanced 모드 + More Options(Weirdness / Style Influence) 슬라이더 제어 확장.

기존 suno_automation.py 는 전혀 수정하지 않고, 그 검증된 헬퍼들을 재사용해
슬라이더 설정 단계만 추가한 '튜닝' 생성 경로를 별도로 제공한다.

- Weirdness / Style Influence 는 Suno UI 상 `<div role="slider" aria-valuenow=0~100>`
  형태의 ARIA 커스텀 슬라이더다. focus 후 화살표 키로 값을 옮긴다 (step=1).
- weirdness / style_influence 가 None 이면 해당 슬라이더는 건드리지 않는다
  (= Suno 기본값 50% 유지). 따라서 둘 다 None 이면 동작은 기존 advanced 와 동일.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from suno_automation import (
    GENERATION_TIMEOUT_SEC,
    PROFILE_DIR,
    SUNO_CREATE_URL,
    GenerateResult,
    SunoError,
    _download_song,
    _ensure_logged_in,
    _existing_song_ids,
    _paste_into,
    _parse_duration_str,
    _resolve_filename,
    _sanitize_filename,
    _wait_new_song_ids,
    _wait_until_rendered,
)

_WEIRDNESS_LABEL = "Weirdness"
_STYLE_INFLUENCE_LABEL = "Style Influence"


# ---------- 입력 검증 / 로그 ----------

def _validate_pct(name: str, v: int | None) -> int | None:
    if v is None:
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        raise SunoError(f"{name} 값이 정수가 아닙니다: {v!r}")
    if not (0 <= iv <= 100):
        raise SunoError(f"{name} 는 0~100 사이여야 합니다 (입력: {v}).")
    return iv


def _write_prompt_log_tuned(
    prompt_dir: Path, lyrics: str, styles: str, title: str,
    weirdness: int | None, style_influence: int | None,
) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = _sanitize_filename(title) if title.strip() else "untitled"
    md_path = prompt_dir / f"{ts}_{name}_tuned.md"
    content = (
        f"## Title\n{title}\n\n"
        f"## Style of Music\n{styles}\n\n"
        f"## More Options\n"
        f"- Weirdness: {weirdness if weirdness is not None else 'default(50)'}\n"
        f"- Style Influence: {style_influence if style_influence is not None else 'default(50)'}\n\n"
        f"## Lyrics\n{lyrics}\n"
    )
    md_path.write_text(content, encoding="utf-8")
    return md_path


# ---------- 슬라이더 제어 ----------

def _read_slider(page: Page, label: str) -> int | None:
    el = page.query_selector(f'[role="slider"][aria-label="{label}"]')
    if not el:
        return None
    v = el.get_attribute("aria-valuenow")
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _set_aria_slider(page: Page, label: str, target: int) -> int | None:
    """ARIA 슬라이더를 target(0~100)으로 설정하고 실제 도달값을 반환.

    focus → ArrowLeft 로 최솟값(0)까지 → ArrowRight 로 target 까지.
    값을 읽어가며 멈추므로 step 크기에 무관하게 동작한다.
    """
    el = page.query_selector(f'[role="slider"][aria-label="{label}"]')
    if not el:
        return None
    el.scroll_into_view_if_needed()
    el.focus()
    page.wait_for_timeout(150)

    # 최솟값으로 내림 (범위 0~100, step≥1 이므로 120회면 충분)
    for _ in range(120):
        page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(80)

    cur = _read_slider(page, label)
    guard = 0
    while cur is not None and cur < target and guard < 200:
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(25)
        nxt = _read_slider(page, label)
        if nxt is None or nxt == cur:  # 더 안 움직이면 중단
            break
        cur = nxt
        guard += 1
    return cur


def _open_more_options(page: Page) -> bool:
    """More Options 패널을 펼친다. 슬라이더가 보이면 성공."""
    if page.query_selector(f'[role="slider"][aria-label="{_WEIRDNESS_LABEL}"]'):
        return True
    for sel in (
        'button:has-text("More Options")',
        'button:has-text("More options")',
        '[data-testid*="more" i]',
    ):
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            page.wait_for_timeout(900)
            if page.query_selector(f'[role="slider"][aria-label="{_WEIRDNESS_LABEL}"]'):
                return True
    return bool(page.query_selector(f'[role="slider"][aria-label="{_WEIRDNESS_LABEL}"]'))


def _apply_more_options(
    page: Page, weirdness: int | None, style_influence: int | None
) -> dict:
    """지정된 슬라이더만 설정. 둘 다 None 이면 아무것도 하지 않는다."""
    applied: dict[str, int | None] = {}
    if weirdness is None and style_influence is None:
        return applied
    if not _open_more_options(page):
        raise SunoError("More Options 패널/슬라이더를 찾지 못했습니다 (Suno UI 변경 가능).")
    if weirdness is not None:
        applied["weirdness"] = _set_aria_slider(page, _WEIRDNESS_LABEL, weirdness)
    if style_influence is not None:
        applied["style_influence"] = _set_aria_slider(page, _STYLE_INFLUENCE_LABEL, style_influence)
    return applied


# ---------- 폼 작성 (Advanced + 슬라이더) ----------

def _fill_form_and_submit_tuned(
    page: Page, lyrics: str, styles: str, title: str,
    weirdness: int | None = None, style_influence: int | None = None,
    dry_run: bool = False,
) -> dict:
    """기존 _fill_form_and_submit 와 동일한 Advanced 폼 작성 + More Options 슬라이더 설정.

    dry_run=True 면 Create 를 누르지 않고 적용된 슬라이더 값을 반환 (크레딧 0, 통합 테스트용).
    """
    page.goto(SUNO_CREATE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)

    if page.query_selector('div[class*="overlay"], div[class*="modal"]'):
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)

    advanced = page.query_selector('button:has-text("Advanced")')
    if advanced:
        advanced.click()
        page.wait_for_timeout(800)

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

    page.wait_for_timeout(600)

    # --- 신규: More Options 슬라이더 설정 ---
    applied = _apply_more_options(page, weirdness, style_influence)
    page.wait_for_timeout(400)

    if dry_run:
        return applied

    create_btn = page.query_selector('button:has-text("Create")')
    if not create_btn:
        raise SunoError("Create 버튼을 찾지 못했습니다.")
    if create_btn.is_disabled():
        raise SunoError("Create 버튼이 비활성 상태입니다. 입력값을 확인하세요.")
    create_btn.click()
    return applied


# ---------- 엔트리포인트 ----------

def generate_songs_tuned(
    lyrics: str, styles: str, title: str = "",
    weirdness: int | None = None, style_influence: int | None = None,
    dry_run: bool = False,
) -> GenerateResult:
    """Advanced 모드 + Weirdness/Style Influence 슬라이더 적용 후 곡 2개 생성·다운로드.

    weirdness / style_influence: 0~100 정수 또는 None(기본 50% 유지).
    dry_run=True: Create 누르지 않고 폼 작성 + 슬라이더 설정까지만 수행해
        적용값을 GenerateResult.durations['applied_*'] 형태가 아니라
        song_ids=[]·files=[] 로 반환 (테스트용, 크레딧 0).
    """
    if not lyrics.strip():
        raise SunoError("lyrics가 비어 있습니다.")
    if not styles.strip():
        raise SunoError("styles가 비어 있습니다.")
    weirdness = _validate_pct("weirdness", weirdness)
    style_influence = _validate_pct("style_influence", style_influence)

    cwd = Path.cwd()
    mp3_dir = cwd / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    if not dry_run:
        _write_prompt_log_tuned(cwd / "prompt", lyrics, styles, title, weirdness, style_influence)

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

            applied = _fill_form_and_submit_tuned(
                page, lyrics, styles, title, weirdness, style_influence, dry_run=dry_run
            )

            if dry_run:
                # 적용값을 검증 가능한 형태로 반환 (실제 생성 안 함)
                return GenerateResult(
                    files=[], song_ids=[],
                    durations={k: float(v) for k, v in applied.items() if v is not None},
                )

            new_ids = _wait_new_song_ids(page, before, timeout_sec=90)
            if not new_ids:
                raise SunoError("새로 생성된 곡 ID가 감지되지 않았습니다.")
            new_ids = new_ids[:2]

            status = _wait_until_rendered(page, new_ids, timeout_sec=GENERATION_TIMEOUT_SEC)
            details = status.get("details", {})
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
                    files.append(target.relative_to(cwd).as_posix())

            if not files:
                raise SunoError(
                    f"곡 생성은 감지되었으나 다운로드에 실패했습니다 (song_ids={new_ids})."
                )
            return GenerateResult(files=files, song_ids=new_ids, durations=durations)
        finally:
            browser.close()
