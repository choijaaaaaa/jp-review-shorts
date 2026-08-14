# health-shorts 메인 페이지의 "🇯🇵 일본상품리뷰" 탭이 읽는 topic 목록
# 집계 스크립트(2026-08-14). WHY: health-shorts index.html이 이 프로젝트
# 파일을 직접 fetch()할 수 있게(형제 디렉터리 상대경로) output/topics_hub.json
# 하나로 topic·title·dashboard_path만 모아둔다 — card_news_hub.json과
# 달리 캡션 전문을 담지 않는다(각 topic 자체 dashboard.html이 그 역할을
# 하므로 여기는 목록/링크 용도만).
#
# 사용법: python3 -m lib.jp_review_hub
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def collect_topics() -> dict:
    topics = []
    for captions_path in sorted((ROOT / "data").glob("*/platform_captions.json")):
        topic = captions_path.parent.name
        try:
            spec = json.loads(captions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        dashboard = ROOT / "output" / topic / "dashboard.html"
        if not dashboard.exists():
            continue
        topics.append({
            "topic": topic,
            "title": spec.get("title") or topic,
            "dashboard_path": f"output/{topic}/dashboard.html",
        })
    return {"topics": topics}


def write_hub_json(out_path: Path | None = None) -> Path:
    out_path = out_path or (ROOT / "output" / "topics_hub.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(collect_topics(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    path = write_hub_json()
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"{path}에 저장했습니다 — topic {len(data['topics'])}건")
    for t in data["topics"]:
        print(f"  {t['topic']}: {t['title']}")
