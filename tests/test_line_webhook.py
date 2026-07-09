"""Tests for LINE inbound webhook: signature verification + event parsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from restaurant_api.integrations.line import parse_events, verify_signature


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_verify_signature_accepts_valid():
    secret = "s3cr3t"
    body = b'{"events":[]}'
    assert verify_signature(secret, body, _sign(secret, body)) is True


def test_verify_signature_rejects_tampered_body():
    secret = "s3cr3t"
    sig = _sign(secret, b'{"events":[]}')
    assert verify_signature(secret, b'{"events":[{"type":"follow"}]}', sig) is False


def test_verify_signature_rejects_wrong_secret():
    body = b'{"events":[]}'
    assert verify_signature("right", body, _sign("wrong", body)) is False


def test_verify_signature_rejects_empty_secret_or_sig():
    body = b"x"
    assert verify_signature("", body, "anything") is False
    assert verify_signature("secret", body, "") is False


def test_parse_events_extracts_follow():
    body = json.dumps(
        {
            "events": [
                {
                    "type": "follow",
                    "replyToken": "R1",
                    "source": {"type": "user", "userId": "U123"},
                }
            ]
        }
    ).encode()
    events = parse_events(body)
    assert len(events) == 1
    assert events[0].type == "follow"
    assert events[0].source_user_id == "U123"
    assert events[0].reply_token == "R1"


def test_parse_events_extracts_text_message():
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "source": {"userId": "U1"},
                    "message": {"type": "text", "text": "抽獎"},
                }
            ]
        }
    ).encode()
    events = parse_events(body)
    assert events[0].message_text == "抽獎"


def test_parse_events_ignores_non_text_message_body():
    body = json.dumps(
        {"events": [{"type": "message", "message": {"type": "sticker"}}]}
    ).encode()
    assert parse_events(body)[0].message_text is None


def test_parse_events_malformed_yields_empty():
    assert parse_events(b"not json") == []
    assert parse_events(b'{"no_events_key": 1}') == []
    assert parse_events(b"[]") == []


def test_parse_events_skips_non_dict_entries():
    body = json.dumps({"events": ["garbage", {"type": "follow"}]}).encode()
    events = parse_events(body)
    assert len(events) == 1
    assert events[0].type == "follow"
