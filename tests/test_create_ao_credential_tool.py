"""Tests for the AO credential helper used by Execution Plane tooling."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from types import ModuleType
    from urllib.request import Request


def _load_tool() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "create_ao_credential.py"
    spec = importlib.util.spec_from_file_location("create_ao_credential", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@pytest.mark.parametrize(
    ("scenario", "final_method"),
    [
        ("create", "POST"),
        ("refresh", "PATCH"),
    ],
)
def test_credential_helper_creates_or_refreshes_the_named_ao_credential(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    final_method: str,
) -> None:
    existing = scenario == "refresh"
    tool = _load_tool()
    credential_id = UUID("12345678-1234-5678-1234-567812345678")
    project_id = "11111111-1114-1111-1111-111111111111"
    credential_type_id = "22222222-2224-2222-2222-222222222222"
    responses = [
        {"access_token": "ao-access-token"},
        {"resources": [{"id": project_id, "is_default": True}]},
        {"resources": [{"id": credential_type_id, "name": "HTTP Bearer Token"}]},
        {"resources": [{"id": str(credential_id), "name": "execution-plane-test-kind"}] if existing else []},
        {"id": str(credential_id)},
    ]
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request: Request, **_kwargs: object) -> _FakeResponse:
        payload = json.loads(request.data) if request.data is not None else None
        calls.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "payload": payload,
                "authorization": request.get_header("Authorization"),
            }
        )
        assert responses
        return _FakeResponse(responses.pop(0))

    monkeypatch.setattr(tool, "urlopen", fake_urlopen)

    result = tool.create_or_refresh_credential(
        api_url="https://ao.example.test",
        cluster_name="test-kind",
        token="dispatcher-token",  # noqa: S106
        username="admin",
        password="ao-password",  # noqa: S106
        insecure=True,
    )

    assert result == credential_id
    assert [call["method"] for call in calls] == ["POST", "GET", "GET", "GET", final_method]
    assert calls[1]["url"].endswith("/api/v1/projects?limit=100")
    assert calls[2]["url"].endswith("/api/v1/credential_types?limit=100")
    assert calls[3]["url"].endswith("/api/v1/credentials?limit=100&name=execution-plane-test-kind")
    assert calls[4]["payload"] is not None
    final_payload = calls[4]["payload"]
    if existing:
        assert final_payload == {"inputs": {"token": "dispatcher-token"}, "enabled": True}
        assert calls[4]["url"].endswith(f"/api/v1/credentials/{credential_id}")
    else:
        assert final_payload["credential_type_id"] == credential_type_id
        assert final_payload["inputs"] == {"token": "dispatcher-token"}
        assert final_payload["project_id"] == project_id
    assert calls[4]["authorization"] == "Bearer ao-access-token"
