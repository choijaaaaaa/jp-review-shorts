# raw_footage/_general/ 인트로 클립 사용 편중 검사(2026-08-17 도입). WHY:
# 사용자가 "인트로 초반 영상이 거의 똑같은거로 계속 쓰는데"라고 지적 —
# 실측해보니 raw_footage/_general/엔 7개 클립이 있는데 14개 topic 중 9개가
# 그중 딱 하나(20260809_140627.mp4)만 골라 썼고, 3개는 한 번도 안 쓰였다.
# 코드가 강제한 게 아니라(Stage 3 프레임 식별은 세션이 육안으로 직접
# 고르는 수동 단계) 여러 세션이 매번 "정렬했을 때 가장 먼저 나오는 파일"을
# 습관적으로 골라서 생긴 편중으로 보인다.
#
# 이 스크립트는 새 topic의 intro_broll을 정할 때 "지금 가장 안 쓰인 클립이
# 뭔지" 바로 보여준다 — 강제하는 게이트는 아니고(빌드를 막지 않음),
# CLAUDE.md 관례상 새 topic 작성 전에 한 번 실행해서 참고할 것.
#
# 사용법: python3 -m lib.check_intro_variety
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERAL_DIR = ROOT / "raw_footage" / "_general"


def collect_usage() -> Counter:
    usage: Counter = Counter()
    for spec_path in sorted((ROOT / "data").glob("*/jp_review_spec.json")):
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        intro = spec.get("intro_broll") or []
        if not intro:
            continue
        clip = intro[0].get("clip", "")
        if clip.startswith("raw_footage/_general/"):
            usage[Path(clip).name] += 1
    return usage


if __name__ == "__main__":
    usage = collect_usage()
    all_clips = sorted(p.name for p in GENERAL_DIR.glob("*.mp4")) if GENERAL_DIR.is_dir() else []

    print("=== raw_footage/_general/ 인트로 첫 클립 사용 현황 ===")
    for name in all_clips:
        count = usage.get(name, 0)
        marker = " ⚠️ 미사용" if count == 0 else ""
        print(f"  {name}: {count}회{marker}")

    unused = [n for n in all_clips if usage.get(n, 0) == 0]
    if unused:
        print(f"\n다음 topic의 intro_broll[0]엔 아래 중 하나를 우선 고려할 것: {', '.join(unused)}")
    elif usage:
        least = min(usage, key=lambda k: usage[k])
        print(f"\n가장 적게 쓰인 클립: {least} ({usage[least]}회)")
