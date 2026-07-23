# PlanPilot v2 Architecture

## Design Principles

- **SDK-first** — The CLI is a thin wrapper around a fully-functional SDK. Programmatic usage is the default, CLI is convenience.

- **Layered dependencies** — Strict downward-only dependencies. Each layer depends only on the layer directly below it. No bypassing, no circular dependencies, no peer imports.

- **Adapter pattern** — Both providers (GitHub, Jira, Linear) and renderers (Markdown, wiki) are pluggable adapters. The core engine knows nothing about concrete implementations.

- **SOLID principles** — Single responsibility, open/closed via adapters, Liskov substitution for Provider/Renderer, interface segregation via focused ABCs, dependency inversion throughout.

- **Async-first** — All I/O operations are async. The SDK and engine are fully async-compatible.

## Layer Architecture

```mermaid
flowchart TB
    subgraph Contracts["Contracts Layer"]
        PlanDomain["plan domain<br/>PlanItem, PlanItemType<br/>Plan"]
        ItemDomain["item domain<br/>Item, CreateItemInput<br/>UpdateItemInput, ItemSearchFilters"]
        SyncDomain["sync domain<br/>SyncEntry, SyncMap<br/>SyncResult"]
        ConfigDomain["config domain<br/>PlanPilotConfig, FieldConfig"]
        ProviderDomain["provider domain<br/>Provider ABC"]
        RendererDomain["renderer domain<br/>BodyRenderer ABC"]
        Exceptions["exceptions<br/>PlanPilotError hierarchy"]
    end

    subgraph Core["Core Layer"]
        Engine["core/engine/<br/>SyncEngine<br/>orchestration"]
        PlanCore["core/plan/<br/>load, validate, hash"]
        Providers["core/providers/<br/>concrete adapters<br/>+ factory"]
        Renderers["core/renderers/<br/>concrete renderers<br/>+ factory"]
        Auth["core/auth/<br/>token resolution<br/>+ factory"]
        CoreConfig["core/config/<br/>load + scaffold"]
    end

    subgraph SDK["SDK Layer"]
        PlanPilot["PlanPilot<br/>public API facade"]
    end

    subgraph CLI["CLI Layer"]
        CLIParser["cli/parser.py<br/>argument schema"]
        CLIApp["cli/app.py<br/>routing + exits"]
        CLICommands["cli/commands/*<br/>sync, clean, init, map sync"]
        CLIPersistence["cli/persistence/*<br/>sync_map + remote_plan"]
    end

    CLIParser --> CLIApp
    CLIApp --> CLICommands
    CLICommands --> SDK
    CLICommands --> CLIPersistence
    SDK --> Engine
    SDK --> PlanCore
    SDK --> Providers
    SDK --> Renderers
    SDK --> Auth
    SDK --> CoreConfig
    Engine --> ProviderDomain
    Engine --> RendererDomain
    Engine --> PlanDomain
    Engine --> ItemDomain
    Engine --> SyncDomain
    Engine --> ConfigDomain
    PlanCore --> PlanDomain
    Providers --> ProviderDomain
    Providers --> ItemDomain
    Renderers --> RendererDomain
    Renderers --> PlanDomain
    Auth --> ConfigDomain
    RendererDomain --> PlanDomain
    ProviderDomain --> ItemDomain
```

### Contracts

Pure data types and abstract interfaces. Six domains with clear responsibilities. See [contracts.md](contracts.md) for all type definitions and field details.

### Core

Core contains runtime business logic and provider integrations.

| Module | Responsibility | Spec |
|--------|---------------|------|
| `core/engine/` | Sync orchestration (5-phase pipeline) | [engine.md](engine.md) |
| `core/plan/` | Load, validate, hash plan files | [plan.md](../modules/plan.md) |
| `core/providers/` | Concrete provider adapters + factory | [providers.md](../modules/providers.md) |
| `core/renderers/` | Concrete renderer implementations + factory | [renderers.md](../modules/renderers.md) |
| `core/auth/` | Token resolution strategies + factory | [auth.md](../modules/auth.md) |
| `core/config/` | Config loading and scaffold helpers | [config.md](../modules/config.md) |
| `core/map_sync/` | Remote plan ID discovery/selection and local sync-map reconciliation | [map-sync.md](../modules/map-sync.md) |
| `core/clean/` | Metadata-scoped item discovery and leaf-first deletion planning | [clean.md](../modules/clean.md) |

**Rules:** Engine receives Provider and Renderer via dependency injection. Provider internals stay isolated under `core/providers/*`. Core modules must not import CLI modules.

### SDK

The composition root — the only place that sees all Core modules and wires them together. See [sdk.md](../modules/sdk.md).

### CLI

Thin shell wrapper implemented as a package (`cli/parser.py`, `cli/app.py`, `cli/commands/*`). Commands import from the SDK public API and approved CLI persistence helpers. See [cli.md](../modules/cli.md).

## Dependency Rules

| Layer | Can Import From | Cannot Import From |
|-------|----------------|-------------------|
| **Contracts** | Other Contract domains (downward only), stdlib, third-party | Core, SDK, CLI |
| **Core** | Contracts and approved Core peers/utilities, stdlib, third-party | CLI |
| **SDK** | Core, Contracts (re-exports selected types publicly) | CLI, CLI persistence |
| **CLI** | SDK public API (which re-exports selected Contracts types), approved CLI persistence helpers | Core directly, provider internals |

The SDK re-exports Contracts types (e.g. `SyncResult`, `PlanPilotConfig`, `PlanItemType`) so that CLI and external callers access them through the SDK without importing Contracts directly.

