from pathlib import Path

import aiosqlite
import pytest

from donna.capabilities.registry import CapabilityInput, CapabilityRegistry


@pytest.fixture
async def registry(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedding BLOB,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            notes TEXT
        );
        CREATE INDEX ix_capability_status ON capability(status);
        CREATE INDEX ix_capability_trigger_type ON capability(trigger_type);
    """)
    await conn.commit()
    reg = CapabilityRegistry(conn)
    yield reg
    await conn.close()


async def test_register_and_get_by_name(registry):
    cap = await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price or availability changes",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="seed")
    assert cap.name == "product_watch"
    assert cap.status == "active"

    fetched = await registry.get_by_name("product_watch")
    assert fetched is not None
    assert fetched.name == "product_watch"
    assert fetched.input_schema["properties"]["url"]["type"] == "string"


async def test_get_by_name_returns_none_for_missing(registry):
    assert await registry.get_by_name("nope") is None


async def test_list_all(registry):
    for name in ["a", "b", "c"]:
        await registry.register(CapabilityInput(
            name=name,
            description=f"cap {name}",
            input_schema={},
            trigger_type="on_message",
        ), created_by="seed")
    caps = await registry.list_all()
    assert len(caps) == 3
    assert {c.name for c in caps} == {"a", "b", "c"}


async def test_register_duplicate_name_raises(registry):
    await registry.register(CapabilityInput(
        name="dup",
        description="first",
        input_schema={},
        trigger_type="on_message",
    ), created_by="seed")
    with pytest.raises(ValueError, match="already exists"):
        await registry.register(CapabilityInput(
            name="dup",
            description="second",
            input_schema={},
            trigger_type="on_message",
        ), created_by="seed")


async def test_update_status(registry):
    await registry.register(CapabilityInput(
        name="test_cap",
        description="test",
        input_schema={},
        trigger_type="on_message",
    ), created_by="seed")
    await registry.update_status("test_cap", "pending_review")
    cap = await registry.get_by_name("test_cap")
    assert cap.status == "pending_review"


async def test_list_all_with_status_filter(registry):
    await registry.register(CapabilityInput(
        name="active_cap", description="a", input_schema={}, trigger_type="on_message",
    ), created_by="seed")
    await registry.register(CapabilityInput(
        name="review_cap", description="b", input_schema={}, trigger_type="on_message",
    ), created_by="seed")
    await registry.update_status("review_cap", "pending_review")

    active_caps = await registry.list_all(status="active")
    assert len(active_caps) == 1
    assert active_caps[0].name == "active_cap"

    review_caps = await registry.list_all(status="pending_review")
    assert len(review_caps) == 1
    assert review_caps[0].name == "review_cap"


@pytest.mark.slow
async def test_semantic_search_returns_ranked_matches(registry):
    await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price or availability changes",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")
    await registry.register(CapabilityInput(
        name="news_check",
        description="Fetch recent news articles about a topic",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")
    await registry.register(CapabilityInput(
        name="parse_task",
        description="Extract structured task fields from a natural language message",
        input_schema={},
        trigger_type="on_message",
    ), created_by="seed")

    results = await registry.semantic_search("watch this shirt for a price drop", k=3)
    assert len(results) == 3
    assert results[0][0].name == "product_watch"
    assert -1.0 <= results[0][1] <= 1.0


@pytest.mark.slow
async def test_register_stores_embedding(registry):
    cap = await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price changes",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")
    assert cap.embedding is not None
    assert len(cap.embedding) == 384 * 4


@pytest.mark.slow
async def test_register_flags_similar_capability_for_review(registry):
    await registry.register(CapabilityInput(
        name="product_price_watch",
        description="Monitor a product URL for price drops and availability",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="seed")

    flagged = await registry.register(CapabilityInput(
        name="watch_product_price",
        description="Watch a product URL for price changes and stock availability",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="claude")

    assert flagged.status == "pending_review"

    results = await registry.semantic_search("monitor product price", k=5)
    names = [cap.name for cap, _ in results]
    assert "watch_product_price" not in names
    assert "product_price_watch" in names
