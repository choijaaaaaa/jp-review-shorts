# 일본 직접촬영 상품 리뷰 Shorts 템플릿(Stage 7) — 단일 상품 딥다이브 포맷.
# WHY 이 파일이 2026-08-13에 통째로 새로 짜였는지: 원래는 "상품 여러 개를
# b-roll+정보카드로 훑는" 멀티 상품 리뷰였는데(구 make_info_card_png/
# _build_review_timeline/render()), 사용자가 "하나의 품목에 대한 설명이 아니며
# 킷캣은 왜 넣었으며" 피드백으로 완전히 단일 상품 딥다이브로 방향 전환했다.
# 그 뒤 삼양불닭카레_JP_1 topic 하나를 놓고 15번 넘게 반복 피드백을 받으며
# /tmp 임시 스크립트로만 검증했던 디자인(썸네일 커버컷→인트로 훅칩→아이템
# 왔다갔다→칠판 상세설명)을 이 파일에 정식으로 코드화한 것 — 다음 topic부터는
# 매번 /tmp에서 새로 짜지 않고 이 render_single_product()만 호출하면 된다.
#
# 씬 구성(전부 narration.srt 문장 경계로 길이가 자동 결정됨, 수동 타임스탬프
# 입력 불필요 — 구 버전의 "product마다 start/end 직접 기입" 방식 폐기):
#   Scene0: 1초 무음 정지컷(아이템+훅 문구, 사실상 썸네일용)
#   Scene1: 인트로 b-roll 이어붙이기 + 훅 텍스트 칩 팝인 애니메이션 + 상품 태그
#   Scene2: 아이템 클로즈업 정왕복(최대 1~2회 왕복, 시청자가 물건을 알아볼 시간)
#   Scene3: 칠판(나무틀+상세 후기 불릿) + 위쪽엔 실사용 영상(없으면 정지 플레이스홀더)
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.bgm import mix_bgm  # noqa: E402
from lib.video_assembler import (  # noqa: E402
    _assert_title_glyph_coverage, _build_ad_tag_badge, _make_caption_png, _NO_SPACE_WRAP_LANGS,
    _parse_srt, _split_long_caption_entries, _title_font_for_lang, _wrap_text_for_lang,
    AD_TAG_TEXT_BY_LANG, build_instagram_safe_video,
)

W, H = 1080, 1920
FPS = 30

# lib/video_assembler.py의 _YT_SAFE_RIGHT/_YT_SAFE_BOTTOM과 반드시 같은 값이어야
# 한다(다른 3개 템플릿도 각자 로컬 복제해서 씀 — import 불가한 함수 지역 상수라
# 이 프로젝트의 기존 컨벤션 그대로 따름).
_YT_SAFE_RIGHT = 150
_YT_SAFE_BOTTOM = 320
_SAFE_TOP = 50
_SAFE_LEFT = 50
SAFE_X1 = W - _YT_SAFE_RIGHT
SAFE_Y1 = H - _YT_SAFE_BOTTOM

PANEL_FILL = (18, 18, 22, 175)  # 다크 프로스티드(2026-08-13, "흰 바탕에 글만
# 올려놓은 느낌" 피드백 이후 확정) — 이 파일의 모든 텍스트 패널/칩이 공유하는 톤
SHADOW = (0, 0, 0)
ACCENT = (255, 209, 77)  # 훅 텍스트·칠판·뱃지 전부 공유하는 포인트 컬러

BOARD_GREEN = (36, 58, 48)
WOOD = (92, 62, 40)
CHALK = (225, 225, 220)
BOARD_FRAME_T = 24

COVER_DURATION = 1.0  # Scene0(무음 썸네일 커버컷) 고정 길이
INTRO_END_BUFFER = 0.4  # WHY: 인트로 영상이 훅 문장 오디오보다 이 정도 먼저
# 끝나야 다음 씬(아이템 리빌) 전환이 문장-문장 사이 자연스러운 pause 안에서
# 일어난다(실측: pause 자체가 ~0.32초라 완전히 안 겹침) — 오디오 뒤에 남는
# 여운은 다음 씬 시작으로 자연스럽게 흡수됨.
_MIN_SCENE_DURATION = 0.6  # 이보다 짧아지면 재생 자체가 부자연스러워 clamp

# 나레이션 자막(2026-08-13 도입, "생각해보니까 그게없네 자막이" 피드백) —
# 인트로/아이템 씬은 화면이 비교적 비어있어 훅칩 위쪽 안전영역에 그냥 얹지만,
# 칠판 씬은 위(스크롤+인용카드)·아래(칠판 판)가 이미 꽉 차있어서 그 사이에
# 전용 밴드를 하나 더 넣는다 — 그만큼 칠판 판 비율을 줄여서 확보(0.34→0.30).
_CAPTION_UPPER_Y = 820  # WHY 820(2026-08-13, "칠판씬 자막 위치 근방으로 맞추자"
# 피드백으로 300→820) — 인트로/아이템 씬엔 훅칩(중심 H*0.62≈1190, 최대 폰트
# 기준 칩+상품태그필까지 대략 y=1080~1400 차지)이 있어서 완전히 같은 y는
# 못 쓴다 — 훅칩 바로 위로 최대한 내림. 나머지 간격은 칠판 씬 쪽 밴드를
# 위로 옮겨서 좁힌다(아래 _CHALKBOARD_BOARD_RATIO 참고).
_CAPTION_BAND_H = 180  # WHY 140→180(2026-08-13, "자막 폰트 키우자" 피드백으로
# 확대) — 밴드가 커야 큰 폰트도 안 잘리고 들어감.
_INTRO_CAPTION_FONT_SIZE = 70  # WHY 60→70(2026-08-14, "썸네일도 간결하고
# 크게" 피드백 — 칠판 밴드가 64→76으로 커진 것과 같은 비율로, 인트로
# 자막도 `_make_caption_png` 기본값(60)보다 키운다).
_INTRO_CAPTION_MAX_W = 940  # `_make_caption_png` 기본 max_width와 동일 —
# 분할 판정에 쓰는 폭이 실제 렌더링 폭과 어긋나면 줄바꿈 지점이 달라진다.
_CHALKBOARD_BOARD_RATIO = 0.42  # WHY 0.30→0.36(2026-08-13, "위 스크롤 영상
# Height 좀더 줄이고... 자막 위치랑 맞추라했는데 왜 위에있냐" 피드백) — 칠판
# 판을 더 키워서 top_video를 줄이면 그만큼 자막 밴드 시작 y가 위로
# 당겨져서(밴드는 top_video 바로 다음이라) 인트로/아이템 자막(y=820)과의
# 간격이 좁혀진다. WHY 0.36→0.42(2026-08-16, 안전영역 실측 버그 수정과
# 함께) — 판 하단 320px가 유튜브 UI에 가려지는 게 확인되면서(위
# `_CHALKBOARD_SAFE_LOCAL_Y1` 참고) 실제 쓸 수 있는 안전 높이가 372px밖에
# 안 됐다. 15개 topic 전수 실측 결과 불릿 5개 안팎 topic은 이 정도로도
# 최소폰트(아래 bullet_font_size 하한 참고)에서 들어오지만, 여유가 너무
# 없어서(available_h≈237px) 살짝만 길어져도 렌더가 실패한다 — 0.42로
# 키워 안전 높이를 351px까지 확보. top_video(review-scroll) 영역이 그만큼
# 줄지만(top_h 1048→934px) 리뷰 스크롤 자체가 안 보이는 것보다는 낫다.
_CHALKBOARD_BOARD_H = round(H * _CHALKBOARD_BOARD_RATIO / 2) * 2
_CHALKBOARD_TOP_H = H - _CHALKBOARD_BOARD_H - _CAPTION_BAND_H

# WHY 이 두 상수(2026-08-16, 실기기 스크린샷 실측 버그 수정 — "모바일에서 볼때
# 덮이는 아래쪽, 우측하단의 버튼이나 텍스트들이 칠판의 자막을 가려버리는데")
# — 칠판 판(board)은 vstack의 마지막 요소라 프레임 최하단(H)까지 이어진다.
# 그런데 `_build_chalkboard_slab`은 여태 board_w/board_h만 보고 불릿을
# 채웠지, 위에서 정의된 SAFE_X1/SAFE_Y1(유튜브 쇼츠 좋아요·공유·리믹스·
# 채널명 UI가 차지하는 우측 150px·하단 320px)을 전혀 참조하지 않았다 —
# `_assert_within_safe_area`가 정의만 되고 이 파일 어디서도 호출되지 않고
# 있었던 것도 같은 결함의 증상. 실측: board_h=692px 중 로컬 좌표 372px까지만
# 진짜 안전 영역이고 나머지 320px(거의 절반)이 화면에서 유튜브 UI에 가려짐.
_CHALKBOARD_BOARD_START_Y = _CHALKBOARD_TOP_H + _CAPTION_BAND_H  # == H - _CHALKBOARD_BOARD_H
_CHALKBOARD_SAFE_LOCAL_Y1 = SAFE_Y1 - _CHALKBOARD_BOARD_START_Y

RAW_FOOTAGE_DIR = PROJECT_ROOT / "raw_footage"
USER_TRIM_DIR = RAW_FOOTAGE_DIR / "user_trim"  # lib.local_studio 트림 툴 출력 위치


# ---------------------------------------------------------------------------
# 공용 유틸
# ---------------------------------------------------------------------------