## UML Class Diagram

```mermaid
classDiagram
    class PlanItemType {
        <<enum>>
        EPIC
        STORY
        TASK
    }

    class PlanItem {
        +str id
        +PlanItemType type
        +str title
        +str? goal
        +str? motivation
        +str? parent_id
        +list~str~ sub_item_ids
        +list~str~ depends_on
        +list~str~ requirements
        +list~str~ acceptance_criteria
        +list~str~ success_metrics
        +list~str~ assumptions
        +list~str~ risks
        +Estimate? estimate
        +Verification? verification
        +SpecRef? spec_ref
        +Scope? scope
        +dict~str, str~ fields
    }

    class Plan {
        +list~PlanItem~ items
    }

    class Item {
        <<abstract>>
        +str id
        +str key
        +str url
        +str title
        +str body
        +PlanItemType? item_type
        +reconcile_relations(parent, blockers)*
    }

    class SyncResult {
        +SyncMap sync_map
        +dict items_created
        +bool dry_run
    }

    class Provider {
        <<abstract>>
        +__aenter__() Provider
        +__aexit__()
        +search_items(ItemSearchFilters) list~Item~
        +create_item(CreateItemInput) Item
        +update_item(str, UpdateItemInput) Item
        +get_item(str) Item
        +delete_item(str)
    }

    class RenderContext {
        +str plan_id
        +str parent_ref
        +list~tuple~ sub_items
        +dict dependencies
    }

    class BodyRenderer {
        <<abstract>>
        +render(PlanItem, RenderContext) str
    }

    class SyncEngine {
        -Provider provider
        -BodyRenderer renderer
        -PlanPilotConfig config
        -bool dry_run
        +sync(Plan, str) SyncResult
    }

    class PlanPilot {
        -Provider | None provider
        -BodyRenderer renderer
        -PlanPilotConfig config
        +sync(plan: Plan | None, *, dry_run: bool) SyncResult
    }

    PlanItem --> PlanItemType : has type
    Plan --> PlanItem : contains
    Item --> PlanItemType : has item_type
    SyncEngine --> Provider : uses
    SyncEngine --> BodyRenderer : uses
    SyncEngine --> Plan : processes
    SyncEngine --> SyncResult : returns
    PlanPilot --> SyncEngine : constructs
    PlanPilot --> Provider : injects
    PlanPilot --> BodyRenderer : injects
```

## Data Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant SDK as PlanPilot
    participant Engine as SyncEngine
    participant Item as Item
    participant Provider as Provider ABC
    participant Renderer as BodyRenderer

    User->>CLI: planpilot sync --config planpilot.json --apply
    CLI->>SDK: config = load_config(path)
    CLI->>SDK: pp = await PlanPilot.from_config(config, renderer_name="markdown")
    CLI->>SDK: sync(dry_run=False | true)
    SDK->>SDK: load_plan(config.plan_paths)
    SDK->>SDK: compute_plan_id(plan)
    alt apply mode
        SDK->>SDK: resolve token + create provider
        SDK->>Provider: __aenter__()
        Provider-->>SDK: authenticated provider
    else dry-run mode
        SDK->>SDK: create DryRunProvider (no auth/network)
    end
    SDK->>Engine: SyncEngine(provider, renderer, config, dry_run)
    SDK->>Engine: sync(plan, plan_id)

    Engine->>Provider: search_items(filters)
    Provider-->>Engine: list[Item]

    loop For each PlanItem
        Engine->>Renderer: render(item, RenderContext)
        Renderer-->>Engine: body string
        Engine->>Provider: create_item(CreateItemInput)
        Provider-->>Engine: Item (with provider injected)
    end

    Engine->>Renderer: render(item, RenderContext with cross-refs)
    Renderer-->>Engine: updated body strings
    Engine->>Provider: update_item(id, UpdateItemInput)

    Engine->>Item: reconcile_relations(parent, blockers)
    Item->>Provider: internal relation add/remove calls

    Engine-->>SDK: SyncResult
    alt apply mode
        SDK->>Provider: __aexit__()
    end
    SDK-->>CLI: SyncResult
    alt apply mode
        CLI->>CLI: persist sync-map
    else dry-run mode
        CLI->>CLI: persist dry-run sync-map only
    end
    CLI-->>User: formatted output
```

## Key Architectural Decisions

### Contracts as domain-organized vocabulary

Contracts are organized into six focused domains (plan, item, sync, config, provider, renderer), each with clear responsibility and minimal cross-domain dependencies. This prevents the "models/ junk drawer" anti-pattern.

### Provider and Renderer ABCs live in Contracts, not Core

The interfaces define **what the system needs** from external adapters. They are part of the domain vocabulary. Core contains only **how** — concrete implementations and factories.

### Deterministic plan hashing is canonicalized

`PlanHasher` computes `plan_id` from canonically ordered plan items plus canonical JSON serialization, including normalization for empty-vs-missing optional containers.

### Engine and Providers are Core peers

The engine receives a `Provider` ABC and calls its methods. Concrete adapters implement `Provider` but don't know the engine exists. They communicate only through Contracts. Relation convergence happens through `Item.reconcile_relations(...)`, which lets providers handle add/remove semantics internally.

### SDK is the composition root

The SDK is the only place that sees all Core domains and wires them together. Core modules may import approved peer utilities within Core, but runtime composition and cross-domain assembly still happens in the SDK.

### CLI depends only on SDK

The CLI is pure I/O — argument parsing and output formatting. It imports only from the SDK's public API surface. The CLI could be replaced with a web UI or a script and the SDK would work unchanged.
