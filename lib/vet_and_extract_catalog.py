# 카탈로그 세그먼트를 실제 짧은 파일로 물리적으로 추출 + (broll 등 요청 시)
# 얼굴 노출 자동 검사(2026-08-15 도입, 2026-08-15 재구조화로 등록 경로까지 통합).
#
# WHY 물리적 추출과 자동 얼굴검사를 분리하는지: 추출(자르기)은 기계적이라 모든
# footage_role에 안전하게 적용 가능하지만, 얼굴검사는 hero/supporting(상품
# examine·매대 브라우징)에서 캐릭터 인쇄 포장지·굿즈를 실제 얼굴로 오탐하는
# 비율이 너무 높아(실측) 지금은 broll 전용이다 — 그렇다고 hero/supporting을
# raw_footage/ 원본 참조 상태로 방치하면 "구조화해서 재사용 쉽게" 원칙에
# 어긋나므로, detect=False로 물리적 추출만 먼저 해두고 `safety_reviewed`
# 필드를 명시적으로 false로 남겨 "캐시는 있지만 안전검증은 아직" 상태를
# 카탈로그 자체에서 조회 가능하게 만든다(태그 없음=미확인이 아니라 필드
# 자체가 정직하게 false를 말하게).
#
# WHY 등록 경로까지 이 파일이 담당하는지: 기존엔 "물리적 추출 배치 실행" →
# "카탈로그 clip/in/out을 footage_clips/로 재배선"이 별도의 1회성 수작업
# 스크립트였다(재현 불가능, 다음 세션이 다시 하려면 로직을 처음부터 다시
# 짜야 함) — run()이 추출과 재배선을 한 번에 원자적으로 처리하도록 합쳐서
# "새 세그먼트를 등록하면 그 즉시 footage_clips/ 캐시가 생긴다"를 표준
# 경로로 만든다. register_segment()는 신규 세그먼트를 카탈로그에 처음
# 추가할 때부터 이 경로를 강제한다(하드코딩된 JSON 직접 편집 대신).
#
# 사용법:
#   .venv/bin/python3 -m lib.vet_and_extract_catalog run                       # broll, 얼굴검사 포함(기본)
#   .venv/bin/python3 -m lib.vet_and_extract_catalog run --roles hero,supporting --no-detect  # 추출만
#   .venv/bin/python3 -m lib.vet_and_extract_catalog run --limit 20            # 일부만(테스트용)
#   .venv/bin/python3 -m lib.vet_and_extract_catalog validate                  # 카탈로그 정합성 검사
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
KNOWN_ROLES = ("hero", "supporting", "broll")


def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _save_catalog(catalog: dict) -> None:
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


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


