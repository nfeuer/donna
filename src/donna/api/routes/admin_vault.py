"""Read-only admin routes for the Obsidian vault."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router

router = admin_router()


def _get_vault_client(request: Request) -> Any:
    client = getattr(request.app.state, "vault_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Vault not configured")
    return client


def _get_vault_git(request: Request) -> Any:
    return getattr(request.app.state, "vault_git", None)


@router.get("/vault/notes")
async def list_notes(
    request: Request,
    folder: str = Query(default="", description="Filter by top-level folder"),
) -> dict[str, Any]:
    """List all notes in the vault."""
    client = _get_vault_client(request)
    paths = await client.list(folder=folder, recursive=True)
    notes = []
    for path in paths:
        try:
            mtime, size = await client.stat(path)
            notes.append({"path": path, "mtime": mtime, "size": size})
        except Exception:
            notes.append({"path": path, "mtime": None, "size": None})
    return {"notes": notes, "count": len(notes)}


@router.get("/vault/notes/{path:path}")
async def read_note(request: Request, path: str) -> dict[str, Any]:
    """Read a single note by path."""
    client = _get_vault_client(request)
    try:
        note = await client.read(path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "path": note.path,
        "content": note.content,
        "frontmatter": note.frontmatter,
        "mtime": note.mtime,
        "size": note.size,
    }


@router.get("/vault/status")
async def vault_status(request: Request) -> dict[str, Any]:
    """Vault health summary."""
    client = getattr(request.app.state, "vault_client", None)
    git = _get_vault_git(request)
    if client is None:
        return {"connected": False, "note_count": 0, "last_commit": None}
    root = client.root
    connected = root.exists() and root.is_dir()
    note_count = 0
    if connected:
        try:
            paths = await client.list(recursive=True)
            note_count = len(paths)
        except Exception:
            pass
    last_commit = None
    if git is not None:
        try:
            commits = await git.log(limit=1)
            if commits:
                last_commit = {"sha": commits[0].sha, "message": commits[0].message}
        except Exception:
            pass
    return {
        "connected": connected,
        "root": str(root),
        "note_count": note_count,
        "last_commit": last_commit,
    }


@router.get("/vault/history")
async def vault_history(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Recent vault git commits."""
    git = _get_vault_git(request)
    if git is None:
        return {"commits": [], "count": 0}
    try:
        commits = await git.log(limit=limit)
    except Exception:
        return {"commits": [], "count": 0}
    return {
        "commits": [{"sha": c.sha, "message": c.message} for c in commits],
        "count": len(commits),
    }
