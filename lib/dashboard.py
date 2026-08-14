# topic별 업로드 대시보드 생성기(2026-08-14). WHY: 포스팅 API가 없는
# 플랫폼(네이버 블로그·클립·페이스북·유튜브 등)이라 자동 업로드가 불가능 —
# 영상 미리보기 + 플랫폼별 캡션(수정 가능)을 한 페이지에 모아두고, 사람이
# 확인한 뒤 버튼 눌러 플랫폼으로 이동해서 수동 업로드하는 흐름을 지원한다.
# health-shorts의 lib/dashboard.py(CARD_TEMPLATE·완료 체크·Supabase
# posting_log 연동)와 같은 인터랙션 언어를 그대로 재사용 — 완료 체크가
# 같은 Supabase 테이블에 쌓이므로 나중에 두 프로젝트를 한 곳에서 집계해도
# topic 이름만 겹치지 않으면 문제없다("health-shorts와 완전 별개
# 프로젝트"라는 CLAUDE.md 원칙과는 무관 — 이건 순수 포스팅 상태 추적용
# 공유 테이블이라 콘텐츠 파이프라인 자체를 공유하는 게 아님).
#
# 사용법: python3 -m lib.dashboard <topic>
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


CARD_TEMPLATE = """
<div class="platform-card" data-done-key="{done_key}">
  <div class="platform-head">
    <div class="platform-name-wrap">
      <span class="type-badge badge-{type}">{type_label}</span>
      <h3>{name}</h3>
    </div>
    <div class="head-actions">
      <label class="done-check">
        <input type="checkbox" class="done-toggle" data-key="{done_key}" data-name="{name}">
        <span>완료</span>
      </label>
      <a class="btn-go" href="{url}" target="_blank" rel="noopener">열기 →</a>
    </div>
  </div>
  {action_line}
  <textarea class="caption-box" id="cap-{idx}" spellcheck="false">{caption}</textarea>
  <div class="card-actions">
    <button class="btn-copy" data-target="cap-{idx}">캡션 복사</button>
    <a class="btn-go" href="{url}" target="_blank" rel="noopener">열기 →</a>
    <span class="edit-hint">직접 수정 가능</span>
  </div>
</div>
"""

TYPE_LABEL = {"video": "영상", "text": "텍스트"}

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title} — 일본상품리뷰 업로드 대시보드</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg-top: #fdf5f5; --bg-bottom: #f6e8e8;
    --ink: #241f1f; --ink-soft: #8b7676;
    --accent: #c62828; --accent-deep: #8e1c1c; --accent-soft: #fbdede;
    --gold: #b27a26; --gold-soft: #f1e3c6; --panel: #fffdfd; --rule: #ecdada;
    --video: #3a6ea5; --video-soft: #dbe9f7;
    --text: #4a8f6b; --text-soft: #dcefe4;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", -apple-system, sans-serif;
    background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
    color: var(--ink);
  }}
  header {{ padding: 32px 24px 20px; text-align: center; }}
  header .eyebrow {{
    display: inline-block; background: var(--accent); color: #fff; font-size: 12px; font-weight: 700;
    padding: 5px 16px; border-radius: 999px; margin-bottom: 12px;
  }}
  header h1 {{ margin: 0; font-size: 22px; line-height: 1.4; }}
  main {{ max-width: 720px; margin: 0 auto; padding: 0 20px 60px; display: flex; flex-direction: column; gap: 20px; }}
  .video-block {{ text-align: center; }}
  .video-block video {{
    width: 100%; max-width: 300px; aspect-ratio: 9/16; border-radius: 14px; background: #000;
  }}
  .video-block .dl-link {{
    display: inline-block; margin-top: 10px; background: var(--accent); color: #fff; text-decoration: none;
    font-size: 13px; font-weight: 700; padding: 9px 18px; border-radius: 999px;
  }}
  .platform-card {{
    background: var(--panel); border: 1px solid var(--rule); border-radius: 16px; padding: 18px;
    display: flex; flex-direction: column; gap: 10px;
  }}
  .platform-card.is-done {{ opacity: 0.55; border-color: var(--gold); background: var(--gold-soft); }}
  .platform-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }}
  .platform-name-wrap {{ display: flex; align-items: center; gap: 8px; }}
  .platform-name-wrap h3 {{ margin: 0; font-size: 16px; }}
  .type-badge {{
    font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  }}
  .badge-video {{ background: var(--video-soft); color: var(--video); }}
  .badge-text {{ background: var(--text-soft); color: var(--text); }}
  .head-actions {{ display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }}
  .done-check {{
    display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700;
    color: var(--ink-soft); cursor: pointer; user-select: none;
  }}
  .done-check input {{ width: 15px; height: 15px; accent-color: var(--accent); cursor: pointer; }}
  .platform-card.is-done .done-check {{ color: var(--gold); }}
  .btn-go {{
    background: var(--accent); color: #fff; text-decoration: none; font-size: 13px; font-weight: 700;
    padding: 9px 16px; border-radius: 999px; white-space: nowrap;
  }}
  .action-line {{ font-size: 13px; color: var(--ink-soft); line-height: 1.5; }}
  .caption-box {{
    width: 100%; min-height: 140px; border: 1px solid var(--rule); border-radius: 10px; padding: 12px;
    font-family: inherit; font-size: 13px; line-height: 1.6; color: var(--ink); resize: vertical;
  }}
  .card-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .btn-copy {{
    background: var(--accent-soft); color: var(--accent-deep); border: none; border-radius: 999px;
    padding: 9px 16px; font-size: 13px; font-weight: 700; cursor: pointer; font-family: inherit;
  }}
  .btn-copy.copied {{ background: var(--gold-soft); color: var(--gold); }}
  .edit-hint {{ font-size: 12px; color: var(--ink-soft); }}
