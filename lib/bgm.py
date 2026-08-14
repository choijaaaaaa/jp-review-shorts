# 배경음악(BGM) 선택 + 믹싱. WHY: 지금까지 전 영상이 TTS 내레이션 단독(완전
# 무음 배경)이라 "저품질/템플릿" 인상에 기여한다는 판단(2026-08-05) — 사용자가
# 유튜브 오디오 보관함에서 직접 무보컬 트랙을 골라 assets_library/music/에
# 받아뒀고, 그걸 topic마다 결정론적으로 하나 골라 내레이션 아래 아주 낮은
# 볼륨으로 까는 "밑바탕" 용도로만 쓴다("소리 엄청 줄여가지고 밑바탕으로 해" —
# 사용자 확정). 트랙 소스가 유튜브 오디오 보관함이라 저작권 클레임 걱정 없음.
from __future__ import annotations

import subprocess
from pathlib import Path

_MUSIC_DIR = Path(__file__).resolve().parent.parent / "assets_library" / "music"

# WHY -24dB(2026-08-05): -16dB로 올려서 들어본 뒤 "노래 걍 줄여 목소리가
# 더중요" 피드백으로 원복 — 내레이션 전달력이 BGM 존재감보다 항상 우선.
# 필요시 이 값만 조정하면 전체 파이프라인에 일괄 반영된다.
BGM_VOLUME_DB = -24
FADE_SEC = 1.5


def _tracks() -> list[Path]:
    return sorted(_MUSIC_DIR.glob("*.mp3"))


def pick_track(seed: str) -> Path | None:
    """seed 문자열(보통 topic 폴더명 또는 그에 준하는 식별자) 기준 결정론적
    선택 — 이 프로젝트의 다른 topic-seed 로직(배지 색 램프, 칠판 낙서 등)과
    같은 공식: 진짜 랜덤이 아니라 같은 seed는 항상 같은 트랙을 고른다(재생성
    해도 결과 재현 가능). 트랙이 하나도 없으면(assets_library/music/이 아직
    안 채워진 상태) None — 호출부는 이 경우 BGM 없이 조용히 폴백한다."""
    tracks = _tracks()
    if not tracks:
        return None
    idx = sum(ord(c) * (i * 5 + 7) for i, c in enumerate(seed)) % len(tracks)
    return tracks[idx]


def bgm_filter_segment(seed: str, duration: float, in_label: str, out_label: str) -> tuple[str, Path] | None:
    """트림+볼륨다운+페이드인/아웃을 적용하는 filter_complex 조각 하나를
    만들어 (조각 문자열, 실제 고른 트랙 경로) 튜플로 반환한다. 트랙이 없으면
    None. WHY 별도 함수로 분리: `mix_bgm`(내레이션 길이 그대로 까는 단순 케이스)
    와 `video_assembler.py`의 `assemble()`(제목/엔딩 카드 무음 구간까지 포함한
    전체 영상 길이 동안 깔아야 해서 기존 adelay/apad 필터와 같은 filter_complex
    안에서 조립해야 하는 케이스)가 이 조각을 공유해서 볼륨/페이드 값이 한
    곳(BGM_VOLUME_DB, FADE_SEC)에서만 관리되게 한다."""
    track = pick_track(seed)
    if track is None:
        return None
    fade_out_start = max(duration - FADE_SEC, 0)
    frag = (
        f"[{in_label}]atrim=0:{duration:.3f},volume={BGM_VOLUME_DB}dB,"
        f"afade=t=in:st=0:d={FADE_SEC},afade=t=out:st={fade_out_start:.3f}:d={FADE_SEC}[{out_label}]"
    )
    return frag, track


def mix_bgm(narration_path: str, out_path: str, duration: float, seed: str) -> str:
    """내레이션 오디오에 BGM을 아주 낮은 볼륨으로 얹은 새 오디오 파일을
    out_path에 만들고 그 경로를 반환한다. BGM 트랙이 없으면 아무 것도 하지
    않고 narration_path를 그대로 반환 — 호출부가 최종 영상 mux 단계에서 이
    함수의 리턴값을 audio_path 자리에 그대로 넣기만 하면 되게 해서, 각
    파이프라인이 이미 갖고 있는 비디오 코덱/트림/-shortest 로직은 전혀 안
    건드려도 된다.
    WHY amix에 normalize=0을 명시하는지: ffmpeg amix 필터는 기본값(normalize=1)
    이면 입력 개수만큼 전체 볼륨을 자동으로 나눠서(2개 입력이면 대략 -6dB)
    내레이션까지 같이 작아진다 — BGM만 volume 필터로 이미 따로 죽였으므로
    내레이션 쪽은 원본 그대로 유지해야 "밑바탕"이 아니라 "내레이션도 같이
    작아진 믹스"가 되는 걸 막는다."""
    result = bgm_filter_segment(seed, duration, in_label="1:a", out_label="bgm")
    if result is None:
        return narration_path
    bgm_frag, track = result

    filter_complex = f"{bgm_frag};[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", narration_path, "-i", str(track),
         "-filter_complex", filter_complex, "-map", "[aout]",
         "-c:a", "aac", "-b:a", "192k", str(out_path)],
        check=True, capture_output=True,
    )
    return str(out_path)
