from types import SimpleNamespace

import pytest

from planpilot.core.contracts.config import FieldConfig
from planpilot.core.contracts.exceptions import CreateItemPartialFailureError, ProviderCapabilityError, ProviderError
from planpilot.core.contracts.item import CreateItemInput, ItemSearchFilters, UpdateItemInput
from planpilot.core.contracts.plan import PlanItemType
from planpilot.core.providers.github.github_gql.fragments import IssueCore, IssueCoreLabels, IssueCoreLabelsNodes
from planpilot.core.providers.github.models import GitHubProviderContext, ResolvedField
from planpilot.core.providers.github.ops import relations as relations_ops
from planpilot.core.providers.github.provider import GitHubProvider


def _provider_module() -> object:
    import sys

    return sys.modules[GitHubProvider.__module__]


def _make_issue_core(
    *,
    id: str = "I1",
    number: int = 42,
    url: str = "https://github.com/acme/repo/issues/42",
    title: str = "T",
    body: str = "B",
    label_names: list[str] | None = None,
) -> IssueCore:
    """Build a typed IssueCore instance for tests."""
    labels = IssueCoreLabels(nodes=[IssueCoreLabelsNodes(id=f"lbl-{n}", name=n) for n in (label_names or [])])
    return IssueCore(id=id, number=number, url=url, title=title, body=body, labels=labels)


def test_item_from_issue_core_parses_item_type_from_metadata() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
    )
    issue = _make_issue_core(
        body="\n".join(
            [
                "PLANPILOT_META_V1",
                "PLAN_ID:abc123",
                "ITEM_ID:T-1",
                "ITEM_TYPE:TASK",
                "END_PLANPILOT_META",
            ]
        )
    )

    item = provider._item_from_issue_core(issue)

    assert item.item_type is PlanItemType.TASK


def test_item_from_issue_core_ignores_invalid_item_type_metadata() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
    )
    issue = _make_issue_core(
        body="\n".join(
            [
                "PLANPILOT_META_V1",
                "PLAN_ID:abc123",
                "ITEM_ID:T-1",
                "ITEM_TYPE:NOT_A_TYPE",
                "END_PLANPILOT_META",
            ]
        )
    )

    item = provider._item_from_issue_core(issue)

    assert item.item_type is None


@pytest.mark.asyncio
async def test_aenter_builds_context(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    async def fake_enter_transport() -> None:
        return None

    async def fake_resolve_repo() -> tuple[str, str, dict[str, str]]:
        return "repo-id", "label-id", {"EPIC": "type-1"}

    async def fake_resolve_project() -> tuple[str, str, int, str | None]:
        return "org", "acme", 1, "project-id"

    async def fake_resolve_project_fields(
        project_id: str,
    ) -> tuple[str | None, list[dict[str, str]], dict[str, ResolvedField]]:
        return "size-f", [{"id": "opt-1", "name": "S"}], {}

    monkeypatch.setattr(provider, "_open_transport", fake_enter_transport)
    monkeypatch.setattr(provider, "_resolve_repo_context", fake_resolve_repo)
    monkeypatch.setattr(provider, "_resolve_project_context", fake_resolve_project)
    monkeypatch.setattr(provider, "_resolve_project_fields", fake_resolve_project_fields)

    entered = await provider.__aenter__()

    assert entered is provider
    assert provider.context.repo_id == "repo-id"
    assert provider.context.project_id == "project-id"
    assert provider.context.size_field_id == "size-f"
    assert provider.context.size_options == [{"id": "opt-1", "name": "S"}]
    # issue_type_ids populated -> supports_issue_type is True
    assert provider.context.supports_issue_type is True


@pytest.mark.asyncio
async def test_aenter_falls_back_to_label_when_no_issue_types(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )

    async def fake_enter_transport() -> None:
        return None

    async def fake_resolve_repo() -> tuple[str, str, dict[str, str]]:
        return "repo-id", "label-id", {}  # no issue types

    async def fake_resolve_project() -> tuple[str, str, int, str | None]:
        return "org", "acme", 1, "project-id"

    async def fake_resolve_project_fields(
        project_id: str,
    ) -> tuple[str | None, list[dict[str, str]], dict[str, ResolvedField]]:
        return None, [], {}

    monkeypatch.setattr(provider, "_open_transport", fake_enter_transport)
    monkeypatch.setattr(provider, "_resolve_repo_context", fake_resolve_repo)
    monkeypatch.setattr(provider, "_resolve_project_context", fake_resolve_project)
    monkeypatch.setattr(provider, "_resolve_project_fields", fake_resolve_project_fields)

    await provider.__aenter__()

    # No issue types found -> supports_issue_type is False, strategy falls back to label
    assert provider.context.supports_issue_type is False
    assert provider.context.create_type_strategy == "label"


@pytest.mark.asyncio
async def test_aexit_closes_client_when_initialized() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
    )

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[object | None, object | None, object | None]] = []

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            self.calls.append((exc_type, exc_val, exc_tb))

    client = _Client()
    provider._client = client  # type: ignore[assignment]

    await provider.__aexit__(None, None, None)

    assert client.calls == [(None, None, None)]
    assert provider._client is None


