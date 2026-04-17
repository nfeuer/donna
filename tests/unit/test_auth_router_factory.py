"""Router factory tests: deny-by-default is enforced."""

from __future__ import annotations

from donna.api.auth import router_factory


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
