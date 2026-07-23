from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from planpilot.core.contracts.config import FieldConfig, PlanPaths, PlanPilotConfig
from planpilot.core.contracts.exceptions import CreateItemPartialFailureError, ProviderError, SyncError
from planpilot.core.contracts.item import CreateItemInput, Item
from planpilot.core.contracts.plan import Estimate, Plan, PlanItem, PlanItemType
from planpilot.core.contracts.sync import SyncEntry, SyncMap
from planpilot.core.engine.engine import SyncEngine
from planpilot.core.engine.utils import compute_parent_blocked_by, parse_metadata_block
from tests.fakes.provider import FakeItem, FakeProvider
from tests.fakes.renderer import FakeRenderer


def make_config(tmp_path: Path, *, max_concurrent: int = 1, validation_mode: str = "strict") -> PlanPilotConfig:
    return PlanPilotConfig(
        provider="github",
        target="owner/repo",
        board_url="https://github.com/orgs/owner/projects/1",
        plan_paths=PlanPaths(unified=tmp_path / "plan.json"),
        max_concurrent=max_concurrent,
        validation_mode=validation_mode,
    )


def test_parse_metadata_block_parses_valid_block() -> None:
    body = "\n".join(
        [
            "PLANPILOT_META_V1",
            "PLAN_ID:plan-123",
            "ITEM_ID:T1",
            "END_PLANPILOT_META",
            "",
            "# title",
        ]
    )

    metadata = parse_metadata_block(body)

    assert metadata == {"PLAN_ID": "plan-123", "ITEM_ID": "T1"}


def test_parse_metadata_block_returns_empty_when_missing() -> None:
    assert parse_metadata_block("# no metadata") == {}


def test_parse_metadata_block_ignores_invalid_lines_and_empty_keys() -> None:
    body = "\n".join(
        [
            "PLANPILOT_META_V1",
            "INVALID",
            ":missing-key",
            "ITEM_ID:T1",
            "END_PLANPILOT_META",
        ]
    )

    metadata = parse_metadata_block(body)

    assert metadata == {"ITEM_ID": "T1"}


def test_parse_metadata_block_requires_end_marker() -> None:
    body = "\n".join(["PLANPILOT_META_V1", "PLAN_ID:plan-1", "ITEM_ID:E1"])

    assert parse_metadata_block(body) == {}


def test_desired_labels_include_type_label_for_label_strategy(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path).model_copy(
        update={
            "field_config": FieldConfig(
                create_type_strategy="label",
                create_type_map={"TASK": "type:task"},
            )
        }
    )
    engine = SyncEngine(provider, renderer, config)

    assert engine._desired_labels_for_item(PlanItemType.TASK) == ["planpilot", "type:task"]


@pytest.mark.parametrize(
    ("parent_type", "items", "expected"),
    [
        (
            PlanItemType.STORY,
            [
                PlanItem(id="T1", type=PlanItemType.TASK, title="T1", parent_id="S1", depends_on=["T2"]),
                PlanItem(id="T2", type=PlanItemType.TASK, title="T2", parent_id="S2"),
            ],
            {("S1", "S2")},
        ),
        (
            PlanItemType.EPIC,
            [
                PlanItem(id="S1", type=PlanItemType.STORY, title="S1", parent_id="E1", depends_on=["S2"]),
                PlanItem(id="S2", type=PlanItemType.STORY, title="S2", parent_id="E2"),
            ],
            {("E1", "E2")},
        ),
    ],
)
def test_compute_parent_blocked_by_rolls_up_dependencies(
    parent_type: PlanItemType,
    items: list[PlanItem],
    expected: set[tuple[str, str]],
) -> None:
    assert compute_parent_blocked_by(items, parent_type) == expected


