"""The shapes that cross boundaries.

Deliberately small. These types are the contract between JARVIS and every
future agent, so anything added here has to be earned -- a field that
exists because it might be useful ends up half-populated and untrustworthy.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class Lifecycle(str, Enum):
    """Where an agent version is in its life.

    An agent is never edited in place: a change means a new version, so
    the old one can be compared against and rolled back to.
    """

    EXPERIMENTAL = "experimental"   # registered, not routable
    TESTING = "testing"             # routable only when asked for by name
    ACTIVE = "active"               # routable normally
    DEGRADED = "degraded"           # routable last, something is wrong
    DISABLED = "disabled"           # not routable, kept for comparison
    RETIRED = "retired"             # historical only


ROUTABLE = {Lifecycle.ACTIVE, Lifecycle.DEGRADED}


class TaskStatus(str, Enum):
    QUEUED = "queued"                      # ready to run
    BLOCKED = "blocked"                    # waiting on a dependency
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"  # needs the owner
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class Permission(str, Enum):
    """What an agent is allowed to touch.

    Coarse on purpose. Fine-grained permissions read well in a design
    document and become impossible to reason about in practice -- and a
    permission nobody understands gets granted by default.
    """

    READ_MEMORY = "read_memory"
    WRITE_MEMORY = "write_memory"
    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    NETWORK = "network"                # fetch public information
    EXTERNAL_MESSAGE = "external_message"   # email, chat, anything outbound
    PUBLISH = "publish"                # anything the world can see
    SPEND = "spend"                    # anything that costs real money
    DELETE = "delete"
    SENSITIVE = "sensitive"            # credentials, financial, health
    MODIFY_CONFIG = "modify_config"    # change how JARVIS runs
    MODIFY_AGENTS = "modify_agents"    # change what agents exist or may do


# Permissions no agent gets by delegation, ever. An agent that decides
# rewriting JARVIS would be useful must not be able to act on that
# conclusion, however good its reasoning.
NEVER_DELEGATED = {Permission.MODIFY_CONFIG, Permission.MODIFY_AGENTS}

# Permissions whose use always needs the owner, whatever the agent holds.
# These map onto the approval categories seeded in Stage 0.
ALWAYS_APPROVED = {
    Permission.PUBLISH: "publishing",
    Permission.SPEND: "spending",
    Permission.SENSITIVE: "credentials_or_security",
    Permission.EXTERNAL_MESSAGE: "publishing",
    Permission.DELETE: "credentials_or_security",
}


class ModelTier(str, Enum):
    """How much intelligence a job justifies, not which vendor supplies it.

    Naming tiers rather than models is what keeps agents from being
    rewritten when a provider retires a name -- which has already happened
    once on this project.
    """

    CHEAP = "cheap"        # classification, extraction, formatting
    STANDARD = "standard"  # ordinary reasoning and writing
    DEEP = "deep"          # hard reasoning, high cost of being wrong


@dataclass(frozen=True)
class AgentSpec:
    """A registered capability."""

    capability: str
    name: str
    description: str = ""
    domain: str | None = None
    supervisor: str | None = None
    task_types: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    permissions: frozenset[Permission] = frozenset()
    model_tiers: tuple[ModelTier, ...] = (ModelTier.STANDARD,)
    version: int = 1
    status: Lifecycle = Lifecycle.EXPERIMENTAL
    max_cost_inr: Decimal | None = None
    config: dict[str, Any] = field(default_factory=dict)
    id: UUID | None = None


@dataclass
class Step:
    """One intended piece of work, before it becomes a task row.

    Lives here rather than in the orchestrator because it is now the thing
    a planner hands over: a plan is a list of Steps, produced by one
    module and executed by another, and a shape that crosses that
    boundary belongs with the other shapes that do.
    """

    capability: str
    objective: str
    inputs: dict | None = None
    expected_output: str = ""
    constraints: dict | None = None
    after: tuple[str, ...] = ()   # names of steps this one needs
    name: str = ""
    budget_inr: Decimal | None = None


@dataclass(frozen=True)
class Handoff:
    """What an agent is given. The briefing, not the archive.

    Agents do not receive conversation history. They receive an objective
    and the specific facts needed to meet it, because a large shared
    context is expensive, slow, leaks information sideways between
    capabilities, and makes agents worse rather than better by burying the
    instruction in noise.
    """

    task_id: UUID
    workflow_id: UUID | None
    objective: str
    inputs: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    budget_inr: Decimal | None = None
    deadline: datetime | None = None
    min_confidence: float = 0.0
    model_tier: ModelTier = ModelTier.STANDARD
    permissions: frozenset[Permission] = frozenset()


@dataclass
class AgentResult:
    """What an agent gives back.

    `confidence` and `unresolved` are not decoration. An agent that cannot
    say how sure it is, or what it could not settle, forces whoever reads
    the result to assume it is complete -- and a confident wrong answer
    propagates through every task that depends on it.
    """

    output: Any
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    next_action: str | None = None
    cost_inr: Decimal = Decimal(0)
    model_used: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class AgentError(Exception):
    """An agent failed in a way worth recording and possibly retrying."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class PermissionDenied(AgentError):
    """Never retryable: trying again changes nothing but the timestamp."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class BudgetExceeded(AgentError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class ApprovalRequired(AgentError):
    """Not a failure. The task waits for the owner."""

    def __init__(self, message: str, category: str):
        super().__init__(message, retryable=False)
        self.category = category
