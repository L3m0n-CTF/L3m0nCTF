"""The mail alerts and the mail dashboard must watch metrics the relay really produces.

On 18 Nov 2026 mail stopped for hours while the alert "mail queue growing" stayed silent, because the rule (and the Grafana panels) asked for
`mail_spool_queue_count`, `mail_delivered_total` and others that the relay never exposed: it exposes `l3mon_mail_*`. These tests read the rule file and
the dashboard and fail if any mail metric they name is missing from the relay's /metrics page.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import deploy.ops.mail.relay as relay

ROOT = Path(__file__).resolve().parents[2]
ALERTS = (ROOT / "deploy" / "ops" / "prometheus" / "alerts" / "l3mon_alerts.yml").read_text(encoding="utf-8")
RULE_TESTS = (ROOT / "deploy" / "ops" / "prometheus" / "rules_test.yml").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "deploy" / "ops" / "grafana" / "dashboards" / "mail-backups.json").read_text(encoding="utf-8")

METRIC = re.compile(r"\b(?:l3mon_)?mail_[a-z0-9_]+")


@pytest.fixture
def exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(relay, "spool", relay.SpoolDB(tmp_path / "spool.db"))
    return {line.split("{")[0].split(" ")[0] for line in relay.render_metrics().splitlines() if line and not line.startswith("#")}


def alert_expressions() -> list[str]:
    return re.findall(r"^\s*expr:\s*(.+)$", ALERTS, re.MULTILINE)


def mail_names(expressions: list[str]) -> set[str]:
    return {name for expr in expressions for name in METRIC.findall(expr)}


def test_every_mail_metric_an_alert_rule_names_is_one_the_relay_exposes(exposed):
    names = mail_names(alert_expressions())
    assert names, "the mail alerts name no mail metric at all"
    assert names <= exposed, f"alert rules watch metrics the relay does not produce: {sorted(names - exposed)}"


def test_every_mail_metric_a_dashboard_panel_names_is_one_the_relay_exposes(exposed):
    names = mail_names(re.findall(r'"expr":\s*"([^"]+)"', DASHBOARD))
    assert names <= exposed, f"dashboard panels ask for metrics the relay does not produce: {sorted(names - exposed)}"


@pytest.mark.parametrize("alert", ["MailQueueGrowing", "MailQueueStale", "MailProviderCapHigh"])
def test_each_mail_alert_exists_and_has_a_unit_test(alert):
    assert f"alert: {alert}" in ALERTS
    assert f"alertname: {alert}" in RULE_TESTS, f"{alert} has no promtool test in rules_test.yml"


def test_the_stale_queue_alert_uses_the_age_of_the_oldest_message_and_ten_minutes():
    block = ALERTS.split("alert: MailQueueStale", 1)[1].split("- alert:", 1)[0]
    assert "l3mon_mail_queue_age_seconds > 600" in block


def test_the_cap_alert_compares_each_provider_with_its_own_cap_at_80_percent():
    block = ALERTS.split("alert: MailProviderCapHigh", 1)[1].split("- alert:", 1)[0]
    assert "l3mon_mail_sent_24h / on(provider) l3mon_mail_cap" in block and "> 0.8" in block
