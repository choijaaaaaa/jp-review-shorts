# Fish Audio TTS 연동(2026-08-13 도입, Typecast 대체). WHY: 타입캐스트 음성
# 품질에 대한 반복된 불만("이거 품질이 좀 안좋은편같은데") 끝에 교체 결정 —
# 인터페이스(synthesize()의 인자/반환값 모양)는 typecast_tts.py와 동일하게
# 맞춰서 render 파이프라인 쪽은 아무것도 안 바꿔도 되게 했다. 문장 사이 무음
# 삽입·SRT 빌드 로직은 typecast_tts.py와 같은 접근(자체 복제 — 이 프로젝트의
# 기존 컨벤션대로 템플릿/모듈마다 로컬 복제, 공유 모듈로 안 뺌)이지만 Fish
# Audio는 SSE 스트리밍 응답이라 파싱 방식만 다르다.
#
# ⚠️ 보이스 선택(중요, 2026-08-13 실측): Fish Audio의 `/model` 목록 API는
# Fish Audio 자체 큐레이션이 아니라 사용자들이 올린 "보이스 클로닝" 공개
# 마켓플레이스다 — 실제로 조회해보니 카리나(aespa)·아이유·wonyoung·Lisa·
# Song Joong-ki·심지어 "윤석열 대통령"까지 실존 인물 이름을 단 비공식 클론이
# 섞여 있었다(도라에몽·Gojo Satoru 등 저작권 캐릭터 클론도 있음). 이런 보이스는
# 초상권/음성권·정치인 딥페이크 리스크가 커서 이 프로젝트에서 자동으로 고르면
# 안 된다 — `data/fish_audio_voices.json`은 제목에 실존 인물·캐릭터 이름이
# 전혀 없는(순수 설명형 제목만) 항목으로 세션이 직접 골라 큐레이션한 목록이고,
# 새 보이스를 추가할 때도 항상 이 기준으로 사람이 확인 후 추가할 것.
from __future__ import annotations

import base64
import json
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_URL = "https://api.fish.audio/v1/tts/stream/with-timestamp"
# 언어별 보이스 풀 — zh-TW(대만)도 Fish Audio 자체 언어 태그는 "zh"뿐이라
# (2026-08-14 실측: zh-TW로 /model 조회 시 total=0) 대만 topic도 이 zh 풀을
# 그대로 쓴다. 세 풀 전부 위 마켓플레이스 리스크 경고와 동일 기준(순수
# 설명형 제목만)으로 큐레이션됨.
VOICE_POOL_PATH_BY_LANG = {
    "kor": ROOT / "data" / "fish_audio_voices.json",
    "zh-TW": ROOT / "data" / "fish_audio_voices_zh.json",
    "en": ROOT / "data" / "fish_audio_voices_en.json",
}
SENTENCE_END = re.compile(r"[.!?。！？।؟]$")
AUDIO_TEMPO = 1.0  # WHY 1.0(2026-08-13, "너무 빠르네.. 정배속으로 조지면될듯"
# 피드백으로 1.15 → 1.0 되돌림) — Typecast 시절 1.15 배속을 그대로 가져왔었는데
# Fish Audio 보이스로는 너무 빠르게 들린다는 지적. health-shorts도 원래 1.0.
SENTENCE_GAP_MS = 320


def _voice_pool(lang: str = "kor") -> list[dict]:
    path = VOICE_POOL_PATH_BY_LANG.get(lang, VOICE_POOL_PATH_BY_LANG["kor"])
    return json.loads(path.read_text(encoding="utf-8"))


def _voice_reference_id(name: str, lang: str = "kor") -> str:
    for v in _voice_pool(lang):
        if v["name"] == name:
            return v["reference_id"]
    raise ValueError(f"등록되지 않은 Fish Audio 보이스 이름({lang}): {name}")


