---
name: plan-sync
description: Use when a user has PRD/spec/roadmap files and wants .plans artifacts generated and synced to GitHub Issues + a Projects v2 board in one guided flow. Standalone — uses the pinned planpilot CLI runtime through uvx, with exact-version local fallbacks allowed.
---

# Plan Sync

## Overview

End-to-end planning skill: convert PRD/spec/roadmap inputs into schema-aligned `.plans` JSON files, validate integrity, then sync epics/stories/tasks to GitHub Issues and Projects v2 with idempotent reruns.

Supports three modes: `plan` (generate only), `sync` (sync existing plans), `full` (generate + sync).

## Skill Invocation Gate (MANDATORY)

Before any action, list available skills and invoke all that apply. If installed and applicable, you MUST use them. Process skills take priority over implementation skills.

## Prerequisites

- `planpilot` CLI accessible — preferably through the pinned zero-install command `uvx --from planpilot==2.7.0 planpilot`; a local `planpilot` or `python3 -m planpilot` fallback is allowed only when it reports the exact same version
- `gh` CLI installed and authenticated (scopes: `repo`, `project`)

## When to Use

- User asks to turn PRD/spec/roadmap docs into `.plans` JSON artifacts
- User asks to break specs into epics, stories, tasks
- User asks to push epics/stories/tasks into GitHub Issues + Projects v2
- User asks for one-command planning + project sync workflow
- User has multiple overlapping specs and needs one merged executable plan

Do **not** use when the user is asking to implement roadmap tasks.

## Inputs

Required for `plan` mode:
- One or more PRD/spec/roadmap files to decompose

Required for `sync` mode:
- `repo` (`OWNER/REPO`)
- `project_url` (GitHub Projects v2 URL)
- Existing `.plans/*.json` files

Required for `full` mode:
- All of the above

Recommended defaults:
- `plans_dir = .plans`
- `sync_path = .plans/sync-map.json`
- `label = planpilot`
- `status = Backlog`
- `priority = P1`
- `iteration = active`
- `size_field = Size`
- `size_from_tshirt = true`

## Outputs

Planning artifacts:
- `.plans/epics.json`
- `.plans/stories.json`
- `.plans/tasks.json`
- `planpilot.json`

Sync artifacts:
- `<sync_path>.dry-run` (dry-run sync map)
- `<sync_path>` (apply sync map)

---

## Item Scoping Rules

### Hierarchy Definitions

| Level | What it represents | Sizing guidance |
|-------|--------------------|-----------------|
| **Epic** | Strategic outcome or major capability. Maps to a project milestone or theme. | Multiple sprints / weeks of work. |
| **Story** | A single PR-sized deliverable that ships user-visible or system-visible value. | 1–3 days of focused work. If larger, split into multiple stories. |
| **Task** | A concrete, executable unit of work within a story. One developer, one sitting. | Hours, not days. |

### Decomposition Checklist

When analyzing specs, apply these rules:

1. **Epics** — extract from strategic outcomes, high-level features, or capability areas. Each epic should be independently valuable.
2. **Stories** — break each epic into PR-sized deliverables. Each story must:
   - Be implementable in a single PR
   - Have clear acceptance criteria that can be verified
   - Deliver testable, reviewable value
3. **Tasks** — break each story into executable steps. Each task must:
   - Be completable by one developer in one sitting
   - Have concrete requirements (not vague goals)
   - Include verification commands or evidence where possible

### Scoping Fields

Use `scope` to define explicit boundaries for any item:

```json
{
  "scope": {
    "in_scope": ["Build REST endpoint for /api/register", "Input validation"],
    "out_scope": ["OAuth integration", "Password reset flow"]
  }
}
```

### Sizing with Estimates

Use `estimate` on stories and tasks to communicate expected effort:

```json
{ "estimate": { "tshirt": "S" } }
{ "estimate": { "tshirt": "M", "hours": 8 } }
{ "estimate": { "hours": 4 } }
```

Valid t-shirt sizes: `XS`, `S`, `M`, `L`, `XL`. When `size_from_tshirt` is enabled in config, the t-shirt value maps to the project board's Size field.

