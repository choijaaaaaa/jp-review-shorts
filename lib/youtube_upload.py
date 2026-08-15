# 유튜브 쇼츠 자동 업로드 (health-shorts lib/youtube_upload.py 이식).
# WHY 2026-08-14에 다시 다중 채널로 바꿨는지: 처음엔 Basketbrief 채널 하나만
# 전제하고 단순화했는데, 실제로는 콘텐츠가 이미 ko/en/zh-TW 3개 언어로 만들어지고
# 있어서(narration.<lang>.txt 등) 채널도 언어별로 3개(basketbrief-ko/en/tw) 파게
# 됐다 — health-shorts의 _env_prefix 다중 채널 패턴을 그대로 가져온다.
#
# WHY data/와 output/ 폴더 구조가 서로 다른지: 이 프로젝트가 처음 언어를 늘릴 때
# health-shorts처럼 <topic>/<lang>/ 중첩을 쓰지 않고 data/는 파일명에 언어
# 접미사(narration.en.txt), output/은 <lang>/ 서브폴더(output/<topic>/en/)를
# 섞어 쓰는 관례가 이미 굳어 있었다(jp_review_spec.<lang>.json 등 실측 확인,
# 2026-08-14) — 이 파일도 그 기존 관례를 그대로 따른다, 새로 통일하지 않는다.
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from lib.mission_control_log import report_issue

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/youtube"]
HOWTO_AND_STYLE_CATEGORY = "26"
PLATFORM_NAME = "YouTube Shorts"
# WHY 언어별 마커 딕셔너리인지(2026-08-14, en/zh-TW 채널 추가로 필요해짐):
# health-shorts CAPTION_MARKERS와 동일 원칙 — 캡션 작성 언어마다 "제목:"류
# 구분자 표기가 다르다(대시보드 없이 세션이 직접 쓰는 캡션이라 언어 관례를
# 그대로 따름).
CAPTION_MARKERS: dict[str, tuple[str, str]] = {
    "ko": ("제목:", "설명란:\n"),
    "en": ("Title:", "Description:\n"),
    "zh-TW": ("標題:", "說明:\n"),
}
KST = ZoneInfo("Asia/Seoul")
# WHY 저녁 7시인지(2026-08-14 리서치 반영): 한국 유튜브 평일 골든타임은
# 저녁 7~11시, 피크는 8~9시라는 게 여러 출처에서 확인된다 — 쇼츠는 피크
# 1시간 전에 올려야 알고리즘이 초반에 밀어줄 시간을 버는 게 유리하다는
# 전략적 조언이 있어 피크(8~9시) 바로 앞인 19시로 잡았다. en/zh-TW 채널도
# 일단 같은 KST 기준을 그대로 쓴다 — 그 채널들의 실제 시청자 지역이 아직
# 파악 안 돼서(en은 특정 국가 타겟이 불명확) 데이터 쌓이면 채널별로
# 분리할 것.
DAILY_UPLOAD_HOUR_KST = 19
UPLOADED_LOG = ROOT / "output" / "youtube_uploaded.json"


def _env_prefix(channel: str | None) -> str:
    """health-shorts lib/youtube_upload.py의 동명 함수와 동일 — channel이
    없으면(ko) 무접두사 변수(YOUTUBE_CLIENT_ID 등)를 그대로 쓴다."""
    return f"YOUTUBE_{channel.upper().replace('-', '_')}_" if channel else "YOUTUBE_"


def _lang_from_topic(topic: str) -> str:
    """topic이 "돈키호테카레과자_JP_1/en"·".../zh-TW"처럼 언어를 포함하면 그
    언어 코드를, 아니면 기본 "ko"를 반환한다(health-shorts와 동일 관례) —
    data/output 폴더의 실제 언어 접미사(en/zh-TW)를 그대로 쓴다."""
    return topic.rsplit("/", 1)[1] if "/" in topic else "ko"


def _base_topic(topic: str) -> str:
    return topic.rsplit("/", 1)[0] if "/" in topic else topic


# WHY lang과 채널 코드(env var 접두어)가 분리돼 있는지: 콘텐츠 파일의 언어
# 코드는 이미 굳어진 관례(narration.zh-TW.txt 등, BCP-47식 "zh-TW")를 그대로
# 쓰지만, 사용자가 만든 GCP 프로젝트/채널명은 "basketbrief-tw"처럼 짧은
# 코드다 — 데이터 경로 함수는 lang 그대로 쓰고, 자격증명 조회만 이 매핑을
# 거친다.
_CHANNEL_CODE_FOR_LANG = {"ko": None, "en": "en", "zh-TW": "tw"}


