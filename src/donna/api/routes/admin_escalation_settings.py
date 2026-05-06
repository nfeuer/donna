"""Dashboard runtime overrides for the manual-escalation subsystem.

Realizes docs/superpowers/specs/manual-escalation.md §6.3(a) and §10.7
row 1 (optimistic-lock writes). Slice 17 created the
``dashboard_setting`` table and a read-only resolver; this slice 23
module adds the write side: a typed catalog, GET / PUT endpoints, and
the per-task-type override grid.

Endpoint surface (all under ``/admin``):

* ``GET  /escalation-settings`` — every dashboard-mutable toggle plus
  the per-task-type override grid. Each row carries the resolved value,
  the YAML default, and ``updated_at`` / ``updated_by`` provenance for
  the optimistic-lock client.
* ``PUT  /escalation-settings/{key}`` — write one setting with an
  optional ``expected_updated_at`` field for optimistic locking.
* ``PUT  /escalation-settings/task-types/{task_type}`` — write a
  per-task-type override. Same lock semantics, different validation.

The write path always emits an ``escalation_lifecycle`` audit row with
``event='dashboard_setting_changed'`` so the existing dashboard timeline
view (slice 19) records the toggle history alongside other escalation
events. ``hard_monthly_ceiling_usd`` stays YAML-only by deliberate
omission — exposed neither in the catalog nor through any write path.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import structlog
from fastapi import HTTPException, Request

from donna.api.auth import CurrentAdmin, admin_router
from donna.cost.dashboard_settings_catalog import (
    SETTINGS,
    SETTINGS_BY_KEY,
    TASK_TYPE_OVERRIDE_DEFAULT,
    TASK_TYPE_OVERRIDE_VALUES,
    coerce_task_type_override,
    coerce_value,
    days_left_in_month,
    is_known_key,
    max_daily_extension_cap_usd,
    task_type_override_key,
    yaml_default_for,
)
from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_repository import EscalationRepository

logger = structlog.get_logger()

router = admin_router()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _override_rows_for_task_types(
    task_types_config: Any,
    overrides: dict[str, tuple[Any, str, str]],
) -> list[dict[str, Any]]:
    """Build the per-task-type override grid for the GET response.

    Returns one row per task type that declares a ``manual_escalation``
    block. Task types without such a block are intentionally omitted —
    they are never offered manual mode (spec §6.2).
    """
    if task_types_config is None:
        return []
    rows: list[dict[str, Any]] = []
    for name, entry in task_types_config.task_types.items():
        manual = entry.manual_escalation
        if manual is None:
            continue
        key = task_type_override_key(name)
        override = overrides.get(key)
        rows.append({
            "task_type": name,
            "key": key,
            "manual_mode": manual.mode,
            "value": override[0] if override else TASK_TYPE_OVERRIDE_DEFAULT,
            "default": TASK_TYPE_OVERRIDE_DEFAULT,
            "updated_at": override[1] if override else None,
            "updated_by": override[2] if override else None,
        })
    rows.sort(key=lambda r: str(r["task_type"]))
    return rows


@router.get("/escalation-settings")
async def list_escalation_settings(request: Request) -> dict[str, Any]:
    """Return every dashboard-mutable setting + the override grid.

    Shape:

    .. code-block:: json

        {
          "settings": [
            {"key": "...", "value": ..., "default": ..., "value_type": "...",
             "description": "...",
             "updated_at": "2026-...", "updated_by": "nick"},
            ...
          ],
          "task_type_overrides": [
            {"task_type": "skill_auto_draft", "manual_mode": "claude_code",
             "value": "auto", "default": "auto",
             "updated_at": null, "updated_by": null,
             "key": "manual_escalation.task_types.skill_auto_draft.override"},
            ...
          ],
          "constraints": {
            "task_type_override_values":
              ["auto", "force_api", "force_manual", "disabled"],
            "max_daily_extension_cap_usd": 5.0,
            "max_daily_extension_cap_basis": {
              "hard_monthly_ceiling_usd": 150.0,
              "days_left_in_month": 30
            }
          }
        }

    The ``constraints.max_daily_extension_cap_usd`` value is the slider's
    **server-enforced** upper bound today; the dashboard renders it as
    the slider's max so the user cannot move beyond what the PUT will
    accept (§6.3(a)).
    """
    config = getattr(request.app.state, "manual_escalation_config", None)
    if config is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "manual_escalation_config_unavailable",
                "message": (
                    "manual_escalation.yaml failed to load at startup; "
                    "fix the YAML and restart the API process."
                ),
            },
        )
    task_types_config = getattr(request.app.state, "task_types_config", None)

    conn = request.app.state.db.connection
    repo = EscalationRepository(conn)

    raw_rows = await repo.list_dashboard_settings(prefix="manual_escalation.")
    overrides: dict[str, tuple[Any, str, str]] = {
        key: (value, updated_at, updated_by)
        for key, value, updated_at, updated_by in raw_rows
    }

    settings_payload: list[dict[str, Any]] = []
    for spec in SETTINGS:
        override = overrides.get(spec.key)
        # Slice 23 — fall back to legacy aliases for the read side so a
        # row written before the namespace unification still surfaces
        # in the UI as the canonical key. The PUT path always writes
        # the canonical key; the user's next save migrates them.
        if override is None:
            for alias in spec.legacy_aliases:
                legacy = overrides.get(alias)
                if legacy is not None:
                    override = legacy
                    break
        default = yaml_default_for(spec.key, config)
        value = override[0] if override else default
        settings_payload.append({
            "key": spec.key,
            "value": value,
            "default": default,
            "value_type": spec.value_type.__name__,
            "description": spec.description,
            "updated_at": override[1] if override else None,
            "updated_by": override[2] if override else None,
        })

    today = date.today()
    cap = max_daily_extension_cap_usd(config, today)
    constraints = {
        "task_type_override_values": list(TASK_TYPE_OVERRIDE_VALUES),
        "max_daily_extension_cap_usd": round(cap, 2),
        "max_daily_extension_cap_basis": {
            "hard_monthly_ceiling_usd": float(
                config.budget_extension.hard_monthly_ceiling_usd
            ),
            "days_left_in_month": days_left_in_month(today),
        },
    }

    return {
        "settings": settings_payload,
        "task_type_overrides": _override_rows_for_task_types(
            task_types_config, overrides
        ),
        "constraints": constraints,
    }


# ---------------------------------------------------------------------------
# Write — top-level toggles
# ---------------------------------------------------------------------------


async def _write_audit_event(
    conn: Any,
    *,
    user_id: str,
    key: str,
    value: Any,
    previous_value: Any,
    expected_updated_at: str | None,
) -> None:
    """Append an ``escalation_lifecycle`` row for the toggle change.

    The brainstorm gap weighed adding a dedicated audit table vs. reusing
    ``invocation_log``; we reuse so the dashboard timeline view (slice 19)
    surfaces toggle changes alongside actual escalations without bolting
    on a second source. ``escalation_request_id`` stays NULL — these are
    subsystem-level audit rows, not tied to one escalation row.
    """
    import uuid6  # local import: keeps cost.escalation_audit's exact pattern

    payload = {
        "event": "dashboard_setting_changed",
        "key": key,
        "value": value,
        "previous_value": previous_value,
        "had_lock_token": expected_updated_at is not None,
    }
    invocation_id = str(uuid6.uuid7())
    ts = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO invocation_log (
            id, timestamp, task_type, task_id, model_alias, model_actual,
            input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
            output, is_shadow, spot_check_queued, user_id,
            escalation_request_id
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, 0, 0, 0, 0.0, ?, 0, 0, ?, NULL)
        """,
        (
            invocation_id,
            ts,
            ESCALATION_TASK_TYPE,
            "audit",
            "audit",
            key[:16],
            json.dumps(payload),
            user_id,
        ),
    )
    await conn.commit()


