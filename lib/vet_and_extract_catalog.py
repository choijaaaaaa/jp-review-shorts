# 카탈로그 세그먼트를 실제 짧은 파일로 물리적으로 추출하면서 얼굴 노출 여부를
# 자동 검사한다(2026-08-15, "safe" 태그를 재사용마다 사람이 다시 확인해야 하는
# 구조 자체가 반복 사고의 원인이라는 판단 — 한 번 제대로 걸러두면 이후로는
# raw_footage 원본을 다시 참조할 필요가 없어짐).
#
# 사용법:
#   .venv/bin/python3 -m lib.vet_and_extract_catalog run      # 전체 실행
#   .venv/bin/python3 -m lib.vet_and_extract_catalog run --limit 20  # 일부만(테스트용)
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "_footage_catalog.json"
EXTRACT_DIR = ROOT / "footage_clips"
REVIEW_DIR = ROOT / "output" / "_face_review"
YUNET_MODEL = ROOT / "assets_library" / "cv_models" / "face_detection_yunet.onnx"

# WHY 5%(2026-08-15 실측 보정): 배경에 멀리 있는 행인(기존 관례상 허용되는
# far_pedestrians_backs_only)은 프레임 폭의 1~3% 크기로 잡힌다. 실제 사고
# 사례(콜라겐씨젤리 등 4개 topic에 쓰인 얼굴)는 7~9%대였다. 5%를 기준으로
# 그 이상만 "사람이 다시 봐야 할 후보"로 플래그한다 — 완전 자동 배제가
# 아니라 검토 대상 추리기 용도(광고판 속 인물 사진 같은 오탐도 있을 수 있어
# 최종 판단은 사람이 함).
FLAG_WIDTH_RATIO = 0.05
SAMPLE_INTERVAL_S = 0.25


def _needs_manual_rotate(src: str) -> bool:
    return "폴더 1" in str(src)


def extract_clip_only(src: str, in_s: float, out_s: float, out_path: Path) -> None:
    """세그먼트를 원본 화각 그대로(크롭/배속 없이) 정확히 잘라낸다 — 회전 보정과
    정확한 seek(coarse -ss + trim 필터)는 proto_jp_review._trim_and_cover_crop_clip과
    동일 로직(DJI HEVC 키프레임 간격 문제 대응, CLAUDE.md 알려진 함정 참고)."""
    dur = max(out_s - in_s, 0.1)
    rotate = _needs_manual_rotate(src)
    inputs = ["-noautorotate"] if rotate else []
    coarse_seek = max(in_s - 2.0, 0.0)
    accurate_offset = in_s - coarse_seek
    vf = f"trim=start={accurate_offset}:duration={dur},setpts=PTS-STARTPTS"
    if rotate:
        vf += ",transpose=1"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-ss", f"{coarse_seek}", "-i", str(src),
         "-vf", vf, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


def detect_max_face_ratio(clip_path: Path) -> tuple[float, float | None, "cv2.typing.MatLike | None"]:
    """clip_path를 SAMPLE_INTERVAL_S 간격으로 샘플링해서 감지된 얼굴 중 프레임
    폭 대비 가장 큰 비율과, 그 순간의 타임스탬프·프레임(썸네일 저장용)을 반환.
    얼굴이 전혀 없으면 (0.0, None, None)."""
    cap = cv2.VideoCapture(str(clip_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if w == 0 or h == 0 or total_frames == 0:
        cap.release()
        return 0.0, None, None
    detector = cv2.FaceDetectorYN_create(str(YUNET_MODEL), "", (w, h), score_threshold=0.6)
    step = max(int(fps * SAMPLE_INTERVAL_S), 1)
    best_ratio, best_t, best_frame = 0.0, None, None
    frame_idx = 0
    while frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            break
        _, faces = detector.detect(frame)
        if faces is not None:
            for f in faces:
                ratio = float(f[2]) / w
                if ratio > best_ratio:
                    best_ratio, best_t, best_frame = ratio, frame_idx / fps, frame.copy()
        frame_idx += step
    cap.release()
    return best_ratio, best_t, best_frame


def run(limit: int | None = None, roles: tuple[str, ...] = ("broll",)) -> None:
    """WHY roles를 broll로 한정하는지(2026-08-15 실측): hero/supporting(상품 examine
    샷)엔 캐릭터 마스코트·인물 사진이 인쇄된 포장지·굿즈가 화면을 채워서
    얼굴 감지기가 그걸 실제 사람 얼굴로 오탐 대량 발생(예: 스누피 인쇄
    포장지, 피규어 진열장, 엽서 사진). 진짜 위험(신원 식별 가능한 실제
    행인)은 거리/배경 컷(broll)에서만 발생하므로 검사 범위를 거기로
    좁힌다 — hero/supporting까지 보려면 roles에 명시적으로 추가할 것."""
    catalog = json.loads(CATALOG_PATH.read_text())
    segments = [s for s in catalog["segments"] if s.get("footage_role") in roles]
    print(f"대상: {len(segments)}개(footage_role in {roles}, 전체 {len(catalog['segments'])}개 중)")
    if limit:
        segments = segments[:limit]

    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    results = {"clean": [], "flagged": [], "errors": []}
    for i, seg in enumerate(segments):
        sid = seg["id"]
        src = ROOT / seg["clip"]
        if not src.exists():
            results["errors"].append({"id": sid, "reason": f"원본 없음: {seg['clip']}"})
            print(f"[{i+1}/{len(segments)}] {sid}: 원본 없음, 스킵", flush=True)
            continue
        out_path = EXTRACT_DIR / f"{sid}.mp4"
        try:
            if not out_path.exists():
                extract_clip_only(str(src), seg["in"], seg["out"], out_path)
            ratio, t, frame = detect_max_face_ratio(out_path)
        except Exception as e:  # noqa: BLE001 — 배치 작업이라 개별 실패로 전체를 멈추지 않음
            results["errors"].append({"id": sid, "reason": str(e)})
            print(f"[{i+1}/{len(segments)}] {sid}: 에러 {e}", flush=True)
            continue

        if ratio >= FLAG_WIDTH_RATIO:
            thumb_path = REVIEW_DIR / f"{sid}.jpg"
            cv2.imwrite(str(thumb_path), frame)
            results["flagged"].append({"id": sid, "clip": seg["clip"], "in": seg["in"], "out": seg["out"],
                                        "max_face_ratio": round(ratio, 3), "at_t": round(t, 2) if t else None,
                                        "thumb": str(thumb_path.relative_to(ROOT))})
            print(f"[{i+1}/{len(segments)}] {sid}: 플래그(얼굴 비율 {ratio:.1%})", flush=True)
        else:
            results["clean"].append(sid)
            if (i + 1) % 50 == 0:
                print(f"[{i+1}/{len(segments)}] 진행 중... (clean {len(results['clean'])}, flagged {len(results['flagged'])})", flush=True)

    report_path = ROOT / "output" / "_face_review" / "_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n완료: clean={len(results['clean'])} flagged={len(results['flagged'])} errors={len(results['errors'])}")
    print(f"리포트: {report_path}")


if __name__ == "__main__":
    import sys
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    run(limit=limit)