### Conflict Resolution (Multi-Spec)

When merging multiple specs:
- Stricter acceptance criteria wins
- If equally strict, newer spec wording wins
- Record assumptions in the item's `assumptions` field

---

## Plan JSON Schema

### Item Types

Every item **must** include a `type` field: `"EPIC"`, `"STORY"`, or `"TASK"`.

### Required Fields (All Types)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Globally unique across all plan files |
| `type` | `string` | `"EPIC"`, `"STORY"`, or `"TASK"` |
| `title` | `string` | Short descriptive title |
| `goal` | `string` | What this item aims to achieve |
| `requirements` | `string[]` | Non-empty list of concrete requirements |
| `acceptance_criteria` | `string[]` | Non-empty list of verifiable criteria |

### Hierarchy Fields

| Field | Type | Rules |
|-------|------|-------|
| `parent_id` | `string \| null` | **Epics**: must be `null` or omitted. **Stories**: must reference an Epic `id`. **Tasks**: must reference a Story `id`. |
| `sub_item_ids` | `string[]` | Optional. List of child item IDs (informational; engine derives children from `parent_id`). |
| `depends_on` | `string[]` | Optional. IDs of items that must complete before this one. Cross-type allowed. |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `motivation` | `string` | Why this item matters |
| `estimate` | `object` | `{"tshirt": "S"}` and/or `{"hours": 6}` |
| `scope` | `object` | `{"in_scope": [...], "out_scope": [...]}` |
| `verification` | `object` | `{"commands": [], "ci_checks": [], "evidence": [], "manual_steps": []}` |
| `spec_ref` | `object` | `{"url": "...", "section": "...", "quote": "..."}` (`url` required if present) |
| `success_metrics` | `string[]` | Measurable outcomes |
| `assumptions` | `string[]` | Assumptions made during decomposition |
| `risks` | `string[]` | Known risks or blockers |
| `fields` | `object` | `{"<project field name>": "<option or iteration title>"}` — sets that item's project-board fields (e.g. `{"Priority": "High", "Area": "Web"}`) on GitHub single-select or iteration fields only. Applied on create and every sync, even when the issue is otherwise unchanged. An explicit value for the configured size field overrides the size derived from `estimate.tshirt`. Unknown field/value names are skipped with a warning, not a sync failure. |

### JSON Rules

- No comments
- No trailing commas
- Strict JSON only

### Plan File Layouts

**Split layout** (recommended): three separate arrays

```
.plans/epics.json    →  [ { Epic }, ... ]
.plans/stories.json  →  [ { Story }, ... ]
.plans/tasks.json    →  [ { Task }, ... ]
```

**Unified layout**: single file with typed items

```
.plans/plan.json  →  { "items": [ { Epic }, { Story }, { Task }, ... ] }
```

---

## Full Schema Examples

### Epic

```json
{
  "id": "E-1",
  "type": "EPIC",
  "title": "User authentication system",
  "goal": "Ship a complete auth system with registration, login, and session management.",
  "requirements": [
    "Support email+password registration",
    "JWT-based session tokens",
    "Secure password storage with bcrypt"
  ],
  "acceptance_criteria": [
    "Users can register and log in",
    "Sessions expire after 24 hours",
    "All auth endpoints return proper error codes"
  ],
  "scope": {
    "in_scope": ["Registration", "Login", "JWT sessions"],
    "out_scope": ["OAuth providers", "2FA", "Password reset"]
  },
  "success_metrics": ["Auth e2e tests pass", "No credential leaks in logs"],
  "sub_item_ids": ["S-1", "S-2"]
}
```

### Story

```json
{
  "id": "S-1",
  "type": "STORY",
  "title": "User registration endpoint",
  "goal": "Build the registration API so new users can create accounts.",
  "parent_id": "E-1",
  "requirements": [
    "POST /api/register accepts email and password",
    "Returns 201 on success, 422 on validation error, 409 on duplicate"
  ],
  "acceptance_criteria": [
    "Valid registration returns 201 with user ID",
    "Duplicate email returns 409",
    "Missing fields return 422"
  ],
  "estimate": { "tshirt": "M" },
  "sub_item_ids": ["T-1", "T-2"]
}
```

