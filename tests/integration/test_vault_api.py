"""Integration tests for Vault admin API routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from donna.config import MemoryConfig, VaultConfig
from donna.integrations.vault import VaultClient


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    (inbox / "note1.md").write_text("---\ntags: [test]\n---\nHello world\n")
    (inbox / "note2.md").write_text("Second note\n")
    return tmp_path


@pytest.fixture
def vault_client(vault_root: Path) -> VaultClient:
    cfg = MemoryConfig(vault=VaultConfig(root=str(vault_root)))
    return VaultClient(config=cfg)


@pytest.fixture
async def client(vault_client: VaultClient) -> AsyncClient:
    from fastapi import FastAPI

    from donna.api.routes.admin_vault import router

    app = FastAPI()
    app.include_router(router, prefix="/admin")
    app.state.vault_client = vault_client
    app.state.vault_git = None

    from donna.api.auth.router_factory import _admin_dep

    async def _override_admin() -> str:
        return "admin-user"

    app.dependency_overrides[_admin_dep] = _override_admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestListNotes:
    async def test_list_all_notes(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/notes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        paths = [n["path"] for n in data["notes"]]
        assert "Inbox/note1.md" in paths
        assert "Inbox/note2.md" in paths

    async def test_list_filtered_by_folder(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/notes", params={"folder": "Inbox"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    async def test_list_empty_folder(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/notes", params={"folder": "Empty"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestReadNote:
    async def test_read_existing_note(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/notes/Inbox/note1.md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "Inbox/note1.md"
        assert "Hello world" in data["content"]
        assert data["frontmatter"]["tags"] == ["test"]

    async def test_read_missing_note_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/notes/Inbox/missing.md")
        assert resp.status_code == 404


class TestVaultStatus:
    async def test_status_connected(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["note_count"] == 2
        assert data["last_commit"] is None

    async def test_status_not_configured(self) -> None:
        from fastapi import FastAPI

        from donna.api.routes.admin_vault import router

        app = FastAPI()
        app.include_router(router, prefix="/admin")
        app.state.vault_client = None
        app.state.vault_git = None

        from donna.api.auth.router_factory import _admin_dep

        async def _override_admin() -> str:
            return "admin-user"

        app.dependency_overrides[_admin_dep] = _override_admin

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/admin/vault/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False


class TestVaultHistory:
    async def test_history_no_git(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/vault/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["commits"] == []
        assert data["count"] == 0
