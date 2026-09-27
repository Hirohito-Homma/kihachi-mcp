"""Models for approval-gated Ableton Live mutation plans.

A plan is inert data. It carries the fingerprint of the Set it was planned
against, the preconditions each operation needs, and the readback each
operation must produce before it can be called verified.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.models.live_contract import (
    SCHEMA_VERSION,
    STATUS_APPROVAL_REQUIRED,
    STATUS_BLOCKED,
    STATUS_READY,
    SUPPORTED_OPS,
    LiveContractError,
    canonical_hash,
    require_schema_version,
)


@dataclass(frozen=True)
class LivePrecondition:
    """One condition the Live side must confirm before applying an operation."""

    kind: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a precondition from JSON-compatible data."""
        arguments = data.get("arguments")
        return cls(
            kind=str(data.get("kind") or ""),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the precondition to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveConflict:
    """A reason one requested change cannot be planned against this Set."""

    kind: str
    detail: str
    target: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a conflict from JSON-compatible data."""
        target = data.get("target")
        return cls(
            kind=str(data.get("kind") or ""),
            detail=str(data.get("detail") or ""),
            target=dict(target) if isinstance(target, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the conflict to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveMutationOperation:
    """One reversible-by-inspection change request against a Live Set."""

    operation_id: str
    op: str
    target: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    preconditions: list[LivePrecondition] = field(default_factory=list)
    destructive: bool = False
    expected_readback: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject operations the executor has no verified implementation for."""
        if self.op not in SUPPORTED_OPS:
            raise LiveContractError(f"unsupported Live operation '{self.op}'")
        if not self.operation_id:
            raise LiveContractError("operation_id must not be empty")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an operation from JSON-compatible data."""
        target = data.get("target")
        arguments = data.get("arguments")
        readback = data.get("expected_readback")
        return cls(
            operation_id=str(data.get("operation_id") or ""),
            op=str(data.get("op") or ""),
            target=dict(target) if isinstance(target, dict) else {},
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
            preconditions=[
                LivePrecondition.from_dict(item)
                for item in data.get("preconditions") or []
                if isinstance(item, dict)
            ],
            destructive=bool(data.get("destructive")),
            expected_readback=dict(readback) if isinstance(readback, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the operation to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveMutationPlan:
    """An inert, approval-gated sequence of Live operations."""

    request_id: str
    idempotency_key: str
    source_plan_hash: str
    set_fingerprint: str
    expires_at: str
    schema_version: int = SCHEMA_VERSION
    operations: list[LiveMutationOperation] = field(default_factory=list)
    conflicts: list[LiveConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a plan from JSON-compatible data, rejecting other schemas."""
        require_schema_version(data, "LiveMutationPlan")
        return cls(
            request_id=str(data.get("request_id") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
            source_plan_hash=str(data.get("source_plan_hash") or ""),
            set_fingerprint=str(data.get("set_fingerprint") or ""),
            expires_at=str(data.get("expires_at") or ""),
            operations=[
                LiveMutationOperation.from_dict(item)
                for item in data.get("operations") or []
                if isinstance(item, dict)
            ],
            conflicts=[
                LiveConflict.from_dict(item)
                for item in data.get("conflicts") or []
                if isinstance(item, dict)
            ],
            warnings=[str(item) for item in data.get("warnings") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the plan, including its derived counts and status."""
        data = asdict(self)
        data["destructive_operation_count"] = self.destructive_operation_count
        data["approval_required"] = self.approval_required
        data["status"] = self.status
        data["plan_hash"] = self.plan_hash
        return data

    @property
    def destructive_operation_count(self) -> int:
        """Return how many operations change something that already exists."""
        return sum(1 for operation in self.operations if operation.destructive)

    @property
    def approval_required(self) -> bool:
        """Report whether a human must approve before anything is applied."""
        return not self.conflicts and bool(self.operations)

    @property
    def status(self) -> str:
        """Return the pre-execution status of this plan."""
        if self.conflicts:
            return STATUS_BLOCKED
        if not self.operations:
            return STATUS_READY
        return STATUS_APPROVAL_REQUIRED

    @property
    def plan_hash(self) -> str:
        """Return a stable hash that changes whenever the plan changes.

        The approval gate stores this value, so editing an approved plan
        invalidates the approval instead of silently executing new operations.
        """
        return canonical_hash(
            {
                "schema_version": self.schema_version,
                "request_id": self.request_id,
                "idempotency_key": self.idempotency_key,
                "source_plan_hash": self.source_plan_hash,
                "set_fingerprint": self.set_fingerprint,
                "expires_at": self.expires_at,
                "operations": [
                    operation.to_dict() for operation in self.operations
                ],
                "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            }
        )