def run(limit: int | None = None, roles: tuple[str, ...] = ("broll",), detect: bool = True) -> None:
    """물리적 추출 + (detect=True면) 자동 얼굴 검사 + 카탈로그 재배선을 한 번에
    수행한다. 이미 footage_clips/를 가리키는 세그먼트(재배선 완료)는 건너뛴다 —
    재실행해도 안전(idempotent).

    detect=True: 얼굴 비율이 FLAG_WIDTH_RATIO 미만이면 그 자리에서
    safety_reviewed=True로 재배선. 이상이면 카탈로그는 안 건드리고
    output/_face_review/_report.json에만 남겨서 사람이 썸네일을 보고
    exclude_segment() 또는 mark_reviewed()로 직접 결정하게 한다.

    detect=False: 얼굴 검사 없이 추출 + 재배선만 하고 safety_reviewed=False로
    명시(캐시는 만들어두되 "안전 검증은 아직 안 했다"를 정직하게 기록 — 필드
    자체가 없는 것과 다름). hero/supporting처럼 자동 얼굴검사가 오탐 위주라
    무의미한 role에 쓴다.
    """
    catalog = _load_catalog()
    all_segments = catalog["segments"]
    segments = [s for s in all_segments if s.get("footage_role") in roles
                and not str(s.get("clip", "")).startswith("footage_clips/")]
    print(f"대상: {len(segments)}개(footage_role in {roles}, 이미 재배선된 것 제외, 전체 {len(all_segments)}개 중)")
    if limit:
        segments = segments[:limit]

    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    results = {"clean": [], "extracted_unreviewed": [], "flagged": [], "errors": []}
    changed = False
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
        except Exception as e:  # noqa: BLE001 — 배치 작업이라 개별 실패로 전체를 멈추지 않음
            results["errors"].append({"id": sid, "reason": str(e)})
            print(f"[{i+1}/{len(segments)}] {sid}: 에러 {e}", flush=True)
            continue

        if detect:
            try:
                ratio, t, frame = detect_max_face_ratio(out_path)
            except Exception as e:  # noqa: BLE001
                results["errors"].append({"id": sid, "reason": str(e)})
                print(f"[{i+1}/{len(segments)}] {sid}: 얼굴검사 에러 {e}", flush=True)
                continue
            if ratio >= FLAG_WIDTH_RATIO:
                thumb_path = REVIEW_DIR / f"{sid}.jpg"
                cv2.imwrite(str(thumb_path), frame)
                results["flagged"].append({"id": sid, "clip": seg["clip"], "in": seg["in"], "out": seg["out"],
                                            "max_face_ratio": round(ratio, 3), "at_t": round(t, 2) if t else None,
                                            "thumb": str(thumb_path.relative_to(ROOT))})
                print(f"[{i+1}/{len(segments)}] {sid}: 플래그(얼굴 비율 {ratio:.1%}) — 카탈로그 미변경, 사람 확인 필요", flush=True)
                continue  # 사람이 결정할 때까지 재배선하지 않음(raw_footage 원본 경로 유지)
            seg["safety_reviewed"] = True
            results["clean"].append(sid)
        else:
            seg["safety_reviewed"] = False
            results["extracted_unreviewed"].append(sid)

        dur = round(seg["out"] - seg["in"], 3)
        seg["clip"] = f"footage_clips/{sid}.mp4"
        seg["in"] = 0.0
        seg["out"] = dur
        changed = True

        if (i + 1) % 50 == 0:
            print(f"[{i+1}/{len(segments)}] 진행 중... (완료 {len(results['clean'])+len(results['extracted_unreviewed'])}, "
                  f"flagged {len(results['flagged'])})", flush=True)

    if changed:
        _save_catalog(catalog)

    report_path = REVIEW_DIR / "_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: clean={len(results['clean'])} extracted_unreviewed={len(results['extracted_unreviewed'])} "
          f"flagged={len(results['flagged'])} errors={len(results['errors'])}")
    print(f"리포트: {report_path}")


def exclude_segment(seg_id: str, reason: str) -> None:
    """flagged 세그먼트를 사람이 눈으로 확인한 뒤 실제 위반으로 판단했을 때 —
    segments[]에서 빼서 excluded[]로 옮긴다(재사용 시도 자체를 원천 차단,
    2026-08-15 실측 사고 대응 패턴)."""
    catalog = _load_catalog()
    idx = next((i for i, s in enumerate(catalog["segments"]) if s["id"] == seg_id), None)
    if idx is None:
        raise ValueError(f"세그먼트 없음: {seg_id}")
    seg = catalog["segments"].pop(idx)
    catalog.setdefault("excluded", []).append({**seg, "excluded_reason": reason})
    catalog["registered_segments"] = len(catalog["segments"])
    _save_catalog(catalog)
    print(f"제외 완료: {seg_id} ({reason})")


