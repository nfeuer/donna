"""Integration tests for the POST /sms/inbound webhook.

Uses aiohttp test client to send POST requests to the webhook handler.
Twilio signature verification and SmsRouter are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from donna.server import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_twilio_sms(valid_sig: bool = True) -> MagicMock:
    sms = MagicMock()
    sms.verify_signature = MagicMock(return_value=valid_sig)
    return sms


def _make_sms_router() -> MagicMock:
    router = MagicMock()
    router.route_inbound = AsyncMock(return_value=None)
    return router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSmsInboundWebhook:
    async def test_valid_signature_routes_message(self) -> None:
        twilio_sms = _make_twilio_sms(valid_sig=True)
        sms_router = _make_sms_router()

        with patch.dict("os.environ", {"TWILIO_WEBHOOK_URL": "https://example.com/sms/inbound"}):
            app = create_app(twilio_sms=twilio_sms, sms_router=sms_router)

        client = TestClient(TestServer(app))
        await client.start_server()

        try:
            resp = await client.post(
                "/sms/inbound",
                data={"From": "+15555550001", "Body": "buy milk"},
                headers={"X-Twilio-Signature": "valid_sig"},
            )
            assert resp.status == 200
            text = await resp.text()
            assert "<Response/>" in text
            sms_router.route_inbound.assert_called_once_with(
                from_number="+15555550001", body="buy milk"
            )
        finally:
            await client.close()

    async def test_invalid_signature_returns_403(self) -> None:
        twilio_sms = _make_twilio_sms(valid_sig=False)
        sms_router = _make_sms_router()

        with patch.dict("os.environ", {"TWILIO_WEBHOOK_URL": "https://example.com/sms/inbound"}):
            app = create_app(twilio_sms=twilio_sms, sms_router=sms_router)

        client = TestClient(TestServer(app))
        await client.start_server()

        try:
            resp = await client.post(
                "/sms/inbound",
                data={"From": "+15555550001", "Body": "buy milk"},
                headers={"X-Twilio-Signature": "bad_sig"},
            )
            assert resp.status == 403
            sms_router.route_inbound.assert_not_called()
        finally:
            await client.close()

    async def test_no_sms_endpoint_without_config(self) -> None:
        """If create_app is called without twilio_sms, /sms/inbound is not registered."""
        app = create_app()

        client = TestClient(TestServer(app))
        await client.start_server()

        try:
            resp = await client.post(
                "/sms/inbound",
                data={"From": "+15555550001", "Body": "hello"},
            )
            assert resp.status == 404
        finally:
            await client.close()