def _random_voice_name(lang: str = "kor") -> str:
    """topic마다 등록된 보이스 중 하나를 난수로 골라 채널 전체 목소리가
    단조로워지지 않게 한다(typecast_tts.py와 동일 원칙)."""
    return random.choice(_voice_pool(lang))["name"]


def _format_srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n\s*\n")
# WHY 세 갈래 분기(2026-08-13, 다국어 확장 전 실측 확인):
# ① ASCII 문장부호(.!?) + 공백 필수 — "0.09%"처럼 숫자 사이 마침표를 문장
#   경계로 오탐하는 걸 막는다(실제 문장 끝엔 항상 공백/줄바꿈이 뒤따름).
# ② CJK 전각 문장부호(。！？)는 공백 없이 바로 분리 — 한자 문화권은 마침표
#   뒤에 공백을 안 쓰는 게 관행이라(실측: "今天天气很好。我想去海边玩。"에
#   공백이 전혀 없음) ①과 같은 공백 요구를 걸면 중국어 전체가 한 문장으로
#   뭉쳐버린다(실측 확인 — split 결과가 원문 그대로 1개).
# ③ 빈 줄(문단 구분)도 항상 분리 — 태국어처럼 문장부호 관행이 약한
#   언어는 나레이션 작성 시 "한 문단=한 문장" 컨벤션에 의존한다.
def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)  # 공백/문장부호/기호 전부 제거(언어 무관 길이 비교용)


def _normalized_len(s: str) -> int:
    return len(_NORMALIZE_RE.sub("", s))


