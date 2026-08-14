# 캐릭터(Kling 모션 루프) + 실사진 배경 + 자막을 합쳐 숏츠 영상으로 조립.
# WHY: 이 ffmpeg 빌드엔 drawtext/subtitles(libass) 필터가 없어서 PIL로 자막 PNG를 그려
# overlay로 합성한다.
# 2026-07-30: 캐릭터 움직임을 Rhubarb 립싱크(입모양 3장 스위칭)에서 Kling AI
# image2video로 전환 — 입모양만 바뀌는 정지 이미지 스위칭은 "위치만 옮겨졌지 그림
# 자체는 그대로"라 밋밋하다는 피드백. 이제 Kling으로 뽑은 5초 자연스러운 움직임
# 영상(고개 갸웃 등) 하나를 대사 길이에 맞춰 반복 재생 — 대사 내용과 입모양이
# 정확히 맞을 필요는 없는 캐릭터 디자인(비인간 사물)이라 이 방식으로 충분하다.
# 캐릭터를 별도 알파 트랙으로 먼저 만드는 이유는 여전히 유효: 배경 합성은
# "인트로/코너" 딱 2구간으로만 나눠서 처리해야 전체-길이 overlay 체인 성능 문제를
# 피할 수 있다(2026-07-29 cat-fight 작업에서 확인).
from __future__ import annotations

import functools
import math
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# WHY sys.path 조작: 이 파일은 `python3 lib/video_assembler.py`로 직접 실행되는
# 경우(CLAUDE.md에 문서화된 표준 호출법)와 `from lib.video_assembler import
# assemble`로 다른 모듈(rebuild_video.py 등)이 import하는 경우 둘 다 지원해야
# 한다 — 직접 실행 시 sys.path[0]이 lib/ 자기 자신이라 `from lib.bgm import ...`
# 절대 import가 실패하므로, 프로젝트 루트를 sys.path에 먼저 넣는다(templates/
# proto_*.py가 이미 쓰는 것과 동일한 패턴).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.bgm import bgm_filter_segment  # noqa: E402

W, H = 1080, 1920
FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
FPS = 30

# WHY 기본 엔딩 CTA(2026-08-02, "카드뉴스랑 숏폼 영상 마지막에 구독, 좋아요, 팔로우
# 요청하는 글도 추가하자"): 매번 topic마다 문구를 새로 넘길 필요 없이 항상 같은
# 표준 문구로 나가게 모듈 상수로 고정 — end_card_text를 명시로 넘기면 그걸 쓰고,
# 안 넘기면(기본) 이 문구를 쓴다. 완전히 끄고 싶으면 end_card_duration=0.
DEFAULT_END_CARD_TEXT = "더 많은 건강정보가 궁금하다면 구독·좋아요·팔로우 해주세요"

# WHY 칠판 스타일 기본 배경 전환(2026-08-02): 실사진을 그대로 배경에 깔면 밋밋하고
# 눈에 확 안 들어온다는 피드백("real 이미지 그대로 배경으로 넣고 있는데... 확 보이지가
# 않는다") — 카드뉴스처럼 칠판 같은 배경에 감각적인 폰트로 나레이션을 써주는 쪽으로
# 새 topic 기본값을 바꾼다. 폰트는 무료 상업적 이용 가능한 구글 폰트 "Gaegu"(SIL OFL
# 1.1, assets_library/fonts/OFL-Gaegu.txt 참고) — 손글씨/마카체 느낌의 한글 지원 폰트.
CHALK_FONT_PATH = str(Path(__file__).resolve().parent.parent / "assets_library" / "fonts" / "Gaegu-Bold.ttf")
CHALKBOARD_TOP = (32, 66, 48)
CHALKBOARD_BOTTOM = (20, 42, 31)

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets_library" / "fonts"
# WHY 언어별 CHALK_FONT 매핑(2026-08-03 버그 수정): "글로벌 확장 준비" 절에서
# 언어별 폰트(NotoSans 계열)를 스크립트 글리프 커버리지까지 확인해서 이미
# 소싱해뒀는데("실제 렌더링 함수의 FONT_PATH/CHALK_FONT_PATH에 언어별 폰트를
# 선택하는 로직은 아직 연결 안 됨" — CLAUDE.md에 그대로 남아있던 gap), 실제
# 렌더링 코드는 여전히 CHALK_FONT_PATH(한국어 Gaegu 폰트) 하나만 모든 언어에
# 썼다 — 영어는 Gaegu에 라틴 글리프가 우연히 있어서 티가 안 났지만, 아랍어·
# 벵골어·힌디어·태국어·일본어·대만어는 Gaegu에 해당 스크립트 글리프가 아예
# 없어서 네모 박스(tofu)로 깨졌을 것. 언어 코드 → 폰트 파일 매핑 하나로
# 해결한다. 러시아어(키릴)는 NotoSans-Bold.ttf가 이미 커버(위 "폰트" 절 참고).
_ZH_SC_FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"  # WHY macOS 시스템
# 폰트(2026-08-13, 다국어 확장 착수): 간체 중국어(zh)용 NotoSansSC를 새로
# 받을 방법이 없어서(바이너리 폰트 파일 직접 다운로드 불가) FONT_PATH(한국어)
# 도 이미 같은 방식으로 macOS 시스템 폰트를 쓰고 있는 기존 관행을 따랐다 —
# 다른 OS(리눅스 CI 등)에서 렌더링해야 하면 이 경로가 없어서 깨진다, 이식성
# 필요해지면 assets_library/fonts/NotoSansSC-Bold.ttf로 교체할 것.

_CHALK_FONT_BY_LANG: dict[str, str] = {
    "kor": CHALK_FONT_PATH,
    "ar": str(_FONTS_DIR / "NotoSansArabic-Bold.ttf"),
    "bn": str(_FONTS_DIR / "NotoSansBengali-Bold.ttf"),
    "hi": str(_FONTS_DIR / "NotoSansDevanagari-Bold.ttf"),
    "th": str(_FONTS_DIR / "NotoSansThai-Bold.ttf"),
    "ja": str(_FONTS_DIR / "NotoSansJP-Bold.ttf"),
    "zh-TW": str(_FONTS_DIR / "NotoSansTC-Bold.ttf"),
    "zh": _ZH_SC_FONT_PATH,
}
_CHALK_FONT_LATIN_CYRILLIC = str(_FONTS_DIR / "NotoSans-Bold.ttf")


def _chalk_font_for_lang(lang: str) -> str:
    """lang(예: "en", "ar", "kor")에 맞는 손글씨체 폰트 경로를 고른다 — 스크립트별
    전용 폰트가 있으면 그걸 쓰고, 없으면(영어·스페인어·포르투갈어·프랑스어·독일어·
    러시아어·베트남어·터키어·인도네시아어처럼 라틴/키릴 글리프면 충분한 언어) 공용
    NotoSans-Bold.ttf로 폴백한다. 매핑에 없는 새 언어 코드가 들어와도 안전하게
    라틴 폰트로 폴백 — 아예 죽는 것보다 latin-only 글꼴로라도 뜨는 게 낫다."""
    return _CHALK_FONT_BY_LANG.get(lang, _CHALK_FONT_LATIN_CYRILLIC)


# WHY FONT_PATH도 같은 문제(2026-08-03 버그 수정): 제목 카드(썸네일로도 쓰임)·
# 상단 후킹 배너·구식 캡션 스타일이 전부 FONT_PATH(한국어 시스템 폰트
# AppleSDGothicNeo.ttc)를 언어 무관하게 썼다. FONT_PATH는 .ttc(폰트 컬렉션)라
# `index=6`으로 특정 웨이트를 골라 썼는데, 대체 폰트(NotoSans 등)는 단일 .ttf라
# index가 항상 0이어야 한다 — (경로, index) 쌍으로 반환해서 이 차이를 호출부가
# 신경 안 쓰게 한다.
_TITLE_FONT_BY_LANG: dict[str, tuple[str, int]] = {
    "kor": (FONT_PATH, 6),
    "ar": (str(_FONTS_DIR / "NotoSansArabic-Bold.ttf"), 0),
    "bn": (str(_FONTS_DIR / "NotoSansBengali-Bold.ttf"), 0),
    "hi": (str(_FONTS_DIR / "NotoSansDevanagari-Bold.ttf"), 0),
    "th": (str(_FONTS_DIR / "NotoSansThai-Bold.ttf"), 0),
    "ja": (str(_FONTS_DIR / "NotoSansJP-Bold.ttf"), 0),
    "zh-TW": (str(_FONTS_DIR / "NotoSansTC-Bold.ttf"), 0),
    "zh": (_ZH_SC_FONT_PATH, 0),
}
_TITLE_FONT_LATIN_CYRILLIC = (str(_FONTS_DIR / "NotoSans-Bold.ttf"), 0)


def _title_font_for_lang(lang: str) -> tuple[str, int]:
    """제목 카드·상단 배너·구식 캡션용 굵은 산세리프 폰트를 (경로, ttc index)
    쌍으로 고른다 — `_chalk_font_for_lang`과 같은 매핑 원칙, FONT_PATH 전용으로
    ttc index 처리만 다르다."""
    return _TITLE_FONT_BY_LANG.get(lang, _TITLE_FONT_LATIN_CYRILLIC)


# WHY 폰트 글리프 커버리지 자동 검사(2026-08-14, health-shorts card_news.py의
# 동일 사고 조사에서 실측 검증된 방법을 이식): 폰트에 없는 문자를 PIL로 그리면
# 에러 없이 조용히 tofu box(빈 네모)로 렌더링된다 — 그동안은 사람이 완성된
# 영상을 재생해서 육안으로 확인해야만 발견됐다. fontTools로 폰트가 실제
# 지원하는 코드포인트(cmap)를 직접 읽어서, 렌더링을 시작하기 전에 못 그리는
# 문자가 있으면 ValueError로 막는다. 이 프로젝트는 폰트 체계가 두 갈래
# (chalk/title)라 health-shorts와 달리 "현재 언어"를 가리키는 전역 상태 대신
# 호출부가 검사할 폰트를 매번 명시한다.
@functools.lru_cache(maxsize=None)
def _font_cmap(font_path: str, font_number: int = 0) -> frozenset[int]:
    """그 폰트 파일(.ttc 컬렉션이면 font_number번째 face)이 실제로 그릴 수 있는
    유니코드 코드포인트 집합. WHY fontTools인지: Pillow의 ImageFont.getmask()는
    지원 안 하는 글리프도 .notdef(빈 네모, 사각형 bbox가 실제로 있음)를 조용히
    그려서 bbox 존재 여부로는 "글리프가 있다"와 "없어서 tofu box가 나온다"를
    구분할 수 없다 — 폰트의 cmap 테이블을 직접 읽는 fontTools만 정확하게 판별
    가능하다. WHY font_number 파라미터(health-shorts의 kor 처리와 같은 원리):
    `_title_font_for_lang`이 (경로, ttc index) 쌍을 반환해서 같은 .ttc 파일
    안에서도 실제로 렌더링에 쓰는 face가 언어마다 다르다(kor는
    AppleSDGothicNeo.ttc index=6) — 렌더링에 쓰는 face와 다른 face의 cmap을
    읽으면 오탐/누락이 생길 수 있어 항상 실제 index를 그대로 넘긴다.
    (font_path, font_number) 조합별로 캐싱해서 폰트당 한 번만 파싱한다."""
    tt = TTFont(font_path, fontNumber=font_number, lazy=True)
    codepoints: set[int] = set()
    for table in tt["cmap"].tables:
        codepoints |= set(table.cmap.keys())
    return frozenset(codepoints)


def _missing_glyphs(text: str, font_path: str, font_number: int = 0) -> str:
    """text 안에서 그 폰트가 못 그리는 문자만 중복 없이 뽑아 반환(빈 문자열이면
    전부 지원). 공백은 애초에 안 그려지므로 검사 대상에서 뺀다."""
    cmap = _font_cmap(font_path, font_number)
    return "".join(sorted({ch for ch in text if not ch.isspace() and ord(ch) not in cmap}))


def _assert_glyph_coverage(label: str, text: str, font_path: str, font_number: int = 0,
                            lang: str = "kor") -> None:
    """text에 그 폰트가 못 그리는 문자가 있으면 즉시 에러로 막는다 —
    health-shorts card_news.py의 `_assert_glyph_coverage`와 같은 목적(렌더링
    자체는 에러 없이 "성공"하기 때문에, 그동안은 사람이 영상을 끝까지 재생해봐야만
    발견됨)을 이 프로젝트의 두 폰트 체계(chalk/title)에 맞게 이식."""
    missing = _missing_glyphs(text, font_path, font_number)
    if missing:
        raise ValueError(
            f"[lang={lang}] {label}에 폰트({font_path}, index={font_number})가 그리지 못하는 "
            f"문자 발견: {missing!r} (원문: {text!r}) — 언어별 폰트 매핑을 확인하거나 "
            f"해당 언어에 이 문자가 실제로 필요한지 다시 검토할 것."
        )


def _assert_chalk_glyph_coverage(label: str, text: str, lang: str = "kor") -> None:
    """`_chalk_font_for_lang`(칠판 자막·낙서·명패 등 손글씨체)로 그려질 텍스트를
    검사한다."""
    _assert_glyph_coverage(label, text, _chalk_font_for_lang(lang), 0, lang)


def _assert_title_glyph_coverage(label: str, text: str, lang: str = "kor") -> None:
    """`_title_font_for_lang`(제목 카드·상단 배너 등 굵은 산세리프)로 그려질
    텍스트를 검사한다 — ttc index까지 실제 렌더링과 동일하게 맞춰서 확인한다."""
    font_path, font_index = _title_font_for_lang(lang)
    _assert_glyph_coverage(label, text, font_path, font_index, lang)


# WHY 언어별 줄바꿈 단위(2026-08-03 버그 수정, "가슴쓰림_1/ja 제목 카드 글자가
# 화면 양옆으로 잘려나감" 실제 발견): 이 파일의 모든 줄바꿈 로직(_make_title_png/
# _make_title_card_png/_make_caption_png/_make_chalk_caption_png)이 전부
# `text.split()`으로 "단어" 단위를 나눈 뒤 폭이 넘치면 다음 줄로 넘겼다 — 한국어·
# 영어처럼 공백으로 단어가 구분되는 언어는 문제없지만, 일본어·중국어(간체·번체)·
# 태국어는 애초에 띄어쓰기를 안 쓰는 언어(scriptio continua)라 문장 전체가 공백
# 하나 없는 "단어 하나"로 인식돼서 줄바꿈 자체가 아예 안 되고 캔버스 폭을 넘겨
# 그대로 잘려나갔다. 이 언어들만 글자 단위로 폭을 재서 넘치기 직전에 줄을 끊는다.
_NO_SPACE_WRAP_LANGS = {"ja", "zh-TW", "zh", "th"}


def _wrap_text_for_lang(draw, text: str, font, max_width: int, lang: str) -> list[str]:
    if lang in _NO_SPACE_WRAP_LANGS:
        lines, cur = [], ""
        for ch in text:
            test = cur + ch
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
        return lines

    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _parse_srt(srt_path: str) -> list[tuple[float, float, str]]:
    text = Path(srt_path).read_text()
    time_re = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)")
    entries = []
    for block in text.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        m = time_re.match(lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        entries.append((start, end, " ".join(lines[2:])))
    return entries


# WHY 자막 최대 줄 수 강제(2026-08-03, "이런 현상이 어디서도 많이 발생할거같은데
# 언어가 글로벌로 늘어나니까... 칠판을 가득 채워버려서 보기 어렵다... 4줄정도를
# 최대로 해야" — 가슴쓰림_1/ja 스크린샷에서 문장 4개가 한 자막에 뭉쳐 칠판을
# 거의 다 채운 걸 실제로 보고 지적): 근본 원인은 Typecast API가 일부 언어(특히
# 일본어)에서 단어 단위 타임스탬프를 문장 경계 없이 뭉텅이로 반환하는 것이지만,
# 원인이 API든 그냥 원래 긴 문장이든 화면에 자막이 너무 많이 쌓이는 결과는
# 언어·topic과 무관하게 똑같이 나쁘다 — 원인을 하나씩 쫓기보다 렌더링 단계에서
# "화면에 뜨는 자막은 항상 MAX_CAPTION_LINES줄 이하"를 강제하는 안전장치를 둔다.
MAX_CAPTION_LINES = 4
_CAPTION_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？।؟])\s*")


def _count_wrapped_lines(text: str, lang: str, font_size: int = 68, max_width: int = 940) -> int:
    font = ImageFont.truetype(_chalk_font_for_lang(lang), font_size)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    return len(_wrap_text_for_lang(d, text, font, max_width, lang))


def _split_long_caption_entries(
    entries: list[tuple[float, float, str]], lang: str,
) -> list[tuple[float, float, str]]:
    """SRT 한 구간이 MAX_CAPTION_LINES줄을 넘치게 길면 문장 단위(마침표류 기준)로
    쪼개서 여러 구간으로 나눈다 — 시간은 원래 구간 안에서 글자 수 비례로 배분한다
    (정확한 원본 타이밍 기록이 없어 택한 근사치, rebuild_video.py의 문단 내 위치
    비례 배분과 같은 방식). 문장이 하나뿐인데도 넘치면(원래부터 긴 단문) 더
    쪼갤 안전한 기준이 없어 그대로 둔다 — 쉼표 단위 분할까지는 스코프 밖."""
    result = []
    for start, end, text in entries:
        if _count_wrapped_lines(text, lang) <= MAX_CAPTION_LINES:
            result.append((start, end, text))
            continue
        sentences = [s.strip() for s in _CAPTION_SENTENCE_SPLIT_RE.split(text) if s.strip()]
        if len(sentences) <= 1:
            result.append((start, end, text))
            continue
        total_len = sum(len(s) for s in sentences)
        cursor = start
        for i, s in enumerate(sentences):
            seg_end = end if i == len(sentences) - 1 else cursor + (end - start) * (len(s) / total_len)
            result.append((cursor, seg_end, s))
            cursor = seg_end
    return result


# WHY 언어별 고정 사전인지(2026-08-06, "영상은 언어별로 이미 따로 렌더링되니
# 텍스트만 그 언어 단어로 바꾸면 되는거 아님?" 확인): topic 하나가 6개 언어를
# 갖추면 애초에 오디오·자막이 달라 언어별로 별도 mp4를 렌더링하는 구조라(이
# 사전과 무관하게 원래 그럼), 태그 문구를 언어별로 다르게 넣는 데 드는 추가
# 비용은 그 렌더링 호출에 문자열 하나 바꿔 넘기는 정도뿐이다 — 영어 하나로
# 퉁치지 않고 로컬 단어를 쓴다.
# WHY pt가 "Anúncio"에서 "Publicidade"로 바뀌었는지(2026-08-08): 실제
# 리서치 결과(브라질 CONAR 자율규제 기준 문구, archive "영상 광고 표시 의무"
# 절 참고)는 `publicidade`/`#publi`인데 "Anúncio"(공고/발표라는 뜻, 다른
# 단어)로 잘못 들어가 있었다 — 배지 표시용이라 다른 언어 항목과 통일해서
# "#" 없이 대문자로 시작하는 형태로 정정.
AD_TAG_TEXT_BY_LANG = {
    "kor": "광고", "en": "AD", "ja": "広告",
    "es": "Publicidad", "pt": "Publicidade", "ru": "Реклама",
    "zh-TW": "廣告", "zh": "广告", "th": "โฆษณา", "id": "Iklan",
}


def _build_ad_tag_badge(lang: str = "kor", font_size=28, padding=12) -> Image.Image:
    """공정위 표시광고 지침 대응 — 실제 쿠팡/네이버 제휴 링크를 쓰기로 확정한
    영상에만 켠다(ffmpeg 합성 경로는 assemble(..., ad_tag=True), PIL 프레임
    렌더러(checklist/before_after_transition)는 draw_ad_tag_overlay() 참고).
    "처음부터 끝까지 노출" 요건 때문에 약하게(반투명)라도 전체 구간에 계속
    떠 있어야 하고, 후반부에만 넣는 건 안 됨(shopping-shorts-video에서
    확인된 규칙과 동일).

    WHY _title_font_for_lang인지: 일본어 "広告"는 AppleSDGothicNeo(한국어
    시스템 폰트)에 글리프가 없어 깨져 보인다 — 이미 있는 언어별 폰트 매핑을
    그대로 재사용."""
    font_path, font_index = _title_font_for_lang(lang)
    font = ImageFont.truetype(font_path, font_size, index=font_index)
    text = AD_TAG_TEXT_BY_LANG.get(lang, "AD")
    dummy = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = tw + padding * 2, th + padding * 2
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 100))
    draw = ImageDraw.Draw(img)
    draw.text((padding - bbox[0], padding - bbox[1]), text, font=font, fill=(255, 255, 255, 210))
    return img


def _make_ad_tag_png(out_path: Path, lang: str = "kor", font_size=28, padding=12):
    _build_ad_tag_badge(lang, font_size, padding).save(out_path)


def draw_ad_tag_overlay(img: Image.Image, lang: str = "kor", top_margin: int = 16) -> Image.Image:
    """PIL 프레임 단위로 직접 그리는 신규 템플릿(checklist/before_after_transition)
    전용 — ffmpeg overlay 필터 대신 프레임/화면 이미지 하나하나에 배지를 합성한다.
    호출부가 프레임마다(또는 화면 PNG 저장 직전마다) 불러서 "처음부터 끝까지
    노출" 요건을 자연스럽게 만족시킨다. 우상단 위치는 판서형 assemble()의
    `x=main_w-overlay_w-20, y=title_h+16`과 시각적으로 맞춘다."""
    badge = _build_ad_tag_badge(lang)
    x = img.width - badge.width - 20
    img.paste(badge, (x, top_margin), badge)
    return img


def _cover_crop_subject(photo_path: str, out_w: int, out_h: int) -> Image.Image:
    """real 사진을 (out_w x out_h) 프레임에 꽉 차게 cover 크롭 — 정사각형 강제
    리사이즈로 피사체가 밀려나는 문제, 그리고 흰 스튜디오 배경 사진에서 피사체가
    일부만 차지해서 크롭 후에도 흰 여백만 남는 문제, 둘 다 대응한다(2026-08-02,
    "정사각형으로 리사이즈해서 크롭하면 안 됨" / "어떤 물건인지 알 수 있는게
    훨씬 나을거같네"). 원본 비율을 유지한 채 목표 프레임을 완전히 덮을 때까지
    확대하고 중앙을 잘라낸다.

    WHY 픽셀 단위 threshold 대신 행/열 밀도 프로파일(2026-08-02, "real 이미지의
    opacity를 없애라고 아직도 흐려" — 실제로는 블러가 아니라 크롭이 너무 헐거워서
    피사체가 작고 멀게 나온 문제): 흰 스튜디오컷은 배경에 미세한 그림자/비네팅이
    깔려서 "흰색이 아닌 픽셀 하나라도 있으면 subject"식 bounding box는 그림자
    번짐까지 다 잡아버려 사실상 전체 사진 크기로 뻥튀기된다(실측: 단순 bbox가
    전체 프레임의 90%+ 를 차지) — 그러면 확대 배율이 거의 없어서 피사체가 작고
    멀리 보인다. 대신 각 행/열에서 "피사체 픽셀 비율"이 일정 밀도 이상인 범위만
    골라내면, 그림자 번짐 같은 옅은 잡음은 걸러지고 실제로 피사체가 뭉쳐있는
    영역만 남는다 — 촘촘한(0.30) 기준부터 시작해서 전체 면적의 8% 이상을
    커버하는 첫 기준을 채택, 사진마다 피사체 밀도가 달라도 적당히 타이트한
    크롭을 자동으로 찾는다."""
    photo = Image.open(photo_path).convert("RGB")
    gray = photo.convert("L")
    subject_mask = gray.point(lambda x: 255 if x < 235 else 0)
    row_profile = subject_mask.resize((1, photo.height), Image.BOX)
    col_profile = subject_mask.resize((photo.width, 1), Image.BOX)
    rows = list(row_profile.getdata())
    cols = list(col_profile.getdata())
    total_area = photo.width * photo.height
    bbox = None
    for density in (0.30, 0.25, 0.20, 0.15, 0.10, 0.05, 0.02):
        row_thresh = 255 * density
        idx_rows = [i for i, v in enumerate(rows) if v > row_thresh]
        idx_cols = [i for i, v in enumerate(cols) if v > row_thresh]
        if not idx_rows or not idx_cols:
            continue
        candidate = (min(idx_cols), min(idx_rows), max(idx_cols), max(idx_rows))
        area = (candidate[2] - candidate[0]) * (candidate[3] - candidate[1])
        if area >= total_area * 0.08:
            bbox = candidate
            break

    if bbox:
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = round(bw * 0.15), round(bh * 0.15)
        crop_box = (
            max(bbox[0] - pad_x, 0), max(bbox[1] - pad_y, 0),
            min(bbox[2] + pad_x, photo.width), min(bbox[3] + pad_y, photo.height),
        )
        photo = photo.crop(crop_box)

    scale = max(out_w / photo.width, out_h / photo.height)
    resized = photo.resize((round(photo.width * scale), round(photo.height * scale)))
    left = (resized.width - out_w) // 2
    top = (resized.height - out_h) // 2
    return resized.crop((left, top, left + out_w, top + out_h))