def ffprobe_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _probe_audio_layout(path) -> tuple[int, int]:
    """⚠️ csv 출력의 필드 순서가 -show_entries에 나열한 순서와 다르게 나온다
    (실측 2026-08-13: "stream=channels,sample_rate"로 요청해도 "44100,1"처럼
    sample_rate가 먼저 나옴) — position 기반 파싱은 값이 뒤바뀌어 무음
    생성기에 sample_rate=1을 넘기는 사고로 이어졌고, 그 결과가 최종 오디오
    트랙에 비정상 샘플레이트(7350Hz, AAC 스펙상 가장 낮은 값으로 클램프된
    것으로 추정)로 남아 macOS QuickTime이 "알 수 없는 오류"로 재생을 거부하는
    원인이었다. key=value 형식으로 받아서 순서 무관하게 파싱한다."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels,sample_rate",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    values = dict(line.split("=", 1) for line in out.stdout.strip().splitlines() if "=" in line)
    return int(values["channels"]), int(values["sample_rate"])


def _seed_value(topic_name: str, salt: str) -> int:
    """다른 3개 템플릿과 동일한 topic-seeded 변주 공식 — 매 topic마다 패널
    위치/색이 결정론적으로(재실행해도 항상 같게) 달라지게 한다."""
    return sum(ord(c) * (i * 7 + len(salt)) for i, c in enumerate(topic_name + salt))


def _load_font(size: int, lang: str = "kor"):
    font_path, font_index = _title_font_for_lang(lang)
    return ImageFont.truetype(font_path, size, index=font_index)


def _layout_multiline(draw, raw_lines, size, max_width, max_height, lang="kor", min_size=20, line_h_ratio=1.3):
    """폭에 맞게 줄바꿈 → 높이 초과 시 폰트 축소 → 그래도 넘치면 마지막 줄 말줄임표."""
    while True:
        f = _load_font(size, lang)
        wrapped = []
        for line in raw_lines:
            wrapped.extend(_wrap_text_for_lang(draw, line, f, max_width, lang) or [""])
        line_h = round(size * line_h_ratio)
        total_h = line_h * max(len(wrapped), 1)
        if total_h <= max_height or size <= min_size:
            if total_h > max_height and line_h > 0:
                max_lines = max(1, int(max_height // line_h))
                if len(wrapped) > max_lines:
                    wrapped = wrapped[:max_lines]
                    wrapped[-1] = wrapped[-1].rstrip() + "…"
            return wrapped, f, line_h
        size -= 2


def _assert_within_safe_area(label: str, bbox: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = bbox
    if x0 < _SAFE_LEFT or y0 < _SAFE_TOP or x1 > SAFE_X1 or y1 > SAFE_Y1:
        raise ValueError(
            f"[proto_jp_review] '{label}' 세이프 영역 이탈: bbox={bbox}, "
            f"허용범위=({_SAFE_LEFT},{_SAFE_TOP})-({SAFE_X1},{SAFE_Y1})"
        )


def _wrap_simple(text: str, font, max_w: int, draw) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# 클립 자르기 / 리타이밍
# ---------------------------------------------------------------------------

def _trim_and_cover_crop_clip(src: str, in_s: float, out_s: float, out_path: Path,
                               subtitle_band: dict | None = None, speed: float = 1.0) -> None:
    """촬영본 구간을 잘라 1080x1920에 꽉 차게 cover-crop.

    subtitle_band: 샤오홍슈 원본에 박힌 자막을 지우는 대신 그 자리를 불투명
    바로 덮기 위한 좌표(원본 클립 기준 0~1 정규화 비율). CLAUDE.md 샤오홍슈
    규칙 참고 — 인페인팅으로 지우는 시도는 실패로 결론남.

    speed: 재생 속도 배수(1.0=무보정). DJI 도보 b-roll 하이퍼랩스화, 아이템
    정왕복 루프 길이 맞추기 등에 사용.
    """
    dur = max(out_s - in_s, 0.1)
    # WHY -noautorotate + 수동 transpose=1: "폴더 1"(폰 촬영본)은 회전
    # 메타데이터가 -90/+90/누락으로 제각각인데 ffmpeg 기본 autorotate가 +90
    # 태그를 잘못 처리해서 180도 뒤집혀 나오는 걸 실측 확인함 — autorotate를
    # 끄고 항상 직접 transpose=1을 걸어 우회한다.
    needs_manual_rotate = "폴더 1" in str(src)
    inputs = ["-noautorotate"] if needs_manual_rotate else []
    # WHY trim 필터로 정확한 구간을 자르는지: DJI 오즈모 포켓 원본은 HEVC에
    # 키프레임 간격이 넓어서 -i 앞에만 -ss를 주는 fast seek이 요청 시각과 다른
    # 프레임(가까운 키프레임)에 랜딩한다(실측: 카탈로그엔 "얼굴 없음"으로
    # 검증해둔 구간인데 실제 렌더링엔 얼굴 있는 행인이 나옴). -i 앞 -ss(coarse,
    # 목표보다 2초 일찍)로 근처까지 빠르게 이동한 뒤 `trim` 필터로 디코딩된
    # 프레임의 실제 타임스탬프 기준 정확히 자른다 — -ss를 -i 뒤에 두면
    # setpts(speed 배속)와 결합했을 때 배속이 무시되는 별도 버그가 있어서 안 씀.
    coarse_seek = max(in_s - 2.0, 0.0)
    accurate_offset = in_s - coarse_seek
    vf = f"trim=start={accurate_offset}:duration={dur},setpts=PTS-STARTPTS,"
    vf += "transpose=1," if needs_manual_rotate else ""
    if speed != 1.0:
        vf += f"setpts=PTS/{speed},"
    vf += f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if subtitle_band:
        xmin = subtitle_band.get("xmin", 0.0)
        xmax = subtitle_band.get("xmax", 1.0)
        bx = round(xmin * W)
        by = round(subtitle_band["ymin"] * H)
        bw = round((xmax - xmin) * W)
        bh = round((subtitle_band["ymax"] - subtitle_band["ymin"]) * H)
        vf += f",drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=black@0.94:t=fill"
    vf += f",fps={FPS}"
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-ss", f"{coarse_seek}", "-i", str(src),
         "-vf", vf,
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


def _retime_to_duration(src_path: Path, target_duration: float, out_path: Path,
                         max_speed_change: float = 2.5) -> None:
    """이미 렌더링된 무음 클립의 재생 속도를 미세 조정해서 목표 길이에 정확히
    맞춘다 — narration.srt 실측 길이가 TTS 재생성마다(랜덤 보이스 선택 때문에)
    조금씩 달라지는데, 그때마다 원본 소스부터 다시 자르지 않고 이미 만든
    클립을 살짝 리타이밍하는 쪽이 훨씬 싸고 안전하다(2026-08-13 확립된 방식)."""
    raw_dur = ffprobe_duration(src_path)
    if raw_dur <= 0.01:
        raise ValueError(f"[proto_jp_review] 리타이밍 대상 길이 이상함: {src_path}")
    speed = raw_dur / max(target_duration, 0.05)
    speed = max(1 / max_speed_change, min(max_speed_change, speed))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src_path), "-vf", f"setpts=PTS/{speed}", "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


def _concat_clips(paths: list[Path], out_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(f"file '{p.resolve()}'" for p in paths))
        list_path = Path(f.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
            check=True, capture_output=True,
        )
    finally:
        list_path.unlink(missing_ok=True)


def _fit_clip_to_duration(src_path: Path, target_duration: float, out_path: Path) -> None:
    """실사용 클립(사용자가 트림 툴로 직접 잘라 온 것)을 목표 길이에 맞춘다.
    speed를 바꾸면 실사 리액션이 부자연스러워지므로, 길면 자르고 짧으면
    반복 재생(loop)한다 — 정지 프레임 반복(freeze)은 "영상이 흐르지 않는다"는
    과거 피드백의 원인이었어서 안 씀."""
    src_dur = ffprobe_duration(src_path)
    if src_dur >= target_duration:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src_path), "-t", f"{target_duration:.3f}", "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
            check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src_path),
             "-t", f"{target_duration:.3f}", "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
            check=True, capture_output=True,
        )


# ---------------------------------------------------------------------------
# 상품 사진(뱃지/태그/커버 배경 공용 소스)
# ---------------------------------------------------------------------------

def _load_product_photo(spec: dict) -> Image.Image:
    """spec['product_photo']는 {"clip":..,"at":..} 또는 {"image":..} 둘 중 하나.
    정사각형 중앙크롭 PIL RGB 이미지를 반환(뱃지/태그/커버 배경 전부 이걸 재사용)."""
    pp = spec["product_photo"]
    if "image" in pp:
        img = Image.open(PROJECT_ROOT / pp["image"]).convert("RGB")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            frame_path = Path(tmp) / "frame.png"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{pp['at']}", "-i", str(pp["clip"]),
                 "-frames:v", "1", str(frame_path)],
                check=True, capture_output=True,
            )
            img = Image.open(frame_path).convert("RGB")
    return img


def _square_crop(img: Image.Image, size: int) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    cropped = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    return cropped.resize((size, size), Image.LANCZOS)


def _draw_product_tag_pill(canvas: Image.Image, product_photo: Image.Image,
                            product_tag: str, center_x: int, top_y: int,
                            thumb_size: int = 64, font_size: int = 28, lang: str = "kor") -> int:
    """원형 썸네일+이름의 필 배지를 canvas에 직접 합성(가로 중앙정렬). 다음
    콘텐츠가 이어붙을 y좌표(필 하단)를 반환한다."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    tag_font = _load_font(font_size, lang)
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    tb = tmp_draw.textbbox((0, 0), product_tag, font=tag_font)
    text_w = tb[2] - tb[0]
    gap, pad_x = 14, 20
    pill_h = thumb_size + 16
    pill_w = thumb_size + gap + text_w + pad_x * 2
    pill_x0 = center_x - pill_w // 2
    draw.rounded_rectangle([pill_x0, top_y, pill_x0 + pill_w, top_y + pill_h],
                            radius=pill_h // 2, fill=PANEL_FILL)
    thumb = _square_crop(product_photo, thumb_size)
    mask = Image.new("L", (thumb_size, thumb_size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, thumb_size - 1, thumb_size - 1], fill=255)
    tx, ty = pill_x0 + pad_x, top_y + (pill_h - thumb_size) // 2
    canvas.paste(thumb, (tx, ty), mask)
    draw.ellipse([tx, ty, tx + thumb_size, ty + thumb_size], outline=(*ACCENT, 255), width=3)
    text_x = tx + thumb_size + gap
    text_y = top_y + (pill_h - (tb[3] - tb[1])) // 2 - tb[1]
    draw.text((text_x, text_y), product_tag, font=tag_font, fill=(*ACCENT, 255))
    return top_y + pill_h


def _draw_hook_chip(canvas: Image.Image, hook_lines: list[str], center_x: int, center_y: int,
                     font_size: int = 130, lang: str = "kor", max_width: int | None = None) -> tuple[int, int]:
    """두 줄 훅 텍스트(1번째=흰색, 2번째=골드) 칩을 center_y 중심으로 그린다.
    (칩 top_y, 칩 bottom_y) 반환. WHY 58→130(2026-08-14, "화면에 딱 떠서 바로
    인지될 정도로 크게" — 58→80 1차 수정도 부족하다는 반복 피드백, 게다가
    이 파일이 git 관리가 안 되는 프로젝트라 다른 세션의 동시 편집에 58로
    덮어써진 적도 있었음) — 시작 font_size만 올리는 걸론 부족했다. 실측해보니
    카레("한국 브랜드, 한국엔 없다?!" 등 긴 줄)처럼 폭이 넓은 훅 문구는 시작값이
    80이든 130이든 아래 축소 루프가 결국 같은 값(≈86)으로 수렴해버려서 체감상
    안 커졌다 — 진짜 병목은 시작값이 아니라 `max_width`(허용 폭) 자체였다.
    `max_width` 인자를 새로 받아 호출부가 용도별로 다르게 줄 수 있게 하고
    (Scene0 순수 썸네일은 실제 재생 UI가 안 겹치니 훨씬 넓게, Scene1 재생 중
    오버레이는 기존처럼 좁게), 칩 좌우 패딩도 42→28로 줄여 텍스트에 쓸 폭을
    더 확보했다. WHY 폭 넘치면 폰트 자동 축소(2026-08-13): 훅 문구 길이는
    topic마다 세션/에이전트가 자유롭게 쓰는데, 긴 줄이 세이프 영역까지
    침범한 적이 실측으로 발견됨 — 고정폭 줄바꿈 대신 한 줄 유지가 임팩트상
    낫다고 판단해 줄바꿈 대신 폰트를 줄인다. ⚠️ **축소 하한은 70이 아니라
    40(2026-08-14 정정)** — 처음엔 한국어 실측(가장 긴 줄도 94까지만 축소)만
    보고 70으로 올렸는데, 영어 훅 문구는 같은 의미도 문자 수가 훨씬 많아서
    (예: "This KitKat is from Don Quijote Osaka") 실제로 50까지 축소해야
    프레임을 안 넘어가는 topic이 있었다 — 70 바닥에 막혀 글자가 화면 밖으로
    잘려나가는 사고가 실측 확인됨. 하한은 "안 작아 보이게"보다 "절대 안
    잘리게"가 항상 우선이라 여유 있게 40으로 낮춤(언어별 실측 최소값: 한국어
    86~114, 영어 50~62, 대만어 74~88 — 전부 40보다 크므로 이 바닥엔 안 걸림)."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    pad_x, pad_y = 28, 30
    if max_width is None:
        max_width = SAFE_X1 - _SAFE_LEFT - pad_x * 2  # 재생 중 오버레이용 — 세이프 영역 준수
    while True:
        font = _load_font(font_size, lang)
        line_h = round(font_size * 1.35)
        bboxes = [tmp_draw.textbbox((0, 0), ln, font=font) for ln in hook_lines]
        block_w = max(b[2] - b[0] for b in bboxes)
        if block_w <= max_width or font_size <= 40:
            break
        font_size -= 4
    block_h = line_h * len(hook_lines)
    chip_w, chip_h = block_w + pad_x * 2, block_h + pad_y * 2
    chip_x0 = center_x - chip_w // 2
    chip_y0 = center_y - chip_h // 2

    # 부드러운 블러 드롭섀도우(2026-08-13 확정 디자인 — 두꺼운 밈 스타일
    # 아웃라인 대신 은은한 그림자로 실사 배경 위에서도 텍스트가 붕 뜨지 않게)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([chip_x0, chip_y0 + 14, chip_x0 + chip_w, chip_y0 + chip_h + 14],
                          radius=26, fill=SHADOW + (110,))
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))
    canvas.alpha_composite(shadow)

    draw.rounded_rectangle([chip_x0, chip_y0, chip_x0 + chip_w, chip_y0 + chip_h],
                            radius=26, fill=PANEL_FILL)
    for i, (line, bb) in enumerate(zip(hook_lines, bboxes)):
        tx = chip_x0 + (chip_w - (bb[2] - bb[0])) // 2 - bb[0]
        ty = chip_y0 + pad_y + i * line_h - bb[1]
        color = (255, 255, 255, 255) if i == 0 else (*ACCENT, 255)
        draw.text((tx, ty), line, font=font, fill=color)
    return chip_y0, chip_y0 + chip_h


def _build_hook_overlay_png(spec: dict, product_photo: Image.Image, chip_center_y: int,
                             lang: str = "kor") -> Image.Image:
    """훅 칩 + 그 아래 상품 태그 필을 담은 전체화면 투명 PNG(정적 최종 상태 —
    팝인 애니메이션의 마지막 프레임이자, 애니메이션 끝난 뒤 계속 유지되는 오버레이)."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _, chip_bottom = _draw_hook_chip(canvas, spec["hook_lines"], W // 2, chip_center_y, lang=lang)
    _draw_product_tag_pill(canvas, product_photo, spec["product_tag"], W // 2, chip_bottom + 22, lang=lang)
    return canvas


# ---------------------------------------------------------------------------
# Scene0: 무음 썸네일 커버컷
# ---------------------------------------------------------------------------

def _build_cover_scene(spec: dict, product_photo: Image.Image, out_path: Path, lang: str = "kor") -> None:
    bg = product_photo.resize((W, H), Image.LANCZOS) if product_photo.size != (W, H) else product_photo
    # WHY 커버는 정사각 크롭이 아니라 원본 프레임 그대로 리사이즈: 정사각으로
    # 자르면 매대 맥락(가격표 등 "일본에서만 판다"는 시각적 근거)이 잘려나감.
    cover = Image.blend(bg.convert("RGB"), Image.new("RGB", bg.size, (0, 0, 0)), 0.18)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    canvas.paste(cover, (0, 0))
    # WHY max_width를 넓게 따로 주는지(2026-08-14): 이 씬(Scene0)은 실제
    # 재생 중 노출되는 게 아니라 정지 썸네일이라 YouTube 재생 UI(우측 150px
    # 세이프 영역)와 겹칠 일이 없다 — Scene1(재생 중 오버레이)과 같은 좁은
    # 폭 제약을 그대로 쓰면 썸네일 텍스트가 필요 이상으로 작아진다.
    cover_max_width = W - 24 * 2 - 28 * 2  # 캔버스 양쪽 24px 여백만 남기고 칩 패딩(28*2) 제외
    _, chip_bottom = _draw_hook_chip(canvas, spec["hook_lines"], W // 2, H // 2 - 60,
                                      lang=lang, max_width=cover_max_width)
    _draw_product_tag_pill(canvas, product_photo, spec["product_tag"], W // 2, chip_bottom + 34, lang=lang)

    with tempfile.TemporaryDirectory() as tmp:
        cover_path = Path(tmp) / "cover.jpg"
        canvas.convert("RGB").save(cover_path, quality=95)
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", str(cover_path), "-t", f"{COVER_DURATION}",
             "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
            check=True, capture_output=True,
        )


# ---------------------------------------------------------------------------
# Scene1: 인트로 b-roll + 훅 칩 팝인
# ---------------------------------------------------------------------------

_POP_FRAMES = 10  # WHY 프레임 단위 PIL 합성인지: ffmpeg의 scale=eval=frame+pad+
# fade 필터 체인으로 동적 팝인을 시도했다가 오버레이가 통째로 안 보이는 버그를
# 실측(2026-08-13, 빠른 seek·정확한 seek·단색 배경 3중 확인) — 신뢰 가능한
# 대안으로 "프레임 N장 추출 → PIL로 스케일/페이드 합성 → 재인코딩" 방식 채택.


def _build_intro_scene(spec: dict, topic_name: str, product_photo: Image.Image,
                        target_duration: float, tmp_path: Path, out_path: Path,
                        lang: str = "kor") -> None:
    seg_paths = []
    for i, b in enumerate(spec["intro_broll"]):
        seg = tmp_path / f"introseg_{i:02d}.mp4"
        _trim_and_cover_crop_clip(b["clip"], b["in"], b["out"], seg,
                                   subtitle_band=b.get("subtitle_band"), speed=b.get("speed", 1.0))
        seg_paths.append(seg)
    joined = tmp_path / "intro_joined.mp4"
    _concat_clips(seg_paths, joined)
    retimed = tmp_path / "intro_retimed.mp4"
    _retime_to_duration(joined, target_duration, retimed)

    chip_center_y = round(H * 0.62)
    final_overlay = _build_hook_overlay_png(spec, product_photo, chip_center_y, lang=lang)
    overlay_path = tmp_path / "hook_overlay.png"
    final_overlay.save(overlay_path)

    # 1) 처음 _POP_FRAMES장: 커지면서 페이드인하는 팝 애니메이션을 프레임별로 합성
    raw_frames_dir = tmp_path / "raw_frames"
    raw_frames_dir.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(retimed), "-vf", f"fps={FPS}",
         "-frames:v", str(_POP_FRAMES), str(raw_frames_dir / "f_%03d.png")],
        check=True, capture_output=True,
    )
    pop_frames_dir = tmp_path / "pop_frames"
    pop_frames_dir.mkdir()
    n_raw = len(list(raw_frames_dir.glob("f_*.png")))
    for i in range(n_raw):
        raw = Image.open(raw_frames_dir / f"f_{i + 1:03d}.png").convert("RGBA")
        progress = (i + 1) / n_raw
        eased = 1 - (1 - progress) ** 2  # ease-out
        scale = 0.82 + 0.18 * eased
        alpha = eased
        scaled_w, scaled_h = round(W * scale), round(H * scale)
        scaled = final_overlay.resize((scaled_w, scaled_h), Image.LANCZOS)
        frame_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        frame_overlay.paste(scaled, ((W - scaled_w) // 2, (H - scaled_h) // 2), scaled)
        r, g, b_, a = frame_overlay.split()
        a = a.point(lambda p: round(p * alpha))
        frame_overlay = Image.merge("RGBA", (r, g, b_, a))
        composited = raw.copy()
        composited.alpha_composite(frame_overlay)
        composited.convert("RGB").save(pop_frames_dir / f"p_{i + 1:03d}.png")
    pop_clip = tmp_path / "intro_pop.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(pop_frames_dir / "p_%03d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(pop_clip)],
        check=True, capture_output=True,
    )

    # 2) 나머지 구간: 완성된 오버레이를 정적으로 얹기(팝 애니메이션 프레임 수만큼 -ss)
    pop_seconds = n_raw / FPS
    remainder = tmp_path / "intro_remainder.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{pop_seconds:.4f}", "-i", str(retimed),
         "-i", str(overlay_path), "-filter_complex", "[0:v][1:v]overlay=0:0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(remainder)],
        check=True, capture_output=True,
    )
    _concat_clips([pop_clip, remainder], out_path)


