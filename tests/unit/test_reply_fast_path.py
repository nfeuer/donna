"""Tests for fast-path keyword matching and complexity gate."""
from __future__ import annotations

import pytest

from donna.config import FastPathConfig, ReplyIntentDef, ReplyIntentsConfig
from donna.replies.handler import FastPath


def _make_config() -> ReplyIntentsConfig:
    return ReplyIntentsConfig(
        fast_path=FastPathConfig(
            max_length=60,
            multi_intent_signals=[" but ", " and also ", " however "],
            confirm_keywords=["yes", "go ahead", "do it", "ok", "sounds good"],
            reject_keywords=["no", "cancel", "nevermind"],
        ),
        intents={
            "mark_done": ReplyIntentDef(keywords=["done", "finished", "did it"], action="mark_done"),
            "reschedule": ReplyIntentDef(keywords=["reschedule", "tomorrow", "later"], action="reschedule"),
            "busy": ReplyIntentDef(keywords=["busy", "not now", "snooze"], action="snooze"),
        },
    )


class TestComplexityGate:
    def test_short_single_intent_passes(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done") is True

    def test_long_reply_fails(self) -> None:
        fp = FastPath(_make_config())
        long = "I finished half of it and need to call Mike to let him know tomorrow"
        assert fp.is_simple(long) is False

    def test_multi_intent_signal_fails(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done but also reschedule") is False

    def test_conflicting_intents_fail(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done tomorrow") is False

    def test_no_keyword_match_fails(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("what is this about?") is False


class TestKeywordMatch:
    def test_exact_keyword(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("done")
        assert result is not None
        assert result.action == "mark_done"

    def test_keyword_in_phrase(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("yes finished")
        assert result is not None
        assert result.action == "mark_done"

    def test_no_match(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("what?")
        assert result is None

    def test_case_insensitive(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("DONE")
        assert result is not None
        assert result.action == "mark_done"


class TestPlanInterception:
    def test_confirm_keyword(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_confirm("yes") is True
        assert fp.is_plan_confirm("go ahead") is True

    def test_reject_keyword(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_reject("no") is True
        assert fp.is_plan_reject("cancel") is True

    def test_neither(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_confirm("hmm actually do something else") is False
        assert fp.is_plan_reject("hmm actually do something else") is False