def _conflict_response(
    key: str,
    *,
    current_value: Any,
    current_updated_at: str,
    current_updated_by: str,
) -> HTTPException:
    """Build a 409 with the live state so the client can re-render."""
    return HTTPException(
        status_code=409,
        detail={
            "error": "version_mismatch",
            "key": key,
            "current_value": current_value,
            "current_updated_at": current_updated_at,
            "current_updated_by": current_updated_by,
        },
    )


def _validate_top_level_value(
    key: str, value: Any, request: Request
) -> Any:
    """Coerce the payload value to the catalog's declared type and clamp
    sliders against the YAML-only ceiling.
    """
    spec = SETTINGS_BY_KEY[key]
    coerced = coerce_value(spec, value)

    if key == "manual_escalation.budget_extension.max_daily_extension_usd":
        config = request.app.state.manual_escalation_config
        # Negative slider value is a UX bug; reject explicitly.
        if coerced < 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_value",
                    "key": key,
                    "message": "max_daily_extension_usd must be >= 0",
                },
            )
        cap = max_daily_extension_cap_usd(config, date.today())
        if coerced > cap:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "exceeds_monthly_ceiling",
                    "key": key,
                    "message": (
                        "value exceeds hard_monthly_ceiling_usd / "
                        "days_left_in_month — adjust the YAML ceiling "
                        "or pick a lower number."
                    ),
                    "max_allowed_usd": round(cap, 2),
                },
            )
    return coerced


@router.put("/escalation-settings/task-types/{task_type}")
async def put_task_type_override_route(
    request: Request,
    task_type: str,
    user_id: CurrentAdmin,
) -> dict[str, Any]:
    """Write a per-task-type override (auto / force_api / force_manual / disabled).

    Same optimistic-lock contract as the top-level PUT. Rejects task
    types that do not declare a ``manual_escalation`` block, since the
    override is meaningless for them.

    Declared **before** the ``{key:path}`` catch-all PUT so the path
    converter does not steal this URL pattern (FastAPI matches in
    declaration order).
    """
    return await _put_task_type_override_impl(request, task_type, user_id=user_id)