# ---------------------------------------------------------------------------
# Scene2: 아이템 클로즈업 정왕복
# ---------------------------------------------------------------------------

def _build_item_scene(spec: dict, target_duration: float, tmp_path: Path, out_path: Path) -> None:
    item = spec["item_clip"]

    if "clips" in item:
        # ⚠️ 여러 개의 실촬영 구간(같은 상품의 다른 향/각도 등)을 순서대로
        # 이어붙인다(2026-08-13, "이거 관련해서 좀더 뭔가 돌리고 돌리고
        # 하는거보다 더 좋은 방법없을까" 피드백) — 촬영분이 짧다고 한 구간을
        # 정왕복(앞으로 갔다 반대로 되감기)으로 억지로 늘리는 대신, 카탈로그에
        # 같은 product_line으로 등록된 다른 구간이 있으면 그걸 실제 컷으로
        # 붙여서 보여준다. **속도 조정 절대 금지(2026-08-13, "물건 나올 때
        # 영상 배속을 주면 인지가 잘안돼" 피드백)** — 정왕복 경로는 배속으로
        # 정확한 길이를 맞추지만, 여기서는 `_fit_clip_to_duration`과 동일한
        # 원칙(실사용 클립과 같은 이유 — speed를 바꾸면 상품이 뭔지 알아보기
        # 어려워짐)으로 길면 자르고 짧으면 반복 재생만 한다.
        segs = []
        for i, c in enumerate(item["clips"]):
            seg = tmp_path / f"item_seg_{i}.mp4"
            _trim_and_cover_crop_clip(c["clip"], c["in"], c["out"], seg)
            segs.append(seg)
        joined = tmp_path / "item_joined.mp4"
        _concat_clips(segs, joined)
        _fit_clip_to_duration(joined, target_duration, out_path)
        return

    round_trips = max(1, min(2, item.get("round_trips", 1)))  # WHY 최대 2회:
    # "이 아이템 영상 세번정도 왔다갔다하던데... 최대로 왔다갔다 해도 두번정도로
    # 해야될거같은데" 피드백으로 확정된 상한.
    front_avail = item["out"] - item["in"]
    speed = (front_avail * round_trips * 2) / max(target_duration, 0.3)
    speed = max(0.3, min(5.0, speed))

    fwd = tmp_path / "item_fwd.mp4"
    _trim_and_cover_crop_clip(item["clip"], item["in"], item["out"], fwd, speed=speed)
    rev = tmp_path / "item_rev.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(fwd), "-vf", "reverse", "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(rev)],
        check=True, capture_output=True,
    )
    joined = tmp_path / "item_joined.mp4"
    _concat_clips([fwd, rev] * round_trips, joined)
    _retime_to_duration(joined, target_duration, out_path)


