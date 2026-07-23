"""GitHub provider adapter - uses the ariadne-codegen generated GraphQL client."""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType

import httpx

from planpilot.core.contracts.config import FieldConfig
from planpilot.core.contracts.exceptions import (
    CreateItemPartialFailureError,
    ProviderCapabilityError,
    ProviderError,
)
from planpilot.core.contracts.item import CreateItemInput, Item, ItemSearchFilters, UpdateItemInput
from planpilot.core.contracts.plan import PlanItemType
from planpilot.core.contracts.provider import Provider
from planpilot.core.providers.github.github_gql.client import GitHubGraphQLClient
from planpilot.core.providers.github.github_gql.exceptions import GraphQLClientError
from planpilot.core.providers.github.github_gql.fragments import IssueCore
from planpilot.core.providers.github.item import GitHubItem
from planpilot.core.providers.github.models import GitHubProviderContext, ResolvedField
from planpilot.core.providers.github.ops import convert as convert_ops
from planpilot.core.providers.github.ops import crud as crud_ops
from planpilot.core.providers.github.ops import labels as labels_ops
from planpilot.core.providers.github.ops import project as project_ops
from planpilot.core.providers.github.ops import relations as relations_ops

_LOG = logging.getLogger(__name__)


class GitHubProvider(Provider):
    """Thin adapter that delegates all GitHub GraphQL interactions to the generated client."""

    def __init__(
        self,
        *,
        target: str,
        token: str,
        board_url: str,
        label: str = "planpilot",
        field_config: FieldConfig | None = None,
    ) -> None:
        self._target = target
        self._token = token
        self._board_url = board_url
        self._label = label
        self._field_config = field_config or FieldConfig()

        self._client: GitHubGraphQLClient | None = None
        self._project_item_lock = asyncio.Lock()
        self._relations_cache: dict[str, tuple[str | None, set[str]]] | None = None

        self.context = GitHubProviderContext(
            repo_id="",
            label_id="",
            issue_type_ids={},
            project_owner_type="org",
            create_type_strategy=self._field_config.create_type_strategy,
            create_type_map=dict(self._field_config.create_type_map),
        )

    async def __aenter__(self) -> GitHubProvider:
        await self._open_transport()

        repo_id, label_id, issue_type_ids = await self._resolve_repo_context()
        owner_type, _, _, project_id = await self._resolve_project_context()

        size_field_id: str | None = None
        size_options: list[dict[str, str]] = []
        resolved_fields: dict[str, ResolvedField] = {}
        if project_id:
            (
                size_field_id,
                size_options,
                resolved_fields,
            ) = await self._resolve_project_fields(project_id)

        create_type_strategy, create_type_map = self._resolve_create_type_policy(owner_type)

        has_issue_types = bool(issue_type_ids)
        if create_type_strategy == "issue-type" and not has_issue_types:
            _LOG.warning(
                "create_type_strategy is 'issue-type' but repository has no issue types configured; "
                "falling back to 'label' strategy"
            )
            create_type_strategy = "label"

        self.context = GitHubProviderContext(
            repo_id=repo_id,
            label_id=label_id,
            issue_type_ids=issue_type_ids,
            project_owner_type=owner_type,
            project_id=project_id,
            size_field_id=size_field_id,
            size_options=size_options,
            resolved_fields=resolved_fields,
            supports_sub_issues=True,
            supports_blocked_by=True,
            supports_discovery_filters=True,
            supports_issue_type=has_issue_types,
            create_type_strategy=create_type_strategy,
            create_type_map=create_type_map,
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
            self._client = None

    async def search_items(self, filters: ItemSearchFilters) -> list[Item]:
        query_parts = [f"repo:{self._target}", "is:issue"]
        for label in filters.labels:
            escaped_label = label.replace('"', '\\"')
            query_parts.append(f'label:"{escaped_label}"')
        if filters.body_contains:
            escaped_body_contains = filters.body_contains.replace('"', '\\"')
            query_parts.append(f'"{escaped_body_contains}" in:body')
        query = " ".join(query_parts)

        nodes = await self._search_issue_nodes(query)
        return [self._item_from_issue_core(node) for node in nodes]

    async def create_item(self, input: CreateItemInput) -> Item:
        completed_steps: list[str] = []
        issue: IssueCore | None = None

        try:
            issue = await self._create_issue(input)
            completed_steps.extend(["issue_created", "issue_type_set", "labels_set"])

            if self.context.project_id:
                project_item_id = await self._ensure_project_item(issue.id)
                completed_steps.append("project_item_added")

                await self._ensure_project_fields(project_item_id, input)
                completed_steps.append("project_fields_set")

            return self._item_from_issue_core(issue)
        except Exception as exc:
            if issue is None:
                raise
            raise CreateItemPartialFailureError(
                f"create_item failed after issue creation: {exc}",
                created_item_id=issue.id,
                created_item_key=f"#{issue.number}",
                created_item_url=issue.url,
                completed_steps=tuple(completed_steps),
                retryable=False,
            ) from exc

    async def update_item(self, item_id: str, input: UpdateItemInput) -> Item:
        existing = await self.get_item(item_id)

        update_input = UpdateItemInput(
            title=input.title,
            body=input.body,
            item_type=input.item_type,
            labels=input.labels,
            size=input.size,
        )
        issue = await self._update_issue(item_id, update_input)

        if input.labels is not None:
            if self.context.create_type_strategy == "label":
                await self._reconcile_managed_labels(
                    item_id=item_id,
                    item_type=input.item_type,
                    labels=input.labels,
                )
            else:
                await self._ensure_discovery_labels(item_id, input.labels)
        elif self.context.create_type_strategy == "label" and input.item_type is not None:
            await self._reconcile_managed_labels(
                item_id=item_id,
                item_type=input.item_type,
                labels=[self._label],
            )

        effective_labels = list(input.labels or [])
        if self.context.create_type_strategy == "label":
            effective_labels = sorted(set(effective_labels).union({self._label}))
            if input.item_type is not None:
                mapped = self.context.create_type_map.get(input.item_type.value)
                if mapped:
                    effective_labels = sorted(set(effective_labels).union({mapped}))
        if (input.size is not None or input.fields) and self.context.project_id is not None:
            project_item_id = await self._ensure_project_item(item_id)
            await self._ensure_project_fields(
                project_item_id,
                CreateItemInput(
                    title=issue.title,
                    body=issue.body,
                    item_type=input.item_type or existing.item_type or PlanItemType.TASK,
                    labels=effective_labels,
                    size=input.size,
                    fields=input.fields,
                ),
            )

        return self._item_from_issue_core(issue)

    async def get_item(self, item_id: str) -> Item:  # pragma: no cover
        issue = await self._get_issue(item_id)
        return self._item_from_issue_core(issue)

    async def delete_item(self, item_id: str) -> None:  # pragma: no cover
        client = self._require_client()
        try:
            await client.delete_issue(issue_id=item_id)
        except GraphQLClientError as exc:
            raise ProviderError(f"Failed to delete issue {item_id}: {exc}") from exc

    async def add_sub_issue(self, *, child_issue_id: str, parent_issue_id: str) -> None:  # pragma: no cover
        if not self.context.supports_sub_issues:
            raise ProviderCapabilityError("GitHub provider does not support sub-issues.", capability="sub-issues")
        client = self._require_client()
        try:
            await client.add_sub_issue(parent_id=parent_issue_id, child_id=child_issue_id)
            if self._relations_cache is not None:
                self._relations_cache.pop(child_issue_id, None)
        except GraphQLClientError as exc:
            if self._is_duplicate_relation_error(exc):
                _LOG.debug("Sub-issue relationship already exists: %s -> %s", child_issue_id, parent_issue_id)
                return
            raise ProviderError(f"Failed to add sub-issue: {exc}") from exc

    async def remove_sub_issue(self, *, child_issue_id: str, parent_issue_id: str) -> None:  # pragma: no cover
        if not self.context.supports_sub_issues:
            raise ProviderCapabilityError("GitHub provider does not support sub-issues.", capability="sub-issues")
        client = self._require_client()
        try:
            await client.remove_sub_issue(parent_id=parent_issue_id, child_id=child_issue_id)
            if self._relations_cache is not None:
                self._relations_cache.pop(child_issue_id, None)
        except GraphQLClientError as exc:
            if relations_ops.is_missing_relation_error(exc):
                _LOG.debug("Sub-issue relationship already absent: %s -> %s", child_issue_id, parent_issue_id)
                return
            raise ProviderError(f"Failed to remove sub-issue: {exc}") from exc

    async def add_blocked_by(self, *, blocked_issue_id: str, blocker_issue_id: str) -> None:  # pragma: no cover
        if not self.context.supports_blocked_by:
            raise ProviderCapabilityError("GitHub provider does not support blocked-by.", capability="blocked-by")
        client = self._require_client()
        try:
            await client.add_blocked_by(blocked_id=blocked_issue_id, blocker_id=blocker_issue_id)
            if self._relations_cache is not None:
                self._relations_cache.pop(blocked_issue_id, None)
        except GraphQLClientError as exc:
            if self._is_duplicate_relation_error(exc):
                _LOG.debug("Blocked-by relationship already exists: %s -> %s", blocked_issue_id, blocker_issue_id)
                return
            raise ProviderError(f"Failed to add blocked-by relation: {exc}") from exc

    async def remove_blocked_by(self, *, blocked_issue_id: str, blocker_issue_id: str) -> None:  # pragma: no cover
        if not self.context.supports_blocked_by:
            raise ProviderCapabilityError("GitHub provider does not support blocked-by.", capability="blocked-by")
        client = self._require_client()
        try:
            await client.remove_blocked_by(blocked_id=blocked_issue_id, blocker_id=blocker_issue_id)
            if self._relations_cache is not None:
                self._relations_cache.pop(blocked_issue_id, None)
        except GraphQLClientError as exc:
            if relations_ops.is_missing_relation_error(exc):
                _LOG.debug("Blocked-by relationship already absent: %s -> %s", blocked_issue_id, blocker_issue_id)
                return
            raise ProviderError(f"Failed to remove blocked-by relation: {exc}") from exc

    async def prime_relations_cache(self, issue_ids: list[str]) -> None:
        if not issue_ids:
            self._relations_cache = {}
            return
        client = self._require_client()
        data = await client.fetch_relations(ids=issue_ids)
        cache: dict[str, tuple[str | None, set[str]]] = {issue_id: (None, set()) for issue_id in issue_ids}
        for node in data.nodes:
            node_id = getattr(node, "id", None)
            if not isinstance(node_id, str):
                continue
            cache[node_id] = self._extract_relations_from_node(node)
        self._relations_cache = cache

    async def get_relations(self, *, issue_id: str) -> tuple[str | None, set[str]]:
        if self._relations_cache is not None and issue_id in self._relations_cache:
            parent_id, blocker_ids = self._relations_cache[issue_id]
            return parent_id, set(blocker_ids)
        client = self._require_client()
        data = await client.fetch_relations(ids=[issue_id])
        if not data.nodes:
            return None, set()
        for node in data.nodes:
            if node is None:
                continue
            return self._extract_relations_from_node(node)
        return None, set()

    @staticmethod
    def _extract_relations_from_node(node: object) -> tuple[str | None, set[str]]:
        parent_id: str | None = None
        blocker_ids: set[str] = set()
        parent = getattr(node, "parent", None)
        blocked_by = getattr(node, "blocked_by", None)
        if parent is not None:
            candidate_parent_id = getattr(parent, "id", None)
            if isinstance(candidate_parent_id, str):
                parent_id = candidate_parent_id
        if blocked_by is not None and blocked_by.nodes:
            for blocker in blocked_by.nodes:
                blocker_id = getattr(blocker, "id", None)
                if isinstance(blocker_id, str):
                    blocker_ids.add(blocker_id)
        return parent_id, blocker_ids

    @staticmethod
    def _is_duplicate_relation_error(exc: GraphQLClientError) -> bool:
        return relations_ops.is_duplicate_relation_error(exc)

    async def _open_transport(self) -> None:  # pragma: no cover
        from planpilot.core.providers.github._retrying_transport import RetryingTransport

        transport = RetryingTransport()
        http = httpx.AsyncClient(
            transport=transport,
            headers={"Authorization": f"Bearer {self._token}"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(30.0),
        )
        self._client = GitHubGraphQLClient(
            url="https://api.github.com/graphql",
            http_client=http,
        )

    def _require_client(self) -> GitHubGraphQLClient:
        if self._client is None:
            raise ProviderError("Provider is not initialized. Use 'async with'.")
        return self._client

    async def _resolve_repo_context(self) -> tuple[str, str, dict[str, str]]:  # pragma: no cover
        client = self._require_client()
        owner, repo = self._split_target()
        data = await client.fetch_repo(owner=owner, name=repo)

        repository = data.repository
        if repository is None:
            raise ProviderError("Repository not found")
        repo_id = repository.id

        label_id = ""
        if repository.labels and repository.labels.nodes:
            for node in repository.labels.nodes:
                if node and node.name == self._label:
                    label_id = node.id
                    break
        if not label_id:
            label_id = await self._create_label(repo_id)

        issue_type_ids: dict[str, str] = {}
        if repository.issue_types and repository.issue_types.nodes:
            for it_node in repository.issue_types.nodes:
                if it_node:
                    issue_type_ids[it_node.name.upper()] = it_node.id

        return repo_id, label_id, issue_type_ids

    async def _resolve_project_context(self) -> tuple[str, str, int, str | None]:  # pragma: no cover
        return await project_ops.resolve_project_context(self)

    def _resolve_create_type_policy(self, owner_type: str) -> tuple[str, dict[str, str]]:
        return project_ops.resolve_create_type_policy(self, owner_type)

    async def _resolve_project_fields(  # pragma: no cover
        self, project_id: str
    ) -> tuple[str | None, list[dict[str, str]], dict[str, ResolvedField]]:
        return await project_ops.resolve_project_fields(self, project_id)

    async def _search_issue_nodes(self, query: str) -> list[IssueCore]:  # pragma: no cover
        return await crud_ops.search_issue_nodes(self, query)

    async def _create_issue(self, input: CreateItemInput) -> IssueCore:  # pragma: no cover
        return await crud_ops.create_issue(self, input)

    async def _update_issue(self, item_id: str, input: UpdateItemInput) -> IssueCore:  # pragma: no cover
        return await crud_ops.update_issue(self, item_id, input)

    async def _get_issue(self, item_id: str) -> IssueCore:  # pragma: no cover
        return await crud_ops.get_issue(self, item_id)

    async def _get_item_labels(self, item_id: str) -> list[str]:  # pragma: no cover
        return await crud_ops.get_item_labels(self, item_id)

    async def _get_item_label_name_to_id(self, item_id: str) -> dict[str, str]:  # pragma: no cover
        return await crud_ops.get_item_label_name_to_id(self, item_id)

    async def _ensure_issue_type(self, issue_id: str, item_type: PlanItemType) -> None:  # pragma: no cover
        await labels_ops.ensure_issue_type(self, issue_id, item_type)

    async def _ensure_type_label(self, issue_id: str, item_type: PlanItemType) -> None:  # pragma: no cover
        await labels_ops.ensure_type_label(self, issue_id, item_type)

    async def _ensure_discovery_labels(self, issue_id: str, labels: list[str]) -> None:  # pragma: no cover
        await labels_ops.ensure_discovery_labels(self, issue_id, labels)

    async def _remove_labels_by_ids(self, issue_id: str, label_ids: list[str]) -> None:  # pragma: no cover
        await labels_ops.remove_labels_by_ids(self, issue_id, label_ids)

    async def _reconcile_managed_labels(
        self,
        *,
        item_id: str,
        item_type: PlanItemType | None,
        labels: list[str],
    ) -> None:
        desired_labels = set(labels)
        desired_labels.add(self._label)
        if item_type is not None and self.context.create_type_strategy == "label":
            mapped = self.context.create_type_map.get(item_type.value)
            if mapped:
                desired_labels.add(mapped)

        existing_label_ids = await self._get_item_label_name_to_id(item_id)
        managed_names = {self._label}.union(set(self.context.create_type_map.values()))
        existing_names = set(existing_label_ids)
        current_managed = {name for name in existing_label_ids if name in managed_names}

        stale_names = sorted(current_managed.difference(desired_labels))
        missing_names = sorted(desired_labels.difference(existing_names))

        if stale_names:
            remove_ids = [existing_label_ids[name] for name in stale_names if name in existing_label_ids]
            if remove_ids:
                await self._remove_labels_by_ids(item_id, remove_ids)
        if missing_names:
            await self._ensure_discovery_labels(item_id, missing_names)

    async def _resolve_label_ids(self, label_names: list[str]) -> list[str]:  # pragma: no cover
        return await labels_ops.resolve_label_ids(self, label_names)

    async def _find_or_create_label(self, name: str) -> str:  # pragma: no cover
        return await labels_ops.find_or_create_label(self, name)

    async def _create_label(self, repo_id: str, *, name: str | None = None) -> str:  # pragma: no cover
        return await labels_ops.create_label(self, repo_id, name=name)

    async def _ensure_project_item(self, issue_id: str) -> str:  # pragma: no cover
        return await project_ops.ensure_project_item(self, issue_id)

    async def _ensure_project_fields(self, project_item_id: str, input: CreateItemInput) -> None:  # pragma: no cover
        await project_ops.ensure_project_fields(self, project_item_id, input)

    def _item_from_issue_core(self, issue: IssueCore) -> GitHubItem:
        return convert_ops.item_from_issue_core(self, issue)

    def _split_target(self) -> tuple[str, str]:  # pragma: no cover
        return convert_ops.split_target(self._target)