def _make_title_png(text: str, out_path: Path, font_size=64, photo_path: str | None = None,
                     photo_img: Image.Image | None = None, lang: str = "kor") -> int:
    """영상 상단을 가로로 꽉 채우는 후킹 배너. WHY: 작은 알약 모양 라벨은 존재감이
    약해서 스크롤 중 3초컷으로 넘어가는 문제를 못 막는다(2026-07-30 피드백) —
    화면 가로 전체를 덮는 굵은 배너로 바꾸고, 텍스트도 카테고리 라벨이 아니라
    후킹 문구(공감/호기심 유발)를 넣는다. 반환값(배너 높이)은 다른 오버레이가
    이 배너와 겹치지 않게 배치할 때 쓴다.

    WHY photo_path(2026-08-02, "분홍색 바탕 없애도 되고 바탕으로는 그 항목에 대한
    real 이미지를 흐린 색으로"): 단색 배경 대신 topic 대표 실사진을 배너 폭에 맞게
    확대·크롭해서 깐 뒤 반투명 스크림을 얹는다. photo_path가 없으면 기존 단색 배경으로
    폴백한다.

    WHY photo_img(2026-08-02, "배경으로 넣는 real 사진을 글자 뒤에있는거랑 칠판
    이미지 아래위랑 따로따로 넣어놨나보네?? 한 사진으로 해서... 지금은 뭔가 따로따로
    짤려보이잖아"): 배너와 칠판 배경(_build_chalkboard_bg)이 각자 photo_path로
    독립적으로 cover-crop을 하면 서로 다른 배율/영역으로 잘려서 이어지는 사진처럼
    안 보이는 문제가 있었다 — assemble()이 캔버스 전체(W x H) 크기로 미리 한 번만
    만든 배경 이미지를 photo_img로 넘기면, 배너는 그 이미지의 위쪽 box_h만큼만
    잘라 쓴다(같은 사진·같은 배율의 연속된 한 조각). photo_img가 있으면 photo_path는
    무시한다."""
    _fpath, _findex = _title_font_for_lang(lang)
    font = ImageFont.truetype(_fpath, font_size, index=_findex)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    max_text_w = W - 140
    lines = _wrap_text_for_lang(d, text, font, max_text_w, lang)

    line_h = font_size + 16
    # WHY 위쪽 여백만 한 줄 높이(2026-08-02, "맨 위에있는 글자 한 줄만큼은 여백이
    # 생겨야 해"): 대칭 패딩(pad_y*2)이었을 땐 텍스트가 배너 상단에 너무 붙어 보였다
    # — 위쪽만 line_h만큼 비우고 아래쪽은 기존 여백을 유지한다. 배경 사진(box_h 전체)은
    # 그대로 화면 맨 위(y=0)부터 꽉 채우므로 이 여백은 사진 안쪽의 빈 공간일 뿐,
    # 사진 자체가 아래로 밀리는 게 아니다.
    pad_top = line_h
    pad_bottom = 30
    box_h = pad_top + line_h * len(lines) + pad_bottom

    has_photo = photo_img is not None or photo_path
    if has_photo:
        photo = photo_img.crop((0, 0, W, box_h)) if photo_img is not None else _cover_crop_subject(photo_path, W, box_h)
        # WHY 스크림 유지(2026-08-02): "opacity 없애라"는 지적은 칠판 뒤 전체화면
        # 배경(_build_chalkboard_bg)을 가리킨 것이었는데, 그때 배너의 텍스트
        # 가독성용 검은 저알파 스크림까지 같이 빼버렸다가 "위쪽 글자 배경 색상을
        # 왜 지웠냐, 그건 필요하다"는 지적을 받고 되돌렸다 — 배너는 스크림 유지,
        # 칠판 뒤 전체화면 배경만 스크림/블러 없이 원본 그대로 쓴다.
        scrim = Image.new("RGBA", (W, box_h), (0, 0, 0, 90))
        img = Image.alpha_composite(photo.convert("RGBA"), scrim)
    else:
        img = Image.new("RGBA", (W, box_h), (200, 74, 98, 240))
    draw = ImageDraw.Draw(img)
    y = pad_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) / 2 - bbox[0]
        if has_photo:
            draw.text((x, y - bbox[1]), line, font=font, fill=(255, 255, 255, 255),
                       stroke_width=3, stroke_fill=(0, 0, 0, 255))
        else:
            draw.text((x, y - bbox[1]), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(out_path)
    return box_h


# WHY 썸네일 배경색 팔레트(2026-08-03, "썸네일만 봐도 그냥 다 똑같아보이는데" —
# 유튜브 "자동화된 대량생산" 스팸 정책 리스크 완화 목적): 이 카드가 영상 첫
# 프레임이라 플랫폼 자동 썸네일로 그대로 쓰이는데, 배경색이 (200,74,98) 핑크
# 하나로 하드코딩돼 있어서 채널 전체 썸네일이 색상만으로도 전부 똑같아 보였다.
# topic마다 다른 색을 결정적으로(같은 topic은 재생성해도 항상 같은 색) 배정해서
# 시각적으로 구분되게 한다 — 브랜드 톤에서 크게 벗어나지 않는 채도·톤으로 맞춘
# 8색 팔레트.
_TITLE_CARD_ACCENT_PALETTE: list[tuple[int, int, int]] = [
    (200, 74, 98),   # 기존 핑크(하위호환 기본값 포함)
    (74, 110, 200),  # 블루
    (200, 140, 40),  # 오렌지
    (90, 150, 110),  # 그린
    (150, 90, 190),  # 퍼플
    (200, 90, 60),   # 테라코타
    (60, 150, 170),  # 틸
    (170, 110, 150), # 로즈
]


def _accent_color_for_seed(seed: str) -> tuple[int, int, int]:
    """seed 문자열(주로 title)로 팔레트에서 결정적으로 하나를 고른다. WHY 내장
    hash() 대신 문자 코드 합을 쓰는지: 파이썬 문자열 hash()는 프로세스마다
    랜덤 시드가 달라(hash randomization) 같은 topic이 재생성 때마다 다른 색을
    받게 된다 — 항상 같은 결과가 나와야(재생성해도 색이 안 바뀌어야) 하므로
    안정적인 합산 방식을 쓴다."""
    idx = sum(ord(c) for c in seed) % len(_TITLE_CARD_ACCENT_PALETTE)
    return _TITLE_CARD_ACCENT_PALETTE[idx]


# WHY 텍스트 세로 위치도 변주(2026-08-03, 색만으론 부족 — "썸네일별 차별화" 연장):
# 항상 정중앙 배치라 색이 달라도 레이아웃 뼈대가 똑같아 보인다는 지적을 예상해
# 미리 반영 — 3개 지점(위/중앙/아래) 중 하나를 seed로 결정적으로 고른다. 색상용
# seed 합산과 그대로 겹치면 같은 seed로 뽑은 값끼리 항상 같은 조합만 나올 수
# 있어서, 자리수를 하나 더 섞어(문자별 위치 가중) 색과 실질적으로 독립적이게 한다.
_TITLE_CARD_Y_BIAS = [0.30, 0.5, 0.70]


def _text_y_bias_for_seed(seed: str) -> float:
    idx = sum(ord(c) * (i + 1) for i, c in enumerate(seed)) % len(_TITLE_CARD_Y_BIAS)
    return _TITLE_CARD_Y_BIAS[idx]


# WHY 색·세로 위치에 이어 "형태" 자체도 변주(2026-08-04, "썸네일 형태 같은것들도
# 전반적으로 챙겨봐"): 색과 위치가 달라도 매번 "배경색 + 중앙 텍스트"라는 뼈대는
# 동일해서 여전히 비슷해 보인다는 앞선 WHY의 우려가 실제로 남아있었다. banner(글자
# 뒤 반투명 띠)·boxed(글자 둘레 테두리 박스)를 추가해 뼈대 자체를 topic마다 다르게
# 한다. seed 가중치를 색(i+1)·위치(i+2)와 또 다르게(i+3) 섞어 세 값이 서로
# 독립적으로 조합되게 한다.
_TITLE_CARD_STYLES = ["plain", "banner", "boxed", "underline"]


def _title_card_style_for_seed(seed: str) -> str:
    idx = sum(ord(c) * (i + 3) for i, c in enumerate(seed)) % len(_TITLE_CARD_STYLES)
    return _TITLE_CARD_STYLES[idx]


def _make_title_card_png(text: str, out_path: Path, font_size=88, char_path: str | None = None,
                          lang: str = "kor", accent_color: tuple[int, int, int] = (200, 74, 98),
                          y_bias: float = 0.5, style: str = "plain"):
    """영상 맨 앞에 붙는 단색 배경 + 큰 제목 카드. WHY: 플랫폼이 썸네일을 영상
    첫 프레임으로 자동 지정하는 경우가 많아서, 이 카드 자체를 그대로 썸네일로
    쓸 수 있게 글자를 크고 굵게, 배경은 단색으로 단순하게 만든다.

    WHY char_path(2026-07-31, "캐릭터를 큼직하고 흐리게 글자의 배경으로"): 순수
    단색 배경 대신, 캐릭터 이미지를 캔버스보다 훨씬 크게 확대·크롭해서 흐리게 깐
    뒤 ACCENT 톤 스크림을 얹는다 — 브랜드 컬러는 유지하면서 캐릭터가 은은하게
    느껴지는 배경 무드를 만든다.

    WHY accent_color/y_bias 파라미터(2026-08-03, "썸네일만 봐도 다 똑같아보인다"):
    기본값은 기존(핑크·정중앙) 그대로라 이 함수를 직접 부르는 다른 호출부(테스트
    등)는 영향 없음 — assemble()만 title 기준 시드로 계산해서 넘긴다."""
    img = Image.new("RGB", (W, H), accent_color)
    if char_path:
        target = int(H * 1.15)
        char = Image.open(char_path).convert("RGB").resize((target, target))
        char = char.filter(ImageFilter.GaussianBlur(25))
        left, top = (target - W) // 2, (target - H) // 2
        char = char.crop((left, top, left + W, top + H))
        scrim = Image.new("RGBA", (W, H), (*accent_color, 150))
        img = Image.alpha_composite(char.convert("RGBA"), scrim).convert("RGB")
    _fpath, _findex = _title_font_for_lang(lang)
    font = ImageFont.truetype(_fpath, font_size, index=_findex)
    draw = ImageDraw.Draw(img)
    max_text_w = W - 160
    lines = _wrap_text_for_lang(draw, text, font, max_text_w, lang)

    line_h = font_size + 26
    total_h = line_h * len(lines)
    # WHY 클램프(2026-08-03): y_bias가 0.3/0.7처럼 위·아래로 치우쳐도 문단이
    # 길면(여러 줄) 캔버스 밖으로 잘릴 수 있어서, 위아래 60px 여백은 항상
    # 남기도록 범위를 제한한다.
    margin = 60
    y = max(margin, min(y_bias * H - total_h / 2, H - total_h - margin))

    max_tw = max(draw.textbbox((0, 0), line, font=font)[2] - draw.textbbox((0, 0), line, font=font)[0]
                 for line in lines)
    if style == "banner":
        pad_y = 24
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(
            [0, y - pad_y, W, y + total_h + pad_y], fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
    elif style == "boxed":
        pad_x, pad_y = 50, 34
        box = [(W - max_tw) / 2 - pad_x, y - pad_y, (W + max_tw) / 2 + pad_x, y + total_h + pad_y]
        draw.rounded_rectangle(box, radius=20, outline=(255, 255, 255), width=6)
    elif style == "underline":
        bar_w, bar_h = max_tw + 50, 14
        bar_y = y + total_h + 18
        draw.rounded_rectangle(
            [(W - bar_w) / 2, bar_y, (W + bar_w) / 2, bar_y + bar_h],
            radius=bar_h / 2, fill=(255, 255, 255))

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) / 2 - bbox[0], y - bbox[1]), line, font=font, fill=(255, 255, 255))
        y += line_h
    img.save(out_path)


