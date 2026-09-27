"""Human approval and idempotency for Live mutations.

Two separate guarantees live here.

Approval is bound to an exact plan hash. Editing an approved plan, even by one
operation, produces a different hash and the old approval no longer authorizes
anything.

Idempotency is bound to the plan's key. Once a key has been used for an
execution attempt it is retired, so re-sending the same approved plan cannot
create a second set of tracks and clips.
"""

import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from kihachi_mcp.models.live_mutation import LiveMutationPlan

APPROVAL_TTL_SECONDS = 900


class ApprovalError(Exception):
    """Raised when a plan is not authorized to execute."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApprovalGate:
    """Record approvals and retire idempotency keys."""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        configured_path = storage_path or os.getenv("KIHACHI_LIVE_APPROVAL_PATH")
        self._storage_path = (
            Path(configured_path).expanduser() if configured_path else None
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        state = self._load()
        self._approvals: dict[str, dict[str, Any]] = state.get("approvals", {})
        self._consumed: dict[str, str] = state.get("consumed", {})

    def approve(self, plan: LiveMutationPlan) -> str:
        """Record a human approval for exactly this plan and return its token.

        The caller is responsible for having actually shown the plan to a
        person. This method records a decision; it does not make one.
        """
        if plan.conflicts:
            raise ApprovalError(
                "blocked", "a plan with unresolved conflicts cannot be approved"
            )
        if not plan.operations:
            raise ApprovalError("blocked", "a plan with no operations has nothing to approve")
        if plan.idempotency_key in self._consumed:
            raise ApprovalError(
                "duplicate",
                f"idempotency key '{plan.idempotency_key}' was already used",
            )
        token = secrets.token_urlsafe(24)
        self._approvals[token] = {
            "request_id": plan.request_id,
            "idempotency_key": plan.idempotency_key,
            "plan_hash": plan.plan_hash,
            "set_fingerprint": plan.set_fingerprint,
            "approved_at": self._clock().isoformat(),
        }
        self._save()
        return token

    def authorize(self, plan: LiveMutationPlan, token: str) -> None:
        """Raise ``ApprovalError`` unless this exact plan may execute now."""
        if plan.idempotency_key in self._consumed:
            raise ApprovalError(
                "duplicate",
                f"idempotency key '{plan.idempotency_key}' was already used; "
                "KIHACHI will not execute the same plan twice",
            )
        record = self._approvals.get(token)
        if record is None:
            raise ApprovalError("unapproved", "approval token is unknown or retired")
        if record["plan_hash"] != plan.plan_hash:
            raise ApprovalError(
                "plan_changed",
                "the plan changed after it was approved; request approval again",
            )
        if record["request_id"] != plan.request_id:
            raise ApprovalError(
                "plan_changed", "approval token belongs to a different request"
            )
        if self._is_expired(plan):
            raise ApprovalError(
                "expired", "the plan expired; inspect Live and plan again"
            )

    def consume(self, plan: LiveMutationPlan, token: str) -> None:
        """Retire the idempotency key and token after an execution attempt.

        This runs for every attempt, including failures, so a partially applied
        plan is never replayed on top of its own result.
        """
        self._consumed[plan.idempotency_key] = self._clock().isoformat()
        self._approvals.pop(token, None)
        self._save()

    def is_consumed(self, idempotency_key: str) -> bool:
        """Report whether an idempotency key has already been used."""
        return idempotency_key in self._consumed

    def _is_expired(self, plan: LiveMutationPlan) -> bool:
        if not plan.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(plan.expires_at)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return self._clock() > expires

    def _load(self) -> dict[str, Any]:
        if self._storage_path is None or not self._storage_path.exists():
            return {}
        data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Live approval storage must contain a JSON object")
        approvals = data.get("approvals")
        consumed = data.get("consumed")
        return {
            "approvals": dict(approvals) if isinstance(approvals, dict) else {},
            "consumed": dict(consumed) if isinstance(consumed, dict) else {},
        }

    def _save(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._storage_path.parent,
            prefix=f".{self._storage_path.name}.",
            delete=False,
        ) as temporary:
            json.dump(
                {"approvals": self._approvals, "consumed": self._consumed},
                temporary,
                ensure_ascii=False,
                indent=2,
            )
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        temporary_path.replace(self._storage_path)