@pytest.mark.asyncio
async def test_create_item_happy_path_issue_type_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={"TASK": "task-type"},
        project_owner_type="org",
        project_id="project-id",
        supports_issue_type=True,
        create_type_strategy="issue-type",
        create_type_map={"TASK": "Task"},
        size_field_id="size-id",
        size_options=[{"id": "opt-s", "name": "S"}],
    )

    calls: list[str] = []

    async def fake_create_issue(input: CreateItemInput) -> IssueCore:
        calls.append("issue_created")
        return _make_issue_core(title=input.title, body=input.body)

    async def fake_project(issue_id: str) -> str:
        calls.append("project_item_added")
        return "PVTI_1"

    async def fake_fields(project_item_id: str, input: CreateItemInput) -> None:
        calls.append("project_fields_set")

    monkeypatch.setattr(provider, "_create_issue", fake_create_issue)
    monkeypatch.setattr(provider, "_ensure_project_item", fake_project)
    monkeypatch.setattr(provider, "_ensure_project_fields", fake_fields)

    created = await provider.create_item(
        CreateItemInput(title="T", body="B", item_type=PlanItemType.TASK, labels=["planpilot"], size="S")
    )

    assert created.id == "I1"
    # Labels, issue type, and project are set atomically in _create_issue;
    # only project item retrieval and field assignment are separate calls
    assert calls == ["issue_created", "project_item_added", "project_fields_set"]


@pytest.mark.asyncio
async def test_create_item_raises_partial_failure_after_issue_created(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={"TASK": "task-type"},
        project_owner_type="org",
        project_id="project-id",
        create_type_strategy="issue-type",
        create_type_map={"TASK": "Task"},
    )

    async def fake_create_issue(input: CreateItemInput) -> IssueCore:
        return _make_issue_core(title=input.title, body=input.body)

    async def fake_project_item(issue_id: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(provider, "_create_issue", fake_create_issue)
    monkeypatch.setattr(provider, "_ensure_project_item", fake_project_item)

    with pytest.raises(CreateItemPartialFailureError) as excinfo:
        await provider.create_item(CreateItemInput(title="T", body="B", item_type=PlanItemType.TASK))

    assert excinfo.value.created_item_id == "I1"
    # Atomic create succeeded (labels + type set), but project item failed
    assert excinfo.value.completed_steps == ("issue_created", "issue_type_set", "labels_set")


@pytest.mark.asyncio
async def test_update_item_uses_update_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        return _make_issue_core(
            id=item_id, number=2, url="u", title=update_input.title or "old", body=update_input.body or "old"
        )

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)

    updated = await provider.update_item("I1", UpdateItemInput(title="new"))
    assert updated.title == "new"


@pytest.mark.asyncio
async def test_search_items_applies_labels_and_body_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    captured: dict[str, str] = {}

    async def fake_search(query: str) -> list[IssueCore]:
        captured["query"] = query
        return [_make_issue_core(id="I1", number=1, url="u", title="t", body="PLAN_ID:abc")]

    monkeypatch.setattr(provider, "_search_issue_nodes", fake_search)

    items = await provider.search_items(ItemSearchFilters(labels=["planpilot", "foo"], body_contains="PLAN_ID:abc"))

    assert len(items) == 1
    assert 'label:"planpilot"' in captured["query"]
    assert 'label:"foo"' in captured["query"]
    assert '"PLAN_ID:abc" in:body' in captured["query"]


