"""Router factory tests: deny-by-default is enforced."""

from __future__ import annotations

import importlib

import pytest

from donna.api.auth import router_factory
from donna.api.auth.router_factory import _admin_dep

# The six modules that must be admin-guarded (regression guard for the
# 2026-07-02 audit finding: bare APIRouter() bypassed deny-by-default).
ADMIN_GUARDED_MODULES = [
    "capabilities",
    "skills",
    "skill_drafts",
    "skill_candidates",
    "skill_runs",
    "automations",
]


@pytest.mark.parametrize("mod_name", ADMIN_GUARDED_MODULES)
def test_admin_route_module_carries_admin_dep(mod_name):
    mod = importlib.import_module(f"donna.api.routes.{mod_name}")
    dep_callables = [d.dependency for d in mod.router.dependencies]
    assert _admin_dep in dep_callables, (
        f"donna.api.routes.{mod_name}.router is not admin-guarded"
    )


def _route_guarded_by(route, dep_fn) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = list(dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is dep_fn:
            return True
        stack.extend(d.dependencies)
    return False


def test_no_admin_route_is_unguarded():
    """Whole-app invariant: every route under /admin requires admin auth.

    Catches future modules that regress to bare APIRouter().
    """
    from donna.api import create_app

    app = create_app()
    unguarded = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin")
        and not _route_guarded_by(route, _admin_dep)
    ]
    assert unguarded == [], f"Unguarded /admin routes: {unguarded}"


def test_public_liveness_router_no_deps():
    r = router_factory.public_liveness_router()
    assert r.dependencies == []


def test_user_router_has_dependencies():
    r = router_factory.user_router()
    assert len(r.dependencies) >= 1


def test_admin_router_has_dependencies():
    r = router_factory.admin_router()
    assert len(r.dependencies) >= 1


def test_service_router_has_dependencies():
    r = router_factory.service_router()
    assert len(r.dependencies) >= 1
