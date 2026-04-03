"""
Jira integration helpers.

This module intentionally keeps the Jira surface small: validate project
configuration, create issues, and return a stable incident link that can
be surfaced in the UI and audit trail.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Optional


def _normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def jira_settings_ready(project) -> bool:
    return bool(
        getattr(project, "jira_base_url", None)
        and getattr(project, "jira_user_email", None)
        and getattr(project, "jira_api_token", None)
        and getattr(project, "jira_project_key", None)
    )


def _jira_headers(email: str, api_token: str) -> dict:
    auth = base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _jira_request(base_url: str, email: str, api_token: str, *, method: str, path: str, payload: Optional[dict] = None) -> dict:
    url = f"{_normalize_base_url(base_url)}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=_jira_headers(email, api_token))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Jira request failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Jira request failed: {exc.reason}") from exc


def _adf_text(text: str) -> dict:
    content = []
    bullets = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if bullets:
                content.append({"type": "bulletList", "content": bullets})
                bullets = []
            continue
        if line.startswith("- "):
            bullets.append(
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": line[2:]}]}],
                }
            )
            continue
        if bullets:
            content.append({"type": "bulletList", "content": bullets})
            bullets = []
        content.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
    if bullets:
        content.append({"type": "bulletList", "content": bullets})
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": "Created by LastPing."}]}]
    return {"type": "doc", "version": 1, "content": content}


def create_jira_issue(
    *,
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    labels: Optional[list[str]] = None,
) -> dict:
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type or "Task"},
            "description": _adf_text(description),
        }
    }
    if labels:
        payload["fields"]["labels"] = [label for label in labels if label]
    created = _jira_request(
        base_url,
        email,
        api_token,
        method="POST",
        path="/rest/api/3/issue",
        payload=payload,
    )
    issue_key = created.get("key")
    return {
        "key": issue_key,
        "url": f"{_normalize_base_url(base_url)}/browse/{issue_key}" if issue_key else None,
        "raw": created,
    }