def _group_sentences(text: str, words: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """⚠️ Fish Audio의 word segment엔 문장부호가 안 붙어있다(실측 확인 —
    "문장이에요." 원문이 "문장이에요"로 돌아옴). 원래는 원문을 문장 단위로
    나눈 뒤 문장별 "공백 기준 단어 수"만큼 순서대로 소비해서 그룹핑했는데,
    이 방식은 공백으로 단어를 구분하는 언어(한국어·영어)에서만 통한다 —
    다국어 확장 전 실측 확인 결과 **중국어는 공백이 아예 없어서 문장 전체가
    "단어 1개"로 세지는데, Fish Audio는 글자 단위로 12개 세그먼트를
    반환**해서 완전히 어긋난다(1 vs 12). 그래서 "몇 개를 소비할지"를 토큰
    개수가 아니라 **정규화된 글자 길이**(공백·문장부호 제거 후 길이)로
    판단하도록 바꿨다 — 문장이 스페이스로 나뉘든(한국어) 글자 단위로
    나뉘든(중국어) 상관없이, 누적된 반환 세그먼트 길이가 원문 문장 길이에
    도달할 때까지 그리디하게 소비하는 방식이라 토큰화 방식 자체에
    의존하지 않는다. 반환값에 원문 문장 텍스트를 같이 담아둔다(아래
    `_build_srt`가 word segment를 다시 이어붙이지 않고 원문을 그대로 쓰게
    하기 위함 — 중국어처럼 글자 단위 세그먼트를 공백으로 이어붙이면
    "也 有 人 說"처럼 글자마다 공백이 끼는 사고가 났었다, 2026-08-14 실측)."""
    sentences = _split_sentences(text)
    entries: list[tuple[str | None, list[dict]]] = []
    idx = 0
    for sent in sentences:
        target_len = _normalized_len(sent)
        start_idx = idx
        consumed_len = 0
        while idx < len(words) and consumed_len < target_len:
            consumed_len += _normalized_len(words[idx]["text"])
            idx += 1
        chunk = words[start_idx:idx]
        if chunk:
            entries.append((sent, chunk))
    if idx < len(words):
        # 정규화 길이 누적이 딱 안 맞아 남는 나머지 — 대응되는 원문 문장이
        # 없으므로 word segment를 이어붙이는 것 외엔 방법이 없음(드문 경우).
        entries.append((None, words[idx:]))
    return entries


def _build_srt(text: str, words: list[dict]) -> str:
    entries = _group_sentences(text, words)
    lines = []
    for i, (sent, chunk) in enumerate(entries, start=1):
        start = _format_srt_time(chunk[0]["start"])
        end = _format_srt_time(chunk[-1]["end"])
        display_text = sent if sent is not None else " ".join(w["text"] for w in chunk)
        lines.append(f"{i}\n{start} --> {end}\n{display_text}\n")
    return "\n".join(lines)


def _insert_sentence_pauses(text: str, audio_bytes: bytes, words: list[dict]) -> tuple[bytes, list[dict]]:
    """문장 사이에 명시적 무음을 끼워넣는다(typecast_tts.py와 동일 접근) —
    자연 발화만으로는 문장 경계가 뭉개져서 이후 씬 타이밍 계산(narration.srt
    문장 경계 기준)이 부정확해지는 걸 막기 위함."""
    entries = _group_sentences(text, words)
    if len(entries) <= 1:
        return audio_bytes, words

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "src.mp3"
        src.write_bytes(audio_bytes)

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, check=True,
        )
        sample_rate, channels = probe.stdout.strip().split(",")

        silence = tmp_path / "silence.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"anullsrc=r={sample_rate}:cl={'mono' if channels == '1' else 'stereo'}",
             "-t", str(SENTENCE_GAP_MS / 1000), str(silence)],
            check=True, capture_output=True,
        )

        concat_list = tmp_path / "concat.txt"
        new_words = []
        cursor = 0.0
        lines = []
        for i, (_sent, entry) in enumerate(entries):
            seg_start, seg_end = entry[0]["start"], entry[-1]["end"]
            seg_path = tmp_path / f"seg_{i:03d}.wav"
            cmd = ["ffmpeg", "-y", "-i", str(src), "-ss", str(seg_start)]
            if i < len(entries) - 1:
                cmd += ["-to", str(seg_end)]
            cmd += ["-c:a", "pcm_s16le", str(seg_path)]
            subprocess.run(cmd, check=True, capture_output=True)
            lines.append(f"file '{seg_path}'")

            for w in entry:
                new_words.append({
                    "text": w["text"],
                    "start": cursor + (w["start"] - seg_start),
                    "end": cursor + (w["end"] - seg_start),
                })
            cursor += (seg_end - seg_start)

            if i < len(entries) - 1:
                lines.append(f"file '{silence}'")
                cursor += SENTENCE_GAP_MS / 1000

        concat_list.write_text("\n".join(lines))
        out_path = tmp_path / "out.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), str(out_path)],
            check=True, capture_output=True,
        )
        return out_path.read_bytes(), new_words


def _apply_tempo(audio_bytes: bytes, words: list[dict], tempo: float) -> tuple[bytes, list[dict]]:
    """오디오 전체를 tempo배 빠르게(피치 보존) + 단어 타임스탬프도 같은 비율로
    나눠서 SRT와 계속 맞게 한다."""
    if tempo == 1.0:
        return audio_bytes, words
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "src.mp3"
        src.write_bytes(audio_bytes)
        out = tmp_path / "out.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={tempo}", str(out)],
            check=True, capture_output=True,
        )
        scaled_words = [{"text": w["text"], "start": w["start"] / tempo, "end": w["end"] / tempo} for w in words]
        return out.read_bytes(), scaled_words