# ---------------------------------------------------------------------------
# Scene3: 칠판(실사용 영상 + 상세 후기)
# ---------------------------------------------------------------------------

def _build_chalkboard_slab(chalkboard: dict, board_w: int, board_h: int, lang: str = "kor") -> Image.Image:
    import numpy as np

    img = Image.new("RGB", (board_w, board_h), BOARD_GREEN)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 6, (board_h, board_w, 1)).astype(np.int16)
    arr = np.clip(np.array(img).astype(np.int16) + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, board_w - 1, board_h - 1], outline=WOOD, width=BOARD_FRAME_T)
    draw.rectangle(
        [BOARD_FRAME_T - 4, BOARD_FRAME_T - 4, board_w - BOARD_FRAME_T + 3, board_h - BOARD_FRAME_T + 3],
        outline=(60, 40, 26), width=2,
    )

    frame_left = frame_top = BOARD_FRAME_T
    circle_cx, circle_cy, circle_r = frame_left + 26, frame_top + 26, 15
    draw.ellipse([circle_cx - circle_r, circle_cy - circle_r, circle_cx + circle_r, circle_cy + circle_r],
                 outline=CHALK, width=2)
    star_cx, star_cy, star_r = board_w - frame_left - 26, board_h - frame_top - 26, 14
    pts = []
    for i in range(10):
        ang = -3.14159 / 2 + i * 3.14159 / 5
        r = star_r if i % 2 == 0 else star_r * 0.45
        pts.append((star_cx + r * np.cos(ang), star_cy + r * np.sin(ang)))
    draw.polygon(pts, outline=CHALK, width=2)

    title_font = _load_font(60, lang)
    title_text = chalkboard["title"]
    title_x = circle_cx + circle_r + 22
    title_y = frame_top + 10
    draw.text((title_x, title_y), title_text, font=title_font, fill=(255, 255, 255))
    tb = draw.textbbox((title_x, title_y), title_text, font=title_font)
    underline_y = tb[3] + 8
    draw.line([(title_x, underline_y), (tb[2], underline_y)], fill=ACCENT, width=4)

    bullet_x = frame_left + 34
    text_x = bullet_x + 50
    # WHY min()으로 SAFE_X1도 같이 재는지(2026-08-16 버그 수정): 기존 계산은
    # 프레임 장식 여백(30px)만 뺐지 유튜브 UI 세이프 영역(SAFE_X1)은 아예 몰랐다
    # — board_w(=W, 전체 프레임 폭)를 그대로 쓰다 보니 텍스트가 우측 150px
    # 안전 영역을 넘어가는 게 실측으로 확인됐다.
    max_text_w = min(board_w - frame_left - 30 - text_x, SAFE_X1 - text_x)
    bullet_gap = 10  # WHY 16→10(2026-08-16, 안전영역 버그 수정과 함께) —
    # 안전 높이가 좁아진 만큼 불릿 사이 여백도 줄여서 조금이라도 더 확보.
    # WHY min()으로 _CHALKBOARD_SAFE_LOCAL_Y1도 같이 재는지(2026-08-16 버그
    # 수정): board_h 전체(프레임 최하단까지)를 여유 공간으로 썼는데, board
    # 하단 320px는 유튜브 UI(좋아요·공유·리믹스·"비공개" 배지 등)에 실제로
    # 가려지는 영역이다 — 이 로컬 안전한계선을 넘지 않게 강제해야 폰트
    # 축소 루프가 실제로 안전한 높이 안으로 수렴한다.
    available_h = min(
        board_h - frame_left - (underline_y + 34) - 20,  # 아래쪽 장식 여백 20px 확보
        _CHALKBOARD_SAFE_LOCAL_Y1 - (underline_y + 34),
    )

    # ⚠️ 사용법/복용법 섹션(2026-08-13 도입, "사용법도 칠판이랑 Summary
    # 사이에... 인터넷에 다 있잖아" 피드백) — 리뷰 불릿과는 시각적으로
    # 구분되게 소제목("사용법")+번호 목록으로 렌더링한다. `chalkboard`에
    # `usage_steps`가 없으면 완전히 스킵(스낵류처럼 의미있는 사용법이 없는
    # topic도 있음).
    usage_steps = chalkboard.get("usage_steps") or []
    usage_label = chalkboard.get("usage_label", "사용법")

    # WHY 폰트 자동 축소(2026-08-13 실측 버그 수정): 불릿이 리서치 기반이라
    # 길어질 수 있는데("정보량 부족하면 늘려도 됨" 콘텐츠 원칙), 불릿 개수·
    # 줄바꿈 총 높이가 칠판 판 높이를 넘으면 마지막 줄이 하단 프레임에 잘려
    # 나가는 걸 실측으로 발견함(포키아마오우_JP_1) — 안 맞으면 최소 크기까지
    # 폰트를 줄여서 항상 판 안에 들어오게 강제한다. 사용법 섹션이 있으면
    # 같은 축소 루프 안에서 같이 재서(공유 폰트 크기) 전체가 한 번에
    # available_h 안에 맞게 한다. ⚠️ 하한 28→20(2026-08-16, 안전영역 버그
    # 수정과 함께 재실측) — 15개 topic 전수 시뮬레이션 결과 하한을 28로 두면
    # 불릿 5개 이상인 topic 대부분이 새로 좁아진 available_h(위 "안전영역"
    # 주석 참고)를 못 맞춰서 마지막에 아래 `_assert_within_safe_area`가
    # 렌더 자체를 실패시킨다 — "작아 보여도 실제로 다 보이는" 쪽이 "적당한
    # 크기인데 절반이 유튜브 UI에 가려지는" 쪽보다 항상 낫다는 이 파일의
    # 기존 원칙(축소 하한 관련 다른 주석들 참고) 그대로 20까지 더 낮춘다.
    bullet_font_size = 46
    while True:
        bullet_font = _load_font(bullet_font_size, lang)
        single_line_h = round(bullet_font_size * 1.32)
        wrapped_bullets = [_wrap_text_for_lang(draw, b, bullet_font, max_text_w, lang) for b in chalkboard["bullets"]]
        total_h = sum(len(w) * single_line_h + bullet_gap for w in wrapped_bullets)

        if usage_steps:
            label_font_size = round(bullet_font_size * 0.72)
            label_font = _load_font(label_font_size, lang)
            wrapped_steps = [_wrap_text_for_lang(draw, s, bullet_font, max_text_w - 20, lang) for s in usage_steps]
            usage_h = (label_font_size + 18) + sum(len(w) * single_line_h + 10 for w in wrapped_steps)
            total_h += 26 + usage_h  # 구분 간격 26px

        if total_h <= available_h or bullet_font_size <= 20:
            break
        bullet_font_size -= 2

    cur_y = underline_y + 34
    for wrapped in wrapped_bullets:
        draw.text((bullet_x, cur_y + 8), "—", font=bullet_font, fill=ACCENT)
        for wline in wrapped:
            draw.text((text_x, cur_y), wline, font=bullet_font, fill=(255, 255, 255))
            cur_y += single_line_h
        cur_y += bullet_gap

    if usage_steps:
        cur_y += 26
        draw.text((bullet_x, cur_y), usage_label, font=label_font, fill=(*ACCENT, 255))
        cur_y += label_font_size + 18
        for i, wrapped in enumerate(wrapped_steps, start=1):
            draw.text((bullet_x, cur_y + 6), f"{i}.", font=bullet_font, fill=ACCENT)
            for wline in wrapped:
                draw.text((text_x, cur_y), wline, font=bullet_font, fill=(255, 255, 255))
                cur_y += single_line_h
            cur_y += 10

    # WHY 여기서 하드 체크(2026-08-16): 위 축소 루프는 bullet_font_size<=28에서
    # 멈추므로, 콘텐츠가 극단적으로 길면(불릿 개수 많고 사용법까지 있는 topic)
    # 최소 폰트로도 여전히 안전 영역을 넘을 수 있다 — 그 경우 조용히 잘리게
    # 두지 말고 렌더 자체를 실패시켜서(narration.txt/불릿 분량을 줄이라는
    # 신호) 유튜브 UI에 가려진 채 배포되는 사고를 막는다. board 로컬 좌표를
    # 프레임 절대 좌표로 환산(x 오프셋은 0 — board가 프레임 전체 폭을 그대로 씀,
    # y 오프셋은 _CHALKBOARD_BOARD_START_Y).
    _assert_within_safe_area(
        "chalkboard bullets/usage_steps",
        (text_x, underline_y + 34 + _CHALKBOARD_BOARD_START_Y,
         text_x + max_text_w, cur_y + _CHALKBOARD_BOARD_START_Y),
    )
    return img


def _build_chalkboard_badge(product_photo: Image.Image, product_tag: str,
                             thumb_size: int = 108, lang: str = "kor") -> Image.Image:
    """칠판 우상단에 프레임 위로 살짝 걸치는 상품 뱃지(둥근 사각 썸네일+이름표)."""
    thumb = _square_crop(product_photo, thumb_size)
    mask = Image.new("L", (thumb_size, thumb_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, thumb_size - 1, thumb_size - 1], radius=18, fill=255)

    label_font = _load_font(27, lang)
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    ltb = tmp_draw.textbbox((0, 0), product_tag, font=label_font)
    label_w, label_h = ltb[2] - ltb[0], ltb[3] - ltb[1]

    pad = 10
    canvas_w = max(thumb_size, label_w) + pad * 2
    canvas_h = thumb_size + label_h + pad * 3
    badge = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(badge)
    thumb_x = (canvas_w - thumb_size) // 2
    thumb_rgba = Image.new("RGBA", (thumb_size, thumb_size), (0, 0, 0, 0))
    thumb_rgba.paste(thumb, (0, 0), mask)
    badge.paste(thumb_rgba, (thumb_x, 0), thumb_rgba)
    bdraw.rounded_rectangle([thumb_x, 0, thumb_x + thumb_size - 1, thumb_size - 1],
                             radius=18, outline=(*ACCENT, 255), width=3)
    label_x = (canvas_w - label_w) // 2 - ltb[0]
    label_y = thumb_size + pad
    bdraw.text((label_x + 2, label_y + 2), product_tag, font=label_font, fill=(0, 0, 0, 180))
    bdraw.text((label_x, label_y), product_tag, font=label_font, fill=(*ACCENT, 255))
    return badge


def usage_clip_path_for_topic(topic_name: str) -> Path:
    return USER_TRIM_DIR / f"{topic_name}_usage.mp4"


def _write_pending_clip_status(topic_dir: Path, spec: dict, status: str) -> None:
    placeholder = spec.get("usage_placeholder")
    if not placeholder:
        return
    pending_path = topic_dir / "pending_clips.json"
    pending_path.write_text(json.dumps({
        "usage_clip": {
            "status": status,
            "description": placeholder.get("description", ""),
            "xiaohongshu_query": placeholder.get("xiaohongshu_query", ""),
            "target_path": str(usage_clip_path_for_topic(topic_dir.name).relative_to(PROJECT_ROOT)),
        }
    }, ensure_ascii=False, indent=2), encoding="utf-8")


