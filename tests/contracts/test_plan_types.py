from planpilot.core.contracts.plan import Plan, PlanItem, PlanItemType


def test_plan_item_type_enum_values() -> None:
    assert PlanItemType.EPIC.value == "EPIC"
    assert PlanItemType.STORY.value == "STORY"
    assert PlanItemType.TASK.value == "TASK"


def test_plan_item_mutable_defaults_are_not_shared() -> None:
    first = PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic one")
    second = PlanItem(id="E2", type=PlanItemType.EPIC, title="Epic two")

    first.depends_on.append("X")
    first.fields["Priority"] = "High"

    assert second.depends_on == []
    assert second.fields == {}


def test_plan_item_fields_defaults_to_empty_dict() -> None:
    item = PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")

    assert item.fields == {}


def test_plan_item_accepts_project_fields() -> None:
    item = PlanItem(
        id="S1",
        type=PlanItemType.STORY,
        title="Story",
        fields={"Priority": "High", "Horizon": "Now", "Area": "Web"},
    )

    assert item.fields == {"Priority": "High", "Horizon": "Now", "Area": "Web"}


def test_plan_holds_plan_items() -> None:
    plan = Plan(items=[PlanItem(id="E1", type=PlanItemType.EPIC, title="Epic")])

    assert len(plan.items) == 1
    assert plan.items[0].id == "E1"