### Task

```json
{
  "id": "T-1",
  "type": "TASK",
  "title": "Create user database schema",
  "goal": "Define the users table with proper constraints.",
  "parent_id": "S-1",
  "requirements": [
    "Users table with id, email (unique), password_hash, created_at",
    "Migration script runs idempotently"
  ],
  "acceptance_criteria": [
    "Migration creates table without errors",
    "Unique constraint on email is enforced"
  ],
  "estimate": { "tshirt": "S", "hours": 3 },
  "verification": {
    "commands": ["pytest tests/test_db_schema.py -v"],
    "ci_checks": ["test", "lint"],
    "evidence": ["Migration runs cleanly on fresh database"]
  },
  "depends_on": [],
  "spec_ref": {
    "url": "docs/auth-spec.md",
    "section": "Database Design",
    "quote": "Users table must enforce unique email constraint"
  }
}
```

### Task with Dependency

```json
{
  "id": "T-2",
  "type": "TASK",
  "title": "Implement registration endpoint",
  "goal": "Build the POST /api/register handler with input validation.",
  "parent_id": "S-1",
  "requirements": [
    "POST /api/register accepts JSON body with email and password",
    "Passwords hashed with bcrypt before storage",
    "Returns 201 on success with user ID in body"
  ],
  "acceptance_criteria": [
    "Valid registration returns 201",
    "Duplicate email returns 409",
    "Invalid payload returns 422"
  ],
  "estimate": { "tshirt": "M" },
  "verification": {
    "commands": ["pytest tests/test_register.py -v"],
    "ci_checks": ["test", "lint"],
    "evidence": ["API endpoint responds correctly to all cases"]
  },
  "depends_on": ["T-1"]
}
```

---

## Config Schema (`planpilot.json`)

### Full Config

```json
{
  "provider": "github",
  "target": "OWNER/REPO",
  "board_url": "https://github.com/orgs/OWNER/projects/NUMBER",
  "plan_paths": {
    "epics": ".plans/epics.json",
    "stories": ".plans/stories.json",
    "tasks": ".plans/tasks.json"
  },
  "sync_path": ".plans/github-sync-map.json",
  "auth": "gh-cli",
  "validation_mode": "strict",
  "label": "planpilot",
  "max_concurrent": 5,
  "field_config": {
    "status": "Backlog",
    "priority": "P1",
    "iteration": "active",
    "size_field": "Size",
    "size_from_tshirt": true,
    "create_type_strategy": "issue-type",
    "create_type_map": {
      "EPIC": "Epic",
      "STORY": "Story",
      "TASK": "Task"
    }
  }
}
```

### Config Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `provider` | yes | — | `"github"` |
| `target` | yes | — | `OWNER/REPO` |
| `board_url` | yes | — | GitHub Projects v2 URL |
| `plan_paths` | yes | — | Split (`epics`/`stories`/`tasks`) or unified (`unified`) |
| `sync_path` | no | `sync-map.json` | Where to write sync map |
| `auth` | no | `gh-cli` | `"gh-cli"`, `"env"`, or `"token"` |
| `token` | no | — | Required only when `auth = "token"` |
| `validation_mode` | no | `strict` | `"strict"` or `"partial"` |
| `label` | no | `planpilot` | Label applied to all created issues |
| `max_concurrent` | no | `1` | Concurrent API operations (1–10) |
| `field_config` | no | see defaults | Project board field mappings |

### Unified Layout Config

```json
{
  "provider": "github",
  "target": "OWNER/REPO",
  "board_url": "https://github.com/orgs/OWNER/projects/NUMBER",
  "plan_paths": {
    "unified": ".plans/plan.json"
  },
  "sync_path": ".plans/github-sync-map.json"
}
```

---

## CLI Reference

### `planpilot init`

Interactive config generator:

```bash
planpilot init
planpilot init --output ./planpilot.json --defaults
```