# WHY 리서치 출처 스크롤 화면(2026-08-13, "칠판 씬에 실사용 영상 대신 신빙성
# 있는 자료를 스크롤하면서 보여주면 어떨까" 제안 채택): 실사용 영상이 없을 때
# 상품 사진 정지컷만 보여주는 것보다, 실제로 인용한 리서치 출처(일본 블로그·
# 공식 페이지 등) 화면을 위→아래로 스크롤하는 것처럼 보여주는 쪽이 "지어낸
# 정보 아니고 진짜 자료 확인했다"는 근거를 시각적으로도 보여준다 — 칠판
# 불릿이 이미 그 출처의 번역/요약이라 내용도 자연히 맞아떨어진다.
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_SCROLL_CAPTURE_HEIGHT = 3600


_DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# WHY User-Agent 위장(2026-08-15, 우메보시시트_JP_1 실측 버그) — 크롬 헤드리스
# 기본 UA 문자열은 서버에서 봇으로 바로 식별된다. mognavi.jp가 이걸
# 403 Forbidden(nginx 기본 에러 페이지)으로 막는 걸 실측 확인했는데, 그
# 에러 페이지조차 제목·구분선·"nginx" 텍스트가 있어서 24KB나 나가 아래
# 크기 체크(>10_000)를 통과해버렸다 — pending_clips.json엔
# "fulfilled_via_research"로 기록됐지만 실제 칠판 씬 위쪽은 텅 빈 흰 배경
# 이었다. UA를 일반 데스크톱 브라우저로 바꾸니 같은 URL이 정상 캡처됨
# (1.6MB, 실제 상품 사진·후기 텍스트 포함) — 직접 재현·검증함.
def _capture_source_screenshot(url: str, out_path: Path) -> bool:
    """헤드리스 크롬으로 출처 URL을 길게(스크롤 가능한 세로 길이로) 캡처한다.
    네트워크 실패·타임아웃 등 뭐든 실패하면 조용히 False만 반환 — 렌더
    파이프라인 전체를 막으면 안 되고, 호출부가 정지컷 폴백으로 넘어간다."""
    try:
        subprocess.run(
            [CHROME_BIN, "--headless", "--disable-gpu", "--no-sandbox",
             f"--window-size={W},{_SCROLL_CAPTURE_HEIGHT}", "--hide-scrollbars",
             f"--user-agent={_DESKTOP_UA}",
             f"--screenshot={out_path}", url],
            check=True, capture_output=True, timeout=30,
        )
        if not (out_path.exists() and out_path.stat().st_size > 10_000):
            return False
        # WHY 밝기 체크가 추가로 필요한지: UA 위장으로도 못 뚫는 사이트(JS
        # 챌린지·Cloudflare 차단 등)는 여전히 있을 수 있고, 그런 차단
        # 페이지도 크기 기준(>10_000)은 우연히 넘길 수 있다(위 사고 전례).
        # 실제 상품 리뷰 페이지는 사진·색색의 UI가 섞여 있어 거의 흰
        # 화면일 수가 없다는 점을 2차 안전망으로 쓴다 — 정상 캡처를
        # 오탐할 여지를 남기려고 기준을 널널하게(98%) 잡았다.
        with Image.open(out_path) as img:
            gray = img.convert("L")
            hist = gray.histogram()
            near_white = sum(hist[250:])
            total = gray.width * gray.height
            if total > 0 and near_white / total > 0.98:
                return False
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _build_research_scroll_top(screenshot_path: Path, top_h: int, target_duration: float, out_path: Path) -> None:
    """출처 스크린샷을 위→아래로 천천히 패닝(스크롤하는 느낌)해서 top_h
    높이의 배경 영상을 만든다 — 사용자가 실제로 그 페이지를 훑어보는
    느낌을 주는 게 목적이라 되돌아오는 왕복 없이 한 방향으로만 내려간다."""
    img = Image.open(screenshot_path).convert("RGB")
    img_w, img_h = img.size
    if img_w != W:
        scale = W / img_w
        img_h = round(img_h * scale)
        img = img.resize((W, img_h), Image.LANCZOS)
    resized_path = out_path.with_name(out_path.stem + "_resized.jpg")
    img.save(resized_path, quality=95)

    pan_range = max(img_h - top_h, 0)
    if pan_range == 0:
        y_expr = "0"
    else:
        y_expr = f"min({pan_range}\\,{pan_range}*t/{target_duration:.3f})"
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(resized_path), "-t", f"{target_duration:.3f}",
         "-vf", f"crop={W}:{top_h}:0:{y_expr}",
         "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


def _build_quote_card_png(japanese: str, translation: str, canvas_w: int, lang: str = "kor") -> Image.Image:
    """실제 인용 원문(일본어)+번역 카드 — 리서치 스크롤 배경 위에 겹쳐서
    "지어낸 요약이 아니라 진짜 이 문장을 번역한 것" 근거를 보여준다. 원문
    쪽(japanese)은 항상 일본어 폰트/줄바꿈 고정 — 인용 출처는 타깃 언어와
    무관하게 항상 일본어이기 때문. 번역 쪽만 lang을 따라간다."""
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    jp_font = _load_font(36, "ja")
    tr_font = _load_font(32, lang)
    max_text_w = canvas_w - 160
    jp_wrapped = _wrap_text_for_lang(tmp_draw, f"「{japanese}」", jp_font, max_text_w, "ja")
    kr_wrapped = _wrap_text_for_lang(tmp_draw, translation, tr_font, max_text_w, lang)
    jp_line_h = round(36 * 1.4)
    kr_line_h = round(32 * 1.4)
    pad, gap = 30, 16
    content_h = len(jp_wrapped) * jp_line_h + gap + len(kr_wrapped) * kr_line_h
    card_w = canvas_w - 80
    card_h = content_h + pad * 2
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card, "RGBA")
    draw.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=22, fill=PANEL_FILL)
    draw.rounded_rectangle([0, 0, 8, card_h - 1], radius=22, fill=(*ACCENT, 255))
    draw.rectangle([4, 0, 8, card_h - 1], fill=(*ACCENT, 255))
    y = pad
    for line in jp_wrapped:
        draw.text((pad + 20, y), line, font=jp_font, fill=(255, 255, 255, 255))
        y += jp_line_h
    y += gap
    for line in kr_wrapped:
        draw.text((pad + 20, y), line, font=tr_font, fill=(*ACCENT, 255))
        y += kr_line_h
    return card


_QUOTE_STAGGER_SEC = 3.2  # WHY 쌓이는 방식(2026-08-13, "계속 자막을 없애면서
# 새로 올리고 그럴필요없이 그 밑에 공간 많으니까 추가하고 그 위에 엎어치고"
# 피드백으로 교체): 예전엔 카드 하나가 사라지고 다음 카드가 그 자리를 대체하는
# 방식이었는데, 세로 공간이 넉넉히 남길래 새 카드를 이전 카드들 "아래"에 계속
# 추가해서 끝까지 쌓이게 바꿨다 — 한 번 뜬 인용구는 장면이 끝날 때까지 안 사라짐.
_QUOTE_FADE_SEC = 0.4