def _get_credentials(lang: str = "ko") -> Credentials:
    prefix = _env_prefix(_CHANNEL_CODE_FOR_LANG.get(lang, lang))
    creds = Credentials(
        token=None,
        refresh_token=os.environ[f"{prefix}REFRESH_TOKEN"],
        client_id=os.environ[f"{prefix}CLIENT_ID"],
        client_secret=os.environ[f"{prefix}CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _captions_path(topic: str, lang: str) -> Path:
    base = _base_topic(topic)
    suffix = "" if lang == "ko" else f".{lang}"
    return ROOT / "data" / base / f"platform_captions{suffix}.json"


def _video_dir(topic: str, lang: str) -> Path:
    base = _base_topic(topic)
    return ROOT / "output" / base if lang == "ko" else ROOT / "output" / base / lang


def _parse_title_description(caption: str, lang: str = "ko") -> tuple[str, str]:
    title_prefix, desc_marker = CAPTION_MARKERS.get(lang, CAPTION_MARKERS["ko"])
    if desc_marker not in caption:
        raise ValueError(f"예상한 구분자({desc_marker!r})가 캡션에 없음: {caption[:80]!r}...")
    title_part, description = caption.split(desc_marker, 1)
    title = title_part.replace(title_prefix, "", 1).strip()
    return title, description.strip()


def _build_status_body(privacy_status: str, publish_at: str | None) -> dict:
    body = {"selfDeclaredMadeForKids": False}
    if publish_at:
        body["privacyStatus"] = "private"
        body["publishAt"] = publish_at
    else:
        body["privacyStatus"] = privacy_status
    return body


def _find_video_file(topic: str, lang: str) -> Path:
    video_dir = _video_dir(topic, lang)
    # WHY 두 패턴을 다 찾는지: 초기 topic(갸스비_1)은 "<topic>_shorts.mp4",
    # 이후 topic들은 "demo_preview.mp4"로 렌더링돼 있다 — 실제 파일명 규칙이
    # 아직 안 굳어서(video_assembler.py가 --out을 그대로 받으므로 호출부마다
    # 달랐음) 둘 다 시도한다.
    for pattern in ("*shorts.mp4", "demo_preview.mp4"):
        candidates = [p for p in video_dir.glob(pattern) if "_instagram" not in p.name]
        if candidates:
            return candidates[0]
    raise FileNotFoundError(f"영상 파일 없음: {video_dir}/(*shorts.mp4 | demo_preview.mp4)")


def _load_uploaded() -> dict:
    if UPLOADED_LOG.exists():
        return json.loads(UPLOADED_LOG.read_text(encoding="utf-8"))
    return {}


def _mark_uploaded(topic: str, video_id: str) -> None:
    data = _load_uploaded()
    data[topic] = {"video_id": video_id, "uploaded_at": datetime.now(timezone.utc).isoformat()}
    UPLOADED_LOG.parent.mkdir(parents=True, exist_ok=True)
    UPLOADED_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upload_short(
    topic: str,
    video_path: str | None = None,
    privacy_status: str = "private",
    category_id: str = HOWTO_AND_STYLE_CATEGORY,
    publish_at: str | None = None,
) -> dict:
    """topic: "갸스비_1"(ko) 또는 "돈키호테카레과자_JP_1/en"(언어 포함) 형식.
    privacy_status 기본값은 "private" — 처음 몇 개는 비공개로 올려서 결과
    확인 후 "public"으로 바꿔 부를 것을 권장(공개 업로드는 되돌리기 어려운
    작업이라 기본값을 안전한 쪽으로 잡는다)."""
    lang = _lang_from_topic(topic)
    captions_path = _captions_path(topic, lang)
    if not captions_path.exists():
        raise FileNotFoundError(f"{topic}: {captions_path.name}이 없음 — 캡션부터 작성할 것")
    spec = json.loads(captions_path.read_text(encoding="utf-8"))
    platform = next((p for p in spec["platforms"] if p["name"] == PLATFORM_NAME), None)
    if platform is None:
        raise ValueError(f"{topic}: {captions_path.name}에 '{PLATFORM_NAME}' 항목이 없음")

    title, description = _parse_title_description(platform["caption"], lang)
    if len(title) > 100:
        truncated = title[:100].rsplit(" ", 1)[0].rstrip(" ,.!?¿¡-")
        title = truncated or title[:100]

    video_path = video_path or str(_find_video_file(topic, lang))
    if not Path(video_path).exists():
        raise FileNotFoundError(f"영상 파일 없음: {video_path}")

    tags = [w[1:] for w in description.split() if w.startswith("#")]
    status_body = _build_status_body(privacy_status, publish_at)

    youtube = build("youtube", "v3", credentials=_get_credentials(lang))
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
                "defaultLanguage": lang,
                "defaultAudioLanguage": lang,
            },
            "status": status_body,
        },
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    _mark_uploaded(topic, response["id"])
    print(f"[youtube_upload] {topic} -> https://youtube.com/shorts/{response['id']}")
    return response


