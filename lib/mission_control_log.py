# mission-control(../../mission-control) 통합 대시보드로 이슈를 보고하는 부가 채널.
# WHY: 파이프라인/QA 스크립트가 실행 중 발견한 문제(유튜브 업로드 실패 등)를
# 이 프로젝트 콘솔에만 찍고 흩어놓는 대신 한곳(/issues 페이지)에서 모아볼 수
# 있게 mission-control의 POST /api/ingest/log로 전달한다.
#
# ⚠️ 루트 CLAUDE.md "무음 실패 금지" 원칙의 의도적 예외: report_issue()는
# 절대 예외를 밖으로 던지지 않는다 — 환경변수 미설정(로컬에 mission-control을
# 안 띄워둔 세션이 대부분)이나 네트워크 실패가 실제 콘텐츠 파이프라인(유튜브
# 업로드 등)을 중단시키면 안 되기 때문. 이 파일은 부가 로깅 채널일 뿐 —
# 실패해도 호출부의 본 작업 흐름에는 아무 영향이 없어야 한다.
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_LOG = logging.getLogger(__name__)
_TIMEOUT_SEC = 5


def report_issue(
    severity: str,
    category: str,
    message: str,
    entity: str | None = None,
    detail: str | dict | None = None,
    project: str = "jp-review-shorts",
) -> bool:
    """mission-control `/api/ingest/log`에 이슈 하나를 보고한다. 성공 시 True,
    미설정·실패 시 False — 호출부는 반환값을 체크할 필요 없이(로깅 부가
    채널이라 실패해도 무방) fire-and-forget으로 호출하면 된다."""
    url = os.environ.get("MISSION_CONTROL_INGEST_URL")
    secret = os.environ.get("MISSION_CONTROL_INGEST_SECRET")
    if not url or not secret:
        return False
    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api/ingest/log",
            headers={"Authorization": f"Bearer {secret}"},
            json={
                "project": project,
                "severity": severity,
                "category": category,
                "entity": entity,
                "message": message,
                "detail": detail,
            },
            timeout=_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        _LOG.warning("mission-control 이슈 보고 실패(파이프라인은 계속 진행): %s", e)
        return False
