"""Execution receipts for Ableton Live mutations.

A receipt never claims more than the Live side confirmed. ``verified`` requires
that every attempted operation was read back and matched its expectation; any
difference produces ``verification_failed`` with the mismatches listed.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Self

from kihachi_mcp.models.live_contract import (
    EXECUTION_STATUSES,
    SCHEMA_VERSION,
    STATUS_PARTIALLY_APPLIED,
    STATUS_VERIFICATION_FAILED,
    STATUS_VERIFIED,
    LiveContractError,
    require_schema_version,
)


@dataclass(frozen=True)
class LiveReadbackMismatch:
    """One field the Live side reported differently from the expectation."""

    operation_id: str
    field_name: str
    expected: Any = None
    observed: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a mismatch from JSON-compatible data."""
        return cls(
            operation_id=str(data.get("operation_id") or ""),
            field_name=str(data.get("field_name") or ""),
            expected=data.get("expected"),
            observed=data.get("observed"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the mismatch to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveOperationReadback:
    """What Live reported after one operation was applied."""

    operation_id: str
    op: str
    observed: dict[str, Any] = field(default_factory=dict)
    matched: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a readback from JSON-compatible data."""
        observed = data.get("observed")
        return cls(
            operation_id=str(data.get("operation_id") or ""),
            op=str(data.get("op") or ""),
            observed=dict(observed) if isinstance(observed, dict) else {},
            matched=bool(data.get("matched")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the readback to JSON-compatible data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveExecutionReceipt:
    """Truthful record of one attempted, approval-gated Live execution."""

    request_id: str
    status: str
    idempotency_key: str = ""
    schema_version: int = SCHEMA_VERSION
    attempted_operations: list[str] = field(default_factory=list)
    completed_operations: list[str] = field(default_factory=list)
    failed_operation: str = ""
    readback: list[LiveOperationReadback] = field(default_factory=list)
    mismatches: list[LiveReadbackMismatch] = field(default_factory=list)
    set_fingerprint_before: str = ""
    set_fingerprint_after: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        """Reject receipts whose status is outside the agreed vocabulary."""
        if self.status not in EXECUTION_STATUSES:
            raise LiveContractError(f"unsupported execution status '{self.status}'")
        if self.status == STATUS_VERIFIED and self.mismatches:
            raise LiveContractError(
                "a receipt with readback mismatches cannot be verified"
            )
        if self.status == STATUS_VERIFIED and sorted(
            self.completed_operations
        ) != sorted(self.attempted_operations):
            raise LiveContractError(
                "a receipt with incomplete operations cannot be verified"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a receipt from JSON-compatible data, rejecting other schemas."""
        require_schema_version(data, "LiveExecutionReceipt")
        return cls(
            request_id=str(data.get("request_id") or ""),
            status=str(data.get("status") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
            attempted_operations=[
                str(item) for item in data.get("attempted_operations") or []
            ],
            completed_operations=[
                str(item) for item in data.get("completed_operations") or []
            ],
            failed_operation=str(data.get("failed_operation") or ""),
            readback=[
                LiveOperationReadback.from_dict(item)
                for item in data.get("readback") or []
                if isinstance(item, dict)
            ],
            mismatches=[
                LiveReadbackMismatch.from_dict(item)
                for item in data.get("mismatches") or []
                if isinstance(item, dict)
            ],
            set_fingerprint_before=str(data.get("set_fingerprint_before") or ""),
            set_fingerprint_after=str(data.get("set_fingerprint_after") or ""),
            error=str(data.get("error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the receipt to JSON-compatible data."""
        return asdict(self)

    @property
    def is_complete(self) -> bool:
        """Report whether Live confirmed every attempted operation."""
        return self.status == STATUS_VERIFIED

    @property
    def needs_manual_review(self) -> bool:
        """Report whether the Set was left in a state a human must inspect."""
        return self.status in {STATUS_PARTIALLY_APPLIED, STATUS_VERIFICATION_FAILED}