def _next_local_morning_slot(after: datetime) -> datetime:
    local_after = after.astimezone(KST)
    candidate = local_after.replace(hour=DAILY_UPLOAD_HOUR_KST, minute=0, second=0, microsecond=0)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _fetch_queue_tail(lang: str) -> datetime | None:
    """그 채널(언어)에 이미 예약 게시로 올라가 있는 영상 중 가장 늦은 publishAt을
    조회한다 — backlog를 여러 번 나눠 돌려도 기존 예약과 안 겹치게(health-shorts
    fetch_queue_tails와 동일 원칙)."""
    try:
        youtube = build("youtube", "v3", credentials=_get_credentials(lang))
    except Exception:
        return None
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items", [])
    if not items:
        return None
    uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    latest: datetime | None = None
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist, maxResults=50, pageToken=page_token
        ).execute()
        video_ids = [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        if video_ids:
            videos = youtube.videos().list(part="status", id=",".join(video_ids)).execute()
            for v in videos.get("items", []):
                publish_at = v["status"].get("publishAt")
                if publish_at:
                    dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
                    if latest is None or dt > latest:
                        latest = dt
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return latest


def _candidates_for_lang(lang: str, uploaded: dict) -> list[str]:
    data_dir = ROOT / "data"
    out = []
    for topic_dir in sorted(data_dir.iterdir()):
        if not topic_dir.is_dir():
            continue
        full_topic = topic_dir.name if lang == "ko" else f"{topic_dir.name}/{lang}"
        if full_topic in uploaded:
            continue
        captions_path = _captions_path(full_topic, lang)
        if not captions_path.exists():
            continue
        spec = json.loads(captions_path.read_text(encoding="utf-8"))
        if not any(p["name"] == PLATFORM_NAME for p in spec.get("platforms", [])):
            continue
        try:
            _find_video_file(full_topic, lang)
        except FileNotFoundError:
            continue
        out.append(full_topic)
    return out


def upload_backlog(privacy_status: str = "private", langs: list[str] | None = None) -> None:
    """캡션+영상이 준비된 topic 전부를 언어(채널)별로 하루 한 개씩
    (DAILY_UPLOAD_HOUR_KST) 순서대로 예약 업로드한다."""
    uploaded = _load_uploaded()
    for lang in langs or ["ko", "en", "zh-TW"]:
        candidates = _candidates_for_lang(lang, uploaded)
        if not candidates:
            print(f"[youtube_upload] [{lang}] 업로드 대기 중인 topic 없음(캡션+영상 둘 다 준비된 것 기준)")
            continue
        tail = _fetch_queue_tail(lang)
        anchor = max(datetime.now(timezone.utc), tail) if tail else datetime.now(timezone.utc)
        next_slot = _next_local_morning_slot(anchor)
        for topic in candidates:
            publish_at = next_slot.strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                upload_short(topic, privacy_status=privacy_status, publish_at=publish_at)
            except HttpError as e:
                # mission-control에도 보고 — 미설정 세션이 대부분이라 실패해도
                # 조용히 넘어간다(lib/mission_control_log.py 상단 WHY 참고).
                report_issue(
                    severity="error", category="upload_failure",
                    entity=topic, message=str(e),
                )
                print(f"[youtube_upload] {topic} 실패: {e}")
                continue
            next_slot += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", nargs="?", help="단일 topic 업로드 (예: 갸스비_1 또는 돈키호테카레과자_JP_1/en)")
    parser.add_argument("privacy", nargs="?", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("publish_at", nargs="?", default=None, help="ISO 8601 UTC, 예: 2026-08-20T11:00:00Z")
    parser.add_argument("--backlog", action="store_true", help="대기 중인 topic 전부(ko/en/tw) 하루 한 개씩 예약 업로드")
    args = parser.parse_args()

    if args.backlog:
        upload_backlog(privacy_status=args.privacy if args.privacy != "private" else "private")
        return

    if not args.topic:
        parser.error("topic을 지정하거나 --backlog를 쓸 것")
    upload_short(args.topic, privacy_status=args.privacy, publish_at=args.publish_at)


if __name__ == "__main__":
    main()
