# footage_role 분류를 Finder에서 직접 훑어볼 수 있게 만드는 뷰. WHY: 분류 자체는
# _footage_catalog.json에만 있고 raw_footage는 폴더로 안 나누는 게 이 프로젝트
# 관례라(재분류 시마다 파일 이동 없이 JSON만 고치면 되도록) — 그런데 그러면
# 사람이 Finder로 볼 수 있는 게 없어서(2026-08-12, 사용자 지적), 실제 파일
# 이동/복사 없이 output/_segment_previews/*.mp4를 가리키는 심볼릭 링크만
# footage_role별 폴더에 만들어준다. 카탈로그가 바뀌면 이 스크립트를 다시
# 돌리기만 하면 뷰가 갱신됨(별도 소스오브트루스 아님).
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG = PROJECT_ROOT / "data" / "_footage_catalog.json"
PREVIEWS_DIR = PROJECT_ROOT / "output" / "_segment_previews"
BROWSER_DIR = PROJECT_ROOT / "output" / "_footage_browser"


def _sanitize(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)  # 괄호 안 영문 병기 제거(중복 정보라 파일명에서 생략)
    text = re.sub(r"[^\w가-힣]+", "_", text).strip("_")
    return text[:40] or "misc"


def build() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if BROWSER_DIR.exists():
        shutil.rmtree(BROWSER_DIR)
    for role in ("hero", "supporting", "broll"):
        (BROWSER_DIR / role).mkdir(parents=True, exist_ok=True)

    counts = {"hero": 0, "supporting": 0, "broll": 0, "skipped_no_preview": 0}
    for seg in catalog["segments"]:
        role = seg.get("footage_role")
        if role not in counts:
            continue
        preview = PREVIEWS_DIR / f"{seg['id']}.mp4"
        if not preview.exists():
            counts["skipped_no_preview"] += 1
            continue

        label = seg.get("brand") or seg.get("product_line") or "미분류"
        link_name = f"{_sanitize(label)}__{seg['id']}.mp4"
        link_path = BROWSER_DIR / role / link_name
        link_path.symlink_to(preview.resolve())
        counts[role] += 1

    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
