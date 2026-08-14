# 샤오홍슈(小红书/rednote) 영상 가져오기. WHY: 사용자가 저작권을 직접 확보한
# 링크만 준다는 전제 — 이 스크립트는 다운로드 자체보다 "누가 언제 무슨 근거로
# 이 클립을 썼는지" 추적 기록을 남기는 데 더 신경 쓴다(라이선스 없이 남의
# 영상을 긁어다 쓰는 스크립트로 오용되면 안 됨 — CLAUDE.md "샤오홍슈 콘텐츠
# 활용 규칙" 참고).
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LICENSE_LOG = PROJECT_ROOT / "data" / "_xiaohongshu_licenses.json"


def _load_license_log() -> list[dict]:
    if LICENSE_LOG.exists():
        return json.loads(LICENSE_LOG.read_text(encoding="utf-8"))
    return []


def _save_license_log(entries: list[dict]) -> None:
    LICENSE_LOG.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def import_clip(url: str, slug: str, brand: str | None, product_line: str | None,
                 scent: str | None, license_note: str) -> Path:
    """url(사용자가 저작권 확보했다고 확인한 샤오홍슈 게시물 링크)을 다운로드해서
    raw_footage/xiaohongshu/<slug>.mp4에 저장하고(폴더는 안 나눔 — 실제 분류는
    data/_footage_catalog.json이 tags/brand/product_line로 담당, 여기 폴더
    트리로 또 나누면 이중 관리라 폐기했음 — CLAUDE.md 참고),
    data/_xiaohongshu_licenses.json에 출처·근거를 기록한다."""
    out_dir = PROJECT_ROOT / "raw_footage" / "xiaohongshu"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"{slug}.mp4"

    subprocess.run(
        ["yt-dlp", "-o", str(final_path.with_suffix(".%(ext)s")),
         "--merge-output-format", "mp4", url],
        cwd=PROJECT_ROOT, check=True,
    )
    if not final_path.exists():
        raise RuntimeError(f"다운로드 결과를 못 찾음: {final_path}")

    entries = _load_license_log()
    entries.append({
        "file": str(final_path.relative_to(PROJECT_ROOT)),
        "source_url": url,
        "brand": brand,
        "product_line": product_line,
        "scent": scent,
        "license_note": license_note,
        "imported_at": str(date.today()),
    })
    _save_license_log(entries)

    print(f"다운로드 완료: {final_path}")
    print(f"라이선스 기록 추가: {LICENSE_LOG}")
    return final_path


if __name__ == "__main__":
    if len(sys.argv) < 7:
        print("사용법: python3 -m lib.xiaohongshu_import <url> <slug> <브랜드|-> <제품라인|-> <향/맛|-> <라이선스근거메모>")
        sys.exit(1)
    _norm = lambda v: None if v == "-" else v  # noqa: E731
    import_clip(sys.argv[1], sys.argv[2], _norm(sys.argv[3]), _norm(sys.argv[4]),
                _norm(sys.argv[5]), sys.argv[6])