def _overlay_quote_cards(video_path: Path, quotes: list[dict], target_duration: float,
                          top_h: int, tmp_path: Path, out_path: Path, lang: str = "kor") -> None:
    """리서치 스크롤 영상 위에 실제 인용구 카드를 위→아래로 순서대로 쌓아
    올린다(한 번 뜨면 장면 끝까지 유지) — quotes가 비어있으면 원본 그대로 통과."""
    if not quotes:
        subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(out_path)],
                        check=True, capture_output=True)
        return

    top_margin, bottom_margin, gap = 140, 40, 22
    available_h = top_h - top_margin - bottom_margin

    cards = []  # (path, height)
    cum_h = 0
    for i, q in enumerate(quotes):
        card = _build_quote_card_png(q["japanese"], q["translation"], W, lang=lang)
        next_cum = cum_h + card.height + (gap if cards else 0)
        if next_cum > available_h and cards:
            break  # 안 들어가면 앞에서부터 들어가는 만큼만 씀(칠판 불릿 자동축소와 같은 원칙)
        p = tmp_path / f"quote_{i}.png"
        card.save(p)
        cards.append((p, card.height))
        cum_h = next_cum

    lead_in = 0.6
    max_fit = max(1, int((target_duration - lead_in) // _QUOTE_STAGGER_SEC))
    cards = cards[:max_fit]

    inputs = ["-i", str(video_path)]
    for p, _h in cards:
        inputs += ["-loop", "1", "-i", str(p)]

    filters = []
    current = "0:v"
    y = top_margin
    for i, (_p, h) in enumerate(cards):
        idx = i + 1
        start = lead_in + i * _QUOTE_STAGGER_SEC
        nxt_fade = f"fc{i}"
        filters.append(f"[{idx}:v]fade=t=in:st={start:.2f}:d={_QUOTE_FADE_SEC}:alpha=1[{nxt_fade}]")
        nxt = f"v{i}"
        filters.append(
            f"[{current}][{nxt_fade}]overlay=(main_w-overlay_w)/2:{y}:enable='gte(t\\,{start:.2f})'[{nxt}]"
        )
        current = nxt
        y += h + gap
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
         "-map", f"[{current}]", "-t", f"{target_duration:.3f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


_SUMMARY_LABEL = "SUMMARY"
_SUMMARY_BLUR_RADIUS = 45
_SUMMARY_DARKEN_ALPHA = 150  # 0~255, 블러 배경 위 텍스트 가독성용
_SUMMARY_TRANSITION_SEC = 0.5
_SUMMARY_RECAP_COUNT = 2  # 칠판 불릿 중 몇 개를 요약 카드에 다시 보여줄지

# WHY CTA를 언어별 하드코딩 딕셔너리로(2026-08-13, "좋아요랑 팔로우 해달라고
# 정도는 요청을 좀 해야할거같아. 각 국가의 언어로" 피드백) — topic마다 새로
# 쓸 필요 없는 정형화된 문구라 SUMMARY 라벨과 같은 방식(코드에 고정)으로
# 관리한다. ⚠️ th/id/zh 번역은 세션이 직접 옮긴 것 — 네이티브 검수 전이라
# 퍼블리시 전에 한 번 확인 필요.
_CTA_BY_LANG = {
    "kor": "좋아요 · 팔로우로 다음 리뷰도 받아보세요",
    "zh-TW": "喜歡的話按讚 · 追蹤，看更多開箱！",
    "zh": "喜欢的话点赞 · 关注，看更多开箱！",
    "th": "ถ้าชอบกดไลก์ ติดตามดูรีวิวต่อไปด้วยนะ",
    "en": "Like & follow for more Japan finds",
    "id": "Suka & follow untuk review Jepang lainnya",
}


def _build_summary_frame(product_photo: Image.Image, summary_text: str, bullets: list[str],
                          canvas_w: int, canvas_h: int, lang: str = "kor") -> Image.Image:
    """칠판 씬 마지막 구간 전용 "블러 배경 + 중앙 강조 Summary" 정지 프레임
    (2026-08-13, "리뷰 카드 다 올라오고난 후에... 배경에 이미지 블러처리해서
    두고 중앙에 강조해서 Summary" 피드백). narration.txt의 마지막 문장(이미
    녹음된 오디오·SRT 타이밍)을 그대로 재사용 — 새 TTS 호출 없음. ⚠️
    한 문장만 보여주면 "진짜 요약한다는 느낌이 안 난다"는 후속 피드백으로
    칠판 불릿 중 앞의 `_SUMMARY_RECAP_COUNT`개를 축약 재진술로 같이
    보여주고, 맨 아래에 언어별 CTA 한 줄을 추가했다."""
    src_w, src_h = product_photo.size
    scale = max(canvas_w / src_w, canvas_h / src_h)
    resized = product_photo.resize((round(src_w * scale), round(src_h * scale)), Image.LANCZOS)
    x0 = (resized.width - canvas_w) // 2
    y0 = (resized.height - canvas_h) // 2
    bg = resized.crop((x0, y0, x0 + canvas_w, y0 + canvas_h)).convert("RGBA")
    bg = bg.filter(ImageFilter.GaussianBlur(_SUMMARY_BLUR_RADIUS))
    darken = Image.new("RGBA", bg.size, (0, 0, 0, _SUMMARY_DARKEN_ALPHA))
    bg = Image.alpha_composite(bg, darken)

    draw = ImageDraw.Draw(bg, "RGBA")
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    max_card_w = canvas_w - 160
    label_font = _load_font(34)
    label_bbox = tmp_draw.textbbox((0, 0), _SUMMARY_LABEL, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]

    body_lines, body_font, body_line_h = _layout_multiline(
        tmp_draw, [summary_text], size=50, max_width=max_card_w - 112,
        max_height=round(canvas_h * 0.22), min_size=30, lang=lang,
    )
    body_bboxes = [tmp_draw.textbbox((0, 0), ln, font=body_font) for ln in body_lines]

    recap_bullets = bullets[:_SUMMARY_RECAP_COUNT]
    recap_lines, recap_font, recap_line_h = _layout_multiline(
        tmp_draw, recap_bullets, size=32, max_width=max_card_w - 112,
        max_height=round(canvas_h * 0.16), min_size=22, lang=lang,
    ) if recap_bullets else ([], None, 0)
    recap_bboxes = [tmp_draw.textbbox((0, 0), ln, font=recap_font) for ln in recap_lines]

    cta_text = _CTA_BY_LANG.get(lang, _CTA_BY_LANG["kor"])
    cta_font = _load_font(28, lang)
    cta_bbox = tmp_draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_w = cta_bbox[2] - cta_bbox[0]

    label_gap, section_gap = 26, 22
    pad_x, pad_y = 56, 44
    body_h = body_line_h * len(body_lines)
    recap_h = recap_line_h * len(recap_lines)
    cta_h = cta_bbox[3] - cta_bbox[1]
    card_h = (pad_y * 2 + (label_bbox[3] - label_bbox[1]) + label_gap + body_h
              + (section_gap + recap_h if recap_lines else 0) + section_gap + cta_h)
    content_w = max(
        label_w, cta_w,
        max((b[2] - b[0] for b in body_bboxes), default=0),
        max((b[2] - b[0] for b in recap_bboxes), default=0),
    )
    card_w = min(content_w + pad_x * 2, max_card_w)

    cx, cy = canvas_w // 2, canvas_h // 2
    card_x0, card_y0 = cx - card_w // 2, cy - card_h // 2

    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([card_x0, card_y0 + 16, card_x0 + card_w, card_y0 + card_h + 16],
                          radius=28, fill=SHADOW + (130,))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    bg.alpha_composite(shadow)

    draw.rounded_rectangle([card_x0, card_y0, card_x0 + card_w, card_y0 + card_h],
                            radius=28, fill=PANEL_FILL)

    ty = card_y0 + pad_y
    tx = card_x0 + (card_w - label_w) // 2 - label_bbox[0]
    draw.text((tx, ty - label_bbox[1]), _SUMMARY_LABEL, font=label_font, fill=(*ACCENT, 255))
    ty += (label_bbox[3] - label_bbox[1]) + label_gap
    for line, bb in zip(body_lines, body_bboxes):
        lx = card_x0 + (card_w - (bb[2] - bb[0])) // 2 - bb[0]
        draw.text((lx, ty - bb[1]), line, font=body_font, fill=(255, 255, 255, 255))
        ty += body_line_h
    if recap_lines:
        ty += section_gap
        for line, bb in zip(recap_lines, recap_bboxes):
            lx = card_x0 + (card_w - (bb[2] - bb[0])) // 2 - bb[0]
            draw.text((lx, ty - bb[1]), line, font=recap_font, fill=(210, 210, 210, 255))
            ty += recap_line_h
    ty += section_gap
    cx2 = card_x0 + (card_w - cta_w) // 2 - cta_bbox[0]
    draw.text((cx2, ty - cta_bbox[1]), cta_text, font=cta_font, fill=(*ACCENT, 255))
    return bg


def _build_chalkboard_scene(spec: dict, topic_dir: Path, product_photo: Image.Image,
                             target_duration: float, tmp_path: Path, out_path: Path,
                             summary_start_local: float, summary_text: str, lang: str = "kor") -> None:
    board_h = _CHALKBOARD_BOARD_H
    top_h = _CHALKBOARD_TOP_H

    usage_path = usage_clip_path_for_topic(topic_dir.name)
    top_video = tmp_path / "usage_top.mp4"
    if usage_path.exists():
        muted = tmp_path / "usage_muted.mp4"
        subtitle_band = spec.get("usage_clip_subtitle_band")
        vf = f"scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}"
        if subtitle_band:
            xmin, xmax = subtitle_band.get("xmin", 0.0), subtitle_band.get("xmax", 1.0)
            bx, by = round(xmin * W), round(subtitle_band["ymin"] * top_h)
            bw, bh = round((xmax - xmin) * W), round((subtitle_band["ymax"] - subtitle_band["ymin"]) * top_h)
            vf += f",drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=black@0.94:t=fill"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(usage_path), "-vf", vf, "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(muted)],
            check=True, capture_output=True,
        )
        _fit_clip_to_duration(muted, target_duration, top_video)
        _write_pending_clip_status(topic_dir, spec, status="fulfilled")
    else:
        source_url = spec.get("usage_placeholder", {}).get("source_url")
        screenshot_path = tmp_path / "source_screenshot.png"
        if source_url and _capture_source_screenshot(source_url, screenshot_path):
            scroll_raw = tmp_path / "scroll_raw.mp4"
            _build_research_scroll_top(screenshot_path, top_h, target_duration, scroll_raw)
            quotes = spec.get("usage_placeholder", {}).get("quotes", [])
            _overlay_quote_cards(scroll_raw, quotes, target_duration, top_h, tmp_path, top_video, lang=lang)
            _write_pending_clip_status(topic_dir, spec, status="fulfilled_via_research")
        else:
            # 실사용 영상도, 캡처 가능한 출처 URL도 없으면 상품 사진 정지컷으로
            # 대체(최후 폴백) — pending_clips.json에 설명+샤오홍슈 검색어를
            # 남겨서 lib.local_studio UI가 "채워야 할 자리"로 보여줄 수 있게 한다.
            with tempfile.TemporaryDirectory() as ph_tmp:
                ph_img = _square_crop(product_photo, min(W, top_h)).resize((W, top_h), Image.LANCZOS)
                ph_path = Path(ph_tmp) / "placeholder.jpg"
                ph_img.save(ph_path, quality=92)
                subprocess.run(
                    ["ffmpeg", "-y", "-loop", "1", "-i", str(ph_path), "-t", f"{target_duration:.3f}",
                     "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(top_video)],
                    check=True, capture_output=True,
                )
            _write_pending_clip_status(topic_dir, spec, status="pending")

    slab = _build_chalkboard_slab(spec["chalkboard"], W, board_h, lang=lang)
    slab_path = tmp_path / "chalkboard_slab.jpg"
    slab.save(slab_path, quality=95)
    board_video = tmp_path / "chalkboard_video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(slab_path), "-t", f"{target_duration:.3f}",
         "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(board_video)],
        check=True, capture_output=True,
    )

    stacked = tmp_path / "chalkboard_stack.mp4"
    # 자막 밴드(2026-08-13 도입) — 한 번은 인트로/아이템 씬과 통일해서 맨
    # 위로 옮겼었는데, "너무 위로 쳐박혀 있다"는 피드백으로 원래 자리(스크롤+
    # 인용카드 영상과 칠판 판 사이)로 되돌림 — 여기선 빈 판만 깔고, 실제
    # 자막 텍스트는 render_single_product의 _overlay_narration_captions()가
    # 영상 전체를 조립한 뒤 한 번에 굽는다(narration.srt 타이밍 기준 다른
    # 씬 자막과 통일).
    with tempfile.TemporaryDirectory() as band_tmp:
        band_img = Image.new("RGB", (W, _CAPTION_BAND_H), WOOD)
        band_path = Path(band_tmp) / "caption_band.jpg"
        band_img.save(band_path, quality=90)
        caption_band = tmp_path / "caption_band.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", str(band_path), "-t", f"{target_duration:.3f}",
             "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(caption_band)],
            check=True, capture_output=True,
        )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(top_video), "-i", str(caption_band), "-i", str(board_video),
         "-filter_complex", "[0:v][1:v][2:v]vstack=inputs=3[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(stacked)],
        check=True, capture_output=True,
    )

    badge = _build_chalkboard_badge(product_photo, spec["product_tag"], lang=lang)
    badge_path = tmp_path / "badge.png"
    badge.save(badge_path)
    badge_x = W - BOARD_FRAME_T - 20 - badge.width
    # WHY 완전히 top_h 위로(2026-08-13, "아이콘도 자막이랑 겹치네" 버그 수정) —
    # 원래는 badge.height*0.6만큼 프레임 seam 아래로 걸치게 디자인했는데,
    # 자막 밴드를 키우면서(_CAPTION_BAND_H 140→180, board 비율도 커짐) badge가
    # 밴드 자막 텍스트 영역과 최대 65px 겹치는 사고가 실측 확인됐다 — 밴드에
    # 실제 자막 텍스트가 항상 떠 있어서 "살짝 걸치는" 디자인을 유지할 수
    # 없다, 안전하게 밴드 위쪽에 완전히 띄운다.
    badge_y = top_h - badge.height - 10
    pre_summary = tmp_path / "chalkboard_pre_summary.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(stacked), "-i", str(badge_path),
         "-filter_complex", f"[0:v][1:v]overlay={badge_x}:{badge_y}[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(pre_summary)],
        check=True, capture_output=True,
    )

    # ⚠️ 마지막 구간 "블러 배경 + 중앙 Summary" 전환(2026-08-13, "리뷰 카드 다
    # 올라오고난 후에... 배경에 이미지 블러처리해서 두고 중앙에 강조해서
    # Summary" 피드백) — narration 마지막 문장이 시작되는 시점(이미 녹음된
    # 오디오·SRT 기준, 새 TTS 없음)에 크로스페이드로 전환한다.
    summary_frame = _build_summary_frame(product_photo, summary_text, spec["chalkboard"]["bullets"], W, H, lang=lang)
    summary_path = tmp_path / "summary_frame.jpg"
    summary_frame.convert("RGB").save(summary_path, quality=95)
    summary_video = tmp_path / "summary_video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(summary_path), "-t", f"{target_duration:.3f}",
         "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(summary_video)],
        check=True, capture_output=True,
    )
    transition_offset = max(0.0, min(summary_start_local, target_duration - _SUMMARY_TRANSITION_SEC - 0.1))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(pre_summary), "-i", str(summary_video),
         "-filter_complex",
         f"[0:v][1:v]xfade=transition=fade:duration={_SUMMARY_TRANSITION_SEC}:offset={transition_offset:.3f}[v]",
         "-map", "[v]", "-t", f"{target_duration:.3f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(out_path)],
        check=True, capture_output=True,
    )


