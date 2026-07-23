"""Plan contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PlanItemType(StrEnum):
    EPIC = "EPIC"
    STORY = "STORY"
    TASK = "TASK"


class Scope(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_scope: list[str] = Field(default_factory=list)


class SpecRef(BaseModel):
    url: str
    section: str | None = None
    quote: str | None = None


class Estimate(BaseModel):
    tshirt: str | None = None
    hours: float | None = None


class Verification(BaseModel):
    commands: list[str] = Field(default_factory=list)
    ci_checks: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    manual_steps: list[str] = Field(default_factory=list)


class PlanItem(BaseModel):
    id: str
    type: PlanItemType
    title: str
    goal: str | None = None
    motivation: str | None = None
    parent_id: str | None = None
    sub_item_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimate: Estimate | None = None
    verification: Verification | None = None
    spec_ref: SpecRef | None = None
    scope: Scope | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class Plan(BaseModel):
    items: list[PlanItem]