def mark_reviewed(seg_id: str, reviewed: bool = True) -> None:
    """flagged였지만 사람이 오탐(포장지 인쇄 얼굴 등)으로 확인했을 때 —
    safety_reviewed=True로만 세팅하고 재배선은 다음 run() 호출이 처리하게
    둔다(clip이 아직 raw_footage/를 가리키는 상태일 수 있음)."""
    catalog = _load_catalog()
    seg = next((s for s in catalog["segments"] if s["id"] == seg_id), None)
    if seg is None:
        raise ValueError(f"세그먼트 없음: {seg_id}")
    seg["safety_reviewed"] = reviewed
    _save_catalog(catalog)
    print(f"{seg_id}: safety_reviewed={reviewed}")


def register_segment(
    seg_id: str, clip: str, in_s: float, out_s: float, tags: list[str],
    footage_role: str, safety_reviewed: bool, brand: str | None = None,
    product_line: str | None = None, scent: str | None = None,
    subtitle_band: dict | None = None, source: str = "own",
) -> None:
    """신규 세그먼트를 카탈로그에 추가하는 표준 경로(2026-08-15 도입) — 이제부터
    `data/_footage_catalog.json`을 손으로 직접 편집하지 말고 이 함수를 거칠 것.
    WHY: 손편집은 footage_role 누락, safety_reviewed 누락, footage_clips/ 재배선
    누락이 발생해도 아무것도 막아주지 않는다 — 이 함수는 필수값을 파라미터로
    강제하고, 등록 즉시 물리적으로 추출까지 해서 "카탈로그에는 있는데 실제
    파일도 없고 안전확인도 안 된" 상태가 생기지 않게 한다.

    safety_reviewed는 호출부가 명시적으로 판단해서 넘겨야 한다(자동 기본값
    없음) — 등록 전에 CLAUDE.md "촬영본 활용 규칙"의 얼굴·번호판·신체부위
    경계 규칙대로 직접 프레임을 확인했다면 True, 아직 안 했다면 False로
    솔직하게 넣을 것(broll이면 이후 run(roles=("broll",))이 자동 얼굴검사로
    재확인해준다 — hero/supporting은 자동검사가 없으므로 False로 등록했다면
    실제 topic에 쓰기 전 반드시 사람이 확인 후 mark_reviewed()로 뒤집을 것).
    """
    if footage_role not in KNOWN_ROLES:
        raise ValueError(f"footage_role은 {KNOWN_ROLES} 중 하나여야 함: {footage_role}")
    catalog = _load_catalog()
    if any(s["id"] == seg_id for s in catalog["segments"]):
        raise ValueError(f"이미 존재하는 id: {seg_id}")

    src = ROOT / clip
    if not src.exists():
        raise FileNotFoundError(f"원본 없음: {src}")
    out_path = EXTRACT_DIR / f"{seg_id}.mp4"
    extract_clip_only(str(src), in_s, out_s, out_path)

    seg = {
        "id": seg_id, "clip": f"footage_clips/{seg_id}.mp4", "in": 0.0,
        "out": round(out_s - in_s, 3), "tags": tags, "footage_role": footage_role,
        "brand": brand, "product_line": product_line, "scent": scent,
        "source": source, "used_in": [], "safety_reviewed": safety_reviewed,
    }
    if subtitle_band:
        seg["subtitle_band"] = subtitle_band
    catalog["segments"].append(seg)
    catalog["registered_segments"] = len(catalog["segments"])
    _save_catalog(catalog)
    print(f"등록 완료: {seg_id} (footage_role={footage_role}, safety_reviewed={safety_reviewed})")


