# 유튜브 업로드용 OAuth refresh token 발급 스크립트(health-shorts
# lib/youtube_auth_setup.py 이식). --channel(예: en, tw)를 주면
# YOUTUBE_<CODE>_ 접두어 env var를 읽는다 — 안 주면 ko(무접두사) 채널.
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.youtube_upload import SCOPES, _env_prefix  # noqa: E402

load_dotenv()


def main() -> None:
    channel = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--channel" else None
    prefix = _env_prefix(channel)

    client_id = os.environ[f"{prefix}CLIENT_ID"]
    client_secret = os.environ[f"{prefix}CLIENT_SECRET"]

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    label = f"채널 '{channel}'" if channel else "기본(ko) 채널"
    print(f"[youtube_auth] Basketbrief {label} 인증 시작 — 브라우저가 자동으로 안 열리면 아래 URL을 직접 열어서 로그인하세요.")
    creds = flow.run_local_server(port=0, open_browser=True)

    print(f"\n[youtube_auth] 발급 완료 — 아래 값을 .env의 {prefix}REFRESH_TOKEN에 넣으세요:\n")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()
