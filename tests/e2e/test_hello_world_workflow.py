"""Cold-start hello-world workflow through the AO API.

Replaces docs/feature-branch-assets/run-hello-world-demo.sh for the Konflux
Kind pipeline: create a script-node workflow, start an execution, and wait
until AO reports it completed via Execution Plane dispatch.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e]

_SCRIPT_CODE = "echo 'hello world'"


def _hello_world_definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "schema_version": "2.0.0",
        "triggers": [{"id": "trigger", "type": "manual_trigger", "parameters": {}}],
        "nodes": [
            {
                "id": "script_node",
                "name": "Hello Script",
                "type": "script",
                "parameters": {"code": _SCRIPT_CODE, "language": "bash"},
            }
        ],
        "edges": [{"from": "trigger", "to": "script_node"}],
    }


def _activity_by_id(execution: dict[str, Any], activity_id: str) -> dict[str, Any] | None:
    activities = execution.get("activities")
    if not isinstance(activities, list):
        return None
    for activity in activities:
        if isinstance(activity, dict) and activity.get("activity_id") == activity_id:
            return activity
    return None


def _activity_stdout(activity: dict[str, Any]) -> str:
    output = activity.get("output_data")
    if not isinstance(output, dict):
        return ""
    stdout = output.get("stdout")
    if isinstance(stdout, str):
        return stdout
    nested = output.get("output")
    if isinstance(nested, dict) and isinstance(nested.get("stdout"), str):
        return str(nested["stdout"])
    return ""


def test_admin_can_list_a_project(ao_client) -> None:
    project_id = ao_client.default_project_id()
    assert project_id


def test_hello_world_script_workflow_completes(ao_client) -> None:
    name = f"hello-world-e2e-{uuid.uuid4().hex[:8]}"
    workflow = ao_client.create_workflow(
        {
            "name": name,
            "description": "Cold-start EP: single script node echoing hello world",
            "project_id": ao_client.default_project_id(),
            "workflow_definition": _hello_world_definition(name),
        }
    )
    execution = ao_client.create_execution(str(workflow["id"]))
    completed = ao_client.poll_execution(str(execution["id"]))

    assert completed["status"] == "completed", (
        f"expected completed, got {completed.get('status')}; error_details={completed.get('error_details')!r}"
    )
    assert not completed.get("error_details"), completed.get("error_details")

    if completed.get("activities") is None:
        return
    script = _activity_by_id(completed, "script_node")
    assert script is not None, f"script_node activity missing from execution {execution['id']}"
    assert script.get("status") == "completed", (
        f"script_node status={script.get('status')!r} error_details={script.get('error_details')!r}"
    )
    stdout = _activity_stdout(script)
    if stdout:
        assert "hello world" in stdout