def test_compute_parent_blocked_by_handles_non_rollup_and_invalid_edges() -> None:
    items = [
        PlanItem(id="T1", type=PlanItemType.TASK, title="T1", parent_id="S1", depends_on=["T2", "MISSING"]),
        PlanItem(id="T2", type=PlanItemType.TASK, title="T2", parent_id="S1"),
        PlanItem(id="S1", type=PlanItemType.STORY, title="S1", parent_id="E1", depends_on=["UNKNOWN"]),
    ]

    assert compute_parent_blocked_by(items, PlanItemType.TASK) == set()
    assert compute_parent_blocked_by(items, PlanItemType.STORY) == set()


@pytest.mark.asyncio
async def test_sync_discovers_existing_and_skips_create(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)

    existing = await provider.create_item(
        CreateItemInput(
            title="Existing story",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:plan-1",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "# Existing story",
                ]
            ),
            item_type=PlanItemType.STORY,
            labels=[config.label],
        )
    )

    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])

    result = await SyncEngine(provider, renderer, config).sync(plan, "plan-1")

    assert len(provider.search_calls) == 1
    assert provider.search_calls[0].labels == [config.label]
    assert provider.search_calls[0].body_contains == "PLAN_ID:plan-1"
    assert provider.create_calls[1:] == []
    assert result.items_created[PlanItemType.STORY] == 0
    assert result.sync_map.entries["S1"].id == existing.id


@pytest.mark.asyncio
async def test_sync_discovery_ignores_wrong_plan_and_missing_item_id(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)

    await provider.create_item(
        CreateItemInput(
            title="Wrong plan",
            body="\n".join(
                ["PLANPILOT_META_V1", "PLAN_ID:other-plan", "ITEM_ID:S1", "END_PLANPILOT_META", "", "# Wrong"]
            ),
            item_type=PlanItemType.STORY,
            labels=[config.label],
        )
    )
    await provider.create_item(
        CreateItemInput(
            title="Missing item id",
            body="\n".join(["PLANPILOT_META_V1", "PLAN_ID:plan-1", "END_PLANPILOT_META", "", "# Missing"]),
            item_type=PlanItemType.STORY,
            labels=[config.label],
        )
    )

    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])
    result = await SyncEngine(provider, renderer, config).sync(plan, "plan-1")

    assert result.items_created[PlanItemType.STORY] == 1


@pytest.mark.asyncio
async def test_sync_discovery_skips_metadata_plan_mismatch(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)

    await provider.create_item(
        CreateItemInput(
            title="Mismatched metadata plan",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:other-plan",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "Contains PLAN_ID:plan-1 outside metadata.",
                ]
            ),
            item_type=PlanItemType.STORY,
            labels=[config.label],
        )
    )

    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])
    result = await SyncEngine(provider, renderer, config).sync(plan, "plan-1")

    assert result.items_created[PlanItemType.STORY] == 1


@pytest.mark.asyncio
async def test_sync_creates_in_type_level_order(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="S1"),
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic"),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story", parent_id="E1"),
        ]
    )

    await SyncEngine(provider, renderer, config).sync(plan, "plan-2")

    assert [call.item_type for call in provider.create_calls] == [
        PlanItemType.EPIC,
        PlanItemType.STORY,
        PlanItemType.TASK,
    ]


@pytest.mark.asyncio
async def test_sync_enrich_and_relations_use_full_context(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", sub_item_ids=["S1"]),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story", parent_id="E1", sub_item_ids=["T1"]),
            PlanItem(id="S2", type=PlanItemType.STORY, title="Blocked Story", parent_id="E2", sub_item_ids=["T2"]),
            PlanItem(id="E2", type=PlanItemType.EPIC, title="Other Epic", sub_item_ids=["S2"]),
            PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="S1", depends_on=["T2"]),
            PlanItem(id="T2", type=PlanItemType.TASK, title="Blocker", parent_id="S2"),
        ]
    )

    await SyncEngine(provider, renderer, config).sync(plan, "plan-3")

    story_entry = next(entry for entry in provider.update_calls if entry[1].title == "Story")
    body = story_entry[1].body
    assert body is not None
    assert "Parent:" in body
    assert "Sub:" in body

    task_item_id = next(item.id for item in provider.items.values() if item.title == "Task")
    blocker_item_id = next(item.id for item in provider.items.values() if item.title == "Blocker")
    assert provider.dependencies[task_item_id] == {blocker_item_id}

    epic_item_id = next(item.id for item in provider.items.values() if item.title == "Epic")
    other_epic_item_id = next(item.id for item in provider.items.values() if item.title == "Other Epic")
    assert provider.dependencies[epic_item_id] == {other_epic_item_id}


