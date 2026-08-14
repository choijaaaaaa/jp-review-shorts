# 로컬 전용 제작 보조 도구(2026-08-13). WHY 이 파일이 필요한지: jp-review-shorts
# 파이프라인의 칠판 씬 상단에는 사용자가 직접 찍은 "실사용(먹는/쓰는) 영상"이
# 들어가야 하는데, 그 영상을 자르고 무음 처리해서 정확한 위치에 넣는 작업을
# 매번 세션에게 시켜서 ffmpeg 명령을 손으로 부르는 건 비효율적 — 브라우저에서
# 구간만 지정하면 되는 최소 UI를 로컬 서버로 띄운다. 배포용 아님, 인증 없음,
# 반드시 localhost 바인딩만 쓴다(아래 app.run 참고).
#
# 실행: .venv/bin/python3 -m lib.local_studio  (기본 http://127.0.0.1:5151)
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.templates.proto_jp_review import (  # noqa: E402
    compute_scene_durations, render_single_product, usage_clip_path_for_topic,
)
from lib.xiaohongshu_import import import_clip  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
RAW_FOOTAGE_DIR = PROJECT_ROOT / "raw_footage"
USER_TRIM_DIR = RAW_FOOTAGE_DIR / "user_trim"
UPLOAD_TMP_DIR = PROJECT_ROOT / "output" / "_studio_uploads"
ALLOWED_MEDIA_ROOTS = [RAW_FOOTAGE_DIR, OUTPUT_DIR]
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".m4v"}

app = Flask(__name__)


def _single_product_topics() -> list[str]:
    """jp_review_spec.json이 있고 신규(단일 상품 딥다이브) 스키마인 topic만
    대상으로 한다 — "hook_lines" 키로 구분(2026-08-13 이전 멀티상품 스키마인
    갸스비_1은 이 키가 없어서 자동 제외됨; 그 topic은 이미 구 파이프라인으로
    렌더 완료된 별개 산출물이라 이 도구 대상이 아님)."""
    topics = []
    for spec_path in DATA_DIR.glob("*/jp_review_spec.json"):
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "hook_lines" in spec:
            topics.append(spec_path.parent.name)
    return sorted(topics)


def _topic_paths(topic: str) -> dict:
    return {
        "topic_dir": DATA_DIR / topic,
        "spec_path": DATA_DIR / topic / "jp_review_spec.json",
        "audio_path": OUTPUT_DIR / topic / "narration.mp3",
        "srt_path": OUTPUT_DIR / topic / "narration.srt",
        "out_path": OUTPUT_DIR / topic / "demo_preview.mp4",
    }


def _resolve_media_path(rel_path: str) -> Path:
    """요청받은 상대경로를 PROJECT_ROOT 기준 절대경로로 바꾸고, 허용된 루트
    (raw_footage/output) 밖으로 못 나가게 검증한다 — path traversal 방지."""
    candidate = (PROJECT_ROOT / rel_path).resolve()
    for root in ALLOWED_MEDIA_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            if candidate.exists():
                return candidate
        except ValueError:
            continue
    raise FileNotFoundError(f"허용되지 않거나 존재하지 않는 경로: {rel_path}")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/topics")
def api_topics():
    result = []
    for topic in _single_product_topics():
        paths = _topic_paths(topic)
        entry = {"topic": topic, "usage_status": "no_pending_spec"}
        pending_path = paths["topic_dir"] / "pending_clips.json"
        if pending_path.exists():
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            entry.update(pending.get("usage_clip", {}))
        if paths["srt_path"].exists() and paths["audio_path"].exists() and paths["spec_path"].exists():
            try:
                spec = json.loads(paths["spec_path"].read_text(encoding="utf-8"))
                durations = compute_scene_durations(spec, str(paths["srt_path"]), str(paths["audio_path"]))
                entry["target_duration"] = round(durations["chalkboard"], 2)
            except Exception as e:  # noqa: BLE001 — 대시보드 표시용이라 실패해도 조용히 스킵
                entry["duration_error"] = str(e)
        entry["usage_clip_exists"] = usage_clip_path_for_topic(topic).exists()
        result.append(entry)
    return jsonify(result)


@app.get("/api/files")
def api_files():
    files = []
    for root in [RAW_FOOTAGE_DIR]:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in ALLOWED_VIDEO_EXT:
                files.append(str(p.relative_to(PROJECT_ROOT)))
    return jsonify(files)


@app.get("/media/<path:rel_path>")
def media(rel_path: str):
    try:
        real_path = _resolve_media_path(rel_path)
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    return send_file(real_path)


