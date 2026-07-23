"""Public API surface for PlanPilot."""

__version__ = "2.7.0"

from planpilot.core.auth import (
    create_init_token_resolver,
    create_token_resolver,
    resolve_init_token,
    validate_github_auth_for_init,
)
from planpilot.core.config import (
    create_plan_stubs,
    detect_plan_paths,
    detect_target,
    load_config,
    scaffold_config,
    write_config,
)
from planpilot.core.contracts.config import FieldConfig, PlanPaths, PlanPilotConfig
from planpilot.core.contracts.exceptions import (
    AuthenticationError,
    ConfigError,
    PlanLoadError,
    PlanPilotError,
    PlanValidationError,
    ProviderError,
    SyncError,
)
from planpilot.core.contracts.init import InitProgress
from planpilot.core.contracts.plan import Plan, PlanItem, PlanItemType
from planpilot.core.contracts.provider import Provider
from planpilot.core.contracts.renderer import BodyRenderer, RenderContext
from planpilot.core.contracts.sync import CleanResult, MapSyncResult, SyncEntry, SyncMap, SyncResult
from planpilot.core.engine.progress import SyncProgress
from planpilot.core.init import validate_board_url
from planpilot.core.providers import create_provider
from planpilot.core.renderers import create_renderer
from planpilot.sdk import PlanPilot, load_plan

__all__ = [
    "AuthenticationError",
    "BodyRenderer",
    "CleanResult",
    "ConfigError",
    "FieldConfig",
    "InitProgress",
    "MapSyncResult",
    "Plan",
    "PlanItem",
    "PlanItemType",
    "PlanLoadError",
    "PlanPaths",
    "PlanPilot",
    "PlanPilotConfig",
    "PlanPilotError",
    "PlanValidationError",
    "Provider",
    "ProviderError",
    "RenderContext",
    "SyncEntry",
    "SyncError",
    "SyncMap",
    "SyncProgress",
    "SyncResult",
    "__version__",
    "create_init_token_resolver",
    "create_plan_stubs",
    "create_provider",
    "create_renderer",
    "create_token_resolver",
    "detect_plan_paths",
    "detect_target",
    "load_config",
    "load_plan",
    "resolve_init_token",
    "scaffold_config",
    "validate_board_url",
    "validate_github_auth_for_init",
    "write_config",
]
