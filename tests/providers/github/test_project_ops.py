from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from planpilot.core.contracts.config import FieldConfig
from planpilot.core.contracts.item import CreateItemInput
from planpilot.core.contracts.plan import PlanItemType
from planpilot.core.providers.github.github_gql.fetch_project_fields import FetchProjectFields
from planpilot.core.providers.github.models import ResolvedField
from planpilot.core.providers.github.ops.project import ensure_project_fields, resolve_project_fields


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def update_project_field(self, *, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        self.calls.append(
            {
                "operation": "single_select",
                "project_id": project_id,
                "item_id": item_id,
                "field_id": field_id,
                "option_id": option_id,
            }
        )

    async def update_project_iteration_field(
        self, *, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self.calls.append(
            {
                "operation": "iteration",
                "project_id": project_id,
                "item_id": item_id,
                "field_id": field_id,
                "option_id": option_id,
            }
        )


class _FieldResolutionClient:
    async def fetch_project_fields(self, *, project_id: str) -> FetchProjectFields:
        assert project_id == "project-id"
        return FetchProjectFields.model_validate(
            {
                "node": {
                    "__typename": "ProjectV2",
                    "fields": {
                        "nodes": [
                            {
                                "__typename": "ProjectV2SingleSelectField",
                                "id": "size-field",
                                "name": "Size",
                                "dataType": "SINGLE_SELECT",
                                "options": [
                                    {"id": "opt-s", "name": "S"},
                                    {"id": "opt-xl", "name": "XL"},
                                ],
                            }
                        ]
                    },
                }
            }
        )


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
async def test_resolve_project_fields_keeps_configured_size_in_explicit_fields() -> None:
    client = _FieldResolutionClient()
    provider = SimpleNamespace(
        _field_config=FieldConfig(size_field="Size"),
        _require_client=lambda: client,
    )

    size_field_id, size_options, resolved_fields = await resolve_project_fields(provider, "project-id")

    assert size_field_id == "size-field"
    assert size_options == [{"id": "opt-s", "name": "S"}, {"id": "opt-xl", "name": "XL"}]
    assert resolved_fields["Size"] == ResolvedField(
        id="size-field",
        name="Size",
        kind="single_select",
        options=[{"id": "opt-s", "name": "S"}, {"id": "opt-xl", "name": "XL"}],
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
        {
            "operation": "single_select",
            "project_id": "project-id",
            "item_id": "PVTI_1",
            "field_id": "size-field",
            "option_id": "opt-s",
        }
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
        {
            "operation": "single_select",
            "project_id": "project-id",
            "item_id": "PVTI_1",
            "field_id": "priority-field",
            "option_id": "opt-high",
        }
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
async def test_ensure_project_fields_applies_iteration_field_with_iteration_operation() -> None:
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
        {
            "operation": "iteration",
            "project_id": "project-id",
            "item_id": "PVTI_1",
            "field_id": "iteration-field",
            "option_id": "iter-1",
        }
    ]


@pytest.mark.asyncio
async def test_ensure_project_fields_explicit_size_field_overrides_derived_size() -> None:
    provider, client = _make_provider(
        size_field_id="size-field",
        size_options=[{"id": "opt-s", "name": "S"}, {"id": "opt-xl", "name": "XL"}],
        resolved_fields={
            "Size": ResolvedField(
                id="size-field",
                name="Size",
                kind="single_select",
                options=[{"id": "opt-s", "name": "S"}, {"id": "opt-xl", "name": "XL"}],
            ),
        },
    )

    await ensure_project_fields(provider, "PVTI_1", _create_input(size="S", fields={"Size": "XL"}))

    assert client.calls == [
        {
            "operation": "single_select",
            "project_id": "project-id",
            "item_id": "PVTI_1",
            "field_id": "size-field",
            "option_id": "opt-s",
        },
        {
            "operation": "single_select",
            "project_id": "project-id",
            "item_id": "PVTI_1",
            "field_id": "size-field",
            "option_id": "opt-xl",
        },
    ]
