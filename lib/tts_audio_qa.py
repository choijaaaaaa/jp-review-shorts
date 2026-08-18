# TTS 결과물을 다시 STT(Whisper)로 되돌려 원문과 비교하는 언어 무관 검증
# 도구(2026-08-18 도입). WHY: 콜라겐씨젤리_JP_1 한국어 나레이션이 "칠곱종"
# 처럼 깨진 걸 사용자가 직접 들어서 발견했는데, 세션(나)은 오디오를 아예
# 들을 수 없고 사용자도 en/ja/zh-TW 발음은 판단할 방법이 없어서 "다른
# 언어는 지금 뭐가 잘못됐는지도 모른다"는 근본 문제가 지적됐다.
#
# 해법: TTS 오디오 → Voicebox에 이미 내장된 Whisper STT로 재전사 → 그
# 전사 텍스트를 원문 narration.txt와 나란히 비교. 세션은 오디오를 "들을"
# 순 없지만, 전사된 "텍스트"는 어느 언어든 읽고 원문과 비교할 수 있다 —
# 오디오 인식 문제를 텍스트 비교 문제로 바꾸는 게 핵심 아이디어.
#
# ⚠️ 자동 pass/fail 게이트가 아니다 — Whisper(특히 기본 탑재된 base
# 모델)는 그 자체로 오타·단위 표기 정규화(예: "열여덟 개"를 "18개"로
# 되돌려 씀)를 하기 때문에, 원문과 글자 단위로 100% 일치할 거라 기대하면
# 안 된다. 이 도구는 "의심 구간을 사람 눈에 띄게 나란히 보여주는" 용도 —
# 실제 판단(단순 표기 정규화인지, 진짜 발음이 깨진 건지)은 출력을 읽는
# 세션이 한다. 판단 기준: 원문에 없는 엉뚱한 단어/음절이 튀어나오거나
# 문장이 뜻 없이 뭉개지면 의심, 숫자 표기 방식만 다르면(18개 vs 열여덟
# 개) 대개 Whisper 정규화일 뿐 무시해도 됨.
#
# 사용법: python3 -m lib.tts_audio_qa <audio_path> <narration.txt 경로> <lang>
# lang은 Whisper 언어 코드(ko/en/ja/zh 등, ISO 639-1).
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import requests

API_BASE = "http://localhost:17493"


def transcribe(audio_path: Path, language: str) -> str:
    """audio_path(mp3/wav 등)를 Voicebox 내장 Whisper로 전사한다.

    ⚠️ mp3를 그대로 올리면 "could not open/decode file"로 실패하는 경우가
    있었다(2026-08-18 실측) — 항상 16kHz mono wav로 변환해서 보낼 것."""
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "audio.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", str(wav_path)],
            check=True, capture_output=True,
        )
        with open(wav_path, "rb") as f:
            resp = requests.post(
                f"{API_BASE}/transcribe",
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "base", "language": language},
                timeout=180,
            )
        resp.raise_for_status()
        return resp.json()["text"]


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("사용법: python3 -m lib.tts_audio_qa <audio_path> <narration.txt 경로> <lang(ko/en/ja/zh 등)>")
        sys.exit(1)
    audio_arg, narration_arg, lang_arg = sys.argv[1], sys.argv[2], sys.argv[3]

    original = Path(narration_arg).read_text(encoding="utf-8")
    try:
        requests.get(f"{API_BASE}/health", timeout=5)
    except requests.exceptions.ConnectionError:
        print("[tts_audio_qa] localhost:17493에 연결 안 됨 — Voicebox.app이 켜져있는지 확인할 것")
        sys.exit(1)

    transcribed = transcribe(Path(audio_arg), lang_arg)

    print("=== 원문(narration.txt) ===")
    print(original)
    print("\n=== STT 재전사 결과 ===")
    print(transcribed)
    print("\n※ 위 둘을 나란히 읽고 비교할 것 — 숫자 표기 방식 차이(예: '18개' vs "
          "'열여덟 개')는 Whisper 정규화라 무시, 원문에 없는 엉뚱한 단어나 "
          "뜻 없이 뭉개진 구간이 있으면 그 부분만 원본 오디오를 실제로 들어서 확인.")