def _call_tts(text: str, voice_name: str, lang: str = "kor", max_retries: int = 3) -> tuple[bytes, list[dict]]:
    """⚠️ Fish Audio 스트리밍 API 자체가 문장이 여러 개(청크 4개+)로 나뉘는
    긴 텍스트에서 신뢰성 문제가 있다(2026-08-13 실측, 재현 확인) — 마지막
    청크의 alignment는 10초 분량 텍스트를 다 담고 있는데 실제 audio_base64는
    0.1초짜리(1697바이트)만 오는 식으로 조용히 잘리거나, 반대로 텍스트가
    반복 재생성돼서 claimed/actual 길이가 정상치의 3배 넘게 부풀기도 했다
    (같은 8문장 대본으로 3연속 호출 중 1번 재현). 매번 실제 오디오 길이를
    alignment 마지막 단어 end 시각과 대조해서, 허용 오차(15~30%) 밖이면
    전체를 다시 호출한다 — 재호출 비용은 무시할 수준(topic당 몇 원)이라
    안전하게 버리고 다시 받는 쪽이 낫다."""
    for attempt in range(1, max_retries + 1):
        audio_bytes, words = _call_tts_once(text, voice_name, lang)
        if not words:
            print(f"[fish_audio] 시도 {attempt}: 응답에 word timestamp 없음, 재시도")
            continue
        claimed_end = words[-1]["end"]
        actual_dur = _probe_duration_from_bytes(audio_bytes)
        if 0.85 * claimed_end <= actual_dur <= 1.3 * claimed_end:
            return audio_bytes, words
        print(f"[fish_audio] 시도 {attempt}: 길이 불일치(claimed={claimed_end:.1f}s, "
              f"actual={actual_dur:.1f}s) — 재시도")
    raise RuntimeError(
        f"[fish_audio] {max_retries}번 재시도해도 오디오 길이가 alignment과 안 맞음 — "
        f"API 응답이 계속 불안정함"
    )


