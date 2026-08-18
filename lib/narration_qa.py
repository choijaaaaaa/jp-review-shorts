# 나레이션 텍스트 TTS 오독 위험 검사(2026-08-17 도입). WHY: 콜라겐씨젤리_JP_1
# 나레이션 "미용 성분이 7종에서 10종으로"가 TTS에서 "칠곱종"/"셥종"처럼
# 한자어(칠·십)와 순우리말(일곱·열) 숫자 읽기가 뒤섞여 깨지는 사고가 실제로
# 발생했다(사용자 청취로 발견) — "N+순우리말 단위 카운터"(개/명/종/가지/번/
# 살/마리 등) 조합은 순우리말 숫자(하나~아흔아홉)로 읽는 게 자연스러운데,
# 원문에 아라비아 숫자를 그대로 써두면 TTS가 어느 쪽으로 읽을지 헷갈려한다.
# health-shorts CLAUDE.md의 "소수점 퍼센트는 말로 풀어 쓸 것" 규칙과 같은
# 문제의식 — 화면 표시용 텍스트(캡션 등)는 대상 아님, TTS가 실제로 읽는
# narration.txt만 검사한다.
#
# 이 프로젝트엔 health-shorts의 content_review.py 같은 자동 QA 파이프라인이
# 아직 없어서(원래 계획 문서 "content_review.py의 자동 검사는 이후 확장
# 단계로 미룬다" 참고) 독립 스크립트로 둔다 — narration.txt를 다 쓴 직후,
# TTS 호출 전에 항상 이 스크립트로 한 번 확인할 것.
#
# 사용법: python3 -m lib.narration_qa data/<topic>/narration.txt
from __future__ import annotations

import re
import sys
from pathlib import Path

# WHY 이 목록만 검사하는지: 모든 숫자가 위험한 게 아니다 — 연도(2011년),
# 큰 수(3만 7,500밀리그램), 퍼센트(20퍼센트)는 이미 한자어 숫자로 자연스럽게
# 읽히거나 이미 별도 규칙(퍼센트 풀어쓰기)이 있다. 문제는 순우리말이
# 표준인 "작은 수 + 카운터" 조합뿐이다.
COUNTERS = ["종", "개", "명", "가지", "번", "살", "마리", "장", "권", "벌", "켤레", "판", "알", "조각", "군데"]
_PATTERN = re.compile(r"(?<![\d.])(\d{1,2})(" + "|".join(COUNTERS) + ")")


def find_number_counter_risks(text: str) -> list[tuple[int, str, str]]:
    """(줄 번호, 매치된 문자열, 그 줄 전체 텍스트) 리스트를 반환."""
    risks = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        for m in _PATTERN.finditer(line):
            risks.append((i, m.group(0), line))
    return risks


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 -m lib.narration_qa <narration.txt 경로>")
        sys.exit(1)
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    risks = find_number_counter_risks(text)
    if not risks:
        print(f"[narration_qa] {path}: 이상 없음")
        sys.exit(0)
    print(f"[narration_qa] {path}: {len(risks)}건 발견 — 순우리말 숫자로 풀어쓸 것 "
          f"(예: '7종' -> '일곱 종', '10개' -> '열 개')")
    for line_no, match, line in risks:
        print(f"  줄 {line_no}: {match!r}  ->  {line}")
    sys.exit(1)