@pytest.mark.asyncio
async def test_sync_dry_run_runs_pipeline_and_sets_flag(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")])

    result = await SyncEngine(provider, renderer, config, dry_run=True).sync(plan, "plan-4")

    assert result.dry_run is True
    assert len(provider.search_calls) == 1
    assert len(provider.create_calls) == 1
    # Single item with no children - body unchanged after create, enrich skipped
    assert len(provider.update_calls) == 0
    assert result.sync_map.entries["E1"].key == "#1"
    assert result.items_created[PlanItemType.EPIC] == 1


class ConcurrencyProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.active_creates = 0
        self.max_active_creates = 0

    async def create_item(self, input: CreateItemInput) -> Item:
        self.active_creates += 1
        self.max_active_creates = max(self.max_active_creates, self.active_creates)
        try:
            await asyncio.sleep(0.01)
            return await super().create_item(input)
        finally:
            self.active_creates -= 1


@pytest.mark.asyncio
async def test_sync_respects_semaphore_limit(tmp_path: Path) -> None:
    provider = ConcurrencyProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, max_concurrent=2)
    plan = Plan(items=[PlanItem(id=f"E{i}", type=PlanItemType.EPIC, title=f"Epic {i}") for i in range(5)])

    await SyncEngine(provider, renderer, config).sync(plan, "plan-5")

    assert provider.max_active_creates <= 2


@pytest.mark.asyncio
async def test_sync_relations_skip_unresolved_rollup_edges(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", sub_item_ids=["S1"]),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story", parent_id="E1", sub_item_ids=["T1"]),
            PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="S1", depends_on=["T2"]),
            PlanItem(id="T2", type=PlanItemType.TASK, title="Blocker"),
        ]
    )

    await SyncEngine(provider, renderer, config).sync(plan, "plan-7")

    story_id = next(item.id for item in provider.items.values() if item.title == "Story")
    assert provider.dependencies.get(story_id) is None


@pytest.mark.asyncio
async def test_sync_sets_epic_rollup_from_story_dependencies(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic One", sub_item_ids=["S1"]),
            PlanItem(id="E2", type=PlanItemType.EPIC, title="Epic Two", sub_item_ids=["S2"]),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story One", parent_id="E1", depends_on=["S2"]),
            PlanItem(id="S2", type=PlanItemType.STORY, title="Story Two", parent_id="E2"),
        ]
    )

    await SyncEngine(provider, renderer, config).sync(plan, "plan-11")

    epic_one_id = next(item.id for item in provider.items.values() if item.title == "Epic One")
    epic_two_id = next(item.id for item in provider.items.values() if item.title == "Epic Two")
    assert provider.dependencies[epic_one_id] == {epic_two_id}