def _probe_duration_from_bytes(audio_bytes: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        p = f.name
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", p],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    finally:
        Path(p).unlink(missing_ok=True)


def _call_tts_once(text: str, voice_name: str, lang: str = "kor") -> tuple[bytes, list[dict]]:
    """SSE 스트림을 소비해서 오디오(mp3 바이트 이어붙이기)와 단어 타임스탬프
    (chunk_seq별 최신 alignment만 반영 — Fish Audio 문서: "같은 chunk_seq에
    대해 최신 alignment이 이전 값을 대체")를 반환한다."""
    api_key = os.environ["FISH_AUDIO_API_KEY"]
    body = {
        "text": text,
        "reference_id": _voice_reference_id(voice_name, lang),
        "format": "mp3",
        "latency": "normal",
    }
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    # ⚠️ chunk_seq당 이벤트가 여러 번 올 수 있다(실측 확인, 2026-08-13).
    # 처음엔 "최신 값이 이전 값을 대체"라는 Fish Audio 문서 문구를 audio에도
    # 그대로 적용해서 매번 마지막 이벤트로 덮어썼는데, 이게 완전히 거꾸로였다
    # — 직접 SSE를 떠서 대조해보니 같은 chunk_seq=0에 대해 1번째 이벤트가
    # 이미 claimed 길이(6.04s)와 거의 정확히 일치하는(6.00s) 완결된 오디오
    # 96889바이트를 담고 있었고, 2번째 이벤트는 1330바이트짜리 거의 빈
    # "꼬리" 델타였다. "최신 값으로 덮어쓰기"를 하면 이 꼬리 델타가 완결된
    # 오디오를 통째로 지워버려서 actual_dur≈0.1s가 되는 사고가 났다(이게
    # "tts가 짜집기된 것처럼 목소리가 바뀐다"는 사용자 리포트의 실제 원인 —
    # 두 번째 이벤트가 미묘하게 다르게 렌더링된 "거의 전체" 분량이었던
    # 이전 관측 케이스에서도, 그냥 첫 이벤트 하나만 쓰면 애초에 서로 다른
    # 두 렌더링이 섞일 일이 없어서 함께 해결됨). 그래서 audio는 각
    # chunk_seq당 "처음 온 것만" 쓰고 이후 같은 seq 이벤트는 버린다.
    # alignment는 정렬 정보만 갱신되는 성격이라 최신 값을 계속 쓴다.
    audio_chunks: dict[int, bytes] = {}
    alignment_by_chunk: dict[int, tuple[float, dict]] = {}
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8")
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        event = json.loads(payload)
        seq = event.get("chunk_seq", 0)
        if event.get("audio_base64") and seq not in audio_chunks:
            audio_chunks[seq] = base64.b64decode(event["audio_base64"])
        alignment = event.get("alignment")
        if alignment and alignment.get("segments"):
            alignment_by_chunk[seq] = (event.get("chunk_audio_offset_sec", 0.0), alignment)

    # ⚠️ 청크를 바이트째로 그냥 이어붙이면 안 된다(2026-08-13 실측 확인 —
    # "SRT는 41초까지 있다는데 실제 오디오 파일은 29초에서 끊김" 사고 원인).
    # 각 chunk_seq의 audio_base64는 그 자체로 완결된 mp3 스트림(자기 헤더
    # 포함)이라, `b"".join(...)`으로 여러 개의 완결된 mp3를 붙이면 디코더가
    # 첫 스트림만 읽고 멈추거나 길이를 잘못 계산해서 뒷부분이 통째로 잘려
    # 나간다(무손실 PCM/WAV라면 안전하지만 MP3 같은 압축 스트림은 안 됨).
    # 각 청크를 디코드해서 WAV로 만든 뒤 이어붙이고 마지막에 한 번만
    # mp3로 재인코딩한다 — `_insert_sentence_pauses()`가 문장 사이 무음을
    # 끼워넣을 때 쓰는 것과 동일한 검증된 방식.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wav_files = []
        for seq in sorted(audio_chunks):
            src = tmp_path / f"chunk_{seq}.mp3"
            src.write_bytes(audio_chunks[seq])
            wav = tmp_path / f"chunk_{seq}.wav"
            subprocess.run(["ffmpeg", "-y", "-i", str(src), "-c:a", "pcm_s16le", str(wav)],
                            check=True, capture_output=True)
            wav_files.append(wav)
        concat_list = tmp_path / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in wav_files))
        combined_wav = tmp_path / "combined.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(combined_wav)],
            check=True, capture_output=True,
        )
        combined_mp3 = tmp_path / "combined.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", str(combined_wav), str(combined_mp3)],
                        check=True, capture_output=True)
        audio_bytes = combined_mp3.read_bytes()
    words = []
    for seq in sorted(alignment_by_chunk):
        offset, alignment = alignment_by_chunk[seq]
        for seg in alignment["segments"]:
            words.append({"text": seg["text"], "start": seg["start"] + offset, "end": seg["end"] + offset})
    return audio_bytes, words


_MAX_SENTENCES_PER_CALL = 1  # WHY(2026-08-13 실측 확인, 재현됨): Fish Audio
# 스트리밍 응답에서 "마지막 청크"의 alignment는 정상(전체 텍스트를 다 담고
# 있음)인데 실제 audio_base64는 근거리 0.1초짜리로 사실상 비어있게 오는
# 사고가 있었다 — 처음엔 "청크 4개 이상일 때만"인 줄 알았는데, 문장 4개
# (청크 2~3개)로 줄여도 똑같이 재현돼서 "청크가 여러 개면 마지막 청크가
# 불안정하다"는 게 실제 패턴으로 보인다(재시도해도 동일 재현되는 결정론적
# 문제라 단순 재시도로는 안 풀림). 짧은 2문장 테스트(청크 1개로 끝남)는
# 항상 정상이었어서, 한 번 호출에 문장 1개만 넣어 항상 단일 청크로
# 끝나도록 강제한다 — API 호출 수는 늘지만 비용이 무시할 수준이라 안전한
# 쪽을 택함.