</style>
</head>
<body>
<header>
  <span class="eyebrow">일본상품리뷰</span>
  <h1>{title}</h1>
</header>
<main>
  <div class="video-block">
    <video src="{video_path}" controls preload="metadata"></video><br>
    <a class="dl-link" href="{video_path}" download>영상 다운로드</a>
  </div>
  {cards}
</main>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.js"></script>
<script>
const sb = window.supabase.createClient(
  "https://feqjksocdkjqwbeugaiw.supabase.co",
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZlcWprc29jZGtqcXdiZXVnYWl3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYxNzgwOTYsImV4cCI6MjEwMTc1NDA5Nn0.f0ZacCpKcjr3weFCLX4QZfU9ejB5tbjEXJs6hZzZrTA"
);
const TOPIC_NAME = {topic_json};
const STORAGE_PREFIX = "jpr_done_" + TOPIC_NAME + "_";

document.querySelectorAll(".btn-copy").forEach(btn => {{
  btn.addEventListener("click", async () => {{
    const ta = document.getElementById(btn.dataset.target);
    try {{
      await navigator.clipboard.writeText(ta.value);
      btn.textContent = "복사됨"; btn.classList.add("copied");
      setTimeout(() => {{ btn.textContent = "캡션 복사"; btn.classList.remove("copied"); }}, 1500);
    }} catch (err) {{ btn.textContent = "복사 실패"; }}
  }});
}});

(async () => {{
  const {{ data: dbPosted }} = await sb.from("posting_log").select("platform").eq("topic", TOPIC_NAME);
  const dbPostedSet = new Set((dbPosted || []).map(r => r.platform));

  document.querySelectorAll(".done-toggle").forEach(cb => {{
    const storageKey = STORAGE_PREFIX + cb.dataset.key;
    const card = cb.closest(".platform-card");
    if (localStorage.getItem(storageKey) || dbPostedSet.has(cb.dataset.name)) {{
      cb.checked = true;
      card.classList.add("is-done");
    }}
    cb.addEventListener("change", () => {{
      if (cb.checked) {{
        const postedAt = new Date().toISOString();
        localStorage.setItem(storageKey, JSON.stringify({{ topic: TOPIC_NAME, platform: cb.dataset.name, postedAt }}));
        card.classList.add("is-done");
        sb.from("posting_log").upsert({{ topic: TOPIC_NAME, platform: cb.dataset.name, posted_at: postedAt }});
      }} else {{
        localStorage.removeItem(storageKey);
        card.classList.remove("is-done");
        sb.from("posting_log").delete().eq("topic", TOPIC_NAME).eq("platform", cb.dataset.name);
      }}
    }});
  }});
}})();
</script>
</body>
</html>
"""


def generate(topic: str, out_path: str | None = None) -> Path:
    data_dir = ROOT / "data" / topic
    output_dir = ROOT / "output" / topic
    captions_path = data_dir / "platform_captions.json"
    spec = json.loads(captions_path.read_text(encoding="utf-8"))
    title = spec.get("title") or topic

    video_path = None
    for name in ("demo_preview.mp4", f"{topic}_shorts.mp4"):
        if (output_dir / name).exists():
            video_path = name
            break

    cards = []
    for idx, p in enumerate(spec.get("platforms", [])):
        if "name" not in p:
            continue
        ptype = p.get("type", "text")
        done_key = p["name"].lower().replace(" ", "_")
        action_line = f'<div class="action-line">{_esc(p["action"])}</div>' if p.get("action") else ""
        cards.append(CARD_TEMPLATE.format(
            done_key=_esc(done_key),
            type=ptype,
            type_label=TYPE_LABEL.get(ptype, "텍스트"),
            name=_esc(p["name"]),
            url=_esc(p.get("url", "#")),
            action_line=action_line,
            idx=idx,
            caption=_esc(p.get("caption", "")),
        ))
        if "#" not in p.get("caption", ""):
            print(f"⚠️  경고: '{p['name']}' 캡션에 해시태그가 없습니다")

    html = PAGE_TEMPLATE.format(
        title=_esc(title),
        video_path=_esc(video_path) if video_path else "",
        cards="".join(cards),
        topic_json=json.dumps(topic, ensure_ascii=False),
    )

    out_path = Path(out_path) if out_path else output_dir / "dashboard.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"대시보드 생성 완료: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 -m lib.dashboard <topic>")
        sys.exit(1)
    generate(sys.argv[1])