@pytest.mark.asyncio
async def test_sync_enrich_skips_items_without_sync_entries(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")])
    engine = SyncEngine(provider, renderer, config)
    sync_map = SyncMap(plan_id="plan-8", target=config.target, board_url=config.board_url)

    await engine._enrich(plan, "plan-8", sync_map, item_objects={})

    assert provider.update_calls == []


@pytest.mark.asyncio
async def test_enrich_updates_when_item_type_changes_even_if_title_and_body_match(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    existing = await provider.create_item(
        CreateItemInput(
            title="Story",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:plan-type",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "# Story",
                ]
            ),
            item_type=PlanItemType.EPIC,
            labels=[config.label],
        )
    )
    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])
    sync_map = SyncMap(plan_id="plan-type", target=config.target, board_url=config.board_url)
    sync_map.entries["S1"] = SyncEntry(id=existing.id, key=existing.key, url=existing.url, item_type=PlanItemType.EPIC)

    await engine._enrich(plan, "plan-type", sync_map, item_objects={"S1": existing})

    assert len(provider.update_calls) == 1
    assert provider.update_calls[0][1].item_type == PlanItemType.STORY


@pytest.mark.asyncio
async def test_enrich_updates_when_labels_drift_even_if_title_body_and_type_match(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    existing = await provider.create_item(
        CreateItemInput(
            title="Story",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:plan-labels",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "# Story",
                ]
            ),
            item_type=PlanItemType.STORY,
            labels=["wrong-label"],
        )
    )
    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])
    sync_map = SyncMap(plan_id="plan-labels", target=config.target, board_url=config.board_url)
    sync_map.entries["S1"] = SyncEntry(id=existing.id, key=existing.key, url=existing.url, item_type=PlanItemType.STORY)

    await engine._enrich(plan, "plan-labels", sync_map, item_objects={"S1": existing})

    assert len(provider.update_calls) == 1
    assert provider.update_calls[0][1].labels == [config.label]


@pytest.mark.asyncio
async def test_enrich_updates_when_size_drift_even_if_title_body_and_type_match(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    existing = await provider.create_item(
        CreateItemInput(
            title="Story",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:plan-size",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "# Story",
                ]
            ),
            item_type=PlanItemType.STORY,
            labels=[config.label],
            size="S",
        )
    )
    plan = Plan(
        items=[
            PlanItem(
                id="S1",
                type=PlanItemType.STORY,
                title="Story",
                estimate=Estimate(tshirt="M"),
            )
        ]
    )
    sync_map = SyncMap(plan_id="plan-size", target=config.target, board_url=config.board_url)
    sync_map.entries["S1"] = SyncEntry(id=existing.id, key=existing.key, url=existing.url, item_type=PlanItemType.STORY)

    await engine._enrich(plan, "plan-size", sync_map, item_objects={"S1": existing})

    assert len(provider.update_calls) == 1
    assert provider.update_calls[0][1].size == "M"


@pytest.mark.asyncio
async def test_create_merges_config_field_defaults_with_item_overrides(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path).model_copy(
        update={"field_config": FieldConfig(status="Backlog", priority="P1", iteration="active")}
    )
    plan = Plan(
        items=[
            PlanItem(
                id="S1",
                type=PlanItemType.STORY,
                title="Story",
                fields={"Priority": "High", "Horizon": "Now"},
            )
        ]
    )

    await SyncEngine(provider, renderer, config).sync(plan, "plan-fields")

    # Config defaults seed Status/Iteration (item doesn't override them); the item's own
    # Priority wins over the config default; Horizon has no config default and passes through.
    assert provider.create_calls[0].fields == {
        "Status": "Backlog",
        "Priority": "High",
        "Iteration": "active",
        "Horizon": "Now",
    }