@pytest.mark.asyncio
async def test_search_items_escapes_double_quotes_in_body_contains(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    captured: dict[str, str] = {}

    async def fake_search(query: str) -> list[IssueCore]:
        captured["query"] = query
        return [_make_issue_core(id="I1", number=1, url="u", title="t", body="")]

    monkeypatch.setattr(provider, "_search_issue_nodes", fake_search)

    await provider.search_items(ItemSearchFilters(labels=["planpilot"], body_contains='PLAN_ID:"abc"'))

    assert '"PLAN_ID:\\"abc\\"" in:body' in captured["query"]


@pytest.mark.asyncio
async def test_search_items_quotes_and_escapes_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    captured: dict[str, str] = {}

    async def fake_search(query: str) -> list[IssueCore]:
        captured["query"] = query
        return [_make_issue_core(id="I1", number=1, url="u", title="t", body="")]

    monkeypatch.setattr(provider, "_search_issue_nodes", fake_search)

    await provider.search_items(ItemSearchFilters(labels=["needs triage", 'x"y']))

    assert 'label:"needs triage"' in captured["query"]
    assert 'label:"x\\"y"' in captured["query"]


@pytest.mark.asyncio
async def test_search_items_without_body_contains(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    captured: dict[str, str] = {}

    async def fake_search(query: str) -> list[IssueCore]:
        captured["query"] = query
        return [_make_issue_core(id="I1", number=1, url="u", title="t", body="")]

    monkeypatch.setattr(provider, "_search_issue_nodes", fake_search)

    await provider.search_items(ItemSearchFilters(labels=["planpilot"]))
    assert 'label:"planpilot"' in captured["query"]
    assert "PLAN_ID:" not in captured["query"]
    assert "in:body" not in captured["query"]


@pytest.mark.asyncio
async def test_create_item_raises_original_error_when_issue_not_created(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id", label_id="label-id", issue_type_ids={}, project_owner_type="org"
    )

    async def fake_create_issue(input: CreateItemInput) -> IssueCore:
        raise RuntimeError("create boom")

    monkeypatch.setattr(provider, "_create_issue", fake_create_issue)

    with pytest.raises(RuntimeError, match="create boom"):
        await provider.create_item(CreateItemInput(title="T", body="B", item_type=PlanItemType.TASK))


@pytest.mark.asyncio
async def test_update_item_applies_optional_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        project_id="P1",
        create_type_strategy="label",
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        return _make_issue_core(
            id=item_id, number=2, url="u", title=update_input.title or "old", body=update_input.body or "old"
        )

    called: list[str] = []

    async def fake_reconcile_labels(*, item_id: str, item_type: PlanItemType | None, labels: list[str]) -> None:
        assert item_id == "I1"
        assert item_type == PlanItemType.TASK
        assert labels == ["planpilot"]
        called.append("labels")

    async def fake_project_item(item_id: str) -> str:
        called.append("project")
        return "PVTI_1"

    async def fake_project_fields(project_item_id: str, create_input: CreateItemInput) -> None:
        called.append("fields")

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)
    monkeypatch.setattr(provider, "_reconcile_managed_labels", fake_reconcile_labels)
    monkeypatch.setattr(provider, "_ensure_project_item", fake_project_item)
    monkeypatch.setattr(provider, "_ensure_project_fields", fake_project_fields)

    updated = await provider.update_item(
        "I1",
        UpdateItemInput(
            title="new",
            item_type=PlanItemType.TASK,
            labels=["planpilot"],
            size="S",
        ),
    )

    assert updated.title == "new"
    assert called == ["labels", "project", "fields"]


@pytest.mark.asyncio
async def test_update_item_reconciles_type_labels_when_labels_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        create_type_strategy="label",
        create_type_map={"TASK": "type:task", "EPIC": "type:epic"},
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        return _make_issue_core(
            id=item_id,
            number=2,
            url="u",
            title=update_input.title or "old",
            body=update_input.body or "old",
        )

    called: list[tuple[str, PlanItemType | None, list[str]]] = []

    async def fake_reconcile_labels(*, item_id: str, item_type: PlanItemType | None, labels: list[str]) -> None:
        called.append((item_id, item_type, labels))

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)
    monkeypatch.setattr(provider, "_reconcile_managed_labels", fake_reconcile_labels)

    await provider.update_item("I1", UpdateItemInput(title="new", item_type=PlanItemType.TASK))

    assert called == [("I1", PlanItemType.TASK, ["planpilot"])]


