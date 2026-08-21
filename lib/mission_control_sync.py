# mission-control "게시물 발행" 탭용 캡션 동기화 스크립트. WHY: health-shorts의
# 같은 이름 스크립트(health-shorts/lib/mission_control_sync.py)를 이 저장소로
# 이식한 것 — mission-control은 Vercel에 배포돼 어디서든 접근 가능한 반면
# 이 프로젝트 자신의 대시보드(output/<topic>/dashboard.html)는 로컬 파일시스템
# 기반이라 mission-control이 직접 못 읽는다. 이 스크립트는 이 프로젝트의 수동
# 포스팅 대상 캡션을 mission-control 쪽 공유 Supabase(health-shorts 자신의
# Supabase 프로젝트, `mission_control` 스키마)로 미리 합쳐둔다.
#
# ⚠️ 2026-08-21, hs_platform_captions에 project 컬럼 추가(UNIQUE도
# (project, topic, platform_name)로 갱신)로 health-shorts·jp-review-shorts·
# 1bite-history 세 프로젝트가 같은 테이블을 공유하게 됨 — 이 저장소는 이
# 스크립트 안에서 project="jp-review-shorts" 고정. 세 저장소가 서로의 코드를
# 실시간 참조하지 않는 이 워크스페이스 관례상 파일을 복사해 이식한 것이라,
# 공용 로직(청크 분할·upsert 헤더 등)을 고치게 되면 다른 두 프로젝트의 사본도
# 함께 갱신할 것.
#
# 사용법: python3 -m lib.mission_control_sync [--commit]
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# WHY 이 저장소가 실제로 다루는 플랫폼은 네이버 블로그/네이버 클립/페이스북/
# YouTube Shorts/인스타그램 릴스 5개뿐(16개 topic 전수 확인, 쓰레드·틱톡
# 없음) — 그중 YouTube Shorts는 `lib/youtube_upload.py --backlog`로 자동
# 업로드되므로 health-shorts의 `_UI_EXCLUDED_PLATFORMS`(자동 업로드/UI 제외
# 대상은 수동 포스팅 캡션 동기화 대상도 아니라는 원칙)와 동일한 이유로 뺀다.
# "유튜브 쇼츠" 한글 표기는 이 저장소 데이터에 없지만(전수 확인 결과 "YouTube
# Shorts" 영문 표기만 존재) 향후 topic이 다른 표기로 쓸 가능성을 대비해
# 같이 등록해둔다. 이 저장소 자신의 `lib/dashboard.py`는 아직 이 제외 로직이
# 없어 로컬 대시보드엔 YouTube Shorts 카드가 그대로 보이지만(옛 버전을 포크한
# 뒤 health-shorts 쪽 개선을 못 따라간 상태), mission-control 쪽만이라도 실제
# 자동화 상태에 맞게 제외하는 게 맞다고 판단해 여기서만 뺀다 — dashboard.py는
# 이번 작업 범위 밖이라 건드리지 않았다.
_UI_EXCLUDED_PLATFORMS = {"YouTube Shorts", "유튜브 쇼츠"}


def collect_rows() -> list[dict]:
    """data/<topic>/platform_captions.json(언어 변형 `.en.json`/`.zh-TW.json`은
    대상 아님 — 기본판만)을 스캔해 수동 포스팅 대상 플랫폼만 골라 행 목록으로
    만든다."""
    data_dir = ROOT / "data"
    rows: list[dict] = []
    if not data_dir.is_dir():
        return rows

    for topic_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        captions_path = topic_dir / "platform_captions.json"
        if not captions_path.exists():
            continue
        try:
            data = json.loads(captions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  ⚠️  {topic_dir.name}: platform_captions.json 파싱 실패, 건너뜀")
            continue

        for p in data.get("platforms", []):
            name = p.get("name")
            if not name or name in _UI_EXCLUDED_PLATFORMS:
                continue
            caption = p.get("caption")
            if not caption:
                continue
            rows.append({
                "project": "jp-review-shorts",
                "topic": topic_dir.name,
                "platform_name": name,
                "network": p.get("network"),
                "type": p.get("type"),
                "url": p.get("url") or "",
                "caption": caption,
                "no_caption_link": bool(p.get("no_caption_link")),
            })
    return rows


def push_to_supabase(rows: list[dict]) -> int:
    import urllib.request
    import urllib.error

    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 .env에 설정되어 있지 않습니다.")

    body = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/hs_platform_captions?on_conflict=project,topic,platform_name",
        data=body,
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Accept-Profile": "mission_control",
            "Content-Profile": "mission_control",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"upsert 실패: {e.code} {e.read().decode(errors='replace')}") from e
    return len(rows)


def main() -> None:
    commit = "--commit" in sys.argv
    rows = collect_rows()
    by_topic: dict[str, int] = {}
    for r in rows:
        by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
    print(f"topic {len(by_topic)}개, 플랫폼 행 {len(rows)}개 발견")
    for topic, count in by_topic.items():
        print(f"  {topic}: {count}개 플랫폼")

    if not commit:
        print("\ndry-run — DB에 쓰지 않았습니다. 실제로 넣으려면 --commit을 추가하세요.")
        return

    # WHY 500개씩 나눠 보내는지: Supabase REST 요청 payload 크기 제한 대비
    # (health-shorts 원본 스크립트와 동일한 안전장치 — 이 저장소는 16 topic×
    # 4행 안팎이라 실제로는 청크 1개면 끝나지만, 다른 두 프로젝트 사본과
    # 로직을 맞춰두는 편이 유지보수에 낫다).
    total = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        total += push_to_supabase(chunk)
    print(f"\n{total}개 행을 mission_control.hs_platform_captions에 upsert했습니다.")


if __name__ == "__main__":
    main()