def _make_caption_png(text: str, out_path: Path, font_size=60, max_width=940, lang: str = "kor"):
    _fpath, _findex = _title_font_for_lang(lang)
    font = ImageFont.truetype(_fpath, font_size, index=_findex)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    lines = _wrap_text_for_lang(d, text, font, max_width, lang)

    line_heights, max_w = [], 0
    for line in lines:
        bbox = d.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
        max_w = max(max_w, bbox[2] - bbox[0])

    pad_x, pad_y, gap = 30, 18, 8
    box_w, box_h = max_w + pad_x * 2, sum(line_heights) + gap * (len(lines) - 1) + pad_y * 2
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, box_w, box_h], radius=16, fill=(0, 0, 0, 165))
    y = pad_y
    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (box_w - w) / 2 - bbox[0]
        draw.text((x, y - bbox[1]), line, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += lh + gap
    img.save(out_path)


def _make_chalk_caption_png(text: str, out_path: Path, font_size=68, max_width=940, lang: str = "kor"):
    """칠판 배경용 자막(2026-08-02). _make_caption_png와 달리 반투명 박스가 없다 —
    배경 자체가 이미 짙은 칠판색이라 박스를 얹으면 이중으로 어두워지고 사진 위에
    붙인 스티커 같은 느낌만 준다. 대신 Gaegu(분필/마카 느낌 폰트)로 흰 글자를
    직접 쓰고, 옅은 그림자만 살짝 깔아서 배경 톤이 조금 밝은 부분에서도 읽히게 한다.

    WHY lang(2026-08-03 버그 수정): 이 함수가 실제 나레이션 자막(영상 전체에서
    가장 자주, 가장 크게 뜨는 텍스트)을 그리는데 폰트가 CHALK_FONT_PATH(한국어
    Gaegu) 고정이었다 — 비라틴 스크립트 언어는 자막 전체가 깨져(tofu) 보였을
    것. `_chalk_font_for_lang`로 언어별 폰트를 고른다."""
    font = ImageFont.truetype(_chalk_font_for_lang(lang), font_size)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    lines = _wrap_text_for_lang(d, text, font, max_width, lang)

    line_heights, max_w = [], 0
    for line in lines:
        bbox = d.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
        max_w = max(max_w, bbox[2] - bbox[0])

    pad, gap = 20, 14
    img_w, img_h = max_w + pad * 2, sum(line_heights) + gap * (len(lines) - 1) + pad * 2
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad
    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (img_w - w) / 2 - bbox[0]
        draw.text((x + 3, y - bbox[1] + 3), line, font=font, fill=(0, 0, 0, 110))  # 옅은 그림자
        draw.text((x, y - bbox[1]), line, font=font, fill=(255, 255, 255, 255))
        y += lh + gap
    img.save(out_path)


# WHY 칠판 우상단 아이템 라벨(2026-08-02, "칠판 우상단에 멈춰있는 일러스트와 그
# 아래에 그 아이템의 이름을 함께 넣어줘야해" — 코너의 움직이는 캐릭터만으로는
# "이게 뭔지 사람들이 인지 잘 못할듯"하다는 지적): 카드뉴스의 원형 배지(카드뉴스
# `_char_medallion`)와 톤을 맞춘 정지 아이콘(흰 링 + 그림자) + 그 아래 분필체
# 이름 라벨을 만든다 — 카드뉴스는 자체 함수가 따로 있어(캔버스 크기·색상 상수가
# 다름) 그대로 import하지 않고 이 모듈 안에서 동일한 스타일을 재구현했다.
def _make_item_label_png(illust_path: str | None, name: str, out_path: Path,
                          icon_size: int = 108, font_size: int = 40, lang: str = "kor") -> None:
    ring_w = 6
    pad = ring_w + 10
    icon_canvas = icon_size + pad * 2

    if illust_path and Path(illust_path).exists():
        raw = Image.open(illust_path).convert("RGB").resize((icon_size, icon_size))
        raw = raw.convert("RGBA")
        # WHY 자동 키 색 감지(2026-08-01 card_news.py 동일 이유): 캐릭터 배경
        # 크로마키가 초록/파랑/마젠타 등 topic마다 다를 수 있어 모서리 픽셀을
        # 실제 배경색으로 채택한다.
        key = raw.getpixel((2, 2))[:3]
        kr, kg, kb = key
        px = raw.load()
        for yy in range(raw.height):
            for xx in range(raw.width):
                r, g, b, a = px[xx, yy]
                if abs(r - kr) + abs(g - kg) + abs(b - kb) < 160:
                    px[xx, yy] = (r, g, b, 0)
        mask = Image.new("L", (icon_size, icon_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, icon_size, icon_size), fill=255)
        combined_mask = ImageChops.multiply(raw.split()[3], mask)
        icon = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
        icon.paste(raw, (0, 0), combined_mask)
    else:
        icon = None

    icon_img = Image.new("RGBA", (icon_canvas, icon_canvas), (0, 0, 0, 0))
    shadow = Image.new("RGBA", icon_img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [pad - 3, pad + 6, pad + icon_size + 3, pad + icon_size + 12], fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    icon_img = Image.alpha_composite(icon_img, shadow)
    idraw = ImageDraw.Draw(icon_img)
    idraw.ellipse([pad - ring_w, pad - ring_w, pad + icon_size + ring_w, pad + icon_size + ring_w],
                  fill=(255, 255, 255, 255))
    if icon is not None:
        icon_img.paste(icon, (pad, pad), icon)

    font = ImageFont.truetype(_chalk_font_for_lang(lang), font_size)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    canvas_w = max(icon_canvas, text_w + 20)
    gap = 8
    canvas_h = icon_canvas + gap + text_h + 10
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.alpha_composite(icon_img, ((canvas_w - icon_canvas) // 2, 0))
    draw = ImageDraw.Draw(canvas)
    tx = (canvas_w - text_w) / 2 - bbox[0]
    ty = icon_canvas + gap - bbox[1]
    draw.text((tx + 2, ty + 2), name, font=font, fill=(0, 0, 0, 130))
    draw.text((tx, ty), name, font=font, fill=(255, 255, 255, 255))
    canvas.save(out_path)


# WHY 칠판 모서리 낙서(2026-08-02, "파츠같은거 귀여운거 랜덤으로 칠판 모서리쪽에
# 추가하는게 어떨까 싶어 너무 휑하고 별로야"): 칠판 배경이 실사진 그대로라 텍스트가
# 없는 구간이 휑하다는 지적 — 실제 이미지 생성 없이 PIL 도형만으로 그린 작은
# 분필 낙서(별·하트·반짝임·음표·스마일리)를 캡션과 같은 흰색+옅은 그림자 톤으로
# 그려서 칠판 모서리에 하나씩 얹는다. `_stroke_shape`가 캡션과 동일한 그림자 기법
# (오프셋 +3,+3, 검정 alpha 110)을 재사용해서 톤을 맞춘다.
_DOODLE_SIZE = 130


def _doodle_canvas() -> Image.Image:
    return Image.new("RGBA", (_DOODLE_SIZE, _DOODLE_SIZE), (0, 0, 0, 0))


def _stroke_shape(draw_fn) -> Image.Image:
    shadow = _doodle_canvas()
    draw_fn(ImageDraw.Draw(shadow), (3, 3), (0, 0, 0, 110))
    img = _doodle_canvas()
    draw_fn(ImageDraw.Draw(img), (0, 0), (255, 255, 255, 255))
    return Image.alpha_composite(shadow, img)


def _doodle_star() -> Image.Image:
    cx, cy, r_outer, r_inner = _DOODLE_SIZE / 2, _DOODLE_SIZE / 2, 46, 19
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))

    def draw(d, off, color):
        p = [(x + off[0], y + off[1]) for x, y in pts]
        d.line(p + [p[0]], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_heart() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.arc([20 + ox, 15 + oy, 68 + ox, 63 + oy], 130, 360, fill=color, width=5)
        d.arc([62 + ox, 15 + oy, 110 + ox, 63 + oy], 180, 50, fill=color, width=5)
        d.line([(21 + ox, 40 + oy), (65 + ox, 105 + oy)], fill=color, width=5, joint="curve")
        d.line([(109 + ox, 40 + oy), (65 + ox, 105 + oy)], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_sparkle() -> Image.Image:
    def draw(d, off, color):
        cx, cy = _DOODLE_SIZE / 2 + off[0], _DOODLE_SIZE / 2 + off[1]
        for ang in (0, 90, 180, 270):
            rad = math.radians(ang)
            x2, y2 = cx + 42 * math.cos(rad), cy + 42 * math.sin(rad)
            d.line([(cx, cy), (x2, y2)], fill=color, width=5)
        for ang in (45, 135, 225, 315):
            rad = math.radians(ang)
            x2, y2 = cx + 20 * math.cos(rad), cy + 20 * math.sin(rad)
            d.line([(cx, cy), (x2, y2)], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_note() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([20 + ox, 78 + oy, 46 + ox, 100 + oy], outline=color, width=5)
        d.line([(44 + ox, 89 + oy), (44 + ox, 25 + oy)], fill=color, width=5)
        d.line([(44 + ox, 25 + oy), (78 + ox, 35 + oy)], fill=color, width=5, joint="curve")
        d.line([(78 + ox, 35 + oy), (78 + ox, 55 + oy)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_smiley() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 15 + oy, 115 + ox, 115 + oy], outline=color, width=5)
        d.ellipse([42 + ox, 48 + oy, 52 + ox, 58 + oy], fill=color)
        d.ellipse([78 + ox, 48 + oy, 88 + ox, 58 + oy], fill=color)
        d.arc([40 + ox, 55 + oy, 90 + ox, 90 + oy], 20, 160, fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_cloud() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 55 + oy, 55 + ox, 90 + oy], outline=color, width=5)
        d.ellipse([40 + ox, 38 + oy, 85 + ox, 82 + oy], outline=color, width=5)
        d.ellipse([70 + ox, 55 + oy, 112 + ox, 92 + oy], outline=color, width=5)
        d.line([(20 + ox, 88 + oy), (108 + ox, 88 + oy)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_rainbow() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        for i, r in enumerate((48, 36, 24)):
            d.arc([15 + ox + i * 12, 15 + oy + i * 12, 115 + ox - i * 12, 130 + oy - i * 12],
                  180, 360, fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_clover() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 65 + ox, 65 + oy
        for dx, dy in ((-18, -18), (18, -18), (-18, 18), (18, 18)):
            d.ellipse([cx + dx - 20, cy + dy - 20, cx + dx + 20, cy + dy + 20], outline=color, width=5)
        d.line([(cx, cy), (cx + 6, cy + 40)], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_speech_bubble() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.rounded_rectangle([15 + ox, 20 + oy, 115 + ox, 88 + oy], radius=16, outline=color, width=5)
        d.line([(35 + ox, 87 + oy), (30 + ox, 108 + oy), (55 + ox, 88 + oy)], fill=color, width=5, joint="curve")
        font = ImageFont.truetype(CHALK_FONT_PATH, 40)
        d.text((65 + ox, 34 + oy), "?", font=font, fill=color)

    return _stroke_shape(draw)


def _doodle_ribbon() -> Image.Image:
    """리본/보우 매듭 — 처음엔 아래로 늘어지는 꼬리까지 그렸는데 작게 축소·회전되면
    꼬리 선이 뭉개져서 나비 모양처럼 안 보이는 문제가 있었다(2026-08-02 실제 렌더
    확인) — 매듭 두 날개 + 중앙 원만 남겨서 작은 크기에서도 리본으로 읽히게 단순화."""
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 65 + ox, 65 + oy
        d.polygon([(cx, cy), (cx - 44, cy - 30), (cx - 44, cy + 30)], outline=color, width=5)
        d.polygon([(cx, cy), (cx + 44, cy - 30), (cx + 44, cy + 30)], outline=color, width=5)
        d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=color)

    return _stroke_shape(draw)


def _doodle_paw() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([35 + ox, 55 + oy, 95 + ox, 105 + oy], outline=color, width=5)
        d.ellipse([25 + ox, 25 + oy, 47 + ox, 50 + oy], outline=color, width=4)
        d.ellipse([53 + ox, 15 + oy, 75 + ox, 40 + oy], outline=color, width=4)
        d.ellipse([83 + ox, 25 + oy, 105 + ox, 50 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_flower() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy, r = 65 + ox, 65 + oy, 24
        for i in range(6):
            ang = i * math.pi / 3
            px, py = cx + r * math.cos(ang), cy + r * math.sin(ang)
            d.ellipse([px - 17, py - 17, px + 17, py + 17], outline=color, width=4)
        d.ellipse([cx - 11, cy - 11, cx + 11, cy + 11], fill=color)

    return _stroke_shape(draw)


def _doodle_balloon() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 62 + ox, 48 + oy
        d.ellipse([cx - 28, cy - 33, cx + 28, cy + 28], outline=color, width=5)
        d.polygon([(cx - 7, cy + 26), (cx + 7, cy + 26), (cx, cy + 38)], fill=color)
        d.line([(cx, cy + 38), (cx - 7, cy + 62), (cx + 6, cy + 82)], fill=color, width=3, joint="curve")

    return _stroke_shape(draw)


def _doodle_umbrella() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, top = 65 + ox, 30 + oy
        d.arc([cx - 42, top, cx + 42, top + 60], 180, 360, fill=color, width=5)
        d.line([(cx - 42, top + 30), (cx + 42, top + 30)], fill=color, width=3)
        d.line([(cx, top + 30), (cx, top + 92)], fill=color, width=4)
        d.arc([cx - 9, top + 82, cx + 11, top + 102], 0, 170, fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_lightning() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        pts = [(78 + ox, 14 + oy), (52 + ox, 58 + oy), (68 + ox, 58 + oy),
               (48 + ox, 112 + oy), (82 + ox, 62 + oy), (64 + ox, 62 + oy), (78 + ox, 14 + oy)]
        d.polygon(pts, outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_sun() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy, r = 65 + ox, 65 + oy, 26
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=5)
        for ang in range(0, 360, 45):
            rad = math.radians(ang)
            x1, y1 = cx + (r + 8) * math.cos(rad), cy + (r + 8) * math.sin(rad)
            x2, y2 = cx + (r + 22) * math.cos(rad), cy + (r + 22) * math.sin(rad)
            d.line([(x1, y1), (x2, y2)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_crown() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        pts = [(20 + ox, 95 + oy), (20 + ox, 50 + oy), (40 + ox, 70 + oy), (65 + ox, 35 + oy),
               (90 + ox, 70 + oy), (110 + ox, 50 + oy), (110 + ox, 95 + oy)]
        d.line(pts, fill=color, width=5, joint="curve")
        d.line([(20 + ox, 95 + oy), (110 + ox, 95 + oy)], fill=color, width=5)
        for x in (20, 110):
            d.ellipse([x - 6 + ox, 44 + oy, x + 6 + ox, 56 + oy], fill=color)
        d.ellipse([59 + ox, 29 + oy, 71 + ox, 41 + oy], fill=color)

    return _stroke_shape(draw)


def _doodle_gift() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.rectangle([20 + ox, 50 + oy, 100 + ox, 105 + oy], outline=color, width=5)
        d.line([(60 + ox, 50 + oy), (60 + ox, 105 + oy)], fill=color, width=4)
        d.line([(20 + ox, 72 + oy), (100 + ox, 72 + oy)], fill=color, width=4)
        d.line([(60 + ox, 50 + oy), (45 + ox, 25 + oy)], fill=color, width=4, joint="curve")
        d.line([(60 + ox, 50 + oy), (75 + ox, 25 + oy)], fill=color, width=4, joint="curve")

    return _stroke_shape(draw)


def _doodle_bell() -> Image.Image:
    """⚠️ v1은 아치+사선 어깨선+작은 추 원까지 넣었는데 작게 축소·회전되면
    추 원이 몸통과 분리된 점처럼 보여 종처럼 안 읽혔다(실제 렌더로 발견,
    `_doodle_chalk_stick`과 같은 교훈) — 추를 없애고 사선 어깨선을 아치에
    바로 이어붙여 한 덩어리 실루엣으로 단순화."""
    def draw(d, off, color):
        ox, oy = off
        d.line([(30 + ox, 100 + oy), (35 + ox, 55 + oy)], fill=color, width=5, joint="curve")
        d.arc([35 + ox, 25 + oy, 95 + ox, 95 + oy], 180, 360, fill=color, width=5)
        d.line([(95 + ox, 55 + oy), (100 + ox, 100 + oy)], fill=color, width=5, joint="curve")
        d.line([(30 + ox, 100 + oy), (100 + ox, 100 + oy)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_key() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 15 + oy, 50 + ox, 50 + oy], outline=color, width=5)
        d.line([(48 + ox, 32 + oy), (105 + ox, 32 + oy)], fill=color, width=5)
        d.line([(88 + ox, 32 + oy), (88 + ox, 48 + oy)], fill=color, width=4)
        d.line([(103 + ox, 32 + oy), (103 + ox, 44 + oy)], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_anchor() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx = 63 + ox
        d.ellipse([cx - 10, 15 + oy, cx + 10, 35 + oy], outline=color, width=4)
        d.line([(cx, 35 + oy), (cx, 100 + oy)], fill=color, width=5)
        d.line([(cx - 25, 50 + oy), (cx + 25, 50 + oy)], fill=color, width=4)
        d.arc([cx - 35, 60 + oy, cx + 35, 120 + oy], 0, 180, fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_house() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.line([(20 + ox, 60 + oy), (65 + ox, 20 + oy), (110 + ox, 60 + oy)],
               fill=color, width=5, joint="curve")
        d.rectangle([30 + ox, 60 + oy, 100 + ox, 105 + oy], outline=color, width=5)
        d.rectangle([55 + ox, 75 + oy, 75 + ox, 105 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_tree() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx = 65 + ox
        d.polygon([(cx, 15 + oy), (cx - 30, 55 + oy), (cx + 30, 55 + oy)], outline=color, width=5)
        d.polygon([(cx, 40 + oy), (cx - 35, 85 + oy), (cx + 35, 85 + oy)], outline=color, width=5)
        d.rectangle([cx - 8, 85 + oy, cx + 8, 110 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_fish() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([20 + ox, 40 + oy, 85 + ox, 80 + oy], outline=color, width=5)
        d.polygon([(85 + ox, 60 + oy), (112 + ox, 40 + oy), (112 + ox, 80 + oy)], outline=color, width=5)
        d.ellipse([32 + ox, 55 + oy, 40 + ox, 63 + oy], fill=color)

    return _stroke_shape(draw)


def _doodle_bird() -> Image.Image:
    """옛날 낙서의 새 실루엣 — 연결된 아치 두 개로 나는 새를 표현하는 흔한
    간이 도형(글자 'M'처럼 두 번 꺾이는 곡선). 다른 도형처럼 채움/선화가 아니라
    선 두 개뿐이라 아주 작게 축소돼도 뭉개지지 않는다."""
    def draw(d, off, color):
        ox, oy = off
        d.arc([15 + ox, 40 + oy, 60 + ox, 75 + oy], 180, 360, fill=color, width=6)
        d.arc([60 + ox, 40 + oy, 105 + ox, 75 + oy], 180, 360, fill=color, width=6)

    return _stroke_shape(draw)


def _doodle_diamond() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.polygon([(65 + ox, 20 + oy), (100 + ox, 50 + oy), (65 + ox, 110 + oy), (30 + ox, 50 + oy)],
                   outline=color, width=5)
        d.line([(30 + ox, 50 + oy), (100 + ox, 50 + oy)], fill=color, width=3)
        d.line([(65 + ox, 20 + oy), (48 + ox, 50 + oy)], fill=color, width=3)
        d.line([(65 + ox, 20 + oy), (82 + ox, 50 + oy)], fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_apple() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 65 + ox, 72 + oy
        d.arc([cx - 38, cy - 28, cx + 4, cy + 40], 25, 320, fill=color, width=5)
        d.arc([cx - 4, cy - 28, cx + 38, cy + 40], 220, 155, fill=color, width=5)
        d.line([(cx, cy - 28), (cx + 6, cy - 48)], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_spiral() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 65 + ox, 65 + oy
        pts = []
        for i in range(60):
            ang = i * 0.35
            r = 3 + i * 0.9
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.line(pts, fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_candle() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx = 65 + ox
        d.rectangle([cx - 12, 60 + oy, cx + 12, 110 + oy], outline=color, width=5)
        d.line([(cx, 60 + oy), (cx, 42 + oy)], fill=color, width=4)
        d.ellipse([cx - 9, 18 + oy, cx + 9, 42 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_flag() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.line([(30 + ox, 15 + oy), (30 + ox, 115 + oy)], fill=color, width=5)
        d.line([(30 + ox, 20 + oy), (95 + ox, 35 + oy), (30 + ox, 60 + oy)], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_boat() -> Image.Image:
    """⚠️ 원래 나비를 넣었는데 타원 날개가 작게 회전되면 안경처럼 보여
    실제 렌더 확인에서 탈락시켰다(리본 교훈과 동일 — 대칭 타원 2개는 이
    스케일에서 다른 동그란 사물과 구분이 잘 안 됨) — 사다리꼴 선체+삼각
    돛처럼 윤곽이 뚜렷한 배 도형으로 교체."""
    def draw(d, off, color):
        ox, oy = off
        d.polygon([(20 + ox, 90 + oy), (110 + ox, 90 + oy), (95 + ox, 115 + oy), (35 + ox, 115 + oy)],
                   outline=color, width=5)
        d.line([(65 + ox, 90 + oy), (65 + ox, 20 + oy)], fill=color, width=5)
        d.polygon([(65 + ox, 20 + oy), (65 + ox, 80 + oy), (105 + ox, 80 + oy)], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_moon() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        d.arc([25 + ox, 20 + oy, 95 + ox, 110 + oy], 90, 300, fill=color, width=6)

    return _stroke_shape(draw)


def _doodle_hourglass() -> Image.Image:
    """⚠️ v1은 위/아래 삼각형을 한 줄로 이어그렸는데 작게 회전되면 그냥 나비
    넥타이(X자)처럼 보여 모래시계로 안 읽혔다 — 위아래 뚜껑을 별도 굵은
    가로선으로 확실히 그려서 "삼각형 두 개+캡"이라는 걸 더 분명히 했다."""
    def draw(d, off, color):
        ox, oy = off
        d.line([(25 + ox, 20 + oy), (105 + ox, 20 + oy)], fill=color, width=6)
        d.line([(25 + ox, 110 + oy), (105 + ox, 110 + oy)], fill=color, width=6)
        d.polygon([(25 + ox, 20 + oy), (105 + ox, 20 + oy), (65 + ox, 65 + oy)], outline=color, width=5)
        d.polygon([(25 + ox, 110 + oy), (105 + ox, 110 + oy), (65 + ox, 65 + oy)], outline=color, width=5)

    return _stroke_shape(draw)


def _doodle_pencil() -> Image.Image:
    """⚠️ v1은 얇은 대각선 하나뿐이라 작게 회전되면 그냥 사선처럼 보여
    연필로 안 읽혔다(`_doodle_chalk_stick`과 같은 교훈 — 가는 선은 작은
    화면에서 형태 정보를 못 준다) — 몸통을 굵은 평행 사각형(두꺼운 막대)으로
    그리고 끝에 뚜렷한 삼각형 촉을 붙여 실루엣만으로 연필임을 알아보게 했다."""
    def draw(d, off, color):
        ox, oy = off
        d.line([(22 + ox, 108 + oy), (82 + ox, 48 + oy)], fill=color, width=16)
        d.polygon([(82 + ox, 48 + oy), (100 + ox, 22 + oy), (108 + ox, 30 + oy), (90 + ox, 56 + oy)],
                   outline=color, width=4)
        d.polygon([(14 + ox, 116 + oy), (22 + ox, 108 + oy), (30 + ox, 116 + oy)], fill=color)

    return _stroke_shape(draw)


def _doodle_target() -> Image.Image:
    def draw(d, off, color):
        ox, oy = off
        cx, cy = 65 + ox, 65 + oy
        for r in (45, 28, 11):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=4)

    return _stroke_shape(draw)


# WHY 도형 풀을 35개→69개로 늘렸는지(2026-08-03, 글로벌 확장 대비 "파츠같은거
# 엄청 만들고 싶다" — 서브에이전트 3개 병렬로 테마별 설계): 언어권 채널이
# 늘어날수록 "같은 배경 우려먹기" 느낌이 더 잘 드러나서 풀을 한 번 더 크게
# 늘렸다. 세 테마로 나눠 병렬 설계함 — 귀여운 미니 캐릭터(눈코입 있는 구름·
# 별·해 등), 문화색 없는 보편 심볼(지구본·나침반·톱니바퀴 등, 특정 문화·
# 종교·국가 상징 배제), 추상 장식 도형(지그재그·소용돌이·체크마크 등). 기존
# 규칙대로 전부 40px+70px 회전 렌더로 실제 축소 크기에서 읽히는지 확인 후
# 추가함(1개는 40px에서 얼룩져 보여서 탈락 — 발자국 도형).
def _quad_bezier(p0, p1, p2, n=14):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def _off_pts(pts, off):
    return [(x + off[0], y + off[1]) for x, y in pts]


# --- 귀여운 미니 캐릭터(눈·입 있는 버전) ---

def _doodle_cute_cloud_face():
    cx, cy = 65, 68

    def draw(d, off, color):
        ox, oy = off
        bumps = [(-34, 6, 20), (-14, -14, 24), (12, -16, 24), (32, 2, 20)]
        for bx, by, r in bumps:
            bbox = [cx + bx - r + ox, cy + by - r + oy, cx + bx + r + ox, cy + by + r + oy]
            d.arc(bbox, start=180, end=360, fill=color, width=5)
        d.line([(cx - 40 + ox, cy + 10 + oy), (cx - 34 + ox, cy + 26 + oy),
                (cx + 34 + ox, cy + 26 + oy), (cx + 40 + ox, cy + 10 + oy)],
               fill=color, width=5, joint="curve")
        eye_r = 3
        d.ellipse([cx - 14 + ox - eye_r, cy + 6 + oy - eye_r, cx - 14 + ox + eye_r, cy + 6 + oy + eye_r], fill=color)
        d.ellipse([cx + 6 + ox - eye_r, cy + 6 + oy - eye_r, cx + 6 + ox + eye_r, cy + 6 + oy + eye_r], fill=color)
        d.arc([cx - 10 + ox, cy + 6 + oy, cx + 2 + ox, cy + 16 + oy], start=20, end=160, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_star_face():
    cx, cy, r_outer, r_inner = 65, 65, 42, 18
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p + [p[0]], fill=color, width=5, joint="curve")
        ox, oy = off
        eye_r = 3
        d.ellipse([cx - 10 + ox - eye_r, cy - 2 + oy - eye_r, cx - 10 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.ellipse([cx + 10 + ox - eye_r, cy - 2 + oy - eye_r, cx + 10 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.arc([cx - 8 + ox, cy + 2 + oy, cx + 8 + ox, cy + 14 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_sun_face():
    cx, cy, r = 65, 65, 26
    ray_len = 14

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        for i in range(8):
            ang = i * math.pi / 4
            x1 = cx + (r + 4) * math.cos(ang)
            y1 = cy + (r + 4) * math.sin(ang)
            x2 = cx + (r + 4 + ray_len) * math.cos(ang)
            y2 = cy + (r + 4 + ray_len) * math.sin(ang)
            d.line([(x1 + ox, y1 + oy), (x2 + ox, y2 + oy)], fill=color, width=5)
        eye_r = 3
        d.ellipse([cx - 9 + ox - eye_r, cy - 4 + oy - eye_r, cx - 9 + ox + eye_r, cy - 4 + oy + eye_r], fill=color)
        d.ellipse([cx + 9 + ox - eye_r, cy - 4 + oy - eye_r, cx + 9 + ox + eye_r, cy - 4 + oy + eye_r], fill=color)
        d.arc([cx - 8 + ox, cy + oy, cx + 8 + ox, cy + 10 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_teardrop_face():
    cx, cy = 65, 68

    def draw(d, off, color):
        ox, oy = off
        pts = [
            (cx, cy - 40), (cx + 22, cy + 10), (cx + 22, cy + 28), (cx + 8, cy + 40),
            (cx - 8, cy + 40), (cx - 22, cy + 28), (cx - 22, cy + 10), (cx, cy - 40),
        ]
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=5, joint="curve")
        eye_r = 3
        d.ellipse([cx - 9 + ox - eye_r, cy + 14 + oy - eye_r, cx - 9 + ox + eye_r, cy + 14 + oy + eye_r], fill=color)
        d.ellipse([cx + 9 + ox - eye_r, cy + 14 + oy - eye_r, cx + 9 + ox + eye_r, cy + 14 + oy + eye_r], fill=color)
        d.arc([cx - 8 + ox, cy + 18 + oy, cx + 8 + ox, cy + 28 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_mug_face():
    cx, cy = 60, 65

    def draw(d, off, color):
        ox, oy = off
        d.rounded_rectangle([cx - 24 + ox, cy - 24 + oy, cx + 18 + ox, cy + 30 + oy], radius=6, outline=color, width=5)
        d.arc([cx + 8 + ox, cy - 8 + oy, cx + 38 + ox, cy + 22 + oy], start=280, end=90, fill=color, width=5)
        eye_r = 3
        d.ellipse([cx - 13 + ox - eye_r, cy - 2 + oy - eye_r, cx - 13 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.ellipse([cx + 1 + ox - eye_r, cy - 2 + oy - eye_r, cx + 1 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.arc([cx - 12 + ox, cy + 2 + oy, cx + 2 + ox, cy + 12 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_moon_face():
    cx, cy, r = 65, 65, 30

    def draw(d, off, color):
        ox, oy = off
        d.arc([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], start=60, end=310, fill=color, width=5)
        d.arc([cx - r + 18 + ox, cy - r + 6 + oy, cx + r + 10 + ox, cy + r - 6 + oy], start=100, end=280, fill=color, width=5)
        eye_r = 3
        d.ellipse([cx - 14 + ox - eye_r, cy - 2 + oy - eye_r, cx - 14 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.arc([cx - 18 + ox, cy + 2 + oy, cx - 6 + ox, cy + 12 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_flower_face():
    cx, cy, pr = 65, 65, 15
    petal_r = 13

    def draw(d, off, color):
        ox, oy = off
        for i in range(5):
            ang = i * (2 * math.pi / 5) - math.pi / 2
            px = cx + (pr + petal_r * 0.7) * math.cos(ang)
            py = cy + (pr + petal_r * 0.7) * math.sin(ang)
            d.ellipse([px - petal_r + ox, py - petal_r + oy, px + petal_r + ox, py + petal_r + oy], outline=color, width=5)
        eye_r = 3
        d.ellipse([cx - 6 + ox - eye_r, cy - 2 + oy - eye_r, cx - 6 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.ellipse([cx + 6 + ox - eye_r, cy - 2 + oy - eye_r, cx + 6 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.arc([cx - 6 + ox, cy + oy, cx + 6 + ox, cy + 8 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_droplet_wink():
    cx, cy = 65, 68

    def draw(d, off, color):
        ox, oy = off
        pts = [
            (cx, cy - 38), (cx + 20, cy + 8), (cx + 20, cy + 26), (cx + 6, cy + 38),
            (cx - 6, cy + 38), (cx - 20, cy + 26), (cx - 20, cy + 8), (cx, cy - 38),
        ]
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=5, joint="curve")
        eye_r = 3
        d.ellipse([cx - 9 + ox - eye_r, cy + 12 + oy - eye_r, cx - 9 + ox + eye_r, cy + 12 + oy + eye_r], fill=color)
        d.arc([cx + 5 + ox, cy + 10 + oy, cx + 13 + ox, cy + 16 + oy], start=180, end=360, fill=color, width=3)
        d.arc([cx - 7 + ox, cy + 18 + oy, cx + 7 + ox, cy + 26 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_apple_face():
    cx, cy, r = 63, 70, 26

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        d.line([(cx + ox, cy - r + oy), (cx + 4 + ox, cy - r - 12 + oy)], fill=color, width=4)
        d.arc([cx + 2 + ox, cy - r - 14 + oy, cx + 22 + ox, cy - r + 2 + oy], start=200, end=20, fill=color, width=4)
        eye_r = 3
        d.ellipse([cx - 10 + ox - eye_r, cy - 4 + oy - eye_r, cx - 10 + ox + eye_r, cy - 4 + oy + eye_r], fill=color)
        d.ellipse([cx + 10 + ox - eye_r, cy - 4 + oy - eye_r, cx + 10 + ox + eye_r, cy - 4 + oy + eye_r], fill=color)
        d.arc([cx - 9 + ox, cy + oy, cx + 9 + ox, cy + 12 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_egg_face():
    cx, cy = 65, 68

    def draw(d, off, color):
        ox, oy = off
        bbox_top = [cx - 24 + ox, cy - 38 + oy, cx + 24 + ox, cy + 10 + oy]
        bbox_bot = [cx - 30 + ox, cy - 8 + oy, cx + 30 + ox, cy + 38 + oy]
        d.arc(bbox_top, start=180, end=360, fill=color, width=5)
        d.arc(bbox_bot, start=0, end=180, fill=color, width=5)
        d.line([(cx - 24 + ox, cy - 14 + oy), (cx - 30 + ox, cy + 16 + oy)], fill=color, width=5)
        d.line([(cx + 24 + ox, cy - 14 + oy), (cx + 30 + ox, cy + 16 + oy)], fill=color, width=5)
        eye_r = 3
        d.ellipse([cx - 9 + ox - eye_r, cy + oy - eye_r, cx - 9 + ox + eye_r, cy + oy + eye_r], fill=color)
        d.ellipse([cx + 9 + ox - eye_r, cy + oy - eye_r, cx + 9 + ox + eye_r, cy + oy + eye_r], fill=color)
        d.arc([cx - 8 + ox, cy + 4 + oy, cx + 8 + ox, cy + 14 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_cute_leaf_face():
    cx, cy = 65, 65

    def draw(d, off, color):
        ox, oy = off
        pts = [
            (cx - 2, cy - 38), (cx + 22, cy - 14), (cx + 26, cy + 14), (cx + 8, cy + 34),
            (cx - 8, cy + 34), (cx - 26, cy + 14), (cx - 22, cy - 14), (cx - 2, cy - 38),
        ]
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=5, joint="curve")
        d.line([(cx - 2 + ox, cy - 30 + oy), (cx + ox, cy + 28 + oy)], fill=color, width=4)
        eye_r = 3
        d.ellipse([cx - 11 + ox - eye_r, cy - 2 + oy - eye_r, cx - 11 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.ellipse([cx + 7 + ox - eye_r, cy - 2 + oy - eye_r, cx + 7 + ox + eye_r, cy - 2 + oy + eye_r], fill=color)
        d.arc([cx - 9 + ox, cy + 2 + oy, cx + 7 + ox, cy + 14 + oy], start=10, end=170, fill=color, width=3)

    return _stroke_shape(draw)


# --- 문화색 없는 보편 심볼 ---

def _doodle_globe():
    cx, cy, r = 65, 65, 42

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        d.ellipse([cx - r * 0.42 + ox, cy - r + oy, cx + r * 0.42 + ox, cy + r + oy], outline=color, width=4)
        d.line([cx - r + ox, cy + oy, cx + r + ox, cy + oy], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_compass():
    cx, cy, r = 65, 65, 40

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        pts = [(cx, cy - r * 0.78), (cx + r * 0.24, cy), (cx, cy + r * 0.78), (cx - r * 0.24, cy)]
        p = [(x + ox, y + oy) for x, y in pts]
        d.line(p + [p[0]], fill=color, width=5, joint="curve")
        for ang in (0, 90, 180, 270):
            a = math.radians(ang)
            x1, y1 = cx + (r - 2) * math.sin(a), cy - (r - 2) * math.cos(a)
            x2, y2 = cx + (r + 8) * math.sin(a), cy - (r + 8) * math.cos(a)
            d.line([x1 + ox, y1 + oy, x2 + ox, y2 + oy], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_gear():
    cx, cy, r_out, r_in, teeth = 65, 65, 38, 27, 8

    def draw(d, off, color):
        ox, oy = off
        pts = []
        for i in range(teeth * 2):
            ang = math.pi * 2 * i / (teeth * 2)
            r = r_out if i % 2 == 0 else r_in
            pts.append((cx + r * math.cos(ang) + ox, cy + r * math.sin(ang) + oy))
        d.line(pts + [pts[0]], fill=color, width=5, joint="curve")
        d.ellipse([cx - 12 + ox, cy - 12 + oy, cx + 12 + ox, cy + 12 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_lightbulb():
    cx, cy, r = 65, 50, 26

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        d.line([cx - r * 0.45 + ox, cy + r * 0.9 + oy, cx - r * 0.32 + ox, cy + r * 1.6 + oy], fill=color, width=5)
        d.line([cx + r * 0.45 + ox, cy + r * 0.9 + oy, cx + r * 0.32 + ox, cy + r * 1.6 + oy], fill=color, width=5)
        for i in range(3):
            yy = cy + r * 1.55 + i * 9 + oy
            d.line([cx - r * 0.34 + ox, yy, cx + r * 0.34 + ox, yy], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_leaf():
    cx, cy = 65, 62

    def draw(d, off, color):
        ox, oy = off
        top = (cx, cy - 44)
        bot = (cx, cy + 34)
        left = _quad_bezier(bot, (cx - 48, cy), top, n=16)
        right = _quad_bezier(top, (cx + 48, cy), bot, n=16)
        pts = [(x + ox, y + oy) for x, y in left + right]
        d.line(pts, fill=color, width=5, joint="curve")
        d.line([top[0] + ox, top[1] + oy, bot[0] + ox, bot[1] + oy], fill=color, width=4)
        stem = _quad_bezier(bot, (cx + 4, cy + 46), (cx - 10, cy + 54), n=8)
        d.line([(x + ox, y + oy) for x, y in stem], fill=color, width=4, joint="curve")

    return _stroke_shape(draw)


def _doodle_droplet():
    cx, cy, r = 65, 78, 26

    def draw(d, off, color):
        ox, oy = off
        top = (cx, cy - r - 32)
        left = (cx - r, cy)
        right = (cx + r, cy)
        up_left = _quad_bezier(left, (cx - r * 0.3, cy - r * 1.7), top, n=12)
        up_right = _quad_bezier(top, (cx + r * 0.3, cy - r * 1.7), right, n=12)
        arc_pts = []
        for i in range(21):
            ang = math.radians(180) * (i / 20)
            arc_pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        pts = up_left + up_right + arc_pts
        d.line([(x + ox, y + oy) for x, y in pts], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_open_book():
    cx, cy = 65, 65

    def draw(d, off, color):
        ox, oy = off
        w, h = 46, 30

        def quad(p0, p1, p2, n=12):
            return [(x + ox, y + oy) for x, y in _quad_bezier(p0, p1, p2, n)]

        top_l, ctrl_l, spine_top = (cx - w, cy - h * 0.55), (cx - w * 0.5, cy - h * 0.95), (cx, cy - h * 0.25)
        bot_l, ctrl_l2, spine_bot = (cx - w, cy + h * 0.75), (cx - w * 0.5, cy + h * 0.4), (cx, cy + h * 0.55)
        top_r, ctrl_r = (cx + w, cy - h * 0.55), (cx + w * 0.5, cy - h * 0.95)
        bot_r, ctrl_r2 = (cx + w, cy + h * 0.75), (cx + w * 0.5, cy + h * 0.4)
        d.line(quad(top_l, ctrl_l, spine_top), fill=color, width=5, joint="curve")
        d.line([top_l[0] + ox, top_l[1] + oy, bot_l[0] + ox, bot_l[1] + oy], fill=color, width=5)
        d.line(quad(bot_l, ctrl_l2, spine_bot), fill=color, width=5, joint="curve")
        d.line(quad(spine_top, ctrl_r, top_r), fill=color, width=5, joint="curve")
        d.line([top_r[0] + ox, top_r[1] + oy, bot_r[0] + ox, bot_r[1] + oy], fill=color, width=5)
        d.line(quad(bot_r, ctrl_r2, spine_bot), fill=color, width=5, joint="curve")
        d.line([spine_top[0] + ox, spine_top[1] + oy, spine_bot[0] + ox, spine_bot[1] + oy], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_wave():
    cy = 65

    def draw(d, off, color):
        ox, oy = off
        pts = []
        for i in range(41):
            x = -45 + 90 * (i / 40)
            y = 22 * math.sin(x / 15.0)
            pts.append((65 + x + ox, cy + y + oy))
        d.line(pts, fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_magnifying_glass():
    cx, cy, r = 55, 55, 28

    def draw(d, off, color):
        ox, oy = off
        d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy], outline=color, width=5)
        hx1, hy1 = cx + r * 0.72, cy + r * 0.72
        d.line([hx1 + ox, hy1 + oy, hx1 + 26 + ox, hy1 + 26 + oy], fill=color, width=7)

    return _stroke_shape(draw)


def _doodle_puzzle_piece():
    cx, cy, s = 65, 65, 34

    def draw(d, off, color):
        ox, oy = off
        bump = s * 0.35
        x0, y0, x1, y1 = cx - s, cy - s, cx + s, cy - s
        x2, y2, x3, y3 = cx + s, cy + s, cx - s, cy + s
        pts = [(x0, y0), (cx - bump * 0.6, y0)]
        for i in range(13):
            ang = math.pi - math.pi * (i / 12)
            pts.append((cx + bump * math.cos(ang), y0 - bump * 0.9 - bump * 0.9 * math.sin(ang)))
        pts += [(cx + bump * 0.6, y0), (x1, y1), (x1, cy - bump * 0.6)]
        for i in range(13):
            ang = -math.pi / 2 - math.pi * (i / 12)
            pts.append((x1 + bump * 0.9 + bump * 0.9 * math.cos(ang), cy + bump * math.sin(ang)))
        pts += [(x1, cy + bump * 0.6), (x2, y2), (x3, y3), (x0, y0)]
        p = [(x + ox, y + oy) for x, y in pts]
        d.line(p, fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_arrow_up():
    cx, cy = 65, 65

    def draw(d, off, color):
        ox, oy = off
        d.line([cx + ox, cy + 40 + oy, cx + ox, cy - 38 + oy], fill=color, width=6)
        d.line([cx - 18 + ox, cy - 16 + oy, cx + ox, cy - 38 + oy], fill=color, width=6, joint="curve")
        d.line([cx + 18 + ox, cy - 16 + oy, cx + ox, cy - 38 + oy], fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


# --- 추상 장식 도형 ---

def _doodle_zigzag():
    pts = [(20, 90), (45, 40), (65, 90), (85, 40), (110, 90)]

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_dot_cluster():
    centers = [(45, 45), (70, 40), (55, 65), (85, 60), (65, 85), (40, 75), (90, 85)]
    radii = [8, 6, 9, 5, 7, 6, 5]

    def draw(d, off, color):
        for (cx, cy), r in zip(centers, radii):
            x, y = cx + off[0], cy + off[1]
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    return _stroke_shape(draw)


def _doodle_swirl_line():
    pts = []
    turns, steps, cx, cy = 2.4, 60, 65, 65
    for i in range(steps + 1):
        t = i / steps
        ang = t * turns * 2 * math.pi
        r = 6 + t * 40
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_curved_arrow():
    steps = 30
    p0, p1, p2 = (25, 95), (65, 100), (100, 35)
    pts = _quad_bezier(p0, p1, p2, n=steps)
    ex, ey = pts[-1]
    px, py = pts[-3]
    ang = math.atan2(ey - py, ex - px)
    head_len = 20
    a1, a2 = ang + math.radians(150), ang - math.radians(150)
    head1 = [(ex, ey), (ex + head_len * math.cos(a1), ey + head_len * math.sin(a1))]
    head2 = [(ex, ey), (ex + head_len * math.cos(a2), ey + head_len * math.sin(a2))]

    def draw(d, off, color):
        d.line(_off_pts(pts, off), fill=color, width=6, joint="curve")
        d.line(_off_pts(head1, off), fill=color, width=6, joint="curve")
        d.line(_off_pts(head2, off), fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_checkmark():
    pts = [(28, 65), (52, 92), (105, 30)]

    def draw(d, off, color):
        d.line(_off_pts(pts, off), fill=color, width=7, joint="curve")

    return _stroke_shape(draw)


def _doodle_hexagon():
    cx, cy, r = 65, 65, 42
    pts = [(cx + r * math.cos(math.pi / 6 + i * math.pi / 3), cy + r * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p + [p[0]], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_wavy_line():
    pts = []
    steps = 40
    for i in range(steps + 1):
        t = i / steps
        x = 15 + t * 100
        y = 65 + 22 * math.sin(t * 3 * math.pi)
        pts.append((x, y))

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_infinity():
    pts = []
    steps, cx, cy, a = 60, 65, 65, 34
    for i in range(steps + 1):
        t = i / steps * 2 * math.pi
        scale = a / (1 + math.sin(t) ** 2)
        x = cx + scale * math.cos(t)
        y = cy + scale * math.sin(t) * math.cos(t)
        pts.append((x, y))

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p, fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


def _doodle_asterisk():
    cx, cy, r = 65, 65, 38
    lines = []
    for i in range(6):
        ang = i * math.pi / 6
        lines.append(((cx - r * math.cos(ang), cy - r * math.sin(ang)), (cx + r * math.cos(ang), cy + r * math.sin(ang))))

    def draw(d, off, color):
        for (x1, y1), (x2, y2) in lines:
            d.line([(x1 + off[0], y1 + off[1]), (x2 + off[0], y2 + off[1])], fill=color, width=6)

    return _stroke_shape(draw)


def _doodle_scribble_scratch():
    # WHY 끊어진 3줄 사선(2026-08-03): 처음엔 zigzag처럼 이어진 선이었는데
    # 40px에서 zigzag랑 거의 구분이 안 됐다 — 분필로 대충 그은 사선 3개로
    # 바꿔서 확실히 다른 모양으로 읽히게 함.
    strokes = [
        [(30, 95), (48, 55)],
        [(50, 100), (68, 60)],
        [(70, 95), (88, 55)],
    ]

    def draw(d, off, color):
        for s in strokes:
            d.line(_off_pts(s, off), fill=color, width=7, joint="curve")

    return _stroke_shape(draw)


def _doodle_plus_cross():
    cx, cy, r = 65, 65, 34

    def draw(d, off, color):
        d.line([(cx - r + off[0], cy + off[1]), (cx + r + off[0], cy + off[1])], fill=color, width=7)
        d.line([(cx + off[0], cy - r + off[1]), (cx + off[0], cy + r + off[1])], fill=color, width=7)

    return _stroke_shape(draw)


def _doodle_triangle():
    cx, cy, r = 65, 68, 42
    pts = [(cx + r * math.cos(-math.pi / 2 + i * 2 * math.pi / 3), cy + r * math.sin(-math.pi / 2 + i * 2 * math.pi / 3)) for i in range(3)]

    def draw(d, off, color):
        p = _off_pts(pts, off)
        d.line(p + [p[0]], fill=color, width=6, joint="curve")

    return _stroke_shape(draw)


# WHY 8개 추가(2026-08-04, "파츠 좀 늘려줄래... 전반적으로 챙겨봐"): 기존 68종
# 풀에 없던 소재(눈꽃/말풍선 물음표·느낌표/트로피/로켓/연/벙어리장갑/컵케이크)로
# 다양성 확대. 나머지 도돌이와 동일하게 130x130 캔버스 + 흰색 스트로크 +
# 그림자 오프셋 패턴(_stroke_shape)을 그대로 따른다.
def _doodle_snowflake():
    cx, cy, r = 65, 65, 40

    def draw(d, off, color):
        ox, oy = off
        for ang in (0, 60, 120):
            rad = math.radians(ang)
            x1, y1 = cx - r * math.cos(rad) + ox, cy - r * math.sin(rad) + oy
            x2, y2 = cx + r * math.cos(rad) + ox, cy + r * math.sin(rad) + oy
            d.line([(x1, y1), (x2, y2)], fill=color, width=5)
            for t in (-0.55, 0.55):
                mx, my = cx + r * t * math.cos(rad) + ox, cy + r * t * math.sin(rad) + oy
                for side in (-1, 1):
                    brad = rad + side * math.radians(35)
                    bx, by = mx + 14 * math.cos(brad), my + 14 * math.sin(brad)
                    d.line([(mx, my), (bx, by)], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_question_bubble():
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 12 + oy, 115 + ox, 100 + oy], outline=color, width=5)
        d.polygon([(40 + ox, 96 + oy), (58 + ox, 96 + oy), (34 + ox, 118 + oy)], fill=color)
        d.arc([42 + ox, 28 + oy, 88 + ox, 62 + oy], 180, 30, fill=color, width=6)
        d.line([(65 + ox, 58 + oy), (65 + ox, 68 + oy)], fill=color, width=6)
        d.ellipse([61 + ox, 76 + oy, 71 + ox, 86 + oy], fill=color)

    return _stroke_shape(draw)


def _doodle_exclaim_bubble():
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 12 + oy, 115 + ox, 100 + oy], outline=color, width=5)
        d.polygon([(72 + ox, 96 + oy), (90 + ox, 96 + oy), (96 + ox, 118 + oy)], fill=color)
        d.line([(65 + ox, 28 + oy), (65 + ox, 66 + oy)], fill=color, width=7)
        d.ellipse([60 + ox, 76 + oy, 70 + ox, 86 + oy], fill=color)

    return _stroke_shape(draw)


def _doodle_trophy():
    def draw(d, off, color):
        ox, oy = off
        d.line([(30 + ox, 22 + oy), (100 + ox, 22 + oy), (78 + ox, 70 + oy),
                (52 + ox, 70 + oy), (30 + ox, 22 + oy)], fill=color, width=5, joint="curve")
        d.arc([10 + ox, 26 + oy, 40 + ox, 58 + oy], 260, 140, fill=color, width=5)
        d.arc([90 + ox, 26 + oy, 120 + ox, 58 + oy], 40, 280, fill=color, width=5)
        d.line([(65 + ox, 70 + oy), (65 + ox, 90 + oy)], fill=color, width=5)
        d.line([(42 + ox, 90 + oy), (88 + ox, 90 + oy), (82 + ox, 104 + oy),
                (48 + ox, 104 + oy), (42 + ox, 90 + oy)], fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_rocket():
    def draw(d, off, color):
        ox, oy = off
        d.line([(65 + ox, 12 + oy), (44 + ox, 55 + oy), (44 + ox, 88 + oy),
                (86 + ox, 88 + oy), (86 + ox, 55 + oy), (65 + ox, 12 + oy)],
               fill=color, width=5, joint="curve")
        d.ellipse([54 + ox, 42 + oy, 76 + ox, 64 + oy], outline=color, width=5)
        d.line([(44 + ox, 78 + oy), (26 + ox, 106 + oy)], fill=color, width=5)
        d.line([(86 + ox, 78 + oy), (104 + ox, 106 + oy)], fill=color, width=5)
        d.line([(52 + ox, 92 + oy), (46 + ox, 112 + oy)], fill=color, width=5)
        d.line([(78 + ox, 92 + oy), (84 + ox, 112 + oy)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_kite():
    def draw(d, off, color):
        ox, oy = off
        pts = [(65 + ox, 14 + oy), (100 + ox, 52 + oy), (65 + ox, 112 + oy), (30 + ox, 52 + oy)]
        d.line(pts + [pts[0]], fill=color, width=5, joint="curve")
        d.line([(30 + ox, 52 + oy), (100 + ox, 52 + oy)], fill=color, width=4)
        d.line([(65 + ox, 14 + oy), (65 + ox, 112 + oy)], fill=color, width=4)
        tail_start = (65 + ox, 112 + oy)
        for i, (dx, dy) in enumerate([(-6, 10), (6, 10), (-6, 10)]):
            nx, ny = tail_start[0] + dx, tail_start[1] + dy
            d.line([tail_start, (nx, ny)], fill=color, width=3)
            tail_start = (nx, ny)

    return _stroke_shape(draw)


def _doodle_icecream():
    def draw(d, off, color):
        ox, oy = off
        d.line([(50 + ox, 70 + oy), (65 + ox, 116 + oy), (80 + ox, 70 + oy)],
               fill=color, width=5, joint="curve")
        d.arc([38 + ox, 30 + oy, 92 + ox, 84 + oy], 0, 360, fill=color, width=5)
        d.line([(30 + ox, 55 + oy), (100 + ox, 55 + oy)], fill=color, width=4)

    return _stroke_shape(draw)


def _doodle_cupcake():
    def draw(d, off, color):
        ox, oy = off
        d.line([(38 + ox, 78 + oy), (44 + ox, 112 + oy), (86 + ox, 112 + oy), (92 + ox, 78 + oy)],
               fill=color, width=5, joint="curve")
        d.arc([30 + ox, 46 + oy, 60 + ox, 76 + oy], 180, 360, fill=color, width=5)
        d.arc([50 + ox, 40 + oy, 80 + ox, 70 + oy], 180, 360, fill=color, width=5)
        d.arc([70 + ox, 46 + oy, 100 + ox, 76 + oy], 180, 360, fill=color, width=5)
        d.ellipse([61 + ox, 26 + oy, 71 + ox, 36 + oy], fill=color)

    return _stroke_shape(draw)


# WHY 6개 추가(2026-08-04, "더해도 좋아" — 1차 8종에 이어 2차 확장): 위와 같은
# 스타일(130x130 캔버스 + 흰색 스트로크 + 그림자 오프셋)로 아직 없던 소재
# 채움(안경/보타이/편지봉투/자/붓/시계).
def _doodle_glasses():
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([14 + ox, 40 + oy, 54 + ox, 80 + oy], outline=color, width=5)
        d.ellipse([76 + ox, 40 + oy, 116 + ox, 80 + oy], outline=color, width=5)
        d.line([(54 + ox, 58 + oy), (76 + ox, 58 + oy)], fill=color, width=5)
        d.line([(14 + ox, 58 + oy), (2 + ox, 50 + oy)], fill=color, width=5)
        d.line([(116 + ox, 58 + oy), (128 + ox, 50 + oy)], fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_bowtie():
    def draw(d, off, color):
        ox, oy = off
        left = [(20 + ox, 35 + oy), (60 + ox, 65 + oy), (20 + ox, 95 + oy), (20 + ox, 35 + oy)]
        right = [(110 + ox, 35 + oy), (70 + ox, 65 + oy), (110 + ox, 95 + oy), (110 + ox, 35 + oy)]
        d.line(left, fill=color, width=5, joint="curve")
        d.line(right, fill=color, width=5, joint="curve")
        d.ellipse([56 + ox, 55 + oy, 74 + ox, 75 + oy], outline=color, width=5)

    return _stroke_shape(draw)


def _doodle_envelope():
    def draw(d, off, color):
        ox, oy = off
        d.rectangle([16 + ox, 32 + oy, 114 + ox, 98 + oy], outline=color, width=5)
        d.line([(16 + ox, 32 + oy), (65 + ox, 70 + oy), (114 + ox, 32 + oy)],
               fill=color, width=5, joint="curve")

    return _stroke_shape(draw)


def _doodle_ruler():
    def draw(d, off, color):
        ox, oy = off
        d.rectangle([14 + ox, 50 + oy, 116 + ox, 80 + oy], outline=color, width=5)
        for i in range(1, 8):
            x = 14 + i * 14.6
            h = 12 if i % 2 == 0 else 8
            d.line([(x + ox, 50 + oy), (x + ox, 50 + h + oy)], fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_paintbrush():
    def draw(d, off, color):
        ox, oy = off
        d.line([(35 + ox, 112 + oy), (78 + ox, 68 + oy)], fill=color, width=7)
        d.line([(72 + ox, 62 + oy), (96 + ox, 38 + oy)], fill=color, width=10)
        pts = [(88 + ox, 32 + oy), (100 + ox, 16 + oy), (112 + ox, 30 + oy),
               (104 + ox, 44 + oy), (88 + ox, 32 + oy)]
        d.line(pts, fill=color, width=4, joint="curve")

    return _stroke_shape(draw)


def _doodle_clock():
    def draw(d, off, color):
        ox, oy = off
        d.ellipse([15 + ox, 15 + oy, 115 + ox, 115 + oy], outline=color, width=5)
        cx, cy = 65 + ox, 65 + oy
        d.line([(cx, cy), (cx, cy - 32)], fill=color, width=5)
        d.line([(cx, cy), (cx + 24, cy + 10)], fill=color, width=5)
        for ang in range(0, 360, 30):
            rad = math.radians(ang)
            x1, y1 = cx + 44 * math.cos(rad), cy + 44 * math.sin(rad)
            x2, y2 = cx + 50 * math.cos(rad), cy + 50 * math.sin(rad)
            d.line([(x1, y1), (x2, y2)], fill=color, width=3)

    return _stroke_shape(draw)


# WHY 10개 추가(2026-08-04, "계속계속 쭉쭉 더만들어" — 3차 확장): 앞선 두 배치와
# 같은 스타일 그대로, 아직 없던 소재(머그컵/엄지척/메달/책가방/발자국/비행기/
# 열기구/카메라/별똥별/버섯) 채움.
def _doodle_mug():
    def draw(d, off, color):
        ox, oy = off
        d.line([(30 + ox, 40 + oy), (30 + ox, 95 + oy), (90 + ox, 95 + oy), (90 + ox, 40 + oy)],
               fill=color, width=5, joint="curve")
        d.arc([88 + ox, 50 + oy, 116 + ox, 85 + oy], 270, 90, fill=color, width=5)
        d.arc([38 + ox, 8 + oy, 54 + ox, 28 + oy], 200, 40, fill=color, width=3)
        d.arc([64 + ox, 3 + oy, 80 + ox, 23 + oy], 200, 40, fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_thumbs_up():
    def draw(d, off, color):
        ox, oy = off
        d.rounded_rectangle([38 + ox, 18 + oy, 62 + ox, 72 + oy], radius=12, outline=color, width=6)
        d.rounded_rectangle([35 + ox, 70 + oy, 108 + ox, 112 + oy], radius=12, outline=color, width=6)
        for x in (58, 76, 94):
            d.line([(x + ox, 70 + oy), (x + ox, 112 + oy)], fill=color, width=3)

    return _stroke_shape(draw)


def _doodle_medal():
    def draw(d, off, color):
        ox, oy = off
        d.line([(35 + ox, 10 + oy), (65 + ox, 55 + oy), (95 + ox, 10 + oy)],
               fill=color, width=5, joint="curve")
        d.ellipse([35 + ox, 55 + oy, 95 + ox, 115 + oy], outline=color, width=6)
        cx, cy, r_outer, r_inner = 65 + ox, 85 + oy, 16, 7
        pts = []
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            r = r_outer if i % 2 == 0 else r_inner
            pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))
        d.line(pts + [pts[0]], fill=color, width=3, joint="curve")

    return _stroke_shape(draw)


def _doodle_backpack():
    def draw(d, off, color):
        ox, oy = off
        d.rounded_rectangle([28 + ox, 35 + oy, 102 + ox, 115 + oy], radius=18, outline=color, width=5)
        d.rounded_rectangle([45 + ox, 50 + oy, 85 + ox, 80 + oy], radius=8, outline=color, width=4)
        d.arc([40 + ox, 15 + oy, 60 + ox, 45 + oy], 180, 360, fill=color, width=5)
        d.arc([70 + ox, 15 + oy, 90 + ox, 45 + oy], 180, 360, fill=color, width=5)

    return _stroke_shape(draw)


def _doodle_feather():
    def draw(d, off, color):
        ox, oy = off
        d.line([(65 + ox, 10 + oy), (40 + ox, 35 + oy), (35 + ox, 70 + oy), (45 + ox, 100 + oy),
                (65 + ox, 120 + oy), (85 + ox, 100 + oy), (95 + ox, 70 + oy), (90 + ox, 35 + oy),
                (65 + ox, 10 + oy)], fill=color, width=5, joint="curve")
        d.line([(65 + ox, 15 + oy), (65 + ox, 115 + oy)], fill=color, width=3)
        for t in range(1, 5):
            y = 15 + t * 20
            d.line([(65 + ox, y + oy), (50 + ox, y + 10 + oy)], fill=color, width=2)
            d.line([(65 + ox, y + oy), (80 + ox, y + 10 + oy)], fill=color, width=2)

    return _stroke_shape(draw)


def _doodle_airplane():
    def draw(d, off, color):
        ox, oy = off
        d.line([(65 + ox, 10 + oy), (72 + ox, 60 + oy), (65 + ox, 120 + oy), (58 + ox, 60 + oy),
                (65 + ox, 10 + oy)], fill=color, width=5, joint="curve")
        d.line([(20 + ox, 75 + oy), (58 + ox, 68 + oy), (58 + ox, 85 + oy), (20 + ox, 95 + oy)],
               fill=color, width=4, joint="curve")
        d.line([(110 + ox, 75 + oy), (72 + ox, 68 + oy), (72 + ox, 85 + oy), (110 + ox, 95 + oy)],
               fill=color, width=4, joint="curve")
        d.line([(58 + ox, 105 + oy), (45 + ox, 118 + oy), (58 + ox, 112 + oy)],
               fill=color, width=3, joint="curve")
        d.line([(72 + ox, 105 + oy), (85 + ox, 118 + oy), (72 + ox, 112 + oy)],
               fill=color, width=3, joint="curve")

    return _stroke_shape(draw)


def _doodle_hot_air_balloon():
    def draw(d, off, color):
        ox, oy = off
        d.arc([25 + ox, 10 + oy, 105 + ox, 90 + oy], 0, 360, fill=color, width=5)
        d.line([(45 + ox, 85 + oy), (38 + ox, 112 + oy)], fill=color, width=4)
        d.line([(85 + ox, 85 + oy), (92 + ox, 112 + oy)], fill=color, width=4)
        d.line([(65 + ox, 88 + oy), (65 + ox, 112 + oy)], fill=color, width=4)
        d.rectangle([38 + ox, 112 + oy, 92 + ox, 126 + oy], outline=color, width=4)

    return _stroke_shape(draw)


def _doodle_camera():
    def draw(d, off, color):
        ox, oy = off
        d.rounded_rectangle([20 + ox, 40 + oy, 110 + ox, 100 + oy], radius=10, outline=color, width=5)
        d.rectangle([45 + ox, 25 + oy, 75 + ox, 42 + oy], outline=color, width=4)
        d.ellipse([48 + ox, 52 + oy, 82 + ox, 86 + oy], outline=color, width=5)
        d.ellipse([90 + ox, 48 + oy, 100 + ox, 58 + oy], fill=color)

    return _stroke_shape(draw)


def _doodle_shooting_star():
    def draw(d, off, color):
        ox, oy = off
        cx, cy, r_outer, r_inner = 85, 40, 26, 11
        pts = []
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            r = r_outer if i % 2 == 0 else r_inner
            pts.append((cx + r * math.cos(ang) + ox, cy - r * math.sin(ang) + oy))
        d.line(pts + [pts[0]], fill=color, width=4, joint="curve")
        for dx, dy, w in [(-30, 30, 6), (-45, 45, 4), (-58, 58, 3)]:
            d.line([(cx - 15 + ox, cy + 15 + oy), (cx - 15 + dx + ox, cy + 15 + dy + oy)],
                   fill=color, width=w)

    return _stroke_shape(draw)


def _doodle_mushroom():
    def draw(d, off, color):
        ox, oy = off
        d.arc([20 + ox, 25 + oy, 110 + ox, 85 + oy], 180, 360, fill=color, width=5)
        d.line([(48 + ox, 80 + oy), (44 + ox, 118 + oy), (86 + ox, 118 + oy), (82 + ox, 80 + oy)],
               fill=color, width=5, joint="curve")
        for cx, cy in [(45, 45), (65, 35), (85, 48)]:
            d.ellipse([cx - 7 + ox, cy - 7 + oy, cx + 7 + ox, cy + 7 + oy], fill=color)

    return _stroke_shape(draw)


_DOODLES = [
    _doodle_star, _doodle_heart, _doodle_sparkle, _doodle_note, _doodle_smiley,
    _doodle_cloud, _doodle_rainbow, _doodle_clover, _doodle_speech_bubble,
    _doodle_ribbon, _doodle_paw, _doodle_flower, _doodle_balloon, _doodle_umbrella,
    _doodle_lightning, _doodle_sun, _doodle_crown, _doodle_gift, _doodle_bell,
    _doodle_key, _doodle_anchor, _doodle_house, _doodle_tree, _doodle_fish,
    _doodle_bird, _doodle_diamond, _doodle_apple, _doodle_spiral, _doodle_candle,
    _doodle_flag, _doodle_boat, _doodle_moon, _doodle_hourglass, _doodle_pencil,
    _doodle_target,
    _doodle_cute_cloud_face, _doodle_cute_star_face, _doodle_cute_sun_face,
    _doodle_cute_teardrop_face, _doodle_cute_mug_face, _doodle_cute_moon_face,
    _doodle_cute_flower_face, _doodle_cute_droplet_wink, _doodle_cute_apple_face,
    _doodle_cute_egg_face, _doodle_cute_leaf_face,
    _doodle_globe, _doodle_compass, _doodle_gear, _doodle_lightbulb, _doodle_leaf,
    _doodle_droplet, _doodle_open_book, _doodle_wave, _doodle_magnifying_glass,
    _doodle_puzzle_piece, _doodle_arrow_up,
    _doodle_zigzag, _doodle_dot_cluster, _doodle_swirl_line, _doodle_curved_arrow,
    _doodle_checkmark, _doodle_hexagon, _doodle_wavy_line, _doodle_infinity,
    _doodle_asterisk, _doodle_scribble_scratch, _doodle_plus_cross, _doodle_triangle,
    _doodle_snowflake, _doodle_question_bubble, _doodle_exclaim_bubble,
    _doodle_trophy, _doodle_rocket, _doodle_kite, _doodle_icecream, _doodle_cupcake,
    _doodle_glasses, _doodle_bowtie, _doodle_envelope, _doodle_ruler,
    _doodle_paintbrush, _doodle_clock,
    _doodle_mug, _doodle_thumbs_up, _doodle_medal, _doodle_backpack, _doodle_feather,
    _doodle_airplane, _doodle_hot_air_balloon, _doodle_camera, _doodle_shooting_star,
    _doodle_mushroom,
]

# WHY 실측 상수(2026-08-02): 칠판.png(1024x1024)에서 초록 판서면이 실제로 시작/끝나는
# y좌표를 픽셀 스캔으로 구함(위/아래 흰 여백·나무 프레임을 제외한 순수 판서면 범위).
# 이 사진 자체를 바꾸지 않는 한 다시 잴 필요 없음 — CHALKBOARD_CROP_LEFT/RIGHT와
# 같은 성격의 상수.
_CHALKBOARD_GREEN_TOP_ORIG = 142
_CHALKBOARD_GREEN_BOTTOM_ORIG = 866

# WHY 익명 느낌 성+OO(2026-08-02, "주번에 이름이 뜨는데 이XX 김XX 이런식으로
# 들어가게 해... 익명느낌으로 귀엽게"): 처음엔 "김민지"처럼 완성된 가상 이름을
# 썼는데, 실제 인물처럼 보일 수 있으니 실제 신상 정보를 가리는 흔한 관행대로
# 성만 밝히고 이름 자리는 "OO"로 채운다("김OO", "이OO" 등) — 실존 인물 지칭
# 없이도 옛날 교실 게시물 감성은 그대로 유지된다.
_SURNAMES = ["김", "이", "박", "최", "정", "강", "윤", "임", "조", "장", "한", "오"]
# WHY 영어권은 성+OO 대신 흔한 이름 그 자체(2026-08-03, 글로벌 확장): 한국식
# "성+OO" 익명화 관행이 영어권엔 없다 — 미국 교실 담당표(job chart)는 보통 이름만
# 적어두므로("Helper: Alex"), 그 관례를 따라 흔한 영어 이름 풀에서 그대로 뽑는다.
_FIRST_NAMES_EN = ["Alex", "Jordan", "Sam", "Riley", "Casey", "Morgan", "Taylor",
                   "Jamie", "Avery", "Quinn", "Charlie", "Drew"]


def _anon_name(rng: random.Random, lang: str = "kor") -> str:
    if lang == "kor":
        return f"{rng.choice(_SURNAMES)}OO"
    return rng.choice(_FIRST_NAMES_BY_LANG.get(lang, _FIRST_NAMES_EN))


# WHY 명패 종류를 2종→6종→18종으로 늘리고 풀에서 랜덤 선택하게 했는지
# (2026-08-03, "칠판에 있는 파츠들은 랜덤으로 들어가게 해달라했는데 걍 다
# 들어간상태로만 영상을 제작하는듯? 그리고 파츠들 더 다양화 하는게 좋을 것
# 같아" → "파츠 더 늘렸어? 지금 갯수의 2-3배는 늘리고싶네"): 기존엔 "떠든
# 사람"+"주번"이 매 영상마다 고정으로 둘 다 나왔다 — 도형(별/하트 등)만
# 랜덤이고 "어떤 종류의 명패가 뜨는지" 자체는 항상 같아서 여러 topic을 연달아
# 보면 매번 똑같은 골격으로 느껴졌다. 이제 이 풀에서 0~2개를 랜덤으로 뽑아서
# (`_place_chalk_doodle`) topic마다 아예 다른 조합·개수가 나오게 한다. 6종→
# 18종(3배)은 실제 교실 게시물에 흔한 당번·직책 이름을 그대로 재사용해서
# ("떠든 사람"/"지각생"만 2명 형식, 나머지는 전부 1명) 새로 만든 이름이
# 하나도 부자연스럽지 않게 했다.
_NAMEPLATE_POOL = [
    ("떠든 사람: {}", 2, 26),
    ("지각생: {}", 2, 26),
    ("주번 {}", 1, 30),
    ("우유 당번 {}", 1, 30),
    ("칠판 당번 {}", 1, 30),
    ("오늘의 발표자 {}", 1, 26),
    ("청소 당번 {}", 1, 30),
    ("학급 회장 {}", 1, 30),
    ("부회장 {}", 1, 30),
    ("화분 당번 {}", 1, 30),
    ("창문 당번 {}", 1, 30),
    ("분리수거 당번 {}", 1, 24),
    ("줄반장 {}", 1, 30),
    ("급식 당번 {}", 1, 30),
    ("이달의 독서왕 {}", 1, 24),
    ("생일자 {}", 1, 30),
    ("이달의 칭찬왕 {}", 1, 24),
    ("우산 당번 {}", 1, 30),
    # WHY 8개 추가(2026-08-04, "계속계속 쭉쭉 더만들어" — 3차 확장): 위와 같은
    # 실제 한국 교실 당번·직책 관행에서 더 뽑았다. 18→26종.
    ("실내화 검사 {}", 1, 24),
    ("사물함 정리 {}", 1, 24),
    ("손소독 당번 {}", 1, 26),
    ("체육 준비물 {}", 1, 24),
    ("안전 지킴이 {}", 1, 24),
    ("복도 정숙 지킴이 {}", 1, 20),
    ("가방 정리 {}", 1, 26),
    ("칠판 지우개 담당 {}", 1, 20),
]

# WHY 한국 교실 당번 문화를 그대로 번역하지 않는지(2026-08-03, 글로벌 확장):
# "우유 당번"·"분리수거 당번" 같은 개념은 한국 교실 특유의 관행이라 그대로
# 번역하면 어색하다 — 미국 교실에서 실제로 흔히 쓰는 job chart 항목(줄 서기
# 리더, 칠판 지우개 담당, 화분 관리 등)으로 같은 톤(귀엽고 옛날 교실 감성)을
# 유지하면서 새로 구성했다.
_NAMEPLATE_POOL_EN = [
    ("Whisper Alert: {}", 2, 22),
    ("Late Today: {}", 2, 22),
    ("Line Leader: {}", 1, 26),
    ("Board Cleaner: {}", 1, 24),
    ("Class President: {}", 1, 22),
    ("Vice President: {}", 1, 22),
    ("Plant Helper: {}", 1, 26),
    ("Paper Passer: {}", 1, 24),
    ("Recycling Helper: {}", 1, 20),
    ("Today's Speaker: {}", 1, 22),
    ("Reading Star: {}", 1, 24),
    ("Star of the Week: {}", 1, 20),
    ("Window Helper: {}", 1, 24),
    ("Lunch Helper: {}", 1, 24),
]

# WHY 14개 언어 추가 명패 풀(2026-08-03, "언어별로 다르게... 그 나라 감성에
# 맞춰서" — 4개 서브에이전트 병렬 리서치): 한국어/영어 외 13개 글로벌 채널
# 언어(+대만어 별도) 각각의 실제 교실 당번·직책 문화를 웹 검색으로 조사해서
# (번역이 아니라) 그 문화에 맞게 새로 구성했다 — 위 "한국 교실 당번 문화를
# 그대로 번역하지 않는지" WHY와 같은 원칙. 일부 항목(예: 감초 성분 표기가 아닌
# 언어권별 "떠든 사람" 격 문구)은 실제 그 문화에 해당 관행이 있는지 근거가
# 약하면 의도적으로 뺐다(예: 아랍어·벵골어·힌디어는 부정적 콜아웃 항목을 아예
# 빼고 전부 긍정적 직책만 사용 — 리서치 근거 부족 시 안전한 쪽으로 판단).
# 태국어는 실제 학교에서 정식 이름 대신 별명(ชื่อเล่น)으로 부르는 문화가
# 확인돼서 이름 풀도 별명으로 구성함. 각 풀 출처는 커밋 메시지/PR 참고.
_NAMEPLATE_POOL_JA = [
    ("日直: {}", 1, 28), ("給食当番: {}", 1, 22), ("掃除当番: {}", 1, 24),
    ("黒板係: {}", 1, 26), ("図書係: {}", 1, 26), ("生き物係: {}", 1, 24),
    ("配り係: {}", 1, 26), ("掲示係: {}", 1, 24), ("レク係: {}", 1, 26),
    ("新聞係: {}", 1, 26), ("ダンス係: {}", 1, 24), ("学級委員: {}", 1, 22),
    ("窓係: {}", 1, 28), ("バースデー係: {}", 1, 20), ("おしゃべりさん: {}", 2, 20),
    ("遅刻: {}", 2, 26),
]
_FIRST_NAMES_JA = ["はると", "ゆうと", "そうた", "みなと", "けんと", "りく",
                   "さくら", "ゆい", "ひなた", "あおい", "みお", "あかり",
                   "つむぎ", "そら", "ひまり"]

_NAMEPLATE_POOL_TW = [
    ("值日生: {}", 1, 26), ("班長: {}", 1, 28), ("副班長: {}", 1, 24),
    ("風紀股長: {}", 1, 22), ("衛生股長: {}", 1, 22), ("學藝股長: {}", 1, 22),
    ("總務股長: {}", 1, 22), ("康樂股長: {}", 1, 22), ("體育股長: {}", 1, 22),
    ("圖書股長: {}", 1, 22), ("環保股長: {}", 1, 20), ("擦黑板: {}", 1, 26),
    ("排路隊: {}", 1, 24), ("澆花: {}", 1, 28), ("小老師: {}", 1, 24),
    ("愛講話: {}", 2, 22), ("遲到: {}", 2, 26),
]
_FIRST_NAMES_TW = ["家豪", "承恩", "冠廷", "宗翰", "柏翰", "品睿", "昊恩",
                   "詩涵", "雨萱", "佳穎", "惠雯", "怡君", "心妍", "子晴", "立祥"]

_NAMEPLATE_POOL_VI = [
    ("Lớp trưởng: {}", 1, 26), ("Lớp phó học tập: {}", 1, 20),
    ("Lớp phó lao động: {}", 1, 20), ("Lớp phó văn nghệ: {}", 1, 20),
    ("Tổ trưởng: {}", 1, 26), ("Cờ đỏ: {}", 1, 28), ("Trực nhật: {}", 2, 22),
    ("Quét lớp: {}", 1, 24), ("Lau bảng: {}", 1, 24), ("Tưới cây: {}", 1, 24),
    ("Phát vở: {}", 1, 24), ("Sinh nhật: {}", 1, 26),
    ("Hay nói chuyện: {}", 2, 20), ("Đi trễ: {}", 2, 26),
    ("Sao của tuần: {}", 1, 22), ("Đọc sách giỏi: {}", 1, 22),
]
_FIRST_NAMES_VI = ["An", "Bình", "Chi", "Dũng", "Hà", "Huy", "Lan", "Linh",
                   "Mai", "Minh", "Nam", "Ngọc", "Phương", "Thảo", "Tuấn"]

_NAMEPLATE_POOL_TH = [
    ("หัวหน้าห้อง: {}", 1, 26), ("รองหัวหน้าห้อง: {}", 1, 20),
    ("เลขาห้อง: {}", 1, 26), ("เหรัญญิก: {}", 1, 24),
    ("เวรประจำวัน: {}", 2, 20), ("กวาดห้อง: {}", 1, 24),
    ("ลบกระดาน: {}", 1, 22), ("รดน้ำต้นไม้: {}", 1, 20),
    ("แจกสมุด: {}", 1, 24), ("หัวหน้าแถว: {}", 1, 22), ("ถือธง: {}", 1, 26),
    ("วันเกิด: {}", 1, 28), ("ดาวเด่น: {}", 1, 26), ("คุยเก่ง: {}", 2, 24),
    ("มาสาย: {}", 2, 26), ("นักอ่านเก่ง: {}", 1, 22),
]
_FIRST_NAMES_TH = ["พลอย", "แบงค์", "มายด์", "ฟ้า", "กิ๊ฟ", "บอส", "ไอซ์",
                   "มิ้นท์", "เบียร์", "โอม", "มุก", "กอล์ฟ", "น้ำ"]

_NAMEPLATE_POOL_ES = [
    ("Habló mucho: {}", 2, 22), ("Llegó tarde: {}", 2, 22),
    ("Líder de fila: {}", 1, 26), ("Ayudante del día: {}", 1, 22),
    ("Encargado de materiales: {}", 1, 16), ("Encargado del pizarrón: {}", 1, 18),
    ("Presidente de grupo: {}", 1, 20), ("Vicepresidente: {}", 1, 24),
    ("Encargado de las plantas: {}", 1, 16), ("Encargado de la puerta: {}", 1, 18),
    ("Repartidor de cuadernos: {}", 1, 16), ("Encargado del reciclaje: {}", 1, 18),
    ("Lector del día: {}", 1, 24), ("Estrella de la semana: {}", 1, 18),
    ("Cumpleañero del mes: {}", 1, 20), ("Encargado de la fecha: {}", 1, 18),
    ("Encargado del clima: {}", 1, 20), ("Compañero del mes: {}", 1, 20),
]
_FIRST_NAMES_ES = ["Mateo", "Sofía", "Santiago", "Valentina", "Camila", "Diego",
                   "Ximena", "Sebastián", "Mariana", "Alejandro", "Fernanda",
                   "Nicolás", "Isabella", "Emiliano", "Renata"]

_NAMEPLATE_POOL_PT = [
    ("Conversou muito: {}", 2, 20), ("Chegou atrasado: {}", 2, 20),
    ("Ajudante do dia: {}", 1, 22), ("Líder da fila: {}", 1, 24),
    ("Apagador do quadro: {}", 1, 18), ("Delegado de turma: {}", 1, 20),
    ("Vice-delegado: {}", 1, 24), ("Ajudante das plantas: {}", 1, 18),
    ("Ajudante da porta: {}", 1, 20), ("Ajudante dos cadernos: {}", 1, 18),
    ("Ajudante da reciclagem: {}", 1, 16), ("Leitor do dia: {}", 1, 24),
    ("Estrela da semana: {}", 1, 20), ("Aniversariante do mês: {}", 1, 16),
    ("Ajudante do calendário: {}", 1, 16), ("Ajudante do tempo: {}", 1, 20),
    ("Amigo do mês: {}", 1, 24), ("Ajudante da tinta: {}", 1, 20),
]
_FIRST_NAMES_PT = ["Miguel", "Sofia", "Arthur", "Helena", "Heitor", "Alice",
                   "Davi", "Laura", "Bernardo", "Manuela", "Gabriel",
                   "Valentina", "Pedro", "Isabela", "Lucas"]

_NAMEPLATE_POOL_FR = [
    ("A trop parlé : {}", 2, 22), ("En retard : {}", 2, 24),
    ("Chef de rang : {}", 1, 24), ("Responsable du tableau : {}", 1, 16),
    ("Responsable de la date : {}", 1, 16), ("Facteur de la classe : {}", 1, 16),
    ("Distributeur : {}", 1, 24), ("Responsable météo : {}", 1, 20),
    ("Responsable propreté : {}", 1, 18), ("Responsable BCD : {}", 1, 22),
    ("Délégué de classe : {}", 1, 20), ("Délégué suppléant : {}", 1, 20),
    ("Responsable des plantes : {}", 1, 16), ("Responsable de la porte : {}", 1, 16),
    ("Élève de la semaine : {}", 1, 18), ("Anniversaire du mois : {}", 1, 18),
    ("Responsable du tri : {}", 1, 18), ("Lecteur du jour : {}", 1, 22),
]
_FIRST_NAMES_FR = ["Léo", "Emma", "Gabriel", "Louise", "Raphaël", "Alice",
                   "Jules", "Chloé", "Adam", "Léa", "Louis", "Manon",
                   "Nathan", "Camille", "Hugo"]

_NAMEPLATE_POOL_DE = [
    ("Hat zu viel geredet: {}", 2, 20), ("Kam zu spät: {}", 2, 24),
    ("Tafeldienst: {}", 1, 28), ("Austeildienst: {}", 1, 26),
    ("Ordnungsdienst: {}", 1, 24), ("Blumendienst: {}", 1, 26),
    ("Kalenderdienst: {}", 1, 24), ("Klassensprecher: {}", 1, 22),
    ("Stellvertreter: {}", 1, 22), ("Mülldienst: {}", 1, 28),
    ("Fensterdienst: {}", 1, 24), ("Wetterdienst: {}", 1, 24),
    ("Büchereidienst: {}", 1, 22), ("Technikdienst: {}", 1, 24),
    ("Botendienst: {}", 1, 26), ("Stern der Woche: {}", 1, 22),
    ("Geburtstagskind: {}", 1, 22), ("Lesepate: {}", 1, 28),
]
_FIRST_NAMES_DE = ["Ben", "Mia", "Paul", "Emma", "Finn", "Lea", "Noah", "Mila",
                   "Elias", "Anna", "Luis", "Lina", "Felix", "Marie", "Jonas"]

_NAMEPLATE_POOL_RU = [
    ("Шумели на уроке: {}", 2, 20), ("Опоздали сегодня: {}", 2, 20),
    ("Староста класса: {}", 1, 24), ("Дежурный по классу: {}", 1, 20),
    ("Физорг: {}", 1, 28), ("Цветовод: {}", 1, 26), ("Библиотекарь: {}", 1, 24),
    ("Редактор газеты: {}", 1, 20), ("Санитар класса: {}", 1, 22),
    ("Дежурный столовой: {}", 1, 20), ("Хранитель доски: {}", 1, 22),
    ("Помощник учителя: {}", 1, 22), ("Именинник дня: {}", 1, 26),
    ("Чтец недели: {}", 1, 24), ("Открывает окна: {}", 1, 22),
    ("Культорг: {}", 1, 28),
]
_FIRST_NAMES_RU = ["Саша", "Женя", "Максим", "Даша", "Ваня", "Настя",
                   "Витя", "Катя", "Дима", "Лена", "Паша", "Аня",
                   "Миша", "Оля", "Костя"]

_NAMEPLATE_POOL_TR = [
    ("Sınıfta Konuşanlar: {}", 2, 18), ("Bugün Geç Kalanlar: {}", 2, 20),
    ("Sınıf Başkanı: {}", 1, 24), ("Başkan Yardımcısı: {}", 1, 20),
    ("Nöbetçi Öğrenci: {}", 1, 20), ("Tahta Sorumlusu: {}", 1, 20),
    ("Çiçek Sorumlusu: {}", 1, 22), ("Kitap Dağıtan: {}", 1, 22),
    ("Pencere Sorumlusu: {}", 1, 20), ("Temizlik Sorumlusu: {}", 1, 18),
    ("Kitaplık Sorumlusu: {}", 1, 20), ("Doğum Günü: {}", 1, 28),
    ("Haftanın Yıldızı: {}", 1, 20), ("Bugünün Sunucusu: {}", 1, 20),
    ("Tebeşir Sorumlusu: {}", 1, 18), ("Günün Yardımcısı: {}", 1, 22),
]
_FIRST_NAMES_TR = ["Ayşe", "Mehmet", "Elif", "Emre", "Zeynep", "Ahmet",
                   "Ece", "Burak", "Deniz", "Cem", "Selin", "Kerem",
                   "Yusuf", "Ecrin", "Berk"]

_NAMEPLATE_POOL_ID = [
    ("Ribut di Kelas: {}", 2, 22), ("Terlambat Hari Ini: {}", 2, 20),
    ("Ketua Kelas: {}", 1, 26), ("Wakil Ketua Kelas: {}", 1, 22),
    ("Sekretaris Kelas: {}", 1, 22), ("Bendahara Kelas: {}", 1, 22),
    ("Piket Hari Ini: {}", 1, 24), ("Penghapus Papan Tulis: {}", 1, 18),
    ("Penyiram Bunga: {}", 1, 24), ("Petugas Absen: {}", 1, 24),
    ("Seksi Kebersihan: {}", 1, 22), ("Pembagi Buku: {}", 1, 24),
    ("Ulang Tahun: {}", 1, 28), ("Bintang Minggu Ini: {}", 1, 20),
    ("Pembawa Acara Hari Ini: {}", 1, 18), ("Petugas UKS: {}", 1, 24),
]
_FIRST_NAMES_ID = ["Budi", "Siti", "Andi", "Dewi", "Rizky", "Putri",
                   "Agus", "Ayu", "Fajar", "Rina", "Dimas", "Sari",
                   "Bayu", "Indah", "Wahyu"]

_NAMEPLATE_POOL_AR = [
    ("عريف الفصل: {}", 1, 26), ("رئيس الفصل: {}", 1, 26),
    ("نائب الرئيس: {}", 1, 24), ("مسؤول النظافة: {}", 1, 22),
    ("أمين المكتبة: {}", 1, 24), ("مسؤول السبورة: {}", 1, 22),
    ("مسؤول الحضور: {}", 1, 22), ("موزع الكراسات: {}", 1, 20),
    ("حارس الباب: {}", 1, 26), ("مسؤول النباتات: {}", 1, 20),
    ("طالب الأسبوع: {}", 1, 22), ("نجم الأسبوع: {}", 1, 24),
    ("عيد ميلاد: {}", 1, 28), ("لوحة الشرف: {}", 2, 22),
    ("قائد الصف: {}", 1, 24),
]
_FIRST_NAMES_AR = ["أحمد", "محمد", "عمر", "خالد", "يوسف", "علي", "زيد",
                   "مريم", "فاطمة", "عائشة", "نور", "سارة", "زينب", "ليلى"]

_NAMEPLATE_POOL_BN = [
    ("ক্লাস ক্যাপ্টেন: {}", 2, 22), ("শ্রেণি প্রধান: {}", 1, 24),
    ("পরিচ্ছন্নতা প্রধান: {}", 1, 20), ("লাইব্রেরি সহকারী: {}", 1, 20),
    ("সপ্তাহের সেরা শিক্ষার্থী: {}", 1, 18), ("হাজিরা মনিটর: {}", 1, 22),
    ("বোর্ড মনিটর: {}", 1, 22), ("সহ-অধিনায়ক: {}", 1, 22),
    ("গাছ পরিচর্যাকারী: {}", 1, 18), ("দরজা রক্ষক: {}", 1, 26),
    ("জন্মদিন: {}", 1, 30), ("সেরা পাঠক: {}", 1, 24),
    ("হোমওয়ার্ক সংগ্রাহক: {}", 1, 18), ("সাপ্তাহিক তারকা: {}", 2, 20),
]
_FIRST_NAMES_BN = ["রহিম", "করিম", "আরিফ", "হাসান", "ইমরান", "আয়ান", "তারিক",
                   "আয়েশা", "ফাতিমা", "নূর", "জারা", "রেহানা", "নাসরিন", "সাদিয়া"]

_NAMEPLATE_POOL_HI = [
    ("कक्षा मॉनिटर: {}", 1, 24), ("सफाई मॉनिटर: {}", 1, 22),
    ("पुस्तकालय मॉनिटर: {}", 1, 20), ("अनुशासन प्रभारी: {}", 1, 20),
    ("उपस्थिति मॉनिटर: {}", 1, 20), ("ब्लैकबोर्ड ड्यूटी: {}", 1, 20),
    ("कक्षा प्रतिनिधि: {}", 1, 22), ("उप-मॉनिटर: {}", 1, 26),
    ("खेल कप्तान: {}", 1, 26), ("हाउस कैप्टन: {}", 1, 24),
    ("प्रार्थना सभा प्रभारी: {}", 1, 20), ("पौधा प्रभारी: {}", 1, 26),
    ("जन्मदिन: {}", 1, 30), ("सप्ताह का सितारा: {}", 1, 22),
    ("पेपर वितरक: {}", 1, 24), ("सम्मान सूची: {}", 2, 22),
]
_FIRST_NAMES_HI = ["आरव", "विवान", "अर्जुन", "रोहन", "आदित्य", "ईशान", "कबीर",
                   "अनन्या", "दिया", "प्रिया", "रिया", "आराध्या", "सान्वी", "मीरा"]

# WHY 두 개의 레지스트리 딕셔너리로 묶는지: 언어가 15개(+한국어)로 늘어난
# 뒤로도 _anon_name/_place_chalk_doodle에서 if/elif를 15번 반복하지 않고
# lang 코드 하나로 바로 찾게 한다 — 매핑에 없는 코드는 안전하게 영어로 폴백.
_FIRST_NAMES_BY_LANG: dict[str, list[str]] = {
    "en": _FIRST_NAMES_EN, "ja": _FIRST_NAMES_JA, "zh-TW": _FIRST_NAMES_TW,
    "vi": _FIRST_NAMES_VI, "th": _FIRST_NAMES_TH, "es": _FIRST_NAMES_ES,
    "pt": _FIRST_NAMES_PT, "fr": _FIRST_NAMES_FR, "de": _FIRST_NAMES_DE,
    "ru": _FIRST_NAMES_RU, "tr": _FIRST_NAMES_TR, "id": _FIRST_NAMES_ID,
    "ar": _FIRST_NAMES_AR, "bn": _FIRST_NAMES_BN, "hi": _FIRST_NAMES_HI,
}
_NAMEPLATE_POOL_BY_LANG: dict[str, list[tuple[str, int, int]]] = {
    "en": _NAMEPLATE_POOL_EN, "ja": _NAMEPLATE_POOL_JA, "zh-TW": _NAMEPLATE_POOL_TW,
    "vi": _NAMEPLATE_POOL_VI, "th": _NAMEPLATE_POOL_TH, "es": _NAMEPLATE_POOL_ES,
    "pt": _NAMEPLATE_POOL_PT, "fr": _NAMEPLATE_POOL_FR, "de": _NAMEPLATE_POOL_DE,
    "ru": _NAMEPLATE_POOL_RU, "tr": _NAMEPLATE_POOL_TR, "id": _NAMEPLATE_POOL_ID,
    "ar": _NAMEPLATE_POOL_AR, "bn": _NAMEPLATE_POOL_BN, "hi": _NAMEPLATE_POOL_HI,
}


def _doodle_text_box(text: str, font_size: int = 30, double_border: bool = False,
                      lang: str = "kor") -> Image.Image:
    """분필체 글자를 사각 테두리로 감싼 작은 명패 — 주번/떠든 사람/급훈 액자가
    전부 이 틀을 재사용하고 테두리 스타일(단선/이중선)만 다르게 쓴다. WHY 여백을
    font_size에 비례하게 뒀는지(2026-08-02, "급훈처럼 넣어놓은거 크기도 좀 키우고
    글자 폰트도 키워야지"): 여백이 고정값이면 폰트만 키웠을 때 액자가 글자에
    비해 옹색해 보인다 — 폰트 크기에 비례한 여백으로 액자 전체가 같이 커지게 한다."""
    font = ImageFont.truetype(_chalk_font_for_lang(lang), font_size)
    pad_x, pad_y = round(font_size * 0.5), round(font_size * 0.32)
    dummy = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    inner_pad = 6 if double_border else 0
    box_w, box_h = tw + pad_x * 2 + inner_pad * 2, th + pad_y * 2 + inner_pad * 2

    def draw(d, off, color):
        ox, oy = off
        d.rectangle([2 + ox, 2 + oy, box_w - 2 + ox, box_h - 2 + oy], outline=color, width=3)
        if double_border:
            d.rectangle([2 + inner_pad + ox, 2 + inner_pad + oy,
                         box_w - 2 - inner_pad + ox, box_h - 2 - inner_pad + oy], outline=color, width=2)
        d.text((pad_x + inner_pad - bbox[0] + ox, pad_y + inner_pad - bbox[1] + oy),
               text, font=font, fill=color)

    shadow = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw(ImageDraw.Draw(shadow), (2, 2), (0, 0, 0, 100))
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw(ImageDraw.Draw(img), (0, 0), (255, 255, 255, 255))
    return Image.alpha_composite(shadow, img)


def _topic_word_from_seed(seed: str) -> str:
    """doodle_seed(예: "손발저림_1_shorts")에서 topic 폴더명의 카테고리 단어만
    뽑아낸다("손발저림") — "급훈" 액자에 넣을 한 단어 요약으로 쓴다. topic
    폴더명 자체가 이미 "카테고리_번호" 규칙이라(위 "topic 폴더명" 절 참고)
    번호만 떼면 바로 쓸 수 있는 단어가 나온다."""
    word = seed[: -len("_shorts")] if seed.endswith("_shorts") else seed
    return re.sub(r"_\d+$", "", word)


# WHY 칠판 좌표 변환 헬퍼(2026-08-02, 분필통·급훈 액자 추가하며 정리): 원본
# 칠판.png 픽셀 좌표를 최종 캔버스 좌표로 바꾸는 계산이 낙서 배치에 이미 있었는데,
# 분필통 트레이 위치도 같은 변환이 필요해서 공용 함수로 뽑았다 — 크롭/줌 상수가
# 바뀌면 이 함수 한 곳만 고치면 모든 모서리 장식이 같이 맞아떨어진다.
def _chalkboard_orig_to_canvas(x_orig: float, y_orig: float, top_pad: int) -> tuple[float, float]:
    cropped_w = CHALKBOARD_CROP_RIGHT - CHALKBOARD_CROP_LEFT
    scale = (W / cropped_w) * CHALKBOARD_ZOOM
    left_offset = (cropped_w * scale - W) / 2
    x = (x_orig - CHALKBOARD_CROP_LEFT) * scale - left_offset
    y = y_orig * scale + top_pad
    return x, y


# WHY 실측 상수 — 분필 받침대(2026-08-02, "나무받침대랑 분필조각, 지우개까지 다
# 추가하고"): 칠판.png에서 판서면 아래로 튀어나온 나무 받침대(칠판이 서있는
# 스탠드)의 픽셀 범위를 스캔해서 구함 — 이 사진을 바꾸지 않는 한 다시 잴 필요
# 없음. 오른쪽은 코너 캐릭터 자리와 겹치니 왼쪽 절반만 쓴다.
_CHALKBOARD_TRAY_LEFT_ORIG = 310
_CHALKBOARD_TRAY_RIGHT_ORIG = 713
_CHALKBOARD_TRAY_TOP_ORIG = 869
_CHALKBOARD_TRAY_BOTTOM_ORIG = 923


def _doodle_chalk_stick() -> Image.Image:
    """둥근 필통형 분필 — 양끝을 둥글려서 사용감 있는 뭉툭한 느낌 + 몸통 위쪽
    하이라이트 선으로 원통 입체감을 준다.

    ⚠️ 처음엔 단순 `rounded_rectangle` 하나뿐이라 "너무 네모반듯"하다는 지적을
    받았다(2026-08-02, "분필이랑 지우개는 왜 그냥 흰색 네모로만 한거야?") — 실물
    사진을 API로 찾아서 배경 제거하는 것도 검토했지만 Pexels/Unsplash 후보가
    전부 라이프스타일 사진(사람 손 포함)이라 깨끗한 제품샷이 없었고, 배경 제거용
    ML 라이브러리(rembg)를 새로 설치해야 하는 데다 다른 낙서들의 손그림 선화
    톤과도 안 맞을 위험이 있어 — 사용자가 "선화 스타일은 괜찮을거같긴해"로
    확인해준 대로 PIL 도형 디테일만 보강하는 쪽으로 정리.

    ⚠️ v2(디테일만 보강, 크기는 그대로)로도 "너무 애매하다"는 지적이 이어져
    (2026-08-02, "그 지우개랑 분필 너무 애매한데 좀더 고도화못하니?") — 실제
    영상 스케일(휴대폰 화면 기준 이 낙서는 가로 50~60px 안팎)에서는 디테일을
    더해도 선이 가늘면 뭉개져 안 보이는 게 근본 원인이었다. 그래서 v3에서는
    몸체 크기를 40% 키우고(50x15 → 70x21) 선 두께도 3→4로 올려 다른 낙서들
      (`_stroke_shape` 계열, width=5)과 비슷한 굵기로 맞췄다 — 디테일보다
    "작은 화면에서도 형태가 바로 읽히는지"가 우선."""
    w, h = 70, 21
    size = (w + 12, h + 16)

    def draw(d, off, color):
        ox, oy = off
        x0, y0 = 6 + ox, 6 + oy
        x1, y1 = x0 + w, y0 + h
        d.rounded_rectangle([x0, y0, x1, y1], radius=h / 2, outline=color, width=4)
        d.line([(x0 + h * 0.6, y0 + 5), (x1 - h * 0.6, y0 + 5)], fill=color, width=3)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(shadow), (3, 3), (0, 0, 0, 90))
    base = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(base), (0, 0), (255, 255, 255, 255))
    return Image.alpha_composite(shadow, base)


def _doodle_eraser() -> Image.Image:
    """나무받침+펠트 패드 지우개 — 위쪽은 둥근 나무 블록(호 모양), 아래쪽은 펠트
    질감을 암시하는 짧은 세로선 4~5개로 표현해 단순 사각형보다 훨씬 그것답게
    읽힌다(위 `_doodle_chalk_stick` docstring과 같은 이유로 선화 스타일 유지).

    ⚠️ v3에서 분필과 같은 이유로 40% 확대(60x30 → 84x42) + 선 두께 3→4로
    보강 — 작은 화면에서 나무받침/펠트 구분이 더 또렷하게 보인다."""
    w, h = 84, 42
    size = (w + 12, h + 12)

    def draw(d, off, color):
        ox, oy = off
        x0, y0 = 6 + ox, 6 + oy
        x1, y1 = x0 + w, y0 + h
        felt_y = y0 + h * 0.62
        d.arc([x0, y0, x1, y0 + h * 0.5], 180, 360, fill=color, width=4)
        d.line([(x0, y0 + h * 0.25), (x0, felt_y)], fill=color, width=4)
        d.line([(x1, y0 + h * 0.25), (x1, felt_y)], fill=color, width=4)
        d.line([(x0, felt_y), (x1, felt_y)], fill=color, width=4)
        d.arc([x0, felt_y - h * 0.15, x1, y1 + h * 0.15], 10, 170, fill=color, width=4)
        for i in range(5):
            fx = x0 + 10 + i * (w - 20) / 4
            d.line([(fx, felt_y + 3), (fx, y1 - 4)], fill=color, width=3)

    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(shadow), (3, 3), (0, 0, 0, 90))
    base = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(base), (0, 0), (255, 255, 255, 255))
    return Image.alpha_composite(shadow, base)


def _place_chalk_doodle(canvas: Image.Image, seed: str, top_pad: int, per_corner: int = 4,
                         skip_right: bool = False, lang: str = "kor",
                         topic_word: str | None = None) -> Image.Image:
    """칠판을 옛날 교실 감성으로 장식한다(2026-08-02, "좀 많았으면 하는데...
    화려하게 갔으면 싶어" + "나무받침대랑 분필조각, 지우개까지 다 추가하고
    급훈 문구가... 도형도 이것저것 엄청 넣어보자" → "이빠이넣어 귀여운 것들
    제발좀... 나중에 중복 이미지가 아니게 보이게 할때도 좋을 것 같단말이야"):

    ⚠️ **파츠 자체의 등장 여부도 랜덤**(2026-08-03, "칠판에 있는 파츠들은
    랜덤으로 들어가게 해달라했는데 걍 다 들어간상태로만 영상을 제작하는듯?
    그리고 파츠들 더 다양화 하는게 좋을 것 같아"): 처음엔 도형 종류만 랜덤이고
    "어떤 파츠가 뜨는지"(급훈/명패/분필받침대) 자체는 매 영상 고정으로 전부
    나왔다 — 아래 각 파츠 항목에 등장 확률/풀을 표시해뒀다. 자막·캐릭터와 안
    겹치는 "안전지대" 자리 자체는 그대로 유지하되, 그 자리를 채울지 말지와
    무엇으로 채울지를 seed로 결정적 랜덤화한다.

    - **양쪽 위 모서리**: `_DOODLES` 중 서로 다른 도형을 한쪽당 2~`per_corner`개
      (topic마다 다름)씩 흩뿌린다(겹침 방지 재시도 포함). 자막은 판서면 세로
      중앙, 캐릭터는 항상 오른쪽 아래에 나오므로 위쪽 양 모서리는 topic 길이·
      자막 줄 수와 무관하게 절대 안 겹치는 유일한 안전 지대다.
    - **양옆 여백(중간 높이)**: 자막(`_make_chalk_caption_png`, `max_width=940`,
      캔버스 W=1080 기준 중앙 정렬)은 아무리 길어도 좌우로 최소 ~50px는 항상
      비워둔다 — 그 얇은 띠(가장자리에서 45px 폭)에 작은 낙서를 0~3개
      흩뿌린다(0개면 이 구간은 그냥 빈 채로 남음). 위쪽 모서리 클러스터보다
      한참 아래(위 클러스터 바닥 + 60px)부터 시작해서 우상단 아이템 라벨
      (있을 때)과도, 하단 캐릭터(오른쪽 아래)와도 절대 안 겹치게 세로 범위를
      짧게(`_SIDE_MARGIN_BAND_H`) 제한한다.
    - **위쪽 중앙**: "급훈" 액자(70% 확률) — topic 폴더명에서 뽑은 한 단어
      요약(`_topic_word_from_seed`)을 이중 테두리 액자에 넣는다. 양쪽 모서리
      낙서 사이 빈 공간이라 여기도 자막·캐릭터와 안 겹친다.
    - **왼쪽 아래**: `_NAMEPLATE_POOL`(떠든 사람/주번/우유 당번/칠판 당번/
      오늘의 발표자/청소 당번, 6종)에서 0~2개를 골라 쌓아 올린다 — 캐릭터가
      항상 오른쪽 아래를 차지해서 왼쪽 아래가 유일하게 비어있는 하단 모서리다.
      판서면 맨 아래쪽 끝에 붙여서 세로 중앙의 자막과는 안 겹치게 한다.
    - **분필 받침대**(70% 확률): 판서면 아래 나무 받침대 위에 분필 조각 2~3개 + 지우개를
      얹는다 — 받침대의 왼쪽 절반만 쓴다(오른쪽은 코너 캐릭터 자리라 어차피
      캐릭터에 가려짐).

    WHY seed로 topic을 쓰는지: 같은 topic을 재조립해도 매번 낙서·명패·급훈
    단어가 안 바뀌게(재현 가능) — card_news.py의 _photo_backdrop과 같은 패턴.

    WHY skip_right(2026-08-02, 다른 세션과의 git race 커밋 메시지에서 직접
    경고됨: "그 아이템 라벨도 칠판 우상단에 들어가는 것으로 보여서... 배치가
    겹칠 가능성이 있음"): motion_schedule로 캐릭터가 여러 명 번갈아 나오는
    topic은 assemble()이 칠판 우상단에 현재 아이템 아이콘+이름 라벨을 이미
    그리므로, 오른쪽 낙서 클러스터를 얹으면 같은 자리에서 겹친다 — 이 경우
    오른쪽은 스킵하고 왼쪽 낙서만 남긴다(휑함 방지 역할은 라벨이 대신함).

    ⚠️ **유튜브 쇼츠 플레이어 자체 UI와도 안 겹쳐야 함**(2026-08-02, 실제
    모바일 스크린샷으로 확인: "모바일에서 보니까 전체적으로 화면이 작아져서
    양옆에 아이콘들이 잘리네?"): 캡션 안전 여백(위 `_SIDE_MARGIN_W` 계산)은
    "우리 자막과 안 겹치는지"만 따진 거라 유튜브 자체 좋아요/댓글/공유 아이콘
    열(오른쪽 가장자리)이나 채널명·설명 캡션 띠(맨 아래)와는 무관했다 —
    실제로 그 자리에 플랫폼 UI가 항상 떠 있어서 오른쪽 끝·맨 아래 배치는
    작품 안에서는 안전해도 실제 재생 화면에서는 가려지거나 겹쳐 보인다.
    `_YT_SAFE_RIGHT`(오른쪽 150px)/`_YT_SAFE_BOTTOM`(아래쪽 320px, 화면
    비율 기준 대략적 추정치 — 기기·버전마다 UI 위치가 조금씩 다르므로 여유
    있게 잡음) 밖으로 모든 배치를 밀어낸다. 오른쪽 끝 얇은 띠(캡션 안전 45px)
    는 계산해보면 플랫폼 안전 영역(오른쪽 150px)과 아예 안 겹치는 지점이
    없어서(캡션이 비는 자리 자체가 플랫폼 아이콘 열 안쪽이라) 오른쪽 양옆
    여백 띠 낙서는 포기하고 왼쪽만 그린다."""
    rng = random.Random(seed)
    green_top_canvas = round(_chalkboard_orig_to_canvas(0, _CHALKBOARD_GREEN_TOP_ORIG, top_pad)[1])
    green_bottom_canvas = round(_chalkboard_orig_to_canvas(0, _CHALKBOARD_GREEN_BOTTOM_ORIG, top_pad)[1])
    _YT_SAFE_RIGHT = 150
    _YT_SAFE_BOTTOM = 320

    zone_w, zone_h, top_gap = 260, 300, 20
    sides = ("left",) if skip_right else ("left", "right")
    for side in sides:
        # WHY per_corner를 고정 개수로 안 쓰는지(2026-08-03, 파츠 다양화 요청):
        # 이 자리 자체(그리는지 여부)는 배치 안전지대라 항상 유지하되, 몇 개가
        # 뿌려지는지는 topic마다 2~per_corner개로 흔들어서 화면 밀도가 매번
        # 달라 보이게 한다.
        side_count = rng.randint(2, max(per_corner, 2))
        shapes = rng.sample(_DOODLES, min(side_count, len(_DOODLES)))
        placed: list[tuple[float, float]] = []
        for fn in shapes:
            lx = ly = 0
            doodle = None
            for _attempt in range(12):
                size = rng.randint(50, 85)
                doodle = fn().resize((size, size))
                angle = rng.uniform(-18, 18)
                doodle = doodle.rotate(angle, expand=True, resample=Image.BICUBIC)
                lx = rng.randint(0, max(zone_w - doodle.width, 0))
                ly = rng.randint(0, max(zone_h - doodle.height, 0))
                cx, cy = lx + doodle.width / 2, ly + doodle.height / 2
                if all(((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 > 48 for px, py in placed):
                    placed.append((cx, cy))
                    break
            x = 25 + lx if side == "left" else W - _YT_SAFE_RIGHT - zone_w + lx
            y = green_top_canvas + top_gap + ly
            canvas.alpha_composite(doodle, (x, y))

    # 양옆 여백 띠 — WHY 왼쪽만 그리는지: 위 클래스 docstring 참고(오른쪽은
    # 캡션 안전 지점과 플랫폼 UI 안전 지점이 아예 안 겹쳐서 그릴 자리가 없음).
    # WHY 별도 로직인지: 위 모서리 클러스터(zone_w=260)보다 훨씬 얇은 폭
    # (가장자리 45px)이라 같은 겹침 방지 로직을 재사용하면 큰 도형이 자꾸
    # 재시도 실패한다 — 여기 전용으로 작은 사이즈(28~42px)만 쓴다.
    _SIDE_MARGIN_W = 45
    _SIDE_MARGIN_BAND_H = 260
    margin_top = green_top_canvas + top_gap + zone_h + 60
    # WHY 0을 허용하는지(2026-08-03, 파츠 다양화 요청): 예전엔 항상 2~3개가
    # 나와서 이 띠가 한 번도 빈 적이 없었다 — 가끔은 아예 안 나오는 topic도
    # 있어야 "매번 다 채워진 느낌"이 덜하다.
    count = rng.randint(0, 3)
    placed_y: list[float] = []
    for _ in range(count):
        size = rng.randint(28, 42)
        doodle = rng.choice(_DOODLES)().resize((size, size))
        angle = rng.uniform(-20, 20)
        doodle = doodle.rotate(angle, expand=True, resample=Image.BICUBIC)
        for _attempt in range(10):
            ly = rng.randint(0, max(_SIDE_MARGIN_BAND_H - doodle.height, 0))
            if all(abs(ly - py) > 45 for py in placed_y):
                placed_y.append(ly)
                break
        else:
            continue
        lx = rng.randint(0, max(_SIDE_MARGIN_W - doodle.width, 4))
        y = margin_top + ly
        canvas.alpha_composite(doodle, (lx, y))

    # WHY 70% 확률인지(2026-08-03, "걍 다 들어간상태로만 영상을 제작하는듯"):
    # 급훈은 항상 나오는 고정 파츠였다 — 가끔 빠지는 topic도 있어야 매번 같은
    # 골격으로 안 보인다.
    if rng.random() < 0.7:
        motto_word = topic_word if topic_word is not None else _topic_word_from_seed(seed)
        motto = _doodle_text_box(motto_word, font_size=48, double_border=True, lang=lang)
        canvas.alpha_composite(motto, (round((W - motto.width) / 2), green_top_canvas + top_gap + 10))

    # WHY 풀에서 0~2개를 뽑는지: 위 _NAMEPLATE_POOL 정의부 WHY 참고 — "떠든
    # 사람"+"주번" 고정 조합 대신 topic마다 다른 종류·개수의 명패가 뜨게 한다.
    # WHY lang으로 풀을 통째로 바꾸는지(2026-08-03, 글로벌 확장): 한국 교실
    # 당번 문화를 그대로 번역하면 어색해서, 언어별로 아예 다른 문화적으로
    # 자연스러운 풀(_NAMEPLATE_POOL_EN 등)을 쓴다 — 위 _NAMEPLATE_POOL_EN
    # 정의부 WHY 참고. 매핑에 없는 언어 코드는 영어 풀로 안전하게 폴백.
    nameplate_pool = _NAMEPLATE_POOL if lang == "kor" else _NAMEPLATE_POOL_BY_LANG.get(lang, _NAMEPLATE_POOL_EN)
    n_plates = rng.randint(0, 2)
    chosen = rng.sample(nameplate_pool, min(n_plates, len(nameplate_pool)))
    plates = []
    for fmt, name_count, font_size in chosen:
        names = ", ".join(_anon_name(rng, lang) for _ in range(name_count))
        plates.append(_doodle_text_box(fmt.format(names), font_size=font_size, lang=lang))
    # WHY min(...)인지: 원래는 green_bottom_canvas(판서면 실측 하단) 기준으로만
    # 붙였는데, 유튜브 쇼츠 플레이어의 채널명·설명 캡션 띠가 화면 맨 아래
    # _YT_SAFE_BOTTOM(320px)만큼을 항상 가려서 실기기에서는 이 명패 글자가
    # 그 띠와 겹쳐 읽기 힘들었다 — 두 기준 중 더 위쪽(작은 y)을 쓴다.
    stack_bottom = min(green_bottom_canvas - 30, H - _YT_SAFE_BOTTOM)
    y_cursor = stack_bottom
    for plate in plates:
        y_cursor -= plate.height
        canvas.alpha_composite(plate, (30, y_cursor))
        y_cursor -= 12

    tray_top_x, tray_top_y = _chalkboard_orig_to_canvas(_CHALKBOARD_TRAY_LEFT_ORIG, _CHALKBOARD_TRAY_TOP_ORIG, top_pad)
    tray_bottom_x, _ = _chalkboard_orig_to_canvas(_CHALKBOARD_TRAY_RIGHT_ORIG, _CHALKBOARD_TRAY_BOTTOM_ORIG, top_pad)
    tray_mid_x = (tray_top_x + tray_bottom_x) / 2  # 오른쪽 절반은 코너 캐릭터 자리라 왼쪽 절반만 사용
    tray_y = round(tray_top_y) + 6

    # WHY 70% 확률인지: 분필 받침대도 급훈과 같은 이유로 고정 파츠였다 — 가끔
    # 빈 받침대인 topic도 섞는다.
    if rng.random() < 0.7:
        eraser = _doodle_eraser()
        eraser_x = round(tray_top_x) + 20
        canvas.alpha_composite(eraser, (eraser_x, tray_y))
        chalk_x = eraser_x + eraser.width + 14
        for i in range(rng.randint(2, 3)):
            chalk = _doodle_chalk_stick()
            angle = rng.uniform(-15, 15)
            chalk = chalk.rotate(angle, expand=True, resample=Image.BICUBIC)
            cx = chalk_x + i * (chalk.width - 6)
            if cx + chalk.width > tray_mid_x:
                break
            canvas.alpha_composite(chalk, (cx, tray_y + rng.randint(-4, 4)))
    return canvas


def _is_static_image(path: str) -> bool:
    """모션 mp4 대신 정지 illust jpg/png를 코너 캐릭터로 그대로 쓰는 경로인지
    확장자로 판별한다(2026-08-05, 모션 생성 중단 확정). 확장자만 보는 단순
    판별이라 충분 — 이 프로젝트 캐릭터 자산은 모션은 항상 .mp4, 정지 이미지는
    항상 .jpg/.png로 고정된 관례를 따른다."""
    return Path(path).suffix.lower() in (".jpg", ".jpeg", ".png")


def _build_character_segment(motion_path: str, duration: float, out_path: Path, bg_color: str = "0xFFFFFF",
                              flip: bool = False):
    """_build_character_loop의 단일 세그먼트 버전 — 캐릭터 여러 명이 구간별로
    번갈아 나오는 _build_character_schedule에서 재사용한다.

    WHY flip 파라미터(2026-08-03, "매번 똑같은 5초 루프 반복이 아니라 모션·구도에
    변주 주기" — 유튜브 "자동화된 대량생산" 스팸 정책 리스크 완화 목적): 같은
    캐릭터 에셋(예: 커피_motion.mp4)이 여러 topic·여러 영상에 그대로 재사용되면
    영상마다 코너 장면이 픽셀 단위로 똑같아 보인다 — "미세한 변경만 준 대량생산"
    신호를 줄이려고 좌우 반전 옵션을 추가했다. 캐릭터 원화가 좌우 비대칭이 아니라서
    반전해도 어색하지 않다(글자·로고 없는 순수 캐릭터 일러스트 규칙과 일치)."""
    similarity = "0.03" if bg_color.upper() == "0XFFFFFF" else "0.15"
    despill = ""
    if bg_color.upper() == "0X00FF00":
        despill = "despill=type=green:mix=1.0:expand=0,"
    elif bg_color.upper() == "0X0000FF":
        despill = "despill=type=blue:mix=1.0:expand=0,"
    flip_filter = "hflip," if flip else ""
    if _is_static_image(motion_path):
        # WHY: _build_character_loop의 정지 이미지 분기와 동일한 이유(2026-08-05,
        # 모션 생성 중단 확정) — ping-pong 없이 colorkey만 한 번 적용.
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", motion_path, "-t", f"{duration}",
             "-vf", f"{flip_filter}colorkey={bg_color}:{similarity}:{similarity},{despill}format=argb,"
                    "lut=a='if(gt(val\\,16)\\,255\\,0)'",
             "-c:v", "qtrle", str(out_path)],
            check=True, capture_output=True,
        )
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        keyed = tmp_path / "keyed.mov"
        subprocess.run(
            ["ffmpeg", "-y", "-i", motion_path,
             "-vf", f"{flip_filter}colorkey={bg_color}:{similarity}:{similarity},{despill}format=argb,"
                    "lut=a='if(gt(val\\,16)\\,255\\,0)'",
             "-c:v", "qtrle", str(keyed)],
            check=True, capture_output=True,
        )
        reversed_ = tmp_path / "reversed.mov"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(keyed), "-vf", "reverse", "-c:v", "qtrle", str(reversed_)],
            check=True, capture_output=True,
        )
        pingpong = tmp_path / "pingpong.mov"
        list_path = tmp_path / "pp_list.txt"
        list_path.write_text(f"file '{keyed.resolve()}'\nfile '{reversed_.resolve()}'")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c", "copy", str(pingpong)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(pingpong),
             "-t", f"{duration}", "-c:v", "qtrle", str(out_path)],
            check=True, capture_output=True,
        )


def _build_character_schedule(
    schedule: list[tuple[float, float, str]] | list[tuple[float, float, str, str]],
    total_duration: float, out_path: Path, bg_color: str = "0xFFFFFF", flip: bool = False,
):
    """캐릭터 여러 명이 구간별로 번갈아 나오는 캐릭터 트랙. WHY(2026-07-31, 수면음식_1
    — 대추/체리/호두 세 캐릭터가 각자 자기 대사 구간에만 나와야 하는데
    _build_character_loop은 캐릭터 1개를 전체 길이에 반복하는 구조라 못 씀):
    schedule의 각 구간마다 _build_character_segment로 개별 캐릭터 트랙을 만들고
    concat으로 이어붙인다. 각 세그먼트는 독립적으로 ping-pong 처리되므로 세그먼트
    경계에서도 포즈가 끊기지 않는다.

    WHY 세그먼트별 bg_color(2026-08-01, 갑상선방해음식_1/위장더부룩음식_1에서 실제
    발생 — 초록 계열 캐릭터라 파란 배경으로 생성한 일러스트(케일·페퍼민트차 등)가
    섞인 topic에서 전체에 초록 colorkey를 쓰면 파란 배경이 그대로 남는 사고): 튜플이
    (start, end, path) 3개면 전역 bg_color를, (start, end, path, bg_color) 4개면
    그 세그먼트 전용 색을 쓴다 — 캐릭터마다 크로마키 색이 다른 topic(초록/파랑 섞임)을
    지원하기 위함.

    WHY flip이 topic 전체에 하나로 적용되는지(2026-08-03): 세그먼트마다 다르게 주면
    같은 캐릭터가 한 영상 안에서 좌우가 계속 바뀌어 오히려 산만하다 — topic
    하나당 한 번만 결정해서 영상 전체에 일관되게 적용한다."""
    schedule = sorted(schedule, key=lambda x: x[0])
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        seg_paths = []
        for i, entry in enumerate(schedule):
            start, end, motion_path = entry[0], entry[1], entry[2]
            seg_bg_color = entry[3] if len(entry) > 3 else bg_color
            dur = min(end, total_duration) - start
            if dur <= 0.02:
                continue
            seg = tmp_path / f"char_seg_{i:03d}.mov"
            _build_character_segment(motion_path, dur, seg, bg_color=seg_bg_color, flip=flip)
            seg_paths.append(seg)
        list_path = tmp_path / "char_list.txt"
        list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_paths))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )


def _build_character_loop(motion_path: str, total_duration: float, out_path: Path, bg_color: str = "0xFFFFFF",
                           flip: bool = False):
    """Kling 모션 클립(단색 배경)에서 배경을 알파로 빼고, 대사 길이만큼 반복시킨
    알파 채널 영상(qtrle mov)을 만든다. 대사 타이밍과 동기화하지 않고 그냥 반복.

    WHY flip(2026-08-03): _build_character_segment와 같은 이유 — 같은 캐릭터
    에셋이 여러 topic·영상에 재사용될 때 픽셀 단위로 완전히 똑같아 보이지 않게
    좌우 반전 옵션을 준다.

    WHY 정방향+역방향(ping-pong) 이어붙이기: Kling이 생성한 클립은 시작 포즈와
    끝 포즈가 같다는 보장이 없어서, 그냥 -stream_loop로 반복하면 루프 지점마다
    포즈가 툭 끊기는 느낌이 난다(2026-07-30 확인). 정방향 재생 뒤 바로 역방향
    재생을 이어붙이면 마지막 프레임이 항상 첫 프레임으로 대칭 복귀하므로,
    프롬프트가 끝-시작을 맞춰주길 기대하지 않아도 구조적으로 끊김이 없다.

    WHY bg_color 파라미터화 + 초록 배경 권장(2026-07-30): 흰 배경은 캐릭터 얼굴의
    밝은 하이라이트(이마·볼)까지 "흰색에 가깝다"고 오인해서 threshold를 아주 좁게
    잡아야만 했다(0.03) — 그래도 여전히 위험한 여지가 있다. gemini_illust.py의
    STYLE_PROMPT를 초록 배경(#00FF00)으로 바꿔뒀으니, 그 프롬프트로 새로 만든
    캐릭터는 bg_color="0x00FF00"로 넉넉한 threshold(0.15 정도)를 써도 안전하다.
    기존 흰 배경 캐릭터(예: 돼지감자)는 기본값 그대로 좁은 threshold 유지.

    WHY alpha 이분법 처리(2026-07-30): colorkey가 threshold 안쪽 픽셀도 완전
    불투명이 아니라 부분투명(반쯤 섞인 alpha)으로 만드는 경우가 있는데, 이게
    280px로 축소되는 코너 장면에서 배경(초록 잎)이 캐릭터 얼굴에 얼룩덜룩
    비쳐 보이는 원인이었다(세션 내내 "눈 왜곡"으로 오인했던 문제의 진짜 정체 —
    Kling 생성 결과가 아니라 이 로컬 합성 단계의 버그였음). lut=a로 alpha를
    16 기준 완전 불투명(255) 아니면 완전 투명(0)으로 강제해서 부분투명을 없앤다.

    WHY despill(2026-07-31): alpha 이분법 처리는 "안/밖"만 정하지, 안쪽으로 판정된
    가장자리 픽셀 자체의 색(그린 스크린 촬영/렌더링에서 늘 발생하는 초록 스필)은
    그대로 남는다 — 그 결과 캐릭터 테두리에 초록 형광 라인이 둘러진 것처럼 보였다
    (0x00FF00 배경으로 처음 실사용한 v8 클립에서 확인). despill로 가장자리의 잔여
    초록기를 억제한다. despill 필터는 초록/파랑만 지원해서 그 두 색일 때만 적용.

    WHY format=argb (yuva420p 아님, 2026-07-31): qtrle 인코더는 rgb24/rgb555be/argb/
    gray만 지원한다(yuva420p 미지원) — yuva420p로 지정해도 qtrle 인코딩 시 결국 argb로
    재변환되므로, 처음부터 qtrle가 실제로 쓰는 argb를 직접 지정해 불필요한 왕복 변환을
    없앤다.

    ⚠️ WHY .upper() 비교를 대문자 리터럴과 하는지: `bg_color.upper()`는 "0x00FF00"의
    "x"까지 "X"로 바꿔버려서 "0x00FF00"(소문자 x) 리터럴과 절대 같아질 수 없다 —
    이 버그 때문에 despill 분기가 조용히 한 번도 실행된 적이 없었다(2026-07-31,
    초록 배경 v8 클립에서 형광 초록 테두리가 안 없어지던 진짜 원인 — despill을
    mix=1.0까지 올려도 전혀 효과가 없었던 이유). 비교 대상 리터럴도 항상 .upper()로
    맞춰서 이 클래스의 버그가 재발하지 않게 한다."""
    similarity = "0.03" if bg_color.upper() == "0XFFFFFF" else "0.15"
    despill = ""
    if bg_color.upper() == "0X00FF00":
        despill = "despill=type=green:mix=1.0:expand=0,"
    elif bg_color.upper() == "0X0000FF":
        despill = "despill=type=blue:mix=1.0:expand=0,"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        flip_filter = "hflip," if flip else ""
        if _is_static_image(motion_path):
            # WHY(2026-08-05): 모션 생성 자체를 중단하기로 확정("이제 그냥 모션을
            # 생성하지 않는거로 하자") — Kling도 build_static_motion_loop도 더
            # 안 쓰고, 캐릭터 정지 일러스트(jpg)를 그대로 코너에 얹는다. 핑퐁
            # 반복 루프는 애초에 "움직임을 매끄럽게 되돌리기" 위한 처리라
            # 정지 이미지에는 무의미 — colorkey만 한 번 적용해서 정지 화면을
            # 그대로 duration만큼 유지한다(ping-pong/reverse/loop 단계 생략,
            # ffmpeg 호출 수가 4번에서 1번으로 줄어 처리도 훨씬 빠름).
            subprocess.run(
                ["ffmpeg", "-y", "-loop", "1", "-i", motion_path, "-t", f"{total_duration}",
                 "-vf", f"{flip_filter}colorkey={bg_color}:{similarity}:{similarity},{despill}format=argb,"
                        "lut=a='if(gt(val\\,16)\\,255\\,0)'",
                 "-c:v", "qtrle", str(out_path)],
                check=True, capture_output=True,
            )
            return
        keyed = tmp_path / "keyed.mov"
        subprocess.run(
            ["ffmpeg", "-y", "-i", motion_path,
             "-vf", f"{flip_filter}colorkey={bg_color}:{similarity}:{similarity},{despill}format=argb,"
                    "lut=a='if(gt(val\\,16)\\,255\\,0)'",
             "-c:v", "qtrle", str(keyed)],
            check=True, capture_output=True,
        )
        reversed_ = tmp_path / "reversed.mov"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(keyed), "-vf", "reverse", "-c:v", "qtrle", str(reversed_)],
            check=True, capture_output=True,
        )
        pingpong = tmp_path / "pingpong.mov"
        list_path = tmp_path / "pp_list.txt"
        list_path.write_text(f"file '{keyed.resolve()}'\nfile '{reversed_.resolve()}'")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c", "copy", str(pingpong)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(pingpong),
             "-t", f"{total_duration}", "-c:v", "qtrle", str(out_path)],
            check=True, capture_output=True,
        )


def make_gradient_bg(out_path: Path, top=(253, 249, 245), bottom=(246, 237, 230)):
    """실사진이 없는 topic용 배경. WHY(2026-07-31, 수면음식_1): 캐릭터 일러스트(크로마키
    배경 포함)를 실수로 --images 자리에 넣으면 초록 배경이 그대로 노출되는 사고가 났다
    — 실사진이 없을 땐 카드뉴스와 같은 톤의 단색 그라디언트를 배경으로 쓴다."""
    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=color)
    img.save(out_path, quality=95)


CHALKBOARD_PHOTO_PATH = str(Path(__file__).resolve().parent.parent / "assets_library" / "backgrounds" / "칠판.png")
_BACKGROUNDS_DIR = Path(__file__).resolve().parent.parent / "assets_library" / "backgrounds"
# WHY 칠판 색상 변형 풀(2026-08-03, 글로벌 확장 대비 "칠판 색상도 색상인데
# 프레임 색상같은것도 변형을 주는 부분이 될 수 있겠고"): 원본 칠판.png 사진을
# 그대로 색상(HSV hue)만 리컬러해서 만든 변형들 — 캐릭터 배경색 리컬러
# (gemini_illust.recolor_background)와 같은 원리로, **좌표(크롭·트레이·판서면
# 위치)는 전부 그대로 재사용 가능**하다(사진 구도 자체는 안 바뀌고 색만 바뀌므로).
# Gemini로 완전히 새 사진을 생성하지 않은 이유도 이것 — 새 사진마다 구도가
# 달라지면 CHALKBOARD_CROP_LEFT/RIGHT 등 실측 좌표를 매번 다시 재야 한다.
# ⚠️ 흰색 프레임 조합은 제외함 — 아래 board_alpha 로직이 "RGB 전부 225 이상이면
# 배경으로 간주해 투명 처리"하는 흰색 키잉을 쓰는데, 프레임을 흰색으로 칠하면
# 프레임 자체가 투명 처리돼서 뒤쪽 실사진이 비쳐 보이는 사고가 난다.
CHALKBOARD_VARIANTS = [
    CHALKBOARD_PHOTO_PATH,  # 원본(초록 판서면 + 밝은 나무 프레임)
    str(_BACKGROUNDS_DIR / "칠판_검정_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_검정_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_진초록_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_진초록_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_남색_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_초록_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_초록_검정.png"),
    # WHY 8개 추가(2026-08-04, "파츠 좀 늘려줄래... 칠판도 전반적으로 챙겨봐"):
    # 기존 팔레트를 넘어서는 새 보드색(보라/청록/갈색/회색) + 안 쓰였던 프레임
    # 조합 추가. 흰색 프레임 조합은 위 board_alpha 흰색 키잉 사고 때문에 계속 제외.
    str(_BACKGROUNDS_DIR / "칠판_보라_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_보라_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_청록_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_청록_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_갈색_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_회색_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_남색_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_진초록_검정.png"),
    # WHY 8개 추가(2026-08-04, "더해도 좋아" — 위 배치에 이어 2차 확장): 새 보드색
    # 와인(맨지/버건디 톤) 추가 + 그동안 비어있던 색상 교차 조합(갈색×검정,
    # 회색×검정, 청록/보라×밝은나무, 남색×어두운나무) 채움.
    str(_BACKGROUNDS_DIR / "칠판_와인_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_와인_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_와인_어두운나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_갈색_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_회색_검정.png"),
    str(_BACKGROUNDS_DIR / "칠판_청록_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_보라_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_남색_어두운나무.png"),
    # WHY 2개 추가(2026-08-04, "계속계속 쭉쭉 더만들어" — 3차 확장): 보드×프레임
    # 3x9 그리드에서 남은 빈칸 중 대비가 충분한 것만 채움. 갈색×어두운나무는
    # 둘 다 갈색 계열이라 프레임·판서면 경계가 잘 안 보여서 제외했다(생성은
    # 해봤지만 라이브러리에 넣지 않음 — 흰색 프레임 제외와 같은 이유).
    str(_BACKGROUNDS_DIR / "칠판_검정_밝은나무.png"),
    str(_BACKGROUNDS_DIR / "칠판_회색_어두운나무.png"),
]


def pick_chalkboard_variant(seed: str) -> str:
    """topic별 seed로 결정적 랜덤 선택 — 같은 topic을 재조립해도 매번 같은 색
    조합이 나오게 한다(_place_chalk_doodle과 같은 패턴)."""
    return random.Random(seed).choice(CHALKBOARD_VARIANTS)


# WHY 실측 좌표를 상수로 고정(2026-08-02): 실물 칠판 사진(assets_library/backgrounds/
# 칠판.png, 1024x1024)의 좌우 흰 여백을 나무 프레임 가장자리까지 잘라내기 위해
# 픽셀을 직접 스캔해서 찾은 값 — "나무 프레임이 가로 폭에 딱맞게" 요청 반영.
# 이 배경 이미지 자체를 바꾸지 않는 한 다시 측정할 필요 없음. ⚠️ 이 좌표는 색상
# 변형(CHALKBOARD_VARIANTS)에도 그대로 적용됨 — 사진 구도가 동일하기 때문.
CHALKBOARD_CROP_LEFT = 65
CHALKBOARD_CROP_RIGHT = 962
# WHY CHALKBOARD_ZOOM(2026-08-02, "가로는 완전 꽉차게 더 크게... 위아래로 더 키워서
# 아래 일러스트랑 위 글자 직전까지"): 위 CROP_LEFT/RIGHT만으로 너비를 캔버스에 맞추면
# 좌우에 흰 여백이 살짝 남고 세로도 다 못 채운다 — 크롭 폭보다 더 확대한 뒤 가로만
# 캔버스 폭(W)으로 중앙 크롭하면, 같은 배율로 세로도 함께 커져서 두 요청을 동시에
# 만족한다(가로는 여백 없이 꽉 참, 세로는 비례해서 더 커짐).
CHALKBOARD_ZOOM = 1.35
# WHY 위/아래 여백은 안 자르는지: 사진 원본의 흰 위쪽 여백엔 제목 배너가,
# 아래쪽 여백엔 캐릭터가 들어갈 자리라 그대로 살려둔다(사용자 요청) — 다만 세로로
# 늘려서 캔버스(1920)를 다 채우면 위아래 여백이 부족해서, 늘리는 대신 같은 톤의
# 흰색으로 캔버스 크기까지 패딩한다.
CHALKBOARD_BG_FILL = (248, 248, 248)
# WHY CHALKBOARD_CONTENT_BOTTOM(2026-08-02, "칠판을 아예 맨 아래까지 내리고... 그
# 어떤 필요로 하는 아이템을... 넣는게"): 칠판 사진 원본(1024 세로)은 나무 받침대
# 아래로 흰 촬영 배경이 ~924px까지 이어지는데, photo_bg_img 합성 시 이 흰 여백이
# 투명 처리되면서 실사진이 그대로 비쳐 보였다 — "도움이 되는 항목이 화면에 안
# 뜬다"는 지적대로, 이 틈이 정보 없이 방해만 됐다. 실측(픽셀 스캔)으로 프레임+
# 받침대가 끝나는 지점을 찾은 값 — 이 지점 아래는 칠판 톤 단색으로 채워서 판서면이
# 화면 맨 아래까지 이어지는 것처럼 보이게 한다(이 배경 이미지를 바꾸지 않는 한
# 다시 측정할 필요 없음).
CHALKBOARD_CONTENT_BOTTOM = 924
# WHY 폴백값으로만 남김(2026-08-02): 원래는 고정 상수로 썼지만, 상단 배너 높이가
# 제목 줄 수에 따라 달라져서(1줄 vs 2줄) 고정값이면 배너 아래로 흰 틈이 남거나
# 배너와 겹치는 경우가 생겼다 — assemble()이 실제 배너 높이(title_h)를
# _build_chalkboard_bg(top_pad=title_h)로 넘겨서 항상 배너 바로 아래부터 칠판이
# 시작하게 한다. top_pad를 명시하지 않고 이 함수를 단독 호출할 때만 이 기본값을 쓴다.
CHALKBOARD_TOP_PAD = 220


def _chalkboard_display_height() -> int:
    """실제 렌더링되는 칠판 사진의 세로 픽셀 높이(캔버스 폭 W, CHALKBOARD_ZOOM
    배율 적용 후) — _build_chalkboard_bg와 동일한 크롭/스케일 계산을 반복해서
    구한다. WHY 필요한지(2026-08-02, "글이 너무 아래로 쏠려있잖아 칠판 기준으로
    중앙으로"): 자막을 칠판 영역 안에서 세로 중앙 정렬하려면 칠판이 화면에서 실제로
    차지하는 세로 범위를 알아야 한다."""
    photo = Image.open(CHALKBOARD_PHOTO_PATH).convert("RGB")
    cropped_width = CHALKBOARD_CROP_RIGHT - CHALKBOARD_CROP_LEFT
    scale = (W / cropped_width) * CHALKBOARD_ZOOM
    return round(photo.height * scale)


def _build_chalkboard_bg(total_duration: float, out_path: Path, top_pad: int | None = None,
                          photo_bg_path: str | None = None, photo_bg_img: Image.Image | None = None,
                          doodle_seed: str | None = None, doodle_skip_right: bool = False,
                          board_photo_path: str | None = None, lang: str = "kor",
                          topic_word: str | None = None):
    """칠판 스타일 기본 배경(2026-08-02, 실물 칠판 사진으로 교체). 좌우 흰 여백을
    나무 프레임 가장자리까지 잘라서 프레임이 가로 폭에 꽉 차게 만들고, 위아래는
    원본 비율 그대로 살린 뒤 부족한 높이만큼 같은 톤의 흰색으로 패딩해서 캔버스를
    채운다 — 위쪽 흰 여백엔 제목 배너, 아래쪽 흰 여백엔 캐릭터가 들어간다.

    WHY 완전 정적(2026-08-02, "왜 칠판이 움직여 ;; 이제 칠판은 가만있고 자막만
    들어가면 되는거지"): 처음엔 실사진 배경과 통일감을 주려고 미세 zoompan을
    넣었는데, 칠판은 자막을 얹는 고정 판서면이라 배경 자체가 계속 확대되면
    산만하다는 피드백 — zoompan을 빼고 완전히 고정된 한 프레임을 총 길이만큼
    그대로 유지한다.

    WHY photo_bg_path + 흰색 키잉(2026-08-02, "칠판 뒤에 배경에 전체 화면을
    가득채우게 real에서 해당 아이템을 넣어... 흰색 공간이랑 글자같은거 뒤에서
    보일수 있게"): topic 대표 실사진을 캔버스 전체에 깔고, 칠판 사진 자체의 촬영
    배경(흰 벽/바닥 — 초록 판서면·나무 프레임 밖 여백)만 투명 처리해서 그 위에
    얹는다. 초록 판서면·나무 프레임은 흰색과 색이 뚜렷이 달라서 그대로 남고, 흰
    여백 자리에만 실사진이 비친다.

    ⚠️ **흰색 블렌드도, 블러도 뺌**(2026-08-02, "흰색 저게 그림을 가리고있는거같잖아
    ... opacity 안줘도 되겠다" → 블러 32도 여전히 "opacity 계속 넣네"로 재지적 →
    "opacity 아예 없애"): 처음엔 흰 배경과 80:20 블렌드를 넣었다가 뺐는데, 그 다음
    단계였던 강한 블러(card_news.py `_photo_backdrop`과 동일한 32)조차 사진을 흐릿하게
    만들어서 마치 opacity가 낮은 것처럼 보인다는 지적을 받았다 — 블러를 완전히 빼고
    실사진을 그대로(선명하게) 쓴다. 흐림 효과 자체를 이 배경에는 아예 쓰지 않는다.

    WHY photo_bg_img(2026-08-02, "배너랑 칠판 아래위 사진이 따로따로 짤려보이잖아"):
    배너(_make_title_png)와 여기가 각자 photo_bg_path로 독립적인 cover-crop을 하면
    서로 다른 배율/영역이 잘려서 이어지는 사진처럼 안 보인다 — assemble()이 캔버스
    전체(W x H) 크기로 미리 한 번만 만든 이미지를 photo_bg_img로 넘기면, 배너와
    여기 둘 다 같은 이미지의 위/아래 조각을 잘라 쓰게 되어 하나로 이어져 보인다.
    photo_bg_img가 있으면 photo_bg_path는 무시한다.

    WHY doodle_seed(2026-08-02, "파츠같은거 귀여운거 랜덤으로 칠판 모서리쪽에
    추가하는게 어떨까 싶어 너무 휑하고 별로야"): 판서면 위쪽 모서리에 작은 분필
    낙서를 하나 얹어서 빈 배경이 휑해 보이는 걸 덜어낸다 — `_place_chalk_doodle`
    참고. 안 주면(기존 호출부·테스트 호환) 낙서 없이 그대로."""
    with tempfile.TemporaryDirectory() as tmp:
        photo = Image.open(board_photo_path or CHALKBOARD_PHOTO_PATH).convert("RGB")
        cropped = photo.crop((CHALKBOARD_CROP_LEFT, 0, CHALKBOARD_CROP_RIGHT, photo.height))
        scale = (W / cropped.width) * CHALKBOARD_ZOOM
        resized = cropped.resize((round(cropped.width * scale), round(cropped.height * scale)))
        left = (resized.width - W) // 2
        resized = resized.crop((left, 0, left + W, resized.height))

        effective_top_pad = CHALKBOARD_TOP_PAD if top_pad is None else top_pad
        effective_top_pad = min(effective_top_pad, max(H - resized.height, 0))

        if photo_bg_img is not None or photo_bg_path:
            photo_full = photo_bg_img if photo_bg_img is not None else _cover_crop_subject(photo_bg_path, W, H)
            canvas = photo_full.convert("RGBA")

            r, g, b = resized.split()
            thresh = 225

            def _white_band(band):
                return band.point(lambda x: 255 if x >= thresh else 0)

            white_mask = ImageChops.multiply(ImageChops.multiply(_white_band(r), _white_band(g)), _white_band(b))
            board_alpha = ImageChops.invert(white_mask)
            resized_rgba = resized.convert("RGBA")
            resized_rgba.putalpha(board_alpha)
            canvas.alpha_composite(resized_rgba, (0, effective_top_pad))
            canvas = canvas.convert("RGB")

            # ⚠️ WHY 받침대 아래를 칠판 톤 단색으로 덮지 않는지(2026-08-02 되돌림):
            # 다른 세션이 "판서면이 화면 끝까지 이어지는 것처럼" 보이게 하려고 이
            # 구간을 CHALKBOARD_BOTTOM 단색으로 덮었었는데, 그러면서 "real 배경
            # 이미지는 칠판 외 영역에 전체로(끊기지 않고) 나와야 한다"는 요구사항과
            # 정면으로 충돌했다 — "칠판 아래위로 공간이... 왜 계속 다른거로
            # 채워지는지 모르겠네" 지적으로 실제 버그(단색 채움)가 확인됨. 이 구간도
            # board_alpha가 이미 투명 처리해뒀으므로 그대로 두면 실사진이 이어져서
            # 보인다 — 의도한 동작.
        else:
            canvas = Image.new("RGB", (W, H), CHALKBOARD_BG_FILL)
            canvas.paste(resized, (0, effective_top_pad))

        if doodle_seed:
            canvas = _place_chalk_doodle(canvas.convert("RGBA"), doodle_seed, effective_top_pad,
                                          skip_right=doodle_skip_right, lang=lang,
                                          topic_word=topic_word).convert("RGB")

        still = Path(tmp) / "chalkboard.jpg"
        canvas.save(still, quality=95)

        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", str(still), "-t", f"{total_duration}",
             "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)],
            check=True, capture_output=True,
        )


def _build_background(images: list[str], total_duration: float, out_path: Path, xfade_dur: float = 0.7):
    """실사진 슬라이드쇼 배경. WHY 크로스페이드: 이전엔 concat demuxer로 하드컷만
    이어붙여서 사진이 바뀔 때마다 뚝뚝 끊기는 느낌이었다(2026-07-30 피드백: "좀더
    끊김없이 계속 움직일 수 있도록") — xfade로 디졸브 전환을 넣어 계속 움직이는
    느낌을 유지한다. 이미지가 4장 정도로 적어서 xfade 체인이 성능 문제는 없음
    (수백 개 세그먼트를 잇는 것과는 다른 케이스 — cat-fight 교훈은 여기 해당 안 됨)."""
    n = len(images)
    seg_dur = (total_duration + (n - 1) * xfade_dur) / n if n > 1 else total_duration
    frames = max(int(seg_dur * FPS), 1)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        seg_paths = []
        for i, img in enumerate(images):
            seg = tmp_path / f"bg_{i:03d}.mp4"
            vf = (
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"zoompan=z='min(zoom+0.0010,1.10)':d={frames}:s={W}x{H}:fps={FPS}"
            )
            subprocess.run(
                ["ffmpeg", "-y", "-loop", "1", "-i", img, "-t", f"{seg_dur}",
                 "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
                check=True, capture_output=True,
            )
            seg_paths.append(seg)

        if n == 1:
            subprocess.run(["ffmpeg", "-y", "-i", str(seg_paths[0]), "-c", "copy", str(out_path)],
                            check=True, capture_output=True)
            return

        inputs = []
        for p in seg_paths:
            inputs += ["-i", str(p)]
        filters, prev = [], "0:v"
        for i in range(1, n):
            offset = i * (seg_dur - xfade_dur)
            out_label = f"vx{i}" if i < n - 1 else "vout"
            filters.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[{out_label}]")
            prev = out_label
        subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
             "-map", "[vout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)],
            check=True, capture_output=True,
        )


def _build_background_schedule(
    schedule: list[tuple[float, float, list[str]]], total_duration: float, out_path: Path,
):
    """배경 사진도 캐릭터처럼 구간별로 맞춰야 하는 경우(품목별 실사진이 있는 topic).
    WHY(2026-07-31, 당뇨유발음식_1 — 단팥빵 나레이션 구간에 찹쌀떡 사진이 나오는 문제
    발견): 기존 _build_background는 이미지 개수만큼 전체 길이를 균등 분할해서
    나레이션 타이밍과 무관하게 순서대로 보여줬다 — _build_character_schedule과
    동일한 패턴으로, 구간마다 그 구간에 맞는 사진(들)만 골라 별도로 배경을 만들고
    이어붙인다. 각 구간 안에 사진이 여러 장이면 그 구간 안에서만 크로스페이드된다."""
    schedule = sorted(schedule, key=lambda x: x[0])
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        seg_paths = []
        for i, (start, end, imgs) in enumerate(schedule):
            dur = min(end, total_duration) - start
            if dur <= 0.02:
                continue
            seg = tmp_path / f"bg_seg_{i:03d}.mp4"
            _build_background(imgs, dur, seg)
            seg_paths.append(seg)
        list_path = tmp_path / "bg_list.txt"
        list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_paths))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )


def _validate_assemble_glyphs(
    lang: str,
    title: str,
    title_card_text: str | None,
    end_card_text: str | None,
    ad_tag: bool,
    bg_style: str,
    motion_schedule: list[tuple[float, float, str]] | list[tuple[float, float, str, str]] | None,
    item_label_overrides: dict[str, str] | None,
    doodle_seed: str,
    topic_word: str | None,
    srt_path: str,
) -> None:
    """assemble()이 ffmpeg 호출을 시작하기 전에, 실제로 화면에 그려질 텍스트
    전부를 미리 검사한다(2026-08-14, health-shorts card_news.py의
    `_validate_spec_glyphs`와 같은 목적을 이 파일에 이식) — 여러 ffmpeg 단계를
    거친 뒤에야 자막이 tofu box로 깨진 걸 발견하면 그 전까지 만든 중간
    산출물이 낭비되고, 결국 사람이 영상을 끝까지 재생해봐야만 발견된다.

    이 파일은 폰트 체계가 두 갈래다 — title(`_title_font_for_lang`: 상단 배너·
    제목 카드·엔딩 카드·광고 태그)와 chalk(`_chalk_font_for_lang`: 칠판 자막·
    우상단 아이템 라벨·급훈 액자·명패, bg_style="chalkboard"일 때만). 어떤
    텍스트가 실제로 어느 폰트로 그려지는지는 assemble() 본문의 실제 호출부를
    그대로 따라간 것(`_make_title_png`/`_make_title_card_png`/
    `_build_ad_tag_badge` → title, `_make_chalk_caption_png`/
    `_make_item_label_png`/`_place_chalk_doodle` → chalk) — 잘못된 폰트로
    검사하면 오탐/누락이 생긴다."""
    _assert_title_glyph_coverage("상단 후킹 배너", title, lang)
    _assert_title_glyph_coverage("제목 카드(썸네일)", title_card_text or title, lang)
    _assert_title_glyph_coverage("엔딩 카드 CTA", end_card_text or DEFAULT_END_CARD_TEXT, lang)
    if ad_tag:
        _assert_title_glyph_coverage("광고 태그", AD_TAG_TEXT_BY_LANG.get(lang, "AD"), lang)

    # 자막: bg_style="chalkboard"면 _make_chalk_caption_png(chalk 폰트),
    # bg_style="photo"면 _make_caption_png(title 폰트) — assemble() 본문의
    # 실제 분기(4번 자막 굽기 단계)와 정확히 맞춰야 한다.
    caption_check = _assert_chalk_glyph_coverage if bg_style == "chalkboard" else _assert_title_glyph_coverage
    for _start, _end, text in _parse_srt(srt_path):
        caption_check(f"자막(원문: {text[:24]}{'...' if len(text) > 24 else ''})", text, lang)

    # 우상단 아이템 라벨(_make_item_label_png)은 bg_style과 무관하게 항상 chalk
    # 폰트 — motion_schedule로 캐릭터 여러 명이 번갈아 나올 때만 그려진다.
    if motion_schedule:
        for entry in motion_schedule:
            motion_p = Path(entry[2])
            base = motion_p.stem
            if base.endswith("_motion"):
                base = base[: -len("_motion")]
            name = (item_label_overrides or {}).get(base, base)
            _assert_chalk_glyph_coverage(f"칠판 우상단 아이템 라벨({name})", name, lang)

    # 급훈 액자·명패는 _place_chalk_doodle 안에서만(bg_style="chalkboard") 그려진다.
    # 실제로 어떤 명패 문구·이름이 topic-seeded 난수로 뽑히는지는 렌더링 전엔 알 수
    # 없으므로, 그 언어 풀 전체(뽑힐 수 있는 모든 문구·이름 후보)를 검사해서 무엇이
    # 뽑혀도 안전하게 한다.
    if bg_style == "chalkboard":
        motto_word = topic_word if topic_word is not None else _topic_word_from_seed(doodle_seed)
        _assert_chalk_glyph_coverage("칠판 급훈 액자", motto_word, lang)

        nameplate_pool = _NAMEPLATE_POOL if lang == "kor" else _NAMEPLATE_POOL_BY_LANG.get(lang, _NAMEPLATE_POOL_EN)
        for fmt, _name_count, _font_size in nameplate_pool:
            _assert_chalk_glyph_coverage(f"칠판 명패 문구({fmt})", fmt.replace("{}", ""), lang)
        name_candidates = (
            [f"{s}OO" for s in _SURNAMES] if lang == "kor"
            else _FIRST_NAMES_BY_LANG.get(lang, _FIRST_NAMES_EN)
        )
        for name in name_candidates:
            _assert_chalk_glyph_coverage("칠판 명패 이름", name, lang)


def assemble(
    images: list[str] | None,
    motion_path: str | None,
    audio_path: str,
    srt_path: str,
    out_path: str,
    title: str,
    # WHY 기본값 0(2026-07-31): "5초 뒤에 옮기지 말고 처음부터 우하단에" 피드백 이후
    # 이게 표준이 됐다 — 풀스크린 인트로가 필요한 특수한 경우에만 명시적으로 넘길 것.
    intro_duration: float = 0,
    ad_tag: bool = False,
    bg_color: str = "0xFFFFFF",
    title_card_duration: float = 1.3,
    title_card_text: str | None = None,
    title_card_char_path: str | None = None,
    # WHY motion_schedule(2026-07-31, 수면음식_1 — 대추/체리/호두 세 캐릭터가 각자
    # 대사 구간에만 나와야 함): [(start, end, motion_path), ...] 형태로 주면
    # motion_path 대신 이 스케줄로 캐릭터 트랙을 만든다. 시간은 나레이션(오디오) 기준
    # 0초부터 — assemble 내부에서 제목 카드만큼 알아서 밀어준다. motion_path와
    # motion_schedule 둘 다 없으면 에러, 둘 다 있으면 motion_schedule 우선.
    motion_schedule: list[tuple[float, float, str]] | None = None,
    # WHY image_schedule(2026-07-31, 당뇨유발음식_1 — 단팥빵 나레이션 구간에 찹쌀떡
    # 사진이 나오는 문제 발견): motion_schedule과 동일한 패턴. [(start, end,
    # [이미지경로,...]), ...]로 주면 images 대신 이 스케줄로 배경을 만든다 — 품목별
    # 실사진이 있는 topic(여러 캐릭터가 번갈아 나오는 topic)은 배경도 같이 맞출 것.
    image_schedule: list[tuple[float, float, list[str]]] | None = None,
    # WHY bg_style 기본값 "chalkboard"(2026-08-02): 실사진 배경이 밋밋하고 눈에 안
    # 띈다는 피드백으로 새 topic 기본값을 카드뉴스 톤 칠판 배경+분필체 자막으로
    # 바꿨다. "photo"를 명시하면 기존 실사진 슬라이드쇼 방식(images/image_schedule
    # 필요)을 그대로 쓸 수 있다 — 과거 topic 재조립이나 특별히 실사진이 필요한
    # 경우를 위해 남겨둠.
    bg_style: str = "chalkboard",
    # WHY 기본 켜짐(2026-08-02, "숏폼 영상 마지막에 구독, 좋아요, 팔로우 요청하는
    # 글도 추가하자"): 제목 카드와 대칭으로 영상 맨 끝에 CTA 카드를 붙인다 —
    # end_card_duration=0으로 주면 완전히 끌 수 있다(기존 영상 재조립 시 굳이
    # 필요 없는 경우 등).
    end_card_duration: float = 2.0,
    end_card_text: str | None = None,
    end_card_char_path: str | None = None,
    # WHY title_banner_photo_path(2026-08-02, "분홍색 바탕 없애도 되고 바탕으로는
    # 그 항목에 대한 real 이미지를 흐린 색으로"): 상단 배너의 단색 배경을 topic
    # 대표 실사진 블러로 바꾼다. 안 주면 기존 단색 배경 그대로 폴백.
    title_banner_photo_path: str | None = None,
    # WHY lang(2026-08-03, 글로벌 확장): 칠판 낙서의 급훈/명패 문구 풀을 언어별로
    # 바꾸기 위함(_place_chalk_doodle 참고) — 기본값 "kor"면 기존 동작 그대로.
    lang: str = "kor",
    # WHY item_label_overrides(2026-08-03, 글로벌 확장): 우상단 아이템 라벨이
    # motion 파일명 stem(예: "고추")을 그대로 쓰는데 이건 항상 한국어라 영어
    # topic에도 한글이 그대로 노출된다 — {파일명_stem: 표시할 라벨} 매핑을
    # 주면 오버라이드, 없으면(기존 한국어 topic 전부) 기존처럼 파일명 그대로.
    item_label_overrides: dict[str, str] | None = None,
    # WHY topic_word(2026-08-03 버그 수정): "급훈" 액자는 doodle_seed(topic 폴더명)
    # 에서 한국식 "카테고리_번호" 규칙으로 단어를 뽑는데(_topic_word_from_seed),
    # 글로벌 topic 폴더명은 "en_heartburn_1"처럼 언어 코드 프리픽스가 붙어 있어서
    # 그대로 쓰면 "en_heartburn"처럼 어색한 단어가 나온다 — 호출자가 이미 프리픽스
    # 뗀 단어를 넘기면 그걸 그대로 쓰고, 안 주면(기존 한국어 topic 전부) 기존 동작.
    topic_word: str | None = None,
):
    if not motion_path and not motion_schedule:
        raise ValueError("motion_path 또는 motion_schedule 중 하나는 필요합니다")
    if bg_style not in ("chalkboard", "photo"):
        raise ValueError(f"알 수 없는 bg_style: {bg_style!r} (chalkboard 또는 photo만 가능)")
    if bg_style == "photo" and not images and not image_schedule:
        raise ValueError("bg_style='photo'면 images 또는 image_schedule 중 하나는 필요합니다")

    # WHY 어떤 ffmpeg 호출보다도 먼저(2026-08-14): _validate_assemble_glyphs
    # WHY 주석 참고 — 렌더링을 시작하기 전에 못 그리는 문자가 있으면 여기서
    # 막는다.
    _validate_assemble_glyphs(
        lang=lang, title=title, title_card_text=title_card_text,
        end_card_text=end_card_text, ad_tag=ad_tag, bg_style=bg_style,
        motion_schedule=motion_schedule, item_label_overrides=item_label_overrides,
        doodle_seed=Path(out_path).stem, topic_word=topic_word, srt_path=srt_path,
    )

    duration_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, check=True,
    )
    total_duration = float(duration_probe.stdout.strip())

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # WHY title 기반으로 flip 결정(2026-08-03, "매번 똑같은 5초 루프 반복이
        # 아니라 모션·구도에 변주 주기"): accent_color/y_bias와 같은 이유로 topic마다
        # 결정적으로 갈리게 하되, 곱셈 가중치를 다르게 줘서 색·위치와 실질적으로
        # 독립적인 조합이 나오게 한다.
        char_flip = sum(ord(c) * (i * 7 + 3) for i, c in enumerate(title)) % 2 == 0
        char_track = tmp_path / "char.mov"
        if motion_schedule:
            _build_character_schedule(motion_schedule, total_duration, char_track, bg_color=bg_color,
                                       flip=char_flip)
        else:
            _build_character_loop(motion_path, total_duration, char_track, bg_color=bg_color, flip=char_flip)

        # WHY item_schedule(2026-08-02, "칠판 우상단에 멈춰있는 일러스트와...
        # 이름을 함께 넣어줘야해"): motion_schedule의 각 구간(캐릭터 파일 경로)에서
        # 품목명을 추출해서, 그 품목의 일러스트(assets_library/illust/<품목>_illust.jpg)를
        # 자동으로 찾아 칠판 우상단 아이콘+이름 라벨을 구간마다 바꾼다 — 캐릭터가
        # 이미 이 파일명 규칙(<품목>_motion.mp4)을 따르고 있어서 별도 인자 없이
        # 기존 motion_schedule만으로 유도 가능하다. 못 찾으면 아이콘 없이 이름만
        # 나온다.
        #
        # ⚠️ 상단 배너 사진은 여기서 구간별로 따로 찾지 않는다(2026-08-02
        # 되돌림) — 원래 여기서 품목별 real_photo도 같이 찾아서 배너 사진 자체를
        # 구간마다 바꿨었는데, "상단 글자의 배경으로 들어가는 이미지가 전 영역에
        # 들어가야된다니까"라는 반복 지적대로 배너는 항상 shared_bg_photo 하나만
        # 써야 칠판 배경과 이어져 보인다(아래 title_png 오버레이 부분 참고).
        item_schedule: list[dict] = []
        if motion_schedule:
            for entry in motion_schedule:
                seg_start, seg_end, motion_p = entry[0], entry[1], entry[2]
                motion_p = Path(motion_p)
                base = motion_p.stem
                if base.endswith("_motion"):
                    base = base[: -len("_motion")]
                assets_root = motion_p.parent.parent
                illust_p = assets_root / "illust" / f"{base}_illust.jpg"
                item_schedule.append({
                    "start": seg_start,
                    "end": seg_end,
                    "name": (item_label_overrides or {}).get(base, base),
                    "illust": str(illust_p) if illust_p.exists() else None,
                })

        # WHY shared_bg_photo를 여기서 한 번만 만드는지(2026-08-02, "배경으로 넣는
        # real 사진을... 한 사진으로 해서... 따로따로 짤려보이잖아"): 배너와 칠판
        # 배경이 각자 photo_path로 독립적인 cover-crop을 하면 서로 다른 배율/영역이
        # 잘려서 이어지는 사진처럼 안 보인다 — 캔버스 전체(W x H) 크기로 딱 한 번만
        # cover-crop한 뒤, 배너는 이 이미지의 위쪽 조각을, 칠판 배경은 흰 부분만
        # 투명 처리해서 전체를 재사용한다(같은 사진, 같은 배율).
        shared_bg_photo = _cover_crop_subject(title_banner_photo_path, W, H) if title_banner_photo_path else None

        # WHY 배너를 배경보다 먼저 만드는지(2026-08-02, "위아래로 더 키워서... 위
        # 글자 직전까지"): 칠판 배경의 상단 흰 패딩이 배너 높이와 정확히 맞아떨어져야
        # 배너 바로 아래부터 칠판이 시작한다(틈도 안 남고 겹치지도 않고). 배너 높이는
        # 제목 줄 수에 따라 달라지므로(1줄/2줄) 고정값 대신 실제 배너를 먼저 만들어서
        # 그 높이(title_h)를 칠판 배경 생성에 넘긴다.
        title_png = tmp_path / "title.png"
        title_h = _make_title_png(title, title_png, photo_path=title_banner_photo_path,
                                   photo_img=shared_bg_photo, lang=lang)

        # WHY caption_center_y(2026-08-02, "글이 너무 아래로 쏠려있잖아 칠판 기준으로
        # 중앙으로 들어가게"): 기존엔 화면 하단 기준 고정 오프셋(-620)으로 자막을
        # 앉혔는데, 칠판이 훨씬 커진 뒤로는 그 위치가 칠판 영역의 중앙이 아니라
        # 아래쪽에 치우쳐 보였다 — 칠판이 실제로 화면에서 차지하는 세로 범위
        # (title_h ~ title_h+칠판높이)의 중앙에 자막을 놓는다.
        # ⚠️ WHY title_h를 그대로 안 쓰고 effective_top_pad를 다시 계산하는지
        # (2026-08-03 버그 수정, "또 그 글이 좀 아래로 쏠렸네 칠판 중앙으로 들어가야
        # 하는데" — 제목이 4줄이라 title_h가 커진 en_heartburn_1에서 실제 발견):
        # `_build_chalkboard_bg`는 칠판 사진(리사이즈 후 약 1664px, H=1920보다 훨씬
        # 큼)을 `effective_top_pad = min(top_pad, H - 칠판높이)`로 캡을 씌운 위치에
        # 붙인다 — title_h가 이 캡(대략 255px)보다 크면 칠판은 실제로 title_h가
        # 아니라 이 캡 지점부터 시작하는데, 여기 caption_center_y 계산은 그 캡을
        # 무시하고 title_h를 그대로 "칠판 시작점"으로 썼다 — 그래서 실제 칠판
        # 중앙보다 자막이 더 아래로 치우쳐 보였다. `_build_chalkboard_bg`와 정확히
        # 같은 공식으로 effective_top_pad를 다시 계산해서 어긋남을 없앤다.
        caption_center_y = None
        if bg_style == "chalkboard":
            board_h = _chalkboard_display_height()
            effective_top_pad = min(title_h, max(H - board_h, 0))
            board_bottom = min(effective_top_pad + board_h, H)
            caption_center_y = (effective_top_pad + board_bottom) / 2

        bg = tmp_path / "bg.mp4"
        if bg_style == "chalkboard":
            board_seed = Path(out_path).stem
            _build_chalkboard_bg(total_duration, bg, top_pad=title_h, photo_bg_img=shared_bg_photo,
                                  doodle_seed=board_seed, doodle_skip_right=bool(item_schedule),
                                  board_photo_path=pick_chalkboard_variant(board_seed), lang=lang,
                                  topic_word=topic_word)
        elif image_schedule:
            _build_background_schedule(image_schedule, total_duration, bg)
        else:
            _build_background(images, total_duration, bg)

        # 1) 인트로 구간: 캐릭터 크게, 중앙. WHY intro_duration<=0이면 통째로 스킵
        # (2026-07-31, "5초 뒤에 우하단으로 옮기지 말고 처음부터 우하단에 있게"):
        # 인트로 자체를 안 만들고 처음부터 코너(작게) 구간으로 시작한다.
        intro_out = None
        if intro_duration > 0:
            intro_out = tmp_path / "intro.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-t", f"{intro_duration}", "-i", str(bg),
                 "-t", f"{intro_duration}", "-i", str(char_track),
                 "-filter_complex",
                 f"[1:v]scale=760:-1[char];[0:v][char]overlay=x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2-80[v]",
                 "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(intro_out)],
                check=True, capture_output=True,
            )

        # 2) 이후 구간: 캐릭터 작게, 우측 하단(기본). WHY -140(2026-08-02, "그
        # 일러스트는 아래로 더 빼서 나무 틀에 걸치고"): 기존 -320은 캐릭터가
        # 칠판 초록 판서면 안쪽에 붕 떠 보였다 — 칠판 나무 프레임/받침대 쪽으로
        # 더 내려서 걸쳐 앉은 것처럼 보이게 오프셋을 줄였다.
        #
        # WHY 좌/우 코너 + 가끔 생략(2026-08-03, "캐릭터를 다른 위치로 옮기던지...
        # 무조건 위치를 옮기게 해버리는거지" — 유튜브 대량생산 스팸 정책 리스크
        # 완화, Gemini/Kling 재생성 비용 없이 코드만으로): 상단(우측)은 이미
        # 아이템 라벨·광고 태그·낙서가 차지하고 있어서 캐릭터를 거기로 옮기면
        # 겹친다 — 안전한 건 하단 좌/우뿐이라 그 둘만 topic별로 결정적으로
        # 바꾼다. 대략 6개 topic 중 1개는 캐릭터 자체를 아예 안 띄워서(배경+
        # 자막만) 매번 "캐릭터 박힌 코너 영상"이라는 시각적 패턴 자체를 깬다 —
        # 브랜드 정체성이 흐려지지 않게 완전히 랜덤이 아니라 낮은 비율로만.
        main_dur = total_duration - intro_duration
        main_out = tmp_path / "main.mp4"
        char_seed_val = sum(ord(c) * (i * 5 + 2) for i, c in enumerate(title))
        char_shown = char_seed_val % 6 != 0
        char_on_left = (char_seed_val // 6) % 2 == 0
        char_x = "30" if char_on_left else "main_w-overlay_w-30"
        if char_shown:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{intro_duration}", "-t", f"{main_dur}", "-i", str(bg),
                 "-ss", f"{intro_duration}", "-t", f"{main_dur}", "-i", str(char_track),
                 "-filter_complex",
                 f"[1:v]scale=280:-1[char];[0:v][char]overlay=x={char_x}:y=main_h-overlay_h-140[v]",
                 "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(main_out)],
                check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{intro_duration}", "-t", f"{main_dur}", "-i", str(bg),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(main_out)],
                check=True, capture_output=True,
            )

        # 0) 맨 앞 제목 카드 — 단색 배경 + 큼직한 글자, 플랫폼이 영상 첫 프레임을
        # 썸네일로 자동 지정하는 경우가 많아서 이 카드 자체가 썸네일 역할도 한다.
        # WHY -r FPS(2026-07-31 버그 수정): 이 명령에 프레임레이트를 안 주면 ffmpeg가
        # image2 loop 입력에 기본 25fps를 붙이는데, main_out(bg/char 체인)은 30fps라
        # 뒤에서 -c copy로 concat할 때 두 세그먼트의 프레임레이트가 달라 타임스탬프가
        # 어긋난다. 겉보기엔 영상 길이가 실제보다 늘어나 보이고(30/25=1.2배), 캐릭터가
        # 여러 명 번갈아 나오는 영상에서는 캐릭터 전환 타이밍이 한 구간씩 밀려 보이는
        # 형태로 드러났다(수면음식_1에서 실제로 발견) — 캐릭터 1명짜리 영상에서도
        # 전체적인 자막/오디오 싱크가 미세하게 어긋나는 형태로 존재했을 가능성이 있다.
        # WHY title 기준으로 accent_color 시드를 잡는지: 제목 카드·엔드 카드가
        # 같은 seed를 써야 한 영상 안에서 두 카드 색이 서로 다르게 튀지 않는다.
        # y_bias(텍스트 세로 위치)는 제목 카드(=썸네일)에만 적용 — 엔드 카드는
        # 매번 같은 짧은 CTA 문구라 정중앙 유지가 더 안정적으로 보인다.
        accent_color = _accent_color_for_seed(title)
        title_card_png = tmp_path / "title_card.png"
        _make_title_card_png(title_card_text or title, title_card_png, char_path=title_card_char_path,
                              lang=lang, accent_color=accent_color, y_bias=_text_y_bias_for_seed(title),
                              style=_title_card_style_for_seed(title))
        title_card_out = tmp_path / "title_card.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-t", f"{title_card_duration}", "-r", str(FPS), "-i", str(title_card_png),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(title_card_out)],
            check=True, capture_output=True,
        )

        # 0-2) 맨 끝 엔딩 카드 — 제목 카드와 같은 스타일(단색+큰 글자)로 구독/좋아요/
        # 팔로우 CTA. end_card_duration=0이면 통째로 스킵(기존 인트로 스킵 패턴과 동일).
        end_card_out = None
        if end_card_duration > 0:
            end_card_png = tmp_path / "end_card.png"
            _make_title_card_png(end_card_text or DEFAULT_END_CARD_TEXT, end_card_png,
                                  char_path=end_card_char_path or title_card_char_path, lang=lang,
                                  accent_color=accent_color)
            end_card_out = tmp_path / "end_card.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-loop", "1", "-t", f"{end_card_duration}", "-r", str(FPS), "-i", str(end_card_png),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(end_card_out)],
                check=True, capture_output=True,
            )

        combined = tmp_path / "combined.mp4"
        list_path = tmp_path / "scenes.txt"
        scene_files = ([title_card_out] + ([intro_out] if intro_out else []) + [main_out]
                       + ([end_card_out] if end_card_out else []))
        list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in scene_files))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-c", "copy", str(combined)],
            check=True, capture_output=True,
        )

        # WHY video_total: 맨 앞 제목 카드 + 맨 끝 엔딩 카드가 붙어서 영상 전체
        # 길이가 나레이션 길이(total_duration)보다 길어졌다 — 이후 배너/자막 단계는
        # 전부 이 늘어난 길이 기준으로 처리해야 한다.
        video_total = title_card_duration + total_duration + end_card_duration

        # 3) 상단 후킹 배너(+ 우상단 아이템 라벨, + 필요시 광고 태그) — 전체 길이에
        # 한 번만 overlay하는 대신, item_schedule이 있으면(motion_schedule로 캐릭터
        # 여러 명이 번갈아 나오는 topic) 구간마다 다른 배너 사진 + 아이템 라벨을
        # enable=between()으로 스위칭한다. WHY -t를 이미지 입력과 출력 양쪽에 명시:
        # -loop 1 이미지 + -shortest 조합만으로는 종료를 못 잡고 무한정 도는
        # 경우가 있었다(2026-07-30) — 길이를 직접 못박아서 확실히 끝나게 한다.
        titled = tmp_path / "titled.mp4"

        # WHY enable='between(...)': 제목 카드 구간엔 이미 큼직한 훅 카피가 화면
        # 중앙에 떠 있어서 상단 배너까지 같이 뜨면 겹쳐 보인다(2026-07-31 지적) —
        # 배너는 제목 카드가 끝난 뒤부터 나온다. WHY 상한도 뒀는지(2026-08-02, 엔딩
        # 카드 추가): 엔딩 카드도 마찬가지로 CTA 문구가 중앙에 크게 뜨는데, 예전처럼
        # gte로 열어두면 배너가 엔딩 카드 구간까지 계속 떠서 겹친다 — 나레이션
        # 구간(title_card_duration ~ title_card_duration+total_duration)에서만 뜨게
        # 상한을 추가했다.
        cmd_inputs = ["-i", str(combined)]
        filter_parts = []
        current = "0:v"
        next_input_idx = 1

        def _add_input(path: Path) -> int:
            nonlocal next_input_idx
            cmd_inputs.extend(["-loop", "1", "-r", str(FPS), "-t", f"{video_total}", "-i", str(path)])
            idx = next_input_idx
            next_input_idx += 1
            return idx

        # WHY 배너는 item_schedule 여부와 무관하게 항상 title_png(shared_bg_photo)
        # 하나만 전체 나레이션 구간에 쓰는지(2026-08-02, "상단 글자의 배경으로
        # 들어가는 이미지가 전 영역에 들어가야된다니까" — 반복 지적): 예전엔
        # item_schedule이 있으면 구간마다 item["real_photo"]를 따로 cover-crop해서
        # 배너 사진 자체를 바꿨는데, 그러면 배너 바로 아래 칠판 배경(항상
        # shared_bg_photo 하나로 고정)과 서로 다른 사진이 되어 이어붙인 자리가
        # "층져서 이상한 사진"처럼 보였다 — 배너와 칠판 배경은 항상 같은 사진
        # 하나(shared_bg_photo)를 써야 하나로 이어져 보인다는 원칙(위 _build_chalkboard_bg
        # WHY photo_bg_img 참고)이 item_schedule에서만 깨져 있었던 것. 구간별로
        # 바뀌어야 하는 건 우상단의 작은 아이템 아이콘+이름 라벨뿐이다.
        banner_idx = _add_input(title_png)
        enable_expr = f"between(t\\,{title_card_duration}\\,{title_card_duration + total_duration})"
        nxt = "vb"
        filter_parts.append(f"[{current}][{banner_idx}:v]overlay=x=0:y=0:enable='{enable_expr}'[{nxt}]")
        current = nxt

        # WHY ad_tag과 item_schedule을 더 이상 상호배타로 안 두는지(2026-08-06,
        # 손톱_1 실측 확인 — 캐릭터 여러 명 topic은 우상단이 항상 아이템 라벨
        # 차지라 광고 태그가 아예 안 뜨는 사고였음): 둘 다 "우상단"을 원해서
        # 자리가 겹치니, 태그가 켜져 있으면 아이템 라벨을 그 아래로 밀어서
        # 세로로 쌓는다 — 태그는 항상 title_h+16, 라벨은 태그가 있으면 그만큼
        # 더 아래(title_h+16+ad_tag_h+8), 없으면 기존 자리(title_h+20) 그대로.
        ad_tag_h = 0
        if ad_tag:
            ad_png = tmp_path / "ad_tag.png"
            _make_ad_tag_png(ad_png, lang=lang)
            ad_tag_h = Image.open(ad_png).height
            ad_idx = _add_input(ad_png)
            nxt = "vad"
            filter_parts.append(
                f"[{current}][{ad_idx}:v]overlay=x=main_w-overlay_w-20:y={title_h + 16}:enable='{enable_expr}'[{nxt}]")
            current = nxt

        if item_schedule:
            label_y = title_h + 16 + ad_tag_h + 8 if ad_tag else title_h + 20
            for i, item in enumerate(item_schedule):
                seg_start_abs = item["start"] + title_card_duration
                seg_end_abs = item["end"] + title_card_duration
                win = f"between(t\\,{seg_start_abs}\\,{seg_end_abs})"

                label_png = tmp_path / f"label_seg_{i:03d}.png"
                _make_item_label_png(item["illust"], item["name"], label_png, lang=lang)
                label_idx = _add_input(label_png)
                nxt = f"vl{i}"
                filter_parts.append(
                    f"[{current}][{label_idx}:v]overlay=x=main_w-overlay_w-24:y={label_y}:enable='{win}'[{nxt}]")
                current = nxt

        filter_complex = ";".join(filter_parts)
        subprocess.run(
            ["ffmpeg", "-y", *cmd_inputs,
             "-filter_complex", filter_complex,
             "-map", f"[{current}]", "-r", str(FPS), "-t", f"{video_total}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(titled)],
            check=True, capture_output=True,
        )
        combined = titled

        # WHY 자막 합성 전 CFR 재인코딩(2026-08-02 버그 수정): combined는 title_card_out+
        # main_out+end_card_out을 "-f concat -c copy"(스트림 복사)로 이어붙인 뒤 배너를
        # 얹은 결과라, 이어붙인 지점의 PTS/GOP 구조가 살짝 불규칙해질 수 있다 — 특히 정적인
        # 칠판 배경처럼 장면 변화가 거의 없는 구간에서 x264가 비정상적으로 긴 B-프레임
        # 체인을 잡으면, 바로 다음 자막 오버레이 단계에서 overlay 필터가 PTS를 맞추는 동안
        # 자막이 최대 1~2초 안 보이는 사고로 이어졌다(사용자가 "목소리가 자막보다 먼저
        # 나간다"로 실제 발견 — 골다공증_1의 8초 넘는 긴 자막 구간에서 재현). 자막을 얹기
        # 직전에 한 번 깨끗하게 고정 프레임레이트로 재인코딩해서 이후 모든 세그먼트 오버레이가
        # 규칙적인 프레임 구조 위에서 이뤄지게 한다.
        normalized = tmp_path / "normalized.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(combined), "-r", str(FPS), "-vsync", "cfr",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(normalized)],
            check=True, capture_output=True,
        )
        combined = normalized

        # 4) 자막 굽기 (문장 구간별로 짧게 잘라 처리 — 안전한 세그먼트 방식)
        # WHY offset: 자막 타이밍은 오디오(나레이션) 기준 0초부터라, 제목 카드만큼
        # (title_card_duration) 밀어서 실제 영상 타임라인에 맞춰야 한다.
        offset = title_card_duration
        srt_entries = _parse_srt(srt_path)
        # WHY 클램프(2026-08-02 버그 수정): 멀티보이스 TTS(synthesize_segments)로 만든
        # SRT는 문단 사이 무음 간격(SEGMENT_GAP_MS)을 누적한 타임스탬프를 쓰는데, 실제
        # 합쳐진 오디오 길이와 최대 2초 가까이 어긋나는 경우가 실제로 있었다(구내염_1
        # 등에서 확인 — SRT 마지막 자막이 63.1초인데 실제 오디오는 61.1초, 문단 6개
        # =간격 5개×0.4초=2.0초와 거의 정확히 일치). 이 어긋남 때문에 마지막 자막
        # 구간이 total_duration을 넘어 엔딩 카드 영역까지 침범해서, 마지막 자막과
        # 엔딩 카드 CTA 문구가 겹쳐 보이는 사고가 났다. SRT를 신뢰하지 않고 오디오
        # 실측 길이(total_duration, ffprobe로 직접 잰 값)를 항상 상한으로 강제한다 —
        # 어긋남이 어디서 오든(멀티보이스 gap 누적, 반올림 등) 여기서 한 번에 방어한다.
        srt_entries = [
            (start, min(end, total_duration), text)
            for start, end, text in srt_entries
            if start < total_duration
        ]
        # WHY 여기서 쪼개는지: 위 클램프까지 끝난 뒤(시간 범위가 확정된 뒤)라야
        # 문장별로 나눈 구간에 정확한 시간을 비례 배분할 수 있다 — MAX_CAPTION_LINES
        # WHY 주석 참고(_split_long_caption_entries 바로 위).
        srt_entries = _split_long_caption_entries(srt_entries, lang)
        cap_dir = tmp_path / "caps"
        cap_dir.mkdir()
        timeline, cursor = [(0.0, offset, None)], 0.0
        for start, end, text in srt_entries:
            if start > cursor + 0.05:
                timeline.append((cursor + offset, start + offset, None))
            timeline.append((start + offset, end + offset, text))
            cursor = end
        if cursor < total_duration - 0.05:
            timeline.append((cursor + offset, total_duration + offset, None))
        # WHY 엔딩 카드 구간도 세그먼트로 명시(2026-08-02): 위 세그먼트들은 전부
        # total_duration+offset(=title_card_duration+total_duration)까지만 커버한다 —
        # 엔딩 카드를 붙이면서 video_total이 그보다 길어졌는데(end_card_duration만큼)
        # 여기서 세그먼트를 안 만들면 밑에서 concat한 captioned 영상이 combined보다
        # 짧아져서 엔딩 카드 부분이 통째로 잘려나간다. 자막 없는 구간으로 명시해서
        # video_total까지 확실히 채운다.
        if end_card_duration > 0:
            timeline.append((total_duration + offset, video_total, None))

        seg_paths = []
        for i, (start, end, text) in enumerate(timeline):
            dur = end - start
            if dur <= 0.02:
                continue
            seg = tmp_path / f"cap_{i:04d}.mp4"
            if text:
                cap_png = cap_dir / f"cap_{i:04d}.png"
                if bg_style == "chalkboard":
                    _make_chalk_caption_png(text, cap_png, lang=lang)
                else:
                    _make_caption_png(text, cap_png, lang=lang)
                cap_y_expr = (
                    f"{caption_center_y}-overlay_h/2" if caption_center_y is not None
                    else "main_h-overlay_h-620"
                )
                # WHY -r 명시 + trim 필터로 전환(2026-08-02 버그 수정): 원래
                # "-ss {start} -t {dur} -i combined"(입력 단 seek)로 잘랐는데, 이 방식은
                # combined처럼 키프레임이 드문(정적인 칠판 배경이라 대부분 장면이 안 바뀜)
                # 영상에서 seek 지점 근처 프레임을 정확히 못 낼 때가 있었다 — 캐릭터/배경은
                # 이미 맞는 시점으로 나오는데 overlay되는 자막 PNG만 최대 1~2초 가까이
                # 안 보이는 사고로 이어졌다(사용자가 "목소리가 자막보다 먼저 나간다"로 실제
                # 발견, 재현 테스트로 combined 입력을 거칠 때만 재현되고 단독 오버레이는
                # 문제없음을 확인). "-i combined" 통째로 열고 trim 필터로 정확히 잘라내는
                # 방식(디코드 기반이라 느리지만 항상 정확함)으로 바꿔서 해결. 자막 PNG 루프
                # 입력에도 -r을 명시해 두 입력의 프레임레이트를 맞춘다(overlay 프레임 동기화
                # 안전장치, 위 trim 수정과는 별개 원인이었지만 같이 방어).
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(combined),
                     "-loop", "1", "-r", str(FPS), "-t", f"{dur}", "-i", str(cap_png),
                     "-filter_complex",
                     f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS[bg];"
                     f"[bg][1:v]overlay=x=(main_w-overlay_w)/2:y={cap_y_expr}[v]",
                     "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
                    check=True, capture_output=True,
                )
            else:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(combined),
                     "-filter_complex", f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS[v]",
                     "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
                    check=True, capture_output=True,
                )
            seg_paths.append(seg)

        cap_list = tmp_path / "cap_list.txt"
        cap_list.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_paths))
        captioned = tmp_path / "captioned.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cap_list),
             "-c", "copy", str(captioned)],
            check=True, capture_output=True,
        )

        # WHY adelay 대신 -shortest 안 씀: 제목 카드 구간은 무음이어야 하므로 오디오를
        # title_card_duration만큼 뒤로 민다 — 그러면 오디오 길이가 정확히 영상 길이와
        # 같아져서 -shortest로 잘라낼 필요가 없다(제목 카드가 잘려나가는 사고 방지).
        offset_ms = int(title_card_duration * 1000)
        # WHY apad(2026-08-02, "툭툭 끊기는게 너무 듣기싫은데"): 나레이션 오디오가
        # total_duration 끝나는 순간 바로 무음이 되는데, 마지막 단어의 자연스러운
        # 여운(잔향)이 다 가시기 전에 엔딩 카드로 넘어가면서 뚝 끊기는 느낌을 줬다.
        # 텍스트를 늘리는 대신(부자연스러운 발음 위험 — "있어어어" 같은 반복은 TTS가
        # 오히려 이상하게 읽을 수 있음) 오디오 신호 자체에 무음을 패딩한다.
        # WHY 0.7초로(2026-08-02, 0.4초 적용 후 "확실히 좋아졌는데 조금더늘릴수있나"):
        # 0.4초가 방향은 맞다는 게 확인돼서 좀 더 늘렸다. end_card_duration(기본
        # 2.0초)의 절반을 넘지 않게 캡을 씌워서 — 패딩 후 오디오 총 길이
        # (title_card_duration+total_duration+pad)가 영상 전체 길이(video_total =
        # title_card_duration+total_duration+end_card_duration)를 절대 넘지 않는다.
        # 이러면 모션 스케줄·자막·엔딩 카드 등장 시점 등 기존 타이밍 계산은 전혀
        # 안 건드리고, 엔딩 카드가 이미 갖고 있던 무음 구간 앞부분만 나레이션
        # 여운으로 채우는 것뿐이라 안전하다.
        end_pad = min(0.7, end_card_duration * 0.5)
        # WHY 배경음악을 title_card_duration+total_duration+end_card_duration
        # 전체(video_total_duration)에 깔고 나레이션은 그 앞부분만 delay/pad하는지:
        # BGM은 "밑바탕"이라 제목 카드·엔딩 카드 구간에서도 끊기지 않고 계속 흘러야
        # 자연스럽다 — 나레이션 없는 구간(제목 카드)에서 BGM만 뚝 끊기면 오히려
        # 더 어색하다.
        video_total_duration = title_card_duration + total_duration + end_card_duration
        bgm_result = bgm_filter_segment(
            Path(out_path).stem, video_total_duration, in_label="2:a", out_label="bgm",
        )
        if bgm_result is not None:
            bgm_frag, bgm_track = bgm_result
            narr_frag = f"[1:a]adelay={offset_ms}|{offset_ms},apad=pad_dur={end_pad}[narr]"
            filter_complex = (
                f"{narr_frag};{bgm_frag};"
                f"[narr][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            )
            mux_inputs = ["-i", str(captioned), "-i", audio_path, "-i", str(bgm_track)]
        else:
            filter_complex = f"[1:a]adelay={offset_ms}|{offset_ms},apad=pad_dur={end_pad}[a]"
            mux_inputs = ["-i", str(captioned), "-i", audio_path]
        subprocess.run(
            ["ffmpeg", "-y", *mux_inputs,
             "-filter_complex", filter_complex,
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             out_path],
            check=True, capture_output=True,
        )
    print(f"영상 조립 완료: {out_path}")


# WHY 인스타그램 전용 별도 파일(2026-08-04, "인스타 숏츠로 업로드할거를 양옆,
# 상하 더 키워가지고 만들어놓으면 그대로 데스크탑에서 업로드해도 딱 맞게
# 들어가겠는데"): 칠판 배경은 나무 프레임이 캔버스 상하좌우 가장자리에 거의
# 여백 없이 꽉 차게 디자인돼 있다(가로는 "프레임이 폭에 딱 맞게" 의도적 설계,
# 세로는 상단 256px 캡 외엔 여백 없음) — 인스타그램 릴스 UI(프로필·팔로우·
# 캡션 등)가 화면 가장자리를 가리는 세이프존과 거의 정확히 겹쳐서 "칼같이
# 짤린" 느낌을 준다. 유튜브 쇼츠는 자동 업로드라 이 문제가 없고, 같은 영상
# 파일을 그대로 공유해서 쓰고 있어(기존 <topic>_shorts.mp4) 이걸 건드리면
# 유튜브용까지 함께 작아진다 — 그래서 원본은 그대로 두고, 인스타그램용으로만
# 여백을 더한 별도 파일을 새로 만든다.
#
# WHY 단순 레터박스(검은/흰 바) 대신 블러 채우기인지: 콘텐츠를 줄이고 남는
# 자리를 검은 바로 채우면 "잘못 업로드된 영상"처럼 어색해 보인다 — 인스타
# 스토리·릴스에서 세로 영상이 화면에 안 맞을 때 이미 흔하게 쓰는 "블러로 채운
# 배경 위에 원본을 작게 얹는" 방식을 그대로 써서 위화감이 없게 한다.
def build_instagram_safe_video(source_path: str, out_path: Path,
                                margin_scale_x: float = 0.60, margin_scale_y: float = 0.60) -> None:
    """source_path(1080x1920 완성 영상)를 캔버스 중앙에 축소해서 얹고, 남는
    상하좌우 여백은 같은 영상을 확대+블러한 배경으로 채운다 — 원본 해상도
    (1080x1920)는 그대로 유지하면서 실제 콘텐츠(나무 프레임 등) 둘레에 안전
    여백이 생긴다.

    WHY 기본값이 상하좌우 20%(0.60)인지(2026-08-04, 실기기로 직접 여러 비율을
    테스트하며 확정): 처음엔 원본 비율(9:16)을 그대로 유지한 채 한 배율로만
    축소했다가, 가로·세로 비대칭 비율도 몇 차례 시도해봤지만 결국 실기기에서
    "딱이다"로 확정된 값은 상하좌우 동일 20% 여백(margin_scale 0.60)이었다 —
    가로·세로를 따로 받는 파라미터는 남겨두되(추후 다른 배경 포맷에서 비대칭이
    필요할 수 있어서) 기본값은 대칭으로 되돌린다."""
    content_w = round(W * margin_scale_x / 2) * 2
    content_h = round(H * margin_scale_y / 2) * 2
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source_path),
         "-filter_complex",
         f"[0:v]split=2[bgsrc][fgsrc];"
         f"[bgsrc]scale={W}:{H},gblur=sigma=30[bg];"
         f"[fgsrc]scale={content_w}:{content_h}[fg];"
         f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]",
         "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy",
         str(out_path)],
        check=True, capture_output=True,
    )
    print(f"인스타그램용 안전 여백 영상 생성 완료: {out_path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--images", default=None,
                    help="쉼표로 구분된 배경용 실사진 경로들 — --bg-style photo일 때만 필요")
    p.add_argument("--bg-style", default="chalkboard", choices=["chalkboard", "photo"],
                    help="배경 스타일(2026-08-02 기본값 chalkboard로 전환) — "
                         "chalkboard: 짙은 초록 그라디언트+분필체 자막(images 불필요), "
                         "photo: 기존 실사진 슬라이드쇼(images 또는 image_schedule 필요)")
    p.add_argument("--motion", default=None, help="Kling으로 생성한 캐릭터 모션 루프 클립(흰 배경) — 캐릭터 1명짜리 topic용")
    p.add_argument("--motion-schedule", default=None,
                    help="캐릭터 여러 명이 구간별로 번갈아 나올 때 사용. "
                         "형식: 'start-end:경로,start-end:경로,...' (초 단위, 나레이션 기준 0초부터). "
                         "--motion 대신 이걸 쓰면 이 스케줄이 우선한다.")
    p.add_argument("--audio", required=True)
    p.add_argument("--srt", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title", required=True, help="영상 상단에 계속 표시할 주제 라벨")
    p.add_argument("--intro-duration", type=float, default=0)
    p.add_argument("--ad-tag", action="store_true", help="실제 제휴 링크를 쓰기로 확정한 경우에만 켠다")
    p.add_argument("--bg-color", default="0xFFFFFF",
                    help="캐릭터 모션 클립의 배경색(colorkey 대상) — 새 캐릭터는 0x00FF00 권장")
    p.add_argument("--title-card-duration", type=float, default=1.3,
                    help="영상 맨 앞 단색 제목 카드(썸네일용) 길이(초)")
    p.add_argument("--title-card-text", default=None,
                    help="제목 카드에만 쓸 별도 문구(안 주면 --title 그대로 사용) — "
                         "썸네일은 문제 제기 훅만, 상단 배너는 훅+주제 전체를 보여주고 싶을 때 분리")
    p.add_argument("--title-card-char", default=None,
                    help="제목 카드 배경에 크게 흐리게 깔 캐릭터 이미지 경로(안 주면 단색 배경만)")
    p.add_argument("--end-card-duration", type=float, default=2.0,
                    help="영상 맨 끝 구독/좋아요/팔로우 CTA 카드 길이(초) — 0이면 엔딩 카드 생략")
    p.add_argument("--end-card-text", default=None,
                    help="엔딩 카드에 쓸 문구(안 주면 기본 CTA 문구 사용)")
    p.add_argument("--end-card-char", default=None,
                    help="엔딩 카드 배경에 흐리게 깔 캐릭터 이미지 경로(안 주면 --title-card-char 재사용)")
    p.add_argument("--title-banner-photo", default=None,
                    help="상단 후킹 배너 배경에 흐리게 깔 topic 대표 real 이미지 경로(안 주면 단색 배경)")
    args = p.parse_args()

    motion_schedule = None
    if args.motion_schedule:
        motion_schedule = []
        for chunk in args.motion_schedule.split(","):
            # "start-end:path" 또는 "start-end:path:bg_color"(세그먼트별 크로마키 색 override)
            parts = chunk.split(":")
            span, path = parts[0], parts[1]
            start_s, end_s = span.split("-")
            if len(parts) > 2:
                motion_schedule.append((float(start_s), float(end_s), path, parts[2]))
            else:
                motion_schedule.append((float(start_s), float(end_s), path))

    assemble(
        images=args.images.split(",") if args.images else None,
        motion_path=args.motion,
        audio_path=args.audio,
        srt_path=args.srt,
        out_path=args.out,
        title=args.title,
        intro_duration=args.intro_duration,
        ad_tag=args.ad_tag,
        bg_color=args.bg_color,
        title_card_duration=args.title_card_duration,
        title_card_text=args.title_card_text,
        title_card_char_path=args.title_card_char,
        motion_schedule=motion_schedule,
        bg_style=args.bg_style,
        end_card_duration=args.end_card_duration,
        end_card_text=args.end_card_text,
        end_card_char_path=args.end_card_char,
        title_banner_photo_path=args.title_banner_photo,
    )