_BAND_CAPTION_W = W - 80
_BAND_FONT_SIZE = 76  # WHY 64→76(2026-08-14, "자막폰트 좀 작다, 두줄까지만" 피드백)
_BAND_MAX_LINES = 2


def _split_text_to_fit_lines(text: str, font, max_width: int, lang: str,
                              max_lines: int, tmp_draw) -> list[str]:
    """자막 한 덩어리가 `max_lines`줄을 넘치면 여러 개의 순차 캡션으로
    쪼갠다(2026-08-14, "끊어서 가더라도 무조건 최대 두줄까지만, 폰트
    키우자" 피드백) — 폰트를 줄여서 다 욱여넣는 대신 큰 폰트를 유지한다.
    ⚠️ 원래는 어절 개수로 절반씩 재귀 이등분했는데, "빨갛다 둥글다 크다
    맛있다 이 네"처럼 문맥과 무관한 지점(숫자상 중간일 뿐인 위치)에서
    잘려서 뒤가 뭔지 전혀 모를 문장이 나오는 사고가 실측 확인됐다
    (2026-08-14) — 실제 렌더링에 쓸 폰트로 미리 줄바꿈한 뒤, 그 줄
    경계(항상 어절 단위)를 max_lines개씩 묶어서 쪼개는 방식으로 바꿨다 —
    임의의 어절 중간이 아니라 실제 화면에 그려질 줄바꿈 지점 그대로라
    "문장이 뚝 끊기는" 부자연스러움이 훨씬 준다."""
    wrapped = _wrap_text_for_lang(tmp_draw, text, font, max_width, lang)
    if len(wrapped) <= max_lines:
        return [text]
    sep = "" if lang in _NO_SPACE_WRAP_LANGS else " "
    return [sep.join(wrapped[i:i + max_lines]) for i in range(0, len(wrapped), max_lines)]


def _make_band_caption_png(text: str, out_path: Path, max_width: int, max_height: int,
                            lang: str = "kor") -> None:
    """칠판 씬 전용 자막 밴드(높이 고정, `_CAPTION_BAND_H`)에 맞춘 자막 —
    `_make_caption_png`는 높이 제약이 없어서 마침표 없이 긴 인용-attribution
    단문("실제 써본 사람 후기를 보면 '~'는 반응이 있고요" 류)이 밴드를 벗어나
    위/아래 다른 요소(인용카드·칠판 제목)를 가리는 사고가 실측 확인됐다
    (2026-08-13) — 훅칩/칠판 불릿과 같은 `_layout_multiline` 자동 축소로
    밴드 높이 안에 항상 들어오게 강제한다. 텍스트는 이미
    `_overlay_narration_captions`에서 `_split_text_to_fit_lines`로 2줄
    이내로 쪼개져서 들어오므로, 여기서는 최소한만 축소한다(안전망)."""
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    pad_x, pad_y = 30, 16
    lines, font, line_h = _layout_multiline(
        tmp_draw, [text], size=_BAND_FONT_SIZE, max_width=max_width - pad_x * 2,
        max_height=max_height - pad_y * 2, lang=lang, min_size=40,
    )
    img = Image.new("RGBA", (max_width, max_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, max_width, max_height], radius=16, fill=(0, 0, 0, 190))
    total_h = len(lines) * line_h
    y = (max_height - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (max_width - w) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), line, font=font, fill=(255, 255, 255, 255),
                   stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += line_h
    img.save(out_path)


def _overlay_narration_captions(video_path: Path, srt_path: str, cover_duration: float,
                                 chalkboard_start_global: float, video_total: float,
                                 tmp_path: Path, out_path: Path, lang: str = "kor",
                                 summary_global_start: float | None = None) -> None:
    """전체 영상(Scene0~3 합본)에 narration.srt 기준 자막을 굽는다(2026-08-13
    도입, "생각해보니까 그게없네 자막이" 피드백) — lib/video_assembler.py의
    검증된 trim+overlay+concat 방식(세그먼트별로 정확히 잘라 자막 PNG를
    올리는 방식, 키프레임 seek 부정확 문제를 피하려고 -ss 대신 trim 필터 사용)
    을 이 프로젝트 씬 구조에 맞게 로컬 복제 — Scene0(커버컷)는 오디오가 무음
    패딩이라 자막 없음. 자막 y 위치는 두 구간으로 나뉜다: 칠판 씬 진입 전
    (인트로/아이템)은 화면 위쪽 안전영역, 칠판 씬부터는 전용 밴드
    (`_CHALKBOARD_TOP_H`~`_CAPTION_BAND_H`, `_build_chalkboard_scene`가
    미리 확보해둔 자리). ⚠️ `summary_global_start` 이후는 자막을 아예 안
    그린다(2026-08-13 버그 수정) — 그 시점부터는 화면이 이미 Summary
    카드로 전환돼 있고 그 카드 자체가 같은 문장을 중앙에 크게 보여주는데,
    기존처럼 밴드 자막까지 얹으면 같은 문장이 두 번(카드 안+밴드) 겹쳐
    보이는 사고가 실측 확인됐다."""
    srt_entries = _parse_srt(srt_path)
    narration_span = video_total - cover_duration
    srt_entries = [(start, min(end, narration_span), text)
                   for start, end, text in srt_entries if start < narration_span]
    srt_entries = _split_long_caption_entries(srt_entries, lang)

    # ⚠️ 칠판 밴드 구간은 최대 _BAND_MAX_LINES줄로 강제 분할(2026-08-14,
    # "무조건 최대 두줄까지만 자막 넣도록 하고 폰트 키우자" 피드백) — 폰트를
    # 줄여서 3줄 이상 욱여넣는 대신, 긴 문장은 여러 개의 순차 캡션으로
    # 쪼개서 큰 폰트를 유지한다. 시간은 글자 수 비례로 배분
    # (`_split_long_caption_entries`와 같은 방식).
    #
    # ⚠️ 인트로 구간에도 같은 처리 확장(2026-08-14, "썸네일도 간결하고
    # 크게" 재지적으로 실측 확인 — 칠판 자막만 고치고 인트로 자막은 그대로
    # 뒀더니 인트로 씬에서 문단형 3줄 자막이 훅 칩이랑 겹쳐 보이는 문제가
    # 그대로 남아있었다). `_make_caption_png` 기본 폰트(60)보다 키운
    # `_INTRO_CAPTION_FONT_SIZE`로 분할 여부를 계산하고, 실제 렌더링
    # (아래 `_make_caption_png` 호출)도 같은 크기를 쓰도록 맞춘다.
    band_font = _load_font(_BAND_FONT_SIZE, lang)
    intro_font = _load_font(_INTRO_CAPTION_FONT_SIZE, lang)
    tmp_draw_split = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    band_max_w = _BAND_CAPTION_W - 60
    intro_max_w = _INTRO_CAPTION_MAX_W
    chalkboard_start_narration = chalkboard_start_global - cover_duration
    split_entries = []
    for start, end, text in srt_entries:
        in_chalkboard_region = start >= chalkboard_start_narration
        font = band_font if in_chalkboard_region else intro_font
        max_w = band_max_w if in_chalkboard_region else intro_max_w
        chunks = _split_text_to_fit_lines(text, font, max_w, lang, _BAND_MAX_LINES, tmp_draw_split)
        if len(chunks) <= 1:
            split_entries.append((start, end, text))
            continue
        total_len = sum(len(c) for c in chunks)
        cursor2 = start
        for i, chunk in enumerate(chunks):
            seg_end = end if i == len(chunks) - 1 else cursor2 + (end - start) * (len(chunk) / total_len)
            split_entries.append((cursor2, seg_end, chunk))
            cursor2 = seg_end
    srt_entries = split_entries

    cap_dir = tmp_path / "narration_caps"
    cap_dir.mkdir()
    timeline, cursor = [(0.0, cover_duration, None)], 0.0
    for start, end, text in srt_entries:
        if start > cursor + 0.05:
            timeline.append((cursor + cover_duration, start + cover_duration, None))
        timeline.append((start + cover_duration, end + cover_duration, text))
        cursor = end
    if cursor + cover_duration < video_total - 0.05:
        timeline.append((cursor + cover_duration, video_total, None))

    seg_paths = []
    for i, (start, end, text) in enumerate(timeline):
        dur = end - start
        if dur <= 0.02:
            continue
        seg = tmp_path / f"narrcap_{i:04d}.mp4"
        skip_for_summary = summary_global_start is not None and start >= summary_global_start
        if text and not skip_for_summary:
            cap_png = cap_dir / f"narrcap_{i:04d}.png"
            in_chalkboard = start >= chalkboard_start_global
            if in_chalkboard:
                _make_band_caption_png(text, cap_png, _BAND_CAPTION_W, _CAPTION_BAND_H, lang=lang)
                overlay_expr = f"x=(main_w-overlay_w)/2:y={_CHALKBOARD_TOP_H}"
            else:
                _make_caption_png(text, cap_png, font_size=_INTRO_CAPTION_FONT_SIZE,
                                   max_width=_INTRO_CAPTION_MAX_W, lang=lang)
                overlay_expr = f"x=(main_w-overlay_w)/2:y={_CAPTION_UPPER_Y}-overlay_h/2"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-loop", "1", "-r", str(FPS), "-t", f"{dur:.3f}", "-i", str(cap_png),
                 "-filter_complex",
                 f"[0:v]trim=start={start:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS[bg];"
                 f"[bg][1:v]overlay={overlay_expr}[v]",
                 "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-color_range", "tv", str(seg)],
                check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-filter_complex", f"[0:v]trim=start={start:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS[v]",
                 "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-color_range", "tv", str(seg)],
                check=True, capture_output=True,
            )
        seg_paths.append(seg)
    _concat_clips(seg_paths, out_path)


# ---------------------------------------------------------------------------
# 렌더 오케스트레이션
# ---------------------------------------------------------------------------

def compute_scene_durations(spec: dict, srt_path: str, audio_path: str) -> dict:
    """narration.srt 문장 경계 기준으로 4개 씬 길이를 계산한다. render_single_product
    내부에서 쓰는 것과 동일한 로직 — lib.local_studio가 트림 툴에 "이 정도
    길이로 잘라야 함"을 미리 보여주기 위해 렌더 없이 이 함수만 호출한다."""
    cues = _parse_srt(srt_path)
    if not cues:
        raise ValueError(f"[proto_jp_review] narration.srt에서 문장을 못 읽음: {srt_path}")
    hook_n = spec.get("hook_cue_count", 1)
    item_n = spec.get("item_cue_count", 1)
    if len(cues) <= hook_n + item_n:
        raise ValueError(
            f"[proto_jp_review] 문장 수({len(cues)})가 hook_cue_count+item_cue_count"
            f"({hook_n + item_n}) 이하 — 칠판 구간에 배정할 문장이 없음"
        )
    total_duration = ffprobe_duration(audio_path)
    scene1_target = max(_MIN_SCENE_DURATION, cues[hook_n - 1][1] - INTRO_END_BUFFER)
    scene2_end = cues[hook_n + item_n - 1][1]
    scene2_target = max(_MIN_SCENE_DURATION, scene2_end - scene1_target)
    scene3_target = max(_MIN_SCENE_DURATION, total_duration - scene2_end)
    return {
        "narration_total": total_duration,
        "cover": COVER_DURATION,
        "intro": scene1_target,
        "item": scene2_target,
        "chalkboard": scene3_target,
    }