Flags:
- `--output` / `-o` (default: `planpilot.json`): output path
- `--defaults`: skip prompts, use defaults

### `planpilot sync`

Sync plan files to GitHub:

```bash
planpilot sync --config ./planpilot.json --dry-run
planpilot sync --config ./planpilot.json --apply
planpilot sync --config ./planpilot.json --apply --verbose
```

Flags:
- `--config <path>`: required, path to planpilot.json
- `--dry-run` or `--apply`: required, mutually exclusive
- `--verbose` / `-v`: enable debug logging

### `planpilot --version`

Print installed version.

---

## Workflow

### 1) Mode Select

If mode is not explicit, ask once:
1. **Plan only** — generate/validate JSON from specs
2. **Sync only** — sync existing `.plans` files to GitHub
3. **Full** — generate + sync

Default recommendation: **Full**.

### 2) Analyze Specs (plan / full)

1. Read all PRD/spec/roadmap files
2. Extract: outcomes, requirements, constraints, dependencies, acceptance criteria
3. Resolve conflicts across specs (see Conflict Resolution above)
4. Record assumptions in item `assumptions` fields

### 3) Decompose into Plan Items (plan / full)

1. Identify **Epics** from strategic outcomes and major capabilities
2. Break epics into **Stories** — each must be PR-sized (1–3 days)
3. Break stories into **Tasks** — each must be one-sitting executable work
4. Wire hierarchy: set `parent_id` on every story and task
5. Set `depends_on` where sequencing is required
6. Add `estimate`, `scope`, `verification`, `spec_ref` where applicable
7. Write `.plans/epics.json`, `.plans/stories.json`, `.plans/tasks.json`

### 4) Validate (plan / full)

Syntax validation:

```bash
python3 -m json.tool .plans/epics.json >/dev/null
python3 -m json.tool .plans/stories.json >/dev/null
python3 -m json.tool .plans/tasks.json >/dev/null
```

Integrity checks:
- All `id` values globally unique across all plan files
- Every item has `type`, `title`, `goal`, `requirements`, `acceptance_criteria`
- All `parent_id` references exist and type-match (Story → Epic, Task → Story)
- All `depends_on` references exist
- Epics have no `parent_id`
- No circular dependencies

### 5) Preflight (sync / full)

Run preflight **before** config check — you need a working `planpilot` to run `planpilot init`.

#### 5a) Verify planpilot is available

Try each invocation in order until one succeeds:

1. `uvx --from planpilot==2.7.0 planpilot --version` → output must be `planpilot 2.7.0`; set `PLANPILOT_CMD="uvx --from planpilot==2.7.0 planpilot"`
2. `planpilot --version` → output must be `planpilot 2.7.0`; set `PLANPILOT_CMD="planpilot"`
3. `python3 -m planpilot --version` → output must be `planpilot 2.7.0`; set `PLANPILOT_CMD="python3 -m planpilot"`

If all three fail or only mismatched versions are available, STOP and direct the user to install or run the pinned release — see https://github.com/aryeko/planpilot/blob/main/skills/INSTALL.md.

Record which exact-version command succeeded as `PLANPILOT_CMD` — use it for all subsequent planpilot invocations in this session.

#### 5b) Verify GitHub auth

```bash
gh auth status
```

If auth fails, STOP and request login.

### 6) Ensure Config Exists (sync / full)

Check for `planpilot.json` in the working directory.

**If config is NOT found**, prompt the user:

> `planpilot.json` not found. Would you like to generate one?

If the user agrees, run the interactive init wizard. **You MUST run this from the git repository root** so that auto-discovery works:

- `detect_target()` reads the `origin` remote URL to pre-fill `OWNER/REPO` — this only works inside a git repo.
- `detect_plan_paths()` scans `.plans/` and `plans/` for existing plan files to pre-fill paths.
- Outside a git repo, both detections are disabled and every value must be entered manually.

Use the exact-version invocation form that succeeded in preflight (step 5a). Examples use `$PLANPILOT_CMD`; do not substitute an unverified local command.