@router.put("/escalation-settings/{key:path}")
async def put_escalation_setting(
    request: Request,
    key: str,
    user_id: CurrentAdmin,
) -> dict[str, Any]:
    """Write one dashboard_setting row with optimistic locking.

    Body: ``{"value": <bool|number|string>, "expected_updated_at": <iso8601|null>}``

    ``expected_updated_at`` should be the value the client received in
    the most recent ``GET /escalation-settings`` response. Set it to
    ``null`` to assert "no row exists yet" (first-write).

    Returns the new ``updated_at`` so the client can store it for the
    next round-trip without a refetch.
    """
    if not is_known_key(key):
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_setting_key", "key": key},
        )
    # Per-task-type overrides have a dedicated route below — block them
    # here so the validation logic stays tight.
    if key.startswith("manual_escalation.task_types."):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "use_dedicated_task_type_endpoint",
                "key": key,
                "message": "PUT /admin/escalation-settings/task-types/{task_type} instead.",
            },
        )

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_json", "message": str(exc)}
        ) from exc

    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_value", "message": "body.value is required"},
        )

    try:
        coerced = _validate_top_level_value(key, body["value"], request)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_value", "key": key, "message": str(exc)},
        ) from exc
    expected_updated_at = body.get("expected_updated_at")
    if expected_updated_at is not None and not isinstance(expected_updated_at, str):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_expected_updated_at",
                "message": "expected_updated_at must be an ISO-8601 string or null",
            },
        )

    conn = request.app.state.db.connection
    repo = EscalationRepository(conn)

    # Read previous value for audit + 409 payload.
    previous_row = await repo.get_dashboard_setting_row(key)
    previous_value: Any = previous_row[0] if previous_row else None

    ok, value_back, updated_at, updated_by = await repo.set_dashboard_setting_with_lock(
        key,
        coerced,
        expected_updated_at=expected_updated_at,
        updated_by=user_id,
    )
    if not ok:
        raise _conflict_response(
            key,
            current_value=value_back,
            current_updated_at=updated_at,
            current_updated_by=updated_by,
        )

    await _write_audit_event(
        conn,
        user_id=user_id,
        key=key,
        value=coerced,
        previous_value=previous_value,
        expected_updated_at=expected_updated_at,
    )
    logger.info(
        "dashboard_setting_changed",
        key=key,
        value=coerced,
        previous_value=previous_value,
        updated_by=user_id,
    )
    return {
        "key": key,
        "value": coerced,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


# ---------------------------------------------------------------------------
# Write — per-task-type override grid
# ---------------------------------------------------------------------------


async def _put_task_type_override_impl(
    request: Request,
    task_type: str,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Implementation shared by the dedicated PUT route above."""
    task_types_config = getattr(request.app.state, "task_types_config", None)
    if task_types_config is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "task_types_config_unavailable",
                "message": "task_types.yaml failed to load at startup.",
            },
        )
    entry = task_types_config.task_types.get(task_type)
    if entry is None or entry.manual_escalation is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "task_type_not_overridable",
                "task_type": task_type,
                "message": (
                    "Only task types with a manual_escalation block in "
                    "config/task_types.yaml can be overridden."
                ),
            },
        )

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_json", "message": str(exc)},
        ) from exc

    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_value", "message": "body.value is required"},
        )

    try:
        override_value = coerce_task_type_override(body["value"])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_value",
                "task_type": task_type,
                "message": str(exc),
            },
        ) from exc

    expected_updated_at = body.get("expected_updated_at")
    if expected_updated_at is not None and not isinstance(expected_updated_at, str):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_expected_updated_at",
                "message": "expected_updated_at must be an ISO-8601 string or null",
            },
        )

    key = task_type_override_key(task_type)
    conn = request.app.state.db.connection
    repo = EscalationRepository(conn)

    previous_row = await repo.get_dashboard_setting_row(key)
    previous_value: Any = previous_row[0] if previous_row else None

    ok, value_back, updated_at, updated_by = await repo.set_dashboard_setting_with_lock(
        key,
        override_value,
        expected_updated_at=expected_updated_at,
        updated_by=user_id,
    )
    if not ok:
        raise _conflict_response(
            key,
            current_value=value_back,
            current_updated_at=updated_at,
            current_updated_by=updated_by,
        )

    await _write_audit_event(
        conn,
        user_id=user_id,
        key=key,
        value=override_value,
        previous_value=previous_value,
        expected_updated_at=expected_updated_at,
    )
    logger.info(
        "task_type_override_changed",
        task_type=task_type,
        value=override_value,
        previous_value=previous_value,
        updated_by=user_id,
    )
    return {
        "task_type": task_type,
        "key": key,
        "value": override_value,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