def _validate_spec_glyphs(spec: dict, lang: str, srt_path: str, ad_tag: bool) -> None:
    """render_single_product()이 실제로 화면에 그릴 텍스트 전부를 렌더링(ffmpeg
    호출) 시작 전에 검사한다(2026-08-14, health-shorts card_news.py
    `_validate_spec_glyphs`/lib/video_assembler.py `_validate_assemble_glyphs`와
    같은 원칙 — WHY는 그쪽 주석 참고: 여러 ffmpeg 단계를 거친 뒤에야 tofu box를
    발견하면 중간 산출물이 낭비되고 결국 사람이 영상을 끝까지 재생해봐야만
    발견된다). 이 파일은 draw.text가 전부 `_load_font`(=`_title_font_for_lang`)
    하나만 쓴다 — video_assembler.py의 chalk 폰트(`_chalk_font_for_lang`)는
    이 템플릿(단일 상품 딥다이브 포맷)에서 아예 안 쓰이므로 검사할 폰트
    체계가 하나뿐이다."""
    checks = [
        ("훅 칩 1줄", spec["hook_lines"][0]),
        ("훅 칩 2줄", spec["hook_lines"][1]),
        ("상품 태그(필/뱃지)", spec["product_tag"]),
        ("칠판 제목", spec["chalkboard"]["title"]),
    ]
    for i, bullet in enumerate(spec["chalkboard"]["bullets"], start=1):
        checks.append((f"칠판 불릿 {i}", bullet))
    usage_steps = spec["chalkboard"].get("usage_steps") or []
    if usage_steps:
        # WHY 기본값도 명시적으로 검사(2026-08-14): usage_label을 안 주면
        # _build_chalkboard_slab이 한국어 "사용법"으로 기본 폴백하는데, lang이
        # kor가 아니면 그 기본값도 그 언어 폰트로 그려진다 — spec이
        # usage_label을 빠뜨린 비한국어 topic이 있으면 여기서 바로 잡힌다.
        checks.append(("사용법 라벨", spec["chalkboard"].get("usage_label", "사용법")))
        for i, step in enumerate(usage_steps, start=1):
            checks.append((f"사용법 {i}단계", step))
    for label, text in checks:
        _assert_title_glyph_coverage(label, text, lang)

    for i, q in enumerate(spec.get("usage_placeholder", {}).get("quotes") or [], start=1):
        # WHY japanese 원문은 lang이 아니라 "ja" 고정으로 검사(_build_quote_card_png
        # 참고): 인용 원문은 타깃 언어와 무관하게 항상 일본어로 그려진다 — 번역
        # (translation)만 lang을 따라간다.
        _assert_title_glyph_coverage(f"인용구 {i} 원문(일본어)", q["japanese"], "ja")
        _assert_title_glyph_coverage(f"인용구 {i} 번역", q["translation"], lang)

    for _start, _end, text in _parse_srt(srt_path):
        _assert_title_glyph_coverage(
            f"나레이션 자막(원문: {text[:24]}{'...' if len(text) > 24 else ''})", text, lang)

    # 칠판 씬 마지막 "Summary" 카드의 CTA 한 줄(_build_summary_frame) — 언어별
    # 고정 딕셔너리라 매 topic 검사할 필요는 없어 보이지만, _CTA_BY_LANG에 없는
    # lang은 한국어 문구로 폴백하면서 폰트만 그 lang을 따라가는 구조라 매핑에
    # 없는 언어를 쓰면 조용히 깨질 수 있다 — 값싼 검사라 항상 확인한다.
    _assert_title_glyph_coverage("Summary 카드 CTA", _CTA_BY_LANG.get(lang, _CTA_BY_LANG["kor"]), lang)

    if ad_tag:
        _assert_title_glyph_coverage("광고 태그", AD_TAG_TEXT_BY_LANG.get(lang, "AD"), lang)


def render_single_product(topic_dir: str, audio_path: str, srt_path: str, spec_path: str,
                           out_path: str, ad_tag: bool = False, lang: str = "kor") -> None:
    """jp_review_spec.json 하나(단일 상품 딥다이브 스펙)를 최종 mp4로 렌더링한다.

    spec 스키마:
      hook_lines: [str, str] — Scene0/Scene1 훅 칩 두 줄
      product_tag: str — 상품 태그 필/뱃지에 쓰는 이름
      product_photo: {"clip":str,"at":float} 또는 {"image":str}
      intro_broll: [{"clip":str,"in":float,"out":float,"subtitle_band"?:dict}, ...]
      item_clip: {"clip":str,"in":float,"out":float,"round_trips"?:int(1~2)}
        — 또는 {"clips":[{"clip":str,"in":float,"out":float}, ...]}(촬영분이
        짧아 정왕복이 부자연스러울 때, 카탈로그에 같은 상품의 다른 구간이
        있으면 이 형태로 실제 여러 컷을 이어붙임, 2026-08-13 도입)
      chalkboard: {"title":str, "bullets":[str,...]}
      usage_clip_subtitle_band?: dict — 실사용 영상에 중국어 자막이 박혀있으면
      usage_placeholder?: {"description":str, "xiaohongshu_query":str}
      hook_cue_count?: int(기본 1) — 훅(Scene1)이 차지하는 narration.srt 문장 수
      item_cue_count?: int(기본 1) — 아이템 리빌(Scene2)이 차지하는 문장 수
        (나머지 전체 문장은 전부 Scene3 칠판 구간)
    """
    topic_dir = Path(topic_dir)
    audio_path = Path(audio_path)
    spec_path = Path(spec_path)
    out_path = Path(out_path)
    topic_name = topic_dir.name

    if not audio_path.exists():
        raise FileNotFoundError(f"narration audio not found: {audio_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    # WHY 어떤 ffmpeg/ffprobe 호출보다도 먼저(2026-08-14): _validate_spec_glyphs
    # WHY 주석 참고 — 렌더링을 시작하기 전에 못 그리는 문자가 있으면 여기서 막는다.
    _validate_spec_glyphs(spec, lang, srt_path, ad_tag)

    durations = compute_scene_durations(spec, srt_path, audio_path)
    total_duration = durations["narration_total"]
    scene1_target = durations["intro"]
    scene2_target = durations["item"]
    scene3_target = durations["chalkboard"]

    print(f"[proto_jp_review] scenes: cover={COVER_DURATION:.2f}s intro={scene1_target:.2f}s "
          f"item={scene2_target:.2f}s chalkboard={scene3_target:.2f}s (narration {total_duration:.2f}s)")

    # 칠판 씬 마지막 구간 "블러 배경 + 중앙 Summary" 전환 트리거 — narration의
    # 마지막 문장(이미 녹음된 오디오)이 시작되는 시점을 칠판 씬 로컬 시각으로
    # 환산해서 씀(새 TTS 없음).
    cues = _parse_srt(srt_path)
    last_cue_start, _last_cue_end, summary_text = cues[-1]
    scene3_global_start = total_duration - scene3_target
    summary_start_local = max(0.0, last_cue_start - scene3_global_start)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    product_photo = _load_product_photo(spec)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        scene0 = tmp_path / "scene0.mp4"
        _build_cover_scene(spec, product_photo, scene0, lang=lang)

        scene1 = tmp_path / "scene1.mp4"
        _build_intro_scene(spec, topic_name, product_photo, scene1_target, tmp_path, scene1, lang=lang)

        scene2 = tmp_path / "scene2.mp4"
        _build_item_scene(spec, scene2_target, tmp_path, scene2)

        scene3 = tmp_path / "scene3.mp4"
        _build_chalkboard_scene(spec, topic_dir, product_photo, scene3_target, tmp_path, scene3,
                                 summary_start_local, summary_text, lang=lang)

        video_only = tmp_path / "video_only.mp4"
        _concat_clips([scene0, scene1, scene2, scene3], video_only)

        captioned = tmp_path / "video_captioned.mp4"
        _overlay_narration_captions(
            video_only, srt_path, COVER_DURATION,
            COVER_DURATION + scene3_global_start, COVER_DURATION + total_duration,
            tmp_path, captioned, lang=lang, summary_global_start=COVER_DURATION + last_cue_start,
        )
        video_only = captioned

        if ad_tag:
            ad_png = tmp_path / "ad_tag.png"
            _build_ad_tag_badge(lang).save(ad_png)
            tagged = tmp_path / "video_tagged.mp4"
            video_only_duration = ffprobe_duration(video_only)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_only), "-loop", "1", "-i", str(ad_png),
                 "-filter_complex", "[0:v][1:v]overlay=x=main_w-overlay_w-20:y=16[v]",
                 "-map", "[v]", "-t", f"{video_only_duration:.3f}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", str(tagged)],
                check=True, capture_output=True,
            )
            video_only = tagged

        channels, rate = _probe_audio_layout(audio_path)
        layout = "mono" if channels == 1 else "stereo"
        silence = tmp_path / "silence.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=channel_layout={layout}:sample_rate={rate}",
             "-t", f"{COVER_DURATION}", "-c:a", "aac", str(silence)],
            check=True, capture_output=True,
        )
        full_audio = tmp_path / "full_audio.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(silence), "-i", str(audio_path),
             "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
             "-c:a", "aac", str(full_audio)],
            check=True, capture_output=True,
        )
        mixed_audio = mix_bgm(str(full_audio), str(tmp_path / "mixed_audio.m4a"),
                               COVER_DURATION + total_duration, topic_name)

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_only), "-i", mixed_audio,
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", "-c:a", "aac", "-b:a", "160k",
             "-shortest", str(out_path)],
            check=True, capture_output=True,
        )

    instagram_out = out_path.with_name(out_path.stem + "_instagram" + out_path.suffix)
    build_instagram_safe_video(str(out_path), str(instagram_out))

    out_duration = ffprobe_duration(out_path)
    print(f"[proto_jp_review] done -> {out_path} (output duration = {out_duration:.3f}s)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("topic_dir")
    parser.add_argument("audio_path")
    parser.add_argument("srt_path")
    parser.add_argument("spec_path")
    parser.add_argument("out_path")
    parser.add_argument("--ad-tag", action="store_true")
    # WHY(2026-08-15): render_single_product()는 내부적으로 lang을 모든 드로잉
    # 함수에 정확히 전달하지만, 이 CLI는 --lang을 노출한 적이 없어서 en/zh-TW
    # spec을 넘겨도 항상 기본값 "kor"로 검증·렌더링되고 있었다 — 라틴 문자는
    # 한국어 폰트에도 있어서 조용히 통과했지만, zh-TW 훅 문구에 '清真' 같은
    # 한국어 상용 한자 밖 문자가 있으면 글리프 검증에서 막힌다(실측: 베릴스
    # 마카다미아 topic). spec 파일 자체가 언어를 명시하므로 --lang을 명시적으로
    # 받게 고쳤다(기본값은 기존 동작과 동일하게 "kor" 유지).
    parser.add_argument("--lang", default="kor")
    args = parser.parse_args()
    render_single_product(args.topic_dir, args.audio_path, args.srt_path, args.spec_path,
                           args.out_path, ad_tag=args.ad_tag, lang=args.lang)
