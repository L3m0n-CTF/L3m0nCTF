"""Unit tests for the multi-upstream mail relay fallback and mode switching."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import deploy.ops.mail.relay as relay


def test_mail_relay_mode_detection(tmp_path, monkeypatch):
    mode_f = tmp_path / "mode"
    monkeypatch.setattr(relay, "MODE_FILE", mode_f)
    monkeypatch.setattr(relay, "DEFAULT_MODE", "auto")

    # Default
    assert relay.get_active_mode() == "auto"

    # Written to file
    mode_f.write_text("gmail\n", encoding="utf-8")
    assert relay.get_active_mode() == "gmail"

    mode_f.write_text("brevo\n", encoding="utf-8")
    assert relay.get_active_mode() == "brevo"

    mode_f.write_text("hold\n", encoding="utf-8")
    assert relay.get_active_mode() == "hold"


def test_mail_relay_dispatch_hold_mode(tmp_path, monkeypatch):
    mode_f = tmp_path / "mode"
    mode_f.write_text("hold", encoding="utf-8")
    monkeypatch.setattr(relay, "MODE_FILE", mode_f)

    ok, err = relay.dispatch_upstream("sender@example.com", ["rcpt@example.com"], b"data")
    assert ok is False
    assert "Holding mode" in err


def test_mail_relay_auto_fallback_on_primary_failure(tmp_path, monkeypatch):
    mode_f = tmp_path / "mode"
    mode_f.write_text("auto", encoding="utf-8")
    monkeypatch.setattr(relay, "MODE_FILE", mode_f)

    monkeypatch.setattr(relay, "UPSTREAM_HOST", "primary.example.com")
    monkeypatch.setattr(relay, "UPSTREAM_USER", "primary-user")
    monkeypatch.setattr(relay, "FALLBACK_HOST", "smtp.gmail.com")
    monkeypatch.setattr(relay, "FALLBACK_USER", "backup@gmail.com")
    monkeypatch.setattr(relay, "get_mail_password", lambda: "primary-pass")
    monkeypatch.setattr(relay, "get_fallback_password", lambda: "backup-pass")

    # Simulate primary failure (e.g., 550 Quota exceeded) and fallback success
    calls = []

    def mock_send_smtp(host, port, user, password, use_tls, use_ssl, sender, recipients, data):
        calls.append((host, user))
        if host == "primary.example.com":
            return False, "550 Daily quota exceeded"
        if host == "smtp.gmail.com":
            return True, ""
        return False, "Unknown host"

    monkeypatch.setattr(relay, "_send_smtp", mock_send_smtp)

    ok, err = relay.dispatch_upstream("no-reply@l3m0nctf.xyz", ["player@example.org"], b"Subject: Test\r\n\r\nHello")
    assert ok is True
    assert err == ""
    # Check that both primary and fallback were called in order
    assert len(calls) == 2
    assert calls[0] == ("primary.example.com", "primary-user")
    assert calls[1] == ("smtp.gmail.com", "backup@gmail.com")


def test_mail_relay_gmail_only_mode_bypasses_primary(tmp_path, monkeypatch):
    mode_f = tmp_path / "mode"
    mode_f.write_text("gmail", encoding="utf-8")
    monkeypatch.setattr(relay, "MODE_FILE", mode_f)

    monkeypatch.setattr(relay, "UPSTREAM_HOST", "primary.example.com")
    monkeypatch.setattr(relay, "UPSTREAM_USER", "primary-user")
    monkeypatch.setattr(relay, "FALLBACK_HOST", "smtp.gmail.com")
    monkeypatch.setattr(relay, "FALLBACK_USER", "backup@gmail.com")
    monkeypatch.setattr(relay, "get_mail_password", lambda: "primary-pass")
    monkeypatch.setattr(relay, "get_fallback_password", lambda: "backup-pass")

    calls = []

    def mock_send_smtp(host, port, user, password, use_tls, use_ssl, sender, recipients, data):
        calls.append((host, user))
        return True, ""

    monkeypatch.setattr(relay, "_send_smtp", mock_send_smtp)

    ok, err = relay.dispatch_upstream("no-reply@l3m0nctf.xyz", ["player@example.org"], b"Subject: Test\r\n\r\nHello")
    assert ok is True
    assert len(calls) == 1
    assert calls[0] == ("smtp.gmail.com", "backup@gmail.com")