@pytest.mark.asyncio
async def test_reconcile_managed_labels_removes_stale_managed_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        create_type_strategy="label",
        create_type_map={"EPIC": "type:epic", "TASK": "type:task"},
    )

    async def fake_get_label_name_to_id(item_id: str) -> dict[str, str]:
        assert item_id == "I1"
        return {
            "planpilot": "id-planpilot",
            "type:epic": "id-old",
            "custom": "id-custom",
        }

    added: list[list[str]] = []
    removed: list[list[str]] = []

    async def fake_ensure_discovery_labels(item_id: str, labels: list[str]) -> None:
        assert item_id == "I1"
        added.append(labels)

    async def fake_remove_labels_by_ids(item_id: str, label_ids: list[str]) -> None:
        assert item_id == "I1"
        removed.append(label_ids)

    monkeypatch.setattr(provider, "_get_item_label_name_to_id", fake_get_label_name_to_id)
    monkeypatch.setattr(provider, "_ensure_discovery_labels", fake_ensure_discovery_labels)
    monkeypatch.setattr(provider, "_remove_labels_by_ids", fake_remove_labels_by_ids)

    await provider._reconcile_managed_labels(item_id="I1", item_type=PlanItemType.TASK, labels=["planpilot"])

    assert added == [["type:task"]]
    assert removed == [["id-old"]]


@pytest.mark.asyncio
async def test_reconcile_managed_labels_preserves_discovery_label_when_not_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        create_type_strategy="label",
        create_type_map={"TASK": "type:task", "EPIC": "type:epic"},
    )

    async def fake_get_label_name_to_id(item_id: str) -> dict[str, str]:
        assert item_id == "I1"
        return {
            "planpilot": "id-planpilot",
            "type:epic": "id-epic",
        }

    added: list[list[str]] = []
    removed: list[list[str]] = []

    async def fake_ensure_discovery_labels(item_id: str, labels: list[str]) -> None:
        assert item_id == "I1"
        added.append(labels)

    async def fake_remove_labels_by_ids(item_id: str, label_ids: list[str]) -> None:
        assert item_id == "I1"
        removed.append(label_ids)

    monkeypatch.setattr(provider, "_get_item_label_name_to_id", fake_get_label_name_to_id)
    monkeypatch.setattr(provider, "_ensure_discovery_labels", fake_ensure_discovery_labels)
    monkeypatch.setattr(provider, "_remove_labels_by_ids", fake_remove_labels_by_ids)

    await provider._reconcile_managed_labels(item_id="I1", item_type=PlanItemType.TASK, labels=[])

    assert added == [["type:task"]]
    assert removed == [["id-epic"]]


@pytest.mark.asyncio
async def test_update_item_label_strategy_keeps_discovery_label_in_project_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        project_id="P1",
        create_type_strategy="label",
        create_type_map={"TASK": "type:task"},
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        return _make_issue_core(id=item_id, number=2, url="u", title=update_input.title or "old", body="old")

    seen_labels: list[list[str]] = []

    async def fake_reconcile_labels(*, item_id: str, item_type: PlanItemType | None, labels: list[str]) -> None:
        _ = (item_id, item_type, labels)

    async def fake_project_item(item_id: str) -> str:
        _ = item_id
        return "PVTI_1"

    async def fake_project_fields(project_item_id: str, create_input: CreateItemInput) -> None:
        _ = project_item_id
        seen_labels.append(create_input.labels)

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)
    monkeypatch.setattr(provider, "_reconcile_managed_labels", fake_reconcile_labels)
    monkeypatch.setattr(provider, "_ensure_project_item", fake_project_item)
    monkeypatch.setattr(provider, "_ensure_project_fields", fake_project_fields)

    await provider.update_item(
        "I1",
        UpdateItemInput(title="new", item_type=PlanItemType.TASK, labels=["triage"], size="M"),
    )

    assert seen_labels == [["planpilot", "triage", "type:task"]]


