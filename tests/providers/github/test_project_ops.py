from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from planpilot.core.contracts.item import CreateItemInput
from planpilot.core.contracts.plan import PlanItemType
from planpilot.core.providers.github.models import ResolvedField
from planpilot.core.providers.github.ops.project import ensure_project_fields


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def update_project_field(self, *, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        self.calls.append({"project_id": project_id, "item_id": item_id, "field_id": field_id, "option_id": option_id})


def _make_provider(
    *,
    project_id: str | None = "project-id",
    size_field_id: str | None = None,
    size_options: list[dict[str, str]] | None = None,
    resolved_fields: dict[str, ResolvedField] | None = None,
) -> Any:
    client = _FakeClient()
    context = SimpleNamespace(
        project_id=project_id,
        size_field_id=size_field_id,
        size_options=size_options or [],
        resolved_fields=resolved_fields or {},
    )
    return SimpleNamespace(context=context, _require_client=lambda: client), client


def _create_input(*, size: str | None = None, fields: dict[str, str] | None = None) -> CreateItemInput:
    return CreateItemInput(
        title="T",
        body="B",
        item_type=PlanItemType.TASK,
        labels=["planpilot"],
        size=size,
        fields=fields or {},
    )


@pytest.mark.asyncio
async def test_ensure_project_fields_noop_without_project_item_id() -> None:
    provider, client = _make_provider()

    await ensure_project_fields(provider, "", _create_input(fields={"Priority": "High"}))

    assert client.calls == []


@pytest.mark.asyncio
async def test_ensure_project_fields_noop_without_project_id() -> None:
    provider, client = _make_provider(project_id=None)

    await ensure_project_fields(provider, "PVTI_1", _create_input(size="S"))

    assert client.calls == []


@pytest.mark.asyncio
async def test_ensure_project_fields_applies_size() -> None:
    provider, client = _make_provider(
        size_field_id="size-field",
        size_options=[{"id": "opt-s", "name": "S"}],
    )

    await ensure_project_fields(provider, "PVTI_1", _create_input(size="S"))

    assert client.calls == [
        {"project_id": "project-id", "item_id": "PVTI_1", "field_id": "size-field", "option_id": "opt-s"}
    ]


@pytest.mark.asyncio
async def test_ensure_project_fields_applies_generic_single_select_field() -> None:
    provider, client = _make_provider(
        resolved_fields={
            "Priority": ResolvedField(
                id="priority-field",
                name="Priority",
                kind="single_select",
                options=[{"id": "opt-high", "name": "High"}, {"id": "opt-low", "name": "Low"}],
            ),
        }
    )

    await ensure_project_fields(provider, "PVTI_1", _create_input(fields={"Priority": "High"}))

    assert client.calls == [
        {"project_id": "project-id", "item_id": "PVTI_1", "field_id": "priority-field", "option_id": "opt-high"}
    ]


@pytest.mark.asyncio
async def test_ensure_project_fields_applies_multiple_fields_alongside_size() -> None:
    provider, client = _make_provider(
        size_field_id="size-field",
        size_options=[{"id": "opt-s", "name": "S"}],
        resolved_fields={
            "Priority": ResolvedField(
                id="priority-field", name="Priority", kind="single_select", options=[{"id": "opt-high", "name": "High"}]
            ),
            "Horizon": ResolvedField(
                id="horizon-field", name="Horizon", kind="single_select", options=[{"id": "opt-now", "name": "Now"}]
            ),
        },
    )

    await ensure_project_fields(
        provider, "PVTI_1", _create_input(size="S", fields={"Priority": "High", "Horizon": "Now"})
    )

    field_ids_applied = {call["field_id"] for call in client.calls}
    assert field_ids_applied == {"size-field", "priority-field", "horizon-field"}


@pytest.mark.asyncio
async def test_ensure_project_fields_skips_unknown_field_name() -> None:
    provider, client = _make_provider()

    await ensure_project_fields(provider, "PVTI_1", _create_input(fields={"NotAField": "Whatever"}))

    assert client.calls == []


@pytest.mark.asyncio
async def test_ensure_project_fields_skips_unknown_option_name() -> None:
    provider, client = _make_provider(
        resolved_fields={
            "Area": ResolvedField(
                id="area-field", name="Area", kind="single_select", options=[{"id": "opt-web", "name": "Web"}]
            ),
        }
    )

    await ensure_project_fields(provider, "PVTI_1", _create_input(fields={"Area": "NotAnOption"}))

    assert client.calls == []


@pytest.mark.asyncio
async def test_ensure_project_fields_applies_iteration_field_like_single_select() -> None:
    provider, client = _make_provider(
        resolved_fields={
            "Iteration": ResolvedField(
                id="iteration-field",
                name="Iteration",
                kind="iteration",
                options=[{"id": "iter-1", "name": "active"}],
            ),
        }
    )

    await ensure_project_fields(provider, "PVTI_1", _create_input(fields={"Iteration": "active"}))

    assert client.calls == [
        {"project_id": "project-id", "item_id": "PVTI_1", "field_id": "iteration-field", "option_id": "iter-1"}
    ]