def build_contact_sheet(cols: int = 5, thumb_w: int = 272) -> list[Path]:
    """output/_face_review/_report.json의 flagged 썸네일을 그리드 이미지 몇 장으로
    합쳐서 사람이 한 번에 훑어보기 쉽게 만든다(2026-08-15 도입 — 이전엔 이 작업을
    1회성 인라인 스크립트로 했었는데 코드로 안 남겨서 재현 불가능했음, output/
    _segment_previews/와 같은 클래스의 문제라 여기 정식으로 편입). id·얼굴 비율
    라벨을 각 셀 아래 새겨서 report.json 없이 이미지만 봐도 어떤 세그먼트인지
    알 수 있게 한다."""
    from PIL import Image, ImageDraw, ImageFont

    report_path = REVIEW_DIR / "_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"{report_path} 없음 — 먼저 run()을 돌릴 것")
    flagged = json.loads(report_path.read_text(encoding="utf-8")).get("flagged", [])
    if not flagged:
        print("flagged 없음 — 만들 시트 없음")
        return []

    label_h = 36
    cell_h = int(thumb_w * 9 / 16) + label_h
    per_sheet = cols * 6
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except OSError:
        font = ImageFont.load_default()

    sheets = []
    for sheet_idx in range(0, len(flagged), per_sheet):
        batch = flagged[sheet_idx:sheet_idx + per_sheet]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * thumb_w, rows * cell_h), "black")
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(batch):
            r, c = divmod(i, cols)
            thumb_path = ROOT / item["thumb"]
            if not thumb_path.exists():
                continue
            im = Image.open(thumb_path).convert("RGB")
            im.thumbnail((thumb_w, thumb_w))
            x, y = c * thumb_w, r * cell_h
            sheet.paste(im, (x, y))
            label = f"{item['id'][:28]} ({item['max_face_ratio']:.0%})"
            draw.text((x + 4, y + thumb_w * 9 // 16 + 4), label, fill="yellow", font=font)
        out_path = REVIEW_DIR / f"_sheet_{sheet_idx // per_sheet:02d}.jpg"
        sheet.save(out_path, quality=85)
        sheets.append(out_path)
    print(f"시트 {len(sheets)}장 생성: {REVIEW_DIR}/_sheet_*.jpg (flagged {len(flagged)}개)")
    return sheets


def validate() -> bool:
    """카탈로그 정합성 검사 — 새 topic 작업을 시작하기 전이나 커밋 전에 돌려서
    "등록은 됐는데 실제로는 문제 있는" 세그먼트를 잡아낸다. 문제 있으면
    False 반환 + 상세 출력."""
    catalog = _load_catalog()
    segments = catalog["segments"]
    ids = [s["id"] for s in segments]
    problems: list[str] = []

    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"id 중복: {sorted(dupes)}")

    for s in segments:
        sid = s.get("id", "???")
        if s.get("footage_role") not in KNOWN_ROLES:
            problems.append(f"{sid}: footage_role 없음/잘못됨({s.get('footage_role')!r})")
        if "safety_reviewed" not in s:
            problems.append(f"{sid}: safety_reviewed 필드 없음(등록 시 register_segment() 안 거침)")
        clip_path = ROOT / s.get("clip", "")
        if not clip_path.exists():
            problems.append(f"{sid}: clip 파일 없음({s.get('clip')})")
        if str(s.get("clip", "")).startswith("footage_clips/") and s.get("in") != 0.0:
            problems.append(f"{sid}: footage_clips/ 재배선됐는데 in!=0({s.get('in')}) — 재배선 로직 의심")

    if problems:
        print(f"문제 {len(problems)}건:")
        for p in problems:
            print(f"  - {p}")
        return False
    print(f"정상: {len(segments)}개 세그먼트 전부 통과")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] not in ("run", "validate", "sheet"):
        print("사용법: python3 -m lib.vet_and_extract_catalog run [--limit N] [--roles a,b,c] [--no-detect]")
        print("      python3 -m lib.vet_and_extract_catalog validate")
        print("      python3 -m lib.vet_and_extract_catalog sheet   # flagged 썸네일 그리드 생성")
        sys.exit(1)
    if sys.argv[1] == "validate":
        sys.exit(0 if validate() else 1)
    if sys.argv[1] == "sheet":
        build_contact_sheet()
        sys.exit(0)

    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    roles = ("broll",)
    if "--roles" in sys.argv:
        roles = tuple(sys.argv[sys.argv.index("--roles") + 1].split(","))
    detect = "--no-detect" not in sys.argv
    run(limit=limit, roles=roles, detect=detect)