@pytest.mark.asyncio
async def test_update_item_issue_type_strategy_sets_type_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue type is set atomically inside _update_issue; no separate _ensure_issue_type call."""
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={"TASK": "task-id"},
        project_owner_type="org",
        create_type_strategy="issue-type",
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    update_calls: list[tuple[str, UpdateItemInput]] = []

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        update_calls.append((item_id, update_input))
        return _make_issue_core(id=item_id, number=2, url="u", title="new", body="old")

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)

    await provider.update_item("I1", UpdateItemInput(title="new", item_type=PlanItemType.TASK))

    # _update_issue was called; it handles issue_type_id atomically
    assert len(update_calls) == 1
    assert update_calls[0][0] == "I1"
    assert update_calls[0][1].item_type == PlanItemType.TASK


@pytest.mark.asyncio
async def test_update_item_issue_type_strategy_applies_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={"TASK": "task-id"},
        project_owner_type="org",
        create_type_strategy="issue-type",
    )

    async def fake_get_item(item_id: str):
        return provider._item_from_issue_core(_make_issue_core(id=item_id, number=2, url="u", title="old", body="old"))

    async def fake_update_issue(item_id: str, update_input: UpdateItemInput) -> IssueCore:
        return _make_issue_core(id=item_id, number=2, url="u", title=update_input.title or "old", body="old")

    ensured: list[tuple[str, list[str]]] = []

    async def fake_ensure_discovery_labels(issue_id: str, labels: list[str]) -> None:
        ensured.append((issue_id, labels))

    monkeypatch.setattr(provider, "get_item", fake_get_item)
    monkeypatch.setattr(provider, "_update_issue", fake_update_issue)
    monkeypatch.setattr(provider, "_ensure_discovery_labels", fake_ensure_discovery_labels)

    await provider.update_item("I1", UpdateItemInput(title="new", labels=["planpilot", "triage"]))

    assert ensured == [("I1", ["planpilot", "triage"])]


def test_resolve_create_type_policy_user_falls_back_to_label() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/users/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(create_type_strategy="issue-type"),
    )

    strategy, mapping = provider._resolve_create_type_policy("user")
    assert strategy == "label"
    assert mapping["EPIC"] == "Epic"


def test_is_missing_relation_error_detects_absent_relation_messages() -> None:
    class _FakeGraphQLError(Exception):
        pass

    assert relations_ops.is_missing_relation_error(_FakeGraphQLError("not found"))
    assert relations_ops.is_missing_relation_error(_FakeGraphQLError("Relation does not exist"))
    assert not relations_ops.is_missing_relation_error(_FakeGraphQLError("already exists"))


@pytest.mark.asyncio
async def test_prime_relations_cache_avoids_per_item_fetches() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def fetch_relations(self, *, ids: list[str]):
            self.calls.append(ids)
            return SimpleNamespace(
                nodes=[
                    SimpleNamespace(
                        id="I1",
                        parent=SimpleNamespace(id="P1"),
                        blocked_by=SimpleNamespace(nodes=[SimpleNamespace(id="B1")]),
                    ),
                    SimpleNamespace(
                        id="I2",
                        parent=None,
                        blocked_by=SimpleNamespace(nodes=[]),
                    ),
                ]
            )

    client = _Client()
    provider._client = client  # type: ignore[assignment]

    await provider.prime_relations_cache(["I1", "I2"])

    parent1, blockers1 = await provider.get_relations(issue_id="I1")
    parent2, blockers2 = await provider.get_relations(issue_id="I2")

    assert client.calls == [["I1", "I2"]]
    assert parent1 == "P1"
    assert blockers1 == {"B1"}
    assert parent2 is None
    assert blockers2 == set()


@pytest.mark.asyncio
async def test_prime_relations_cache_with_empty_ids_sets_empty_cache() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    await provider.prime_relations_cache([])

    assert provider._relations_cache == {}


@pytest.mark.asyncio
async def test_prime_relations_cache_skips_nodes_without_string_ids() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        async def fetch_relations(self, *, ids: list[str]):
            _ = ids
            return SimpleNamespace(nodes=[SimpleNamespace(id=123, parent=None, blocked_by=SimpleNamespace(nodes=[]))])

    provider._client = _Client()  # type: ignore[assignment]

    await provider.prime_relations_cache(["I1"])

    assert provider._relations_cache == {"I1": (None, set())}


@pytest.mark.asyncio
async def test_get_relations_returns_empty_when_fetch_returns_no_nodes() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        async def fetch_relations(self, *, ids: list[str]):
            _ = ids
            return SimpleNamespace(nodes=[])

    provider._client = _Client()  # type: ignore[assignment]

    parent, blockers = await provider.get_relations(issue_id="I1")

    assert parent is None
    assert blockers == set()


@pytest.mark.asyncio
async def test_get_relations_skips_none_nodes_and_returns_empty() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        async def fetch_relations(self, *, ids: list[str]):
            _ = ids
            return SimpleNamespace(nodes=[None])

    provider._client = _Client()  # type: ignore[assignment]

    parent, blockers = await provider.get_relations(issue_id="I1")

    assert parent is None
    assert blockers == set()


@pytest.mark.asyncio
async def test_get_relations_returns_first_non_none_node() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        async def fetch_relations(self, *, ids: list[str]):
            _ = ids
            return SimpleNamespace(
                nodes=[
                    None,
                    SimpleNamespace(
                        id="I1",
                        parent=SimpleNamespace(id="P1"),
                        blocked_by=SimpleNamespace(nodes=[SimpleNamespace(id="B1")]),
                    ),
                ]
            )

    provider._client = _Client()  # type: ignore[assignment]

    parent, blockers = await provider.get_relations(issue_id="I1")

    assert parent == "P1"
    assert blockers == {"B1"}


@pytest.mark.asyncio
async def test_delete_item_calls_delete_issue() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _Client:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def delete_issue(self, *, issue_id: str) -> None:
            self.deleted.append(issue_id)

    client = _Client()
    provider._client = client  # type: ignore[assignment]

    await provider.delete_item("I-delete")

    assert client.deleted == ["I-delete"]


@pytest.mark.asyncio
async def test_delete_item_wraps_graphql_errors_as_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    class _FakeGraphQLError(Exception):
        pass

    monkeypatch.setattr(_provider_module(), "GraphQLClientError", _FakeGraphQLError)

    class _Client:
        async def delete_issue(self, *, issue_id: str) -> None:
            raise _FakeGraphQLError("cannot delete")

    provider._client = _Client()  # type: ignore[assignment]

    with pytest.raises(ProviderError, match="Failed to delete issue I-delete"):
        await provider.delete_item("I-delete")


@pytest.mark.asyncio
async def test_delete_item_requires_initialized_client() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )

    with pytest.raises(ProviderError, match="Provider is not initialized"):
        await provider.delete_item("I1")


@pytest.mark.asyncio
async def test_add_sub_issue_duplicate_error_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_sub_issues=True,
    )

    class _FakeGraphQLError(Exception):
        pass

    monkeypatch.setattr(_provider_module(), "GraphQLClientError", _FakeGraphQLError)

    class _Client:
        async def add_sub_issue(self, *, parent_id: str, child_id: str) -> None:
            raise _FakeGraphQLError("Duplicate sub-issues")

    provider._client = _Client()  # type: ignore[assignment]

    await provider.add_sub_issue(child_issue_id="I-child", parent_issue_id="I-parent")


@pytest.mark.asyncio
async def test_add_blocked_by_duplicate_error_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_blocked_by=True,
    )

    class _FakeGraphQLError(Exception):
        pass

    monkeypatch.setattr(_provider_module(), "GraphQLClientError", _FakeGraphQLError)

    class _Client:
        async def add_blocked_by(self, *, blocked_id: str, blocker_id: str) -> None:
            raise _FakeGraphQLError("This relation already exists")

    provider._client = _Client()  # type: ignore[assignment]

    await provider.add_blocked_by(blocked_issue_id="I-blocked", blocker_issue_id="I-blocker")


@pytest.mark.asyncio
async def test_remove_sub_issue_missing_relation_error_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_sub_issues=True,
    )

    class _FakeGraphQLError(Exception):
        pass

    monkeypatch.setattr(_provider_module(), "GraphQLClientError", _FakeGraphQLError)

    class _Client:
        async def remove_sub_issue(self, *, parent_id: str, child_id: str) -> None:
            _ = (parent_id, child_id)
            raise _FakeGraphQLError("relation does not exist")

    provider._client = _Client()  # type: ignore[assignment]

    await provider.remove_sub_issue(child_issue_id="I-child", parent_issue_id="I-parent")


@pytest.mark.asyncio
async def test_remove_sub_issue_raises_when_capability_missing() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_sub_issues=False,
    )

    with pytest.raises(ProviderCapabilityError, match="sub-issues"):
        await provider.remove_sub_issue(child_issue_id="I-child", parent_issue_id="I-parent")


@pytest.mark.asyncio
async def test_remove_blocked_by_missing_relation_error_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_blocked_by=True,
    )

    class _FakeGraphQLError(Exception):
        pass

    monkeypatch.setattr(_provider_module(), "GraphQLClientError", _FakeGraphQLError)

    class _Client:
        async def remove_blocked_by(self, *, blocked_id: str, blocker_id: str) -> None:
            _ = (blocked_id, blocker_id)
            raise _FakeGraphQLError("not found")

    provider._client = _Client()  # type: ignore[assignment]

    await provider.remove_blocked_by(blocked_issue_id="I-blocked", blocker_issue_id="I-blocker")


@pytest.mark.asyncio
async def test_remove_blocked_by_raises_when_capability_missing() -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        supports_blocked_by=False,
    )

    with pytest.raises(ProviderCapabilityError, match="blocked-by"):
        await provider.remove_blocked_by(blocked_issue_id="I-blocked", blocker_issue_id="I-blocker")


def test_extract_relations_from_node_ignores_non_string_ids() -> None:
    node = SimpleNamespace(
        parent=SimpleNamespace(id=123),
        blocked_by=SimpleNamespace(nodes=[SimpleNamespace(id="B1"), SimpleNamespace(id=999)]),
    )

    parent_id, blocker_ids = GitHubProvider._extract_relations_from_node(node)

    assert parent_id is None
    assert blocker_ids == {"B1"}


@pytest.mark.asyncio
async def test_reconcile_managed_labels_noop_when_already_desired(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        create_type_strategy="label",
        create_type_map={"TASK": "type:task"},
    )

    async def fake_get_label_name_to_id(item_id: str) -> dict[str, str]:
        _ = item_id
        return {
            "planpilot": "id-planpilot",
            "type:task": "id-task",
            "extra": "id-extra",
        }

    removed: list[list[str]] = []
    added: list[list[str]] = []

    async def fake_remove_labels_by_ids(item_id: str, label_ids: list[str]) -> None:
        _ = item_id
        removed.append(label_ids)

    async def fake_ensure_discovery_labels(item_id: str, labels: list[str]) -> None:
        _ = item_id
        added.append(labels)

    monkeypatch.setattr(provider, "_get_item_label_name_to_id", fake_get_label_name_to_id)
    monkeypatch.setattr(provider, "_remove_labels_by_ids", fake_remove_labels_by_ids)
    monkeypatch.setattr(provider, "_ensure_discovery_labels", fake_ensure_discovery_labels)

    await provider._reconcile_managed_labels(item_id="I1", item_type=PlanItemType.TASK, labels=["planpilot"])

    assert removed == []
    assert added == []


@pytest.mark.asyncio
async def test_reconcile_managed_labels_does_not_readd_existing_non_managed_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitHubProvider(
        target="acme/repo",
        token="token",
        board_url="https://github.com/orgs/acme/projects/1",
        label="planpilot",
        field_config=FieldConfig(),
    )
    provider.context = GitHubProviderContext(
        repo_id="repo-id",
        label_id="label-id",
        issue_type_ids={},
        project_owner_type="org",
        create_type_strategy="label",
        create_type_map={"TASK": "type:task"},
    )

    async def fake_get_label_name_to_id(item_id: str) -> dict[str, str]:
        _ = item_id
        return {
            "planpilot": "id-planpilot",
            "type:task": "id-task",
            "custom": "id-custom",
        }

    added: list[list[str]] = []

    async def fake_ensure_discovery_labels(item_id: str, labels: list[str]) -> None:
        _ = item_id
        added.append(labels)

    monkeypatch.setattr(provider, "_get_item_label_name_to_id", fake_get_label_name_to_id)
    monkeypatch.setattr(provider, "_ensure_discovery_labels", fake_ensure_discovery_labels)

    await provider._reconcile_managed_labels(
        item_id="I1",
        item_type=PlanItemType.TASK,
        labels=["planpilot", "custom"],
    )

    assert added == []


def test_is_duplicate_relation_error_returns_false_for_other_messages() -> None:
    err = Exception("Some unrelated GraphQL failure")
    assert GitHubProvider._is_duplicate_relation_error(err) is False
