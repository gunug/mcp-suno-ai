"""통합 dry-run 테스트: generate_songs_tuned 실제 코드 경로로 폼 작성 + 슬라이더 설정까지
수행하고 Create 직전에 멈춰 적용값을 검증한다. 곡 생성 안 함 (크레딧 0).
"""
from suno_tuned import generate_songs_tuned

r = generate_songs_tuned(
    lyrics="[Instrumental]",
    styles="EDM house, four-on-the-floor kick, steady BPM, constant tempo, 170 BPM",
    title="DryRunTest",
    weirdness=25,
    style_influence=75,
    dry_run=True,
)
print("returned durations(=applied):", r.durations)
print("files:", r.files, "song_ids:", r.song_ids)
w = r.durations.get("weirdness")
s = r.durations.get("style_influence")
ok = (w is not None and s is not None and abs(w - 25) <= 5 and abs(s - 75) <= 5
      and r.files == [] and r.song_ids == [])
print("RESULT:", "PASS" if ok else "FAIL")