@pytest.mark.asyncio
async def test_enrich_reapplies_only_explicit_item_fields_not_config_defaults(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path).model_copy(update={"field_config": FieldConfig(status="Backlog")})
    engine = SyncEngine(provider, renderer, config)

    existing = await provider.create_item(
        CreateItemInput(
            title="Story",
            body="\n".join(
                [
                    "PLANPILOT_META_V1",
                    "PLAN_ID:plan-fields-2",
                    "ITEM_ID:S1",
                    "END_PLANPILOT_META",
                    "",
                    "# Story",
                ]
            ),
            item_type=PlanItemType.STORY,
            labels=[config.label],
        )
    )
    plan = Plan(
        items=[
            PlanItem(
                id="S1",
                type=PlanItemType.STORY,
                title="Story renamed",
                fields={"Area": "Web"},
            )
        ]
    )
    sync_map = SyncMap(plan_id="plan-fields-2", target=config.target, board_url=config.board_url)
    sync_map.entries["S1"] = SyncEntry(id=existing.id, key=existing.key, url=existing.url, item_type=PlanItemType.STORY)

    await engine._enrich(plan, "plan-fields-2", sync_map, item_objects={"S1": existing})

    # Only the item's explicit `fields` are pushed on update - the config-level "Backlog" default
    # is not reapplied, so a Status a human moved on the live board is never overwritten by a rerun.
    assert len(provider.update_calls) == 1
    assert provider.update_calls[0][1].fields == {"Area": "Web"}


@pytest.mark.asyncio
async def test_set_relations_keeps_existing_pairs_when_touched_by_update(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic One", sub_item_ids=["S1"]),
            PlanItem(id="E2", type=PlanItemType.EPIC, title="Epic Two", sub_item_ids=["S2"]),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story One", parent_id="E1", depends_on=["S2"]),
            PlanItem(id="S2", type=PlanItemType.STORY, title="Story Two", parent_id="E2"),
            PlanItem(id="T1", type=PlanItemType.TASK, title="Unrelated new task"),
        ]
    )

    item_objects: dict[str, Item] = {}
    for plan_item in plan.items:
        item_objects[plan_item.id] = await provider.create_item(
            CreateItemInput(title=plan_item.title, body="body", item_type=plan_item.type, labels=[config.label])
        )

    await engine._set_relations(
        plan,
        item_objects=item_objects,
        created_ids={"T1"},
        updated_ids={"E1"},
    )

    epic_one_id = item_objects["E1"].id
    epic_two_id = item_objects["E2"].id
    assert provider.dependencies[epic_one_id] == {epic_two_id}


@pytest.mark.asyncio
async def test_set_relations_processes_pairs_when_nothing_touched(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic One", sub_item_ids=["S1"]),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story One", parent_id="E1"),
        ]
    )
    item_objects: dict[str, Item] = {}
    for plan_item in plan.items:
        item_objects[plan_item.id] = await provider.create_item(
            CreateItemInput(title=plan_item.title, body="body", item_type=plan_item.type, labels=[config.label])
        )

    await engine._set_relations(
        plan,
        item_objects=item_objects,
        created_ids=set(),
        updated_ids=set(),
    )

    story_id = item_objects["S1"].id
    epic_id = item_objects["E1"].id
    assert provider.parents == {story_id: epic_id}
    assert provider.dependencies == {}


@pytest.mark.asyncio
async def test_set_relations_removes_stale_parent_and_dependencies(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])

    story = await provider.create_item(
        CreateItemInput(title="Story", body="body", item_type=PlanItemType.STORY, labels=[config.label])
    )
    # Seed stale remote-only relations that are no longer present in the plan.
    provider.parents[story.id] = "stale-parent"
    provider.dependencies[story.id] = {"stale-blocker"}

    await engine._set_relations(
        plan,
        item_objects={"S1": story},
        created_ids=set(),
        updated_ids=set(),
    )

    assert story.id not in provider.parents
    assert provider.dependencies.get(story.id) is None


@pytest.mark.asyncio
async def test_set_relations_primes_cache_with_provider_ids(tmp_path: Path) -> None:
    class PrimeAwareProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.primed_issue_ids: list[str] = []

        async def prime_relations_cache(self, issue_ids: list[str]) -> None:
            self.primed_issue_ids = issue_ids

    provider = PrimeAwareProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)

    plan = Plan(items=[PlanItem(id="S1", type=PlanItemType.STORY, title="Story")])

    story = await provider.create_item(
        CreateItemInput(title="Story", body="body", item_type=PlanItemType.STORY, labels=[config.label])
    )

    await engine._set_relations(
        plan,
        item_objects={"S1": story},
        created_ids=set(),
        updated_ids=set(),
    )

    assert provider.primed_issue_ids == [story.id]


