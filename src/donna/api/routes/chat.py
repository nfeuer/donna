"""Chat API endpoints for the Donna conversation interface.

REST endpoints for session management, messaging, pinning, and escalation.
All endpoints are client-agnostic — used by Flutter app, web client, etc.
See docs/superpowers/specs/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, HTTPException, Request

from donna.api.auth import CurrentUser, user_router
from donna.chat.types import ChatResponse

router = user_router()


def get_chat_engine(request: Request) -> Any:
    """FastAPI dependency to get the ConversationEngine instance."""
    engine = getattr(request.app.state, "chat_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Chat engine not initialized")
    return engine


def get_database(request: Request) -> Any:
    """FastAPI dependency to get the Database instance."""
    return request.app.state.db


async def _require_session_owner(db: Any, session_id: str, user_id: str) -> Any:
    session = await db.get_chat_session(session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    user_id: CurrentUser,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Send a message and receive a response.

    If session_id is "new", creates a new session owned by the caller.
    """
    text = body.get("text", "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    channel = body.get("channel", "api")

    if session_id == "new":
        sid = None
    else:
        await _require_session_owner(db, session_id, user_id)
        sid = session_id

    resp: ChatResponse = await engine.handle_message(
        session_id=sid,
        user_id=user_id,
        text=text,
        channel=channel,
    )

    return {
        "text": resp.text,
        "needs_escalation": resp.needs_escalation,
        "escalation_reason": resp.escalation_reason,
        "estimated_cost": resp.estimated_cost,
        "suggested_actions": resp.suggested_actions,
        "pin_suggestion": resp.pin_suggestion,
        "session_pinned_task_id": resp.session_pinned_task_id,
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user_id: CurrentUser,
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Get session details and recent messages."""
    session = await _require_session_owner(db, session_id, user_id)
    messages = await db.list_chat_messages(session_id, limit=50)

    return {
        "session": {
            "id": session.id,
            "user_id": session.user_id,
            "channel": session.channel,
            "status": session.status,
            "pinned_task_id": session.pinned_task_id,
            "summary": session.summary,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "message_count": session.message_count,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "intent": m.intent,
                "tokens_used": m.tokens_used,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    user_id: CurrentUser,
    limit: int = 50,
    offset: int = 0,
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """List messages in a session with pagination."""
    await _require_session_owner(db, session_id, user_id)
    messages = await db.list_chat_messages(session_id, limit=limit, offset=offset)
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "intent": m.intent,
                "tokens_used": m.tokens_used,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.post("/sessions/{session_id}/pin")
async def pin_session(
    session_id: str,
    user_id: CurrentUser,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, str]:
    """Pin a session to a task."""
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    await _require_session_owner(db, session_id, user_id)
    await engine.pin_session(session_id=session_id, task_id=task_id)
    return {"status": "pinned", "task_id": task_id}


@router.delete("/sessions/{session_id}/pin")
async def unpin_session(
    session_id: str,
    user_id: CurrentUser,
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, str]:
    """Unpin a session from its task."""
    await _require_session_owner(db, session_id, user_id)
    await engine.unpin_session(session_id=session_id)
    return {"status": "unpinned"}


@router.post("/sessions/{session_id}/escalate")
async def approve_escalation(
    session_id: str,
    user_id: CurrentUser,
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Approve a pending Claude escalation."""
    await _require_session_owner(db, session_id, user_id)
    resp: ChatResponse = await engine.handle_escalation(
        session_id=session_id, user_id=user_id
    )
    return {
        "text": resp.text,
        "needs_escalation": resp.needs_escalation,
        "escalation_reason": resp.escalation_reason,
        "suggested_actions": resp.suggested_actions,
    }


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    user_id: CurrentUser,
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Close a session and generate a summary."""
    await _require_session_owner(db, session_id, user_id)
    summary = await engine.close_session(session_id=session_id)
    return {"status": "closed", "summary": summary}
