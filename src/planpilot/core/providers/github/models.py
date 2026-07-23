"""GitHub provider models."""

from __future__ import annotations

from dataclasses import dataclass, field

from planpilot.core.providers.base import ProviderContext


@dataclass(frozen=True)
class ResolvedField:
    id: str
    name: str
    kind: str
    options: list[dict[str, str]] = field(default_factory=list)


@dataclass
class GitHubProviderContext(ProviderContext):
    repo_id: str
    label_id: str
    issue_type_ids: dict[str, str]
    project_owner_type: str
    project_id: str | None = None
    project_item_ids: dict[str, str] = field(default_factory=dict)
    resolved_fields: dict[str, ResolvedField] = field(default_factory=dict)
    size_field_id: str | None = None
    size_options: list[dict[str, str]] = field(default_factory=list)
    supports_sub_issues: bool = False
    supports_blocked_by: bool = False
    supports_discovery_filters: bool = True
    supports_issue_type: bool = True
    create_type_strategy: str = "issue-type"
    create_type_map: dict[str, str] = field(default_factory=dict)