def _call_tts_batched(text: str, voice_name: str, lang: str = "kor") -> tuple[bytes, list[dict]]:
    """긴 텍스트를 문장 묶음 단위로 나눠 API를 여러 번 호출한 뒤(호출 1회당
    청크 4개 미만이 되도록) WAV로 이어붙여서 한 번 호출한 것과 동일한
    (오디오, 전체 단어 타임스탬프) 결과를 만든다."""
    sentences = _split_sentences(text)
    if len(sentences) <= _MAX_SENTENCES_PER_CALL:
        return _call_tts(text, voice_name, lang)

    batches = [sentences[i:i + _MAX_SENTENCES_PER_CALL]
               for i in range(0, len(sentences), _MAX_SENTENCES_PER_CALL)]
    all_words: list[dict] = []
    cursor = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wav_files = []
        for i, batch in enumerate(batches):
            batch_text = " ".join(batch)
            audio_bytes, words = _call_tts(batch_text, voice_name, lang)
            src = tmp_path / f"batch_{i}.mp3"
            src.write_bytes(audio_bytes)
            wav = tmp_path / f"batch_{i}.wav"
            subprocess.run(["ffmpeg", "-y", "-i", str(src), "-c:a", "pcm_s16le", str(wav)],
                            check=True, capture_output=True)
            wav_files.append(wav)
            for w in words:
                all_words.append({"text": w["text"], "start": w["start"] + cursor, "end": w["end"] + cursor})
            cursor += _probe_duration_from_bytes(audio_bytes)

        concat_list = tmp_path / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in wav_files))
        combined_wav = tmp_path / "combined.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(combined_wav)],
            check=True, capture_output=True,
        )
        combined_mp3 = tmp_path / "combined.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", str(combined_wav), str(combined_mp3)],
                        check=True, capture_output=True)
        audio_bytes = combined_mp3.read_bytes()
    return audio_bytes, all_words


def synthesize(topic: str, text: str, voice_name: str | None = None, lang: str = "kor") -> dict:
    """typecast_tts.synthesize()와 동일한 반환 모양(audio_path/srt_path/
    duration/word_count/words) — render 파이프라인은 어느 쪽을 호출하는지
    신경 쓸 필요 없음. lang="kor"는 기존 output/<topic>/narration.* 경로를
    그대로 쓰고(하위 호환), 그 외 언어는 output/<topic>/<lang>/narration.*로
    분리해서 같은 topic의 다국어 산출물이 서로 덮어쓰지 않게 한다."""
    if voice_name is None:
        voice_name = _random_voice_name(lang)
        print(f"[fish_audio] 보이스 랜덤 선택({lang}): {voice_name}")

    audio_bytes, words = _call_tts_batched(text, voice_name, lang)
    if not words:
        raise RuntimeError("[fish_audio] 응답에 word timestamp가 없음 — API 응답 형식이 바뀌었을 수 있음")
    audio_bytes, words = _insert_sentence_pauses(text, audio_bytes, words)
    audio_bytes, words = _apply_tempo(audio_bytes, words, AUDIO_TEMPO)

    out_dir = ROOT / "output" / topic if lang == "kor" else ROOT / "output" / topic / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "narration.mp3"
    srt_path = out_dir / "narration.srt"
    audio_path.write_bytes(audio_bytes)
    srt_path.write_text(_build_srt(text, words))

    duration = words[-1]["end"] if words else None
    return {
        "audio_path": str(audio_path),
        "srt_path": str(srt_path),
        "duration": duration,
        "word_count": len(words),
        "words": words,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("사용법: python3 -m lib.fish_audio_tts <topic> <narration.txt 경로> [lang]")
        sys.exit(1)
    topic_arg, narration_path = sys.argv[1], sys.argv[2]
    lang_arg = sys.argv[3] if len(sys.argv) > 3 else "kor"
    result = synthesize(topic_arg, Path(narration_path).read_text(encoding="utf-8"), lang=lang_arg)
    print(json.dumps({k: v for k, v in result.items() if k != "words"}, ensure_ascii=False, indent=2))