@app.post("/api/trim")
def api_trim():
    """구간(in_s~out_s)을 잘라 무음화해서 raw_footage/user_trim/<topic>_usage.mp4에
    저장하고, 곧바로 그 topic을 재렌더링한다. source는 기존 파일 경로
    (existing_path) 또는 새로 업로드한 파일(file) 중 하나."""
    topic = request.form.get("topic", "")
    if topic not in _single_product_topics():
        return jsonify({"ok": False, "error": f"알 수 없는 topic: {topic}"}), 400
    try:
        in_s = float(request.form.get("in_s", "0"))
        out_s = float(request.form.get("out_s", "0"))
    except ValueError:
        return jsonify({"ok": False, "error": "in_s/out_s가 숫자가 아님"}), 400
    if out_s <= in_s:
        return jsonify({"ok": False, "error": "out_s는 in_s보다 커야 함"}), 400

    existing_path = request.form.get("existing_path", "").strip()
    upload = request.files.get("file")

    if existing_path:
        try:
            source_path = _resolve_media_path(existing_path)
        except FileNotFoundError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    elif upload and upload.filename:
        UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_VIDEO_EXT:
            return jsonify({"ok": False, "error": f"허용 안 되는 확장자: {suffix}"}), 400
        source_path = UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
        upload.save(source_path)
    else:
        return jsonify({"ok": False, "error": "existing_path 또는 file 중 하나는 필요함"}), 400

    USER_TRIM_DIR.mkdir(parents=True, exist_ok=True)
    out_target = usage_clip_path_for_topic(topic)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{in_s}", "-to", f"{out_s}", "-i", str(source_path),
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_target)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": f"트림 실패: {e.stderr[-800:]}"}), 500

    paths = _topic_paths(topic)
    render_error = None
    try:
        render_single_product(str(paths["topic_dir"]), str(paths["audio_path"]),
                               str(paths["srt_path"]), str(paths["spec_path"]),
                               str(paths["out_path"]))
    except Exception as e:  # noqa: BLE001 — 트림 자체는 성공했으니 렌더 에러는 별도로 보고
        render_error = str(e)

    return jsonify({
        "ok": render_error is None,
        "trimmed_path": str(out_target.relative_to(PROJECT_ROOT)),
        "rendered_path": str(paths["out_path"].relative_to(PROJECT_ROOT)) if render_error is None else None,
        "render_error": render_error,
    })


@app.post("/api/render/<topic>")
def api_render(topic: str):
    if topic not in _single_product_topics():
        return jsonify({"ok": False, "error": f"알 수 없는 topic: {topic}"}), 400
    paths = _topic_paths(topic)
    try:
        render_single_product(str(paths["topic_dir"]), str(paths["audio_path"]),
                               str(paths["srt_path"]), str(paths["spec_path"]),
                               str(paths["out_path"]))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "rendered_path": str(paths["out_path"].relative_to(PROJECT_ROOT))})


@app.post("/api/xiaohongshu_import")
def api_xiaohongshu_import():
    """CLAUDE.md 샤오홍슈 규칙: 저작권을 사용자가 직접 확보한 링크만 쓴다는
    전제라 license_note를 필수값으로 강제한다(빈 값 통과 금지) — 다운로드
    버튼을 누르는 것 자체가 '내가 이 링크의 사용 권한을 확인했다'는 의미가
    되도록, 최소한 근거 한 줄은 남기게 한다."""
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    slug = data.get("slug", "").strip()
    license_note = data.get("license_note", "").strip()
    if not url or not slug or not license_note:
        return jsonify({"ok": False, "error": "url/slug/license_note는 모두 필수"}), 400
    if not (url.startswith("https://www.xiaohongshu.com/") or url.startswith("https://xhslink.com/")):
        return jsonify({"ok": False, "error": "샤오홍슈(xiaohongshu.com/xhslink.com) 링크만 허용"}), 400

    _norm = lambda v: (v.strip() or None) if isinstance(v, str) else None  # noqa: E731
    try:
        out_path = import_clip(url, slug, _norm(data.get("brand")), _norm(data.get("product_line")),
                                _norm(data.get("scent")), license_note)
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": f"yt-dlp 다운로드 실패: {e}"}), 500
    return jsonify({"ok": True, "path": str(out_path.relative_to(PROJECT_ROOT))})