@pytest.mark.asyncio
async def test_sync_strict_mode_raises_on_unresolved_parent(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", parent_id="MISSING")])

    with pytest.raises(SyncError, match="Unresolved parent_id"):
        await SyncEngine(provider, renderer, config).sync(plan, "plan-12")


@pytest.mark.asyncio
async def test_sync_strict_mode_raises_on_unresolved_dependency(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", depends_on=["MISSING"])])

    with pytest.raises(SyncError, match="Unresolved depends_on"):
        await SyncEngine(provider, renderer, config).sync(plan, "plan-9")


@pytest.mark.asyncio
async def test_sync_ignores_self_dependency(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", depends_on=["E1"])])

    await SyncEngine(provider, renderer, config).sync(plan, "plan-13")

    epic_id = next(item.id for item in provider.items.values() if item.title == "Epic")
    assert provider.dependencies.get(epic_id) is None


@pytest.mark.asyncio
async def test_sync_partial_mode_warns_on_unresolved_dependency(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, validation_mode="partial")
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic", depends_on=["MISSING"])])

    with pytest.warns(UserWarning, match="Unresolved depends_on"):
        result = await SyncEngine(provider, renderer, config).sync(plan, "plan-10")

    assert result.sync_map.entries["E1"].id == "fake-id-1"


class PartialFailureProvider(FakeProvider):
    async def create_item(self, input: CreateItemInput) -> Item:
        raise CreateItemPartialFailureError("create partially failed", created_item_id="partial-1")


@pytest.mark.asyncio
async def test_sync_wraps_partial_create_failures(tmp_path: Path) -> None:
    provider = PartialFailureProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")])

    with pytest.raises(SyncError, match="partial"):
        await SyncEngine(provider, renderer, config).sync(plan, "plan-6")


@pytest.mark.asyncio
async def test_set_relations_skips_items_missing_from_item_objects(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")])

    await engine._set_relations(plan, item_objects={}, created_ids={"E1"})

    assert provider.parents == {}
    assert provider.dependencies == {}


@pytest.mark.asyncio
async def test_set_relations_strict_raises_for_external_parent_reference(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, validation_mode="strict")
    engine = SyncEngine(provider, renderer, config)
    existing = await provider.create_item(
        CreateItemInput(title="Task", body="body", item_type=PlanItemType.TASK, labels=[config.label])
    )
    plan = Plan(items=[PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="EXT-1")])

    with pytest.raises(SyncError, match="Unresolved parent_id"):
        await engine._set_relations(plan, item_objects={"T1": existing}, created_ids={"T1"})


@pytest.mark.asyncio
async def test_set_relations_strict_raises_for_self_parent_reference(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, validation_mode="strict")
    engine = SyncEngine(provider, renderer, config)
    existing = await provider.create_item(
        CreateItemInput(title="Task", body="body", item_type=PlanItemType.TASK, labels=[config.label])
    )
    plan = Plan(items=[PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="T1")])

    with pytest.raises(SyncError, match="Unresolved parent_id"):
        await engine._set_relations(plan, item_objects={"T1": existing}, created_ids={"T1"})


@pytest.mark.asyncio
async def test_set_relations_partial_warns_for_self_parent_reference(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, validation_mode="partial")
    engine = SyncEngine(provider, renderer, config)
    existing = await provider.create_item(
        CreateItemInput(title="Task", body="body", item_type=PlanItemType.TASK, labels=[config.label])
    )
    plan = Plan(items=[PlanItem(id="T1", type=PlanItemType.TASK, title="Task", parent_id="T1")])

    with pytest.warns(UserWarning, match="Unresolved parent_id"):
        await engine._set_relations(plan, item_objects={"T1": existing}, created_ids={"T1"})

    assert existing.id not in provider.parents


@pytest.mark.asyncio
async def test_set_relations_skips_story_rollup_without_story_parents(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    engine = SyncEngine(provider, renderer, config)
    plan = Plan(
        items=[
            PlanItem(id="E2", type=PlanItemType.EPIC, title="Epic two"),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story one"),
            PlanItem(id="S2", type=PlanItemType.STORY, title="Story two", parent_id="E2"),
            PlanItem(id="T1", type=PlanItemType.TASK, title="Task one", parent_id="S1", depends_on=["T2"]),
            PlanItem(id="T2", type=PlanItemType.TASK, title="Task two", parent_id="S2"),
        ]
    )

    item_objects: dict[str, Item] = {}
    for plan_item in plan.items:
        item_objects[plan_item.id] = await provider.create_item(
            CreateItemInput(title=plan_item.title, body="body", item_type=plan_item.type, labels=[config.label])
        )

    await engine._set_relations(plan, item_objects, created_ids=set(item_objects))

    epic_ids = {item.id for item in provider.items.values() if item.item_type == PlanItemType.EPIC}
    for item_id in epic_ids:
        assert provider.dependencies.get(item_id) is None


@pytest.mark.asyncio
async def test_build_context_ignores_unresolved_internal_parent(tmp_path: Path) -> None:
    provider = FakeProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path, validation_mode="partial")
    engine = SyncEngine(provider, renderer, config)
    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic"),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story", parent_id="E1"),
        ]
    )

    context = engine._build_context(
        plan,
        plan.items[1],
        "plan-ctx",
        SyncMap(plan_id="plan-ctx", target="t", board_url="b"),
    )

    assert context.parent_ref is None