```bash
cd <repo-root>
$PLANPILOT_CMD init
```

The wizard asks these questions in order. Forward each to the user with the hints below:

| # | Question | Hint for user |
|---|----------|---------------|
| 1 | **Provider** | Currently only `github` is supported. Accept default. |
| 2 | **Target repository (owner/repo)** | Auto-detected from git remote if inside a repo. Verify it matches the intended target. |
| 3 | **Board URL** | The GitHub Projects v2 URL, e.g. `https://github.com/orgs/OWNER/projects/N`. Find this on the project board page. The org is pre-filled from the target. |
| 4 | **Plan file layout** | `Split` (separate epics/stories/tasks files — recommended) or `Unified` (single plan.json). |
| 5 | **Plan file paths** | Auto-detected if `.plans/` already has files. Defaults: `.plans/epics.json`, `.plans/stories.json`, `.plans/tasks.json`. |
| 6 | **Sync map path** | Where sync state is persisted. Default: `.plans/sync-map.json`. |
| 7 | **Authentication strategy** | `gh-cli` (recommended — uses `gh` CLI token), `env` (reads `GITHUB_TOKEN`), or `token` (static token in config — not recommended). |
| 8 | **Advanced options** | Optional. Validation mode (`strict` / `partial`) and max concurrent ops (1–10). Defaults are fine for most cases. |
| 9 | **Create empty plan files?** | Say yes if `.plans/*.json` don't exist yet. Creates empty arrays as stubs. |

After the wizard completes, inform the user:

> Config generated at `planpilot.json`. **Track this file in git** — it defines your sync target and plan layout, and should be shared with collaborators.

If the user prefers non-interactive setup with auto-detected defaults:

```bash
$PLANPILOT_CMD init --defaults
```

This generates a config with placeholder `board_url` that must be edited manually.

### 7) Sync Execution (sync / full)

Always dry-run first:

```bash
$PLANPILOT_CMD sync --config ./planpilot.json --dry-run
```

Review dry-run output. If everything looks correct:

```bash
$PLANPILOT_CMD sync --config ./planpilot.json --apply
```

### 8) Post-Sync Verification (sync / full)

Must verify:
- Sync command succeeded (exit code 0)
- Expected created/existing counts shown in output
- Sync map files written at expected paths
- Rerun dry-run shows no unexpected churn (idempotent)

Report to user:
- Issue URLs for created/updated items
- Count of epics/stories/tasks synced
- Any warnings or errors
- Sync map artifact paths

---

## Common Mistakes

- Stopping at analysis notes without generating `.plans/*.json` files
- **Missing `type` field** on items — every item must have `"type": "EPIC"`, `"STORY"`, or `"TASK"`
- Producing invalid hierarchy (task under epic, story without epic parent)
- Using invalid shorthand for nested fields (e.g. `"estimate": "M"` instead of `{"tshirt": "M"}`)
- Skipping dry-run before apply
- Using old/nonexistent CLI flags
- Returning pseudo-JSON with comments or trailing commas
- Creating stories that are too large (not PR-sized) or tasks that span multiple days
- Omitting `goal`, `requirements`, or `acceptance_criteria` (required by validator)
- Forgetting to set `parent_id` on stories and tasks
- Assuming `planpilot` is available without checking — always run preflight (step 5a) first
- Running `planpilot init` outside the git repo root — auto-detection of target and plan paths will fail
- Not committing `planpilot.json` to git — config must be tracked for reproducible syncs
- Writing `planpilot.json` by hand with invalid field combinations (e.g. both `unified` and `epics` in `plan_paths`)

## Completion Criteria

This skill run is complete when:
1. `.plans/epics.json`, `.plans/stories.json`, `.plans/tasks.json` are valid JSON and schema-compliant
2. All items have `type`, and hierarchy is correctly wired via `parent_id`
3. IDs are globally unique, all references resolve
4. `planpilot.json` is present and correct
5. Dry-run succeeds
6. Apply succeeds (when requested by user)
7. Sync map output exists and rerun behavior is idempotent
