# 일본 직접촬영 상품 리뷰 파이프라인 — 원본 영상 프레임 추출·스코어링(Stage 1~2).
# WHY 최종 판정을 여기서 안 하는지: 선명도·정지 여부는 numpy로 근사할 수 있지만
# "상품이 뭔지", "구도가 좋은지"는 세션이 직접 Read로 봐야 한다(Kling 모션 프레임
# 검수 선례와 동일 원칙) — 이 스크립트는 후보를 클립당 상위 N장으로 압축하는
# 1차 필터링까지만 담당한다.
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_FOOTAGE_ROOT = PROJECT_ROOT / "raw_footage"
OUTPUT_ROOT = PROJECT_ROOT / "output"

FRAME_FPS = 2  # 도보 촬영 기준 초당 2장이면 후보 밀도 충분(전체 프레임 추출은 과함)
SCORE_LONG_EDGE = 720  # 스코어링용 축소 크기(속도) — 저장되는 프레임 원본은 그대로 둠
TOP_N_PER_CLIP = 25


def probe_clip(path: Path) -> dict:
    """ffprobe로 길이·해상도·fps만 먼저 확인 — 세로 촬영 여부 등 이상 케이스 조기 발견."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(r.stdout)["streams"][0]
    num, den = info["r_frame_rate"].split("/")
    return {
        "width": int(info["width"]), "height": int(info["height"]),
        "fps": round(int(num) / int(den), 2),
        "duration": float(info.get("duration", 0)),
        "portrait": int(info["height"]) > int(info["width"]),
    }


def extract_frames(clip_path: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(clip_path), "-vf", f"fps={FRAME_FPS}",
         "-q:v", "3", str(out_dir / "frame_%06d.jpg")],
        capture_output=True, text=True, check=True,
    )
    return sorted(out_dir.glob("frame_*.jpg"))


def _load_gray_small(path: Path) -> np.ndarray:
    img = Image.open(path).convert("L")
    if img.width > img.height:
        new_w, new_h = SCORE_LONG_EDGE, round(img.height * SCORE_LONG_EDGE / img.width)
    else:
        new_h, new_w = SCORE_LONG_EDGE, round(img.width * SCORE_LONG_EDGE / img.height)
    img = img.resize((max(new_w, 1), max(new_h, 1)))
    return np.asarray(img, dtype=np.float64)


def laplacian_variance(gray: np.ndarray) -> float:
    """OpenCV 없이 numpy만으로 계산하는 3x3 라플라시안 분산(Variance of Laplacian)
    — 값이 높을수록 선명(엣지가 뚜렷), 낮을수록 흐림(모션블러 등)."""
    lap = (-4 * gray[1:-1, 1:-1]
           + gray[:-2, 1:-1] + gray[2:, 1:-1]
           + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(lap.var())


def frame_diff(prev_gray: np.ndarray, gray: np.ndarray) -> float:
    """직전 프레임과의 평균 절대 차분 — 낮으면 카메라가 멈춘 구간(상품 클로즈업
    후보), 높으면 이동 중(도보 b-roll 후보)."""
    return float(np.abs(gray - prev_gray).mean())


def score_clip(clip_stem: str, frames: list[Path]) -> list[dict]:
    scores = []
    prev_gray = None
    for f in frames:
        gray = _load_gray_small(f)
        blur = laplacian_variance(gray)
        diff = frame_diff(prev_gray, gray) if prev_gray is not None else None
        scores.append({"frame": f.name, "blur": round(blur, 2),
                        "diff": round(diff, 2) if diff is not None else None})
        prev_gray = gray
    return scores


def process_topic(topic: str) -> Path:
    """raw_footage/<topic>/*.mp4 전체를 추출·스코어링해서
    output/<topic>/_footage_frames/<clip_stem>/scores.json에 기록."""
    src_dir = RAW_FOOTAGE_ROOT / topic
    if not src_dir.is_dir():
        raise FileNotFoundError(f"{src_dir} 없음 — raw_footage/{topic}/에 영상부터 넣어주세요")

    clips = sorted(src_dir.glob("*.mp4"))
    if not clips:
        raise FileNotFoundError(f"{src_dir}에 mp4 없음")

    out_base = OUTPUT_ROOT / topic / "_footage_frames"
    summary = {}
    for clip in clips:
        stem = clip.stem
        info = probe_clip(clip)
        print(f"=== {clip.name} === {info}")
        clip_out = out_base / stem
        frames = extract_frames(clip, clip_out)
        print(f"  {len(frames)}장 추출")
        scores = score_clip(stem, frames)

        # 1차 압축: 선명도(blur) 기준 상위 TOP_N_PER_CLIP장만 남김(전부 세션이
        # 볼 필요는 없음 — 흐릿한 프레임은 애초에 제외)
        ranked = sorted(scores, key=lambda s: s["blur"], reverse=True)[:TOP_N_PER_CLIP]
        ranked_names = {r["frame"] for r in ranked}

        (clip_out / "scores.json").write_text(
            json.dumps({"clip_info": info, "all_frames": scores,
                        "top_candidates": sorted(ranked_names)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary[stem] = {"info": info, "frame_count": len(frames),
                          "candidate_count": len(ranked_names)}
        print(f"  후보 {len(ranked_names)}장으로 압축 -> {clip_out / 'scores.json'}")

    return out_base


def assemble_topic(topic: str, ad_tag: bool = True) -> Path:
    """Stage 6~7 오케스트레이션: TTS(없으면 1회 생성) ->
    proto_jp_review.render_single_product()(인스타그램 세이프 버전까지 내부에서
    같이 만듦). jp_review_spec.json(단일 상품 딥다이브 스키마 — hook_lines/
    product_tag/product_photo/intro_broll/item_clip/chalkboard 등, CLAUDE.md
    "파이프라인" 섹션 참고)/narration.txt는 Stage 3~5에서 세션이 이미 만들어둔
    상태여야 한다 — 이 함수는 그 이후만 담당."""
    from lib.templates import proto_jp_review
    from lib.fish_audio_tts import synthesize

    data_dir = PROJECT_ROOT / "data" / topic
    out_dir = OUTPUT_ROOT / topic
    spec_path = data_dir / "jp_review_spec.json"
    narration_path = data_dir / "narration.txt"
    audio_path = out_dir / "narration.mp3"
    srt_path = out_dir / "narration.srt"

    if not spec_path.exists():
        raise FileNotFoundError(f"{spec_path} 없음 — Stage 5(스펙 작성)부터 끝내야 함")
    if not narration_path.exists():
        raise FileNotFoundError(f"{narration_path} 없음")

    if audio_path.exists():
        print(f"[jp_review] 기존 TTS 재사용(1회 원칙): {audio_path}")
    else:
        print("[jp_review] TTS 생성 중(topic당 1회)...")
        text = narration_path.read_text(encoding="utf-8")
        result = synthesize(topic, text)
        Path(result["audio_path"]).rename(audio_path)
        Path(result["srt_path"]).rename(srt_path)

    out_mp4 = out_dir / f"{topic}_shorts.mp4"
    proto_jp_review.render_single_product(str(data_dir), str(audio_path), str(srt_path),
                                           str(spec_path), str(out_mp4), ad_tag=ad_tag)
    return out_mp4


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("extract", "assemble"):
        print("사용법: python3 -m lib.jp_review_footage extract <topic>")
        print("      python3 -m lib.jp_review_footage assemble <topic>")
        sys.exit(1)
    if sys.argv[1] == "extract":
        process_topic(sys.argv[2])
    else:
        assemble_topic(sys.argv[2])