# ---------------------------------------------------------------------------
# 대시보드 페이지
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>jp-review-shorts 로컬 스튜디오</title>
<style>
  body { font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; background:#111; color:#eee; margin:0; padding:24px; }
  h1 { font-size:20px; } h2 { font-size:16px; color:#ffd14d; margin-top:32px; }
  .card { background:#1c1c1f; border:1px solid #333; border-radius:10px; padding:16px; margin-bottom:16px; }
  .desc { color:#bbb; font-size:14px; line-height:1.5; }
  .query { color:#ffd14d; font-family:monospace; }
  video { width:100%; max-width:340px; background:#000; border-radius:6px; }
  label { display:block; margin-top:10px; font-size:13px; color:#aaa; }
  input[type=text], input[type=number], select { width:100%; box-sizing:border-box; padding:6px; margin-top:4px;
    background:#0c0c0e; border:1px solid #444; color:#eee; border-radius:4px; }
  button { margin-top:12px; padding:8px 14px; background:#ffd14d; color:#111; border:none; border-radius:6px;
    font-weight:bold; cursor:pointer; }
  button:disabled { opacity:0.5; cursor:default; }
  .row { display:flex; gap:8px; } .row > div { flex:1; }
  .status { font-size:13px; margin-top:8px; white-space:pre-wrap; }
  .status.ok { color:#7bd88f; } .status.err { color:#ff8080; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; margin-left:8px; }
  .badge.pending { background:#553; color:#fd6; } .badge.fulfilled { background:#353; color:#8f8; }
  .badge.research { background:#335; color:#9cf; }
</style></head>
<body>
<h1>jp-review-shorts 로컬 스튜디오</h1>
<div id="topics"></div>

<h2>샤오홍슈 링크로 영상 가져오기</h2>
<div class="card">
  <label>링크(xiaohongshu.com / xhslink.com)</label>
  <input type="text" id="xhs-url" placeholder="https://www.xiaohongshu.com/...">
  <div class="row">
    <div><label>파일명(slug)</label><input type="text" id="xhs-slug" placeholder="예: samyang_curry_eat_01"></div>
    <div><label>브랜드(선택)</label><input type="text" id="xhs-brand"></div>
  </div>
  <div class="row">
    <div><label>제품라인(선택)</label><input type="text" id="xhs-product"></div>
    <div><label>향/맛(선택)</label><input type="text" id="xhs-scent"></div>
  </div>
  <label>라이선스 근거(필수 — 이 링크를 쓸 권리가 있는 이유 한 줄)</label>
  <input type="text" id="xhs-license" placeholder="예: 게시자 본인에게 DM으로 사용 허락받음">
  <button onclick="doXhsImport()">다운로드</button>
  <div id="xhs-status" class="status"></div>
</div>

<script>
async function loadTopics() {
  const res = await fetch('/api/topics');
  const topics = await res.json();
  const el = document.getElementById('topics');
  el.innerHTML = '';
  for (const t of topics) {
    const div = document.createElement('div');
    div.className = 'card';
    let statusBadge = '<span class="badge pending">대기중</span>';
    if (t.usage_clip_exists) {
      statusBadge = '<span class="badge fulfilled">완료(실사용 영상)</span>';
    } else if (t.status === 'fulfilled_via_research') {
      statusBadge = '<span class="badge research">완료(리서치 자료 스크롤)</span>';
    }
    div.innerHTML = `
      <h2 style="margin-top:0">${t.topic} ${statusBadge}</h2>
      <div class="desc">${t.description || '(설명 없음)'}</div>
      ${t.xiaohongshu_query ? `<div class="desc">샤오홍슈 검색어: <span class="query">${t.xiaohongshu_query}</span></div>` : ''}
      ${t.target_duration ? `<div class="desc">필요한 길이: 약 <b>${t.target_duration}초</b> (이 시간에 맞춰 자르면 됨, 짧으면 자동 반복재생됨)</div>` : ''}
      <label>기존 파일에서 선택</label>
      <select data-topic="${t.topic}" class="file-select"><option value="">-- 직접 업로드 --</option></select>
      <label>또는 새 파일 업로드</label>
      <input type="file" accept="video/*" class="file-input" data-topic="${t.topic}">
      <video class="preview" data-topic="${t.topic}" controls></video>
      <div class="row">
        <div><label>시작(초)</label><input type="number" step="0.1" class="in-s" data-topic="${t.topic}" value="0"></div>
        <div><label>끝(초)</label><input type="number" step="0.1" class="out-s" data-topic="${t.topic}" value="5"></div>
      </div>
      <button data-topic="${t.topic}" class="use-current-time" type="button">현재 재생 위치를 시작으로</button>
      <button data-topic="${t.topic}" class="trim-btn" type="button">잘라서 적용 + 재조립</button>
      <div class="status" data-topic="${t.topic}"></div>
    `;
    el.appendChild(div);
  }
  document.querySelectorAll('.file-select').forEach(sel => populateFileSelect(sel));
  document.querySelectorAll('.file-input').forEach(inp => inp.addEventListener('change', onFileChosen));
  document.querySelectorAll('.file-select').forEach(sel => sel.addEventListener('change', onExistingChosen));
  document.querySelectorAll('.trim-btn').forEach(btn => btn.addEventListener('click', onTrim));
  document.querySelectorAll('.use-current-time').forEach(btn => btn.addEventListener('click', onUseCurrentTime));
}

async function populateFileSelect(sel) {
  const res = await fetch('/api/files');
  const files = await res.json();
  for (const f of files) {
    const opt = document.createElement('option');
    opt.value = f; opt.textContent = f;
    sel.appendChild(opt);
  }
}

function previewFor(topic) { return document.querySelector(`video.preview[data-topic="${topic}"]`); }

function onFileChosen(e) {
  const topic = e.target.dataset.topic;
  const file = e.target.files[0];
  if (!file) return;
  previewFor(topic).src = URL.createObjectURL(file);
  document.querySelector(`.file-select[data-topic="${topic}"]`).value = '';
}

function onExistingChosen(e) {
  const topic = e.target.dataset.topic;
  const path = e.target.value;
  if (!path) return;
  previewFor(topic).src = '/media/' + path;
}

function onUseCurrentTime(e) {
  const topic = e.target.dataset.topic;
  const v = previewFor(topic);
  document.querySelector(`.in-s[data-topic="${topic}"]`).value = v.currentTime.toFixed(1);
}

async function onTrim(e) {
  const topic = e.target.dataset.topic;
  const statusEl = document.querySelector(`.status[data-topic="${topic}"]`);
  const inS = document.querySelector(`.in-s[data-topic="${topic}"]`).value;
  const outS = document.querySelector(`.out-s[data-topic="${topic}"]`).value;
  const existingPath = document.querySelector(`.file-select[data-topic="${topic}"]`).value;
  const fileInput = document.querySelector(`.file-input[data-topic="${topic}"]`);

  const form = new FormData();
  form.append('topic', topic);
  form.append('in_s', inS);
  form.append('out_s', outS);
  if (existingPath) {
    form.append('existing_path', existingPath);
  } else if (fileInput.files[0]) {
    form.append('file', fileInput.files[0]);
  } else {
    statusEl.textContent = '파일을 선택하거나 업로드하세요';
    statusEl.className = 'status err';
    return;
  }
  e.target.disabled = true;
  statusEl.textContent = '자르고 재조립하는 중...';
  statusEl.className = 'status';
  try {
    const res = await fetch('/api/trim', { method: 'POST', body: form });
    const data = await res.json();
    if (data.ok) {
      statusEl.textContent = '완료! ' + data.rendered_path;
      statusEl.className = 'status ok';
      loadTopics();
    } else {
      statusEl.textContent = '실패: ' + (data.error || data.render_error);
      statusEl.className = 'status err';
    }
  } catch (err) {
    statusEl.textContent = '요청 실패: ' + err;
    statusEl.className = 'status err';
  } finally {
    e.target.disabled = false;
  }
}

async function doXhsImport() {
  const statusEl = document.getElementById('xhs-status');
  const body = {
    url: document.getElementById('xhs-url').value,
    slug: document.getElementById('xhs-slug').value,
    brand: document.getElementById('xhs-brand').value,
    product_line: document.getElementById('xhs-product').value,
    scent: document.getElementById('xhs-scent').value,
    license_note: document.getElementById('xhs-license').value,
  };
  statusEl.textContent = '다운로드 중...';
  statusEl.className = 'status';
  try {
    const res = await fetch('/api/xiaohongshu_import', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.ok) {
      statusEl.textContent = '완료: ' + data.path + ' (위 topic 카드의 "기존 파일에서 선택"에서 바로 고를 수 있음)';
      statusEl.className = 'status ok';
      loadTopics();
    } else {
      statusEl.textContent = '실패: ' + data.error;
      statusEl.className = 'status err';
    }
  } catch (err) {
    statusEl.textContent = '요청 실패: ' + err;
    statusEl.className = 'status err';
  }
}

loadTopics();
</script>
</body></html>
"""


@app.get("/")
def index():
    return _PAGE


if __name__ == "__main__":
    # WHY 127.0.0.1 고정: 로컬 개인 도구다 — 0.0.0.0으로 열면 같은 네트워크의
    # 다른 기기에서도 ffmpeg/yt-dlp를 트리거할 수 있게 돼서 절대 안 됨.
    app.run(host="127.0.0.1", port=5151, debug=False)