class FailingRelationItem(FakeItem):
    """FakeItem whose relation reconciliation raises ProviderError."""

    async def reconcile_relations(self, *, parent: Item | None, blockers: list[Item]) -> None:
        if parent is not None:
            raise ProviderError("sub-issues not supported")
        if blockers:
            raise ProviderError("blocked-by not supported")
        await super().reconcile_relations(parent=parent, blockers=blockers)


class FailingRelationProvider(FakeProvider):
    """Provider that returns items whose relation methods always fail."""

    async def create_item(self, input: CreateItemInput) -> Item:
        self.create_calls.append(input)
        n = self._next_number
        self._next_number += 1
        item = FailingRelationItem(
            _id=f"fake-id-{n}",
            _key=f"#{n}",
            _url=f"https://fake/issues/{n}",
            _title=input.title,
            _body=input.body,
            _item_type=input.item_type,
            _provider=self,
            _labels=list(input.labels),
        )
        self.items[item.id] = item
        return item


@pytest.mark.asyncio
async def test_sync_surfaces_provider_error_from_relation_failure(tmp_path: Path) -> None:
    """ProviderError raised inside _set_relations is unwrapped from the ExceptionGroup."""
    provider = FailingRelationProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic"),
            PlanItem(id="S1", type=PlanItemType.STORY, title="Story", parent_id="E1"),
        ]
    )

    with pytest.raises(ProviderError, match="sub-issues not supported"):
        await SyncEngine(provider, renderer, config).sync(plan, "plan-rel-1")


@pytest.mark.asyncio
async def test_sync_surfaces_provider_error_from_dependency_failure(tmp_path: Path) -> None:
    """ProviderError from add_dependency is unwrapped properly."""
    provider = FailingRelationProvider()
    renderer = FakeRenderer()
    config = make_config(tmp_path)
    plan = Plan(
        items=[
            PlanItem(id="T1", type=PlanItemType.TASK, title="Task A", depends_on=["T2"]),
            PlanItem(id="T2", type=PlanItemType.TASK, title="Task B"),
        ]
    )

    with pytest.raises(ProviderError, match="blocked-by not supported"):
        await SyncEngine(provider, renderer, config).sync(plan, "plan-rel-2")
