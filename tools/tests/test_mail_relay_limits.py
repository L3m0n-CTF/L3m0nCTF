"""Tests for the mail relay's limits and ordering (the 19 Nov 2026 hardening).

What went wrong on 18 Nov: one daily cap counted Brevo and Gmail together, mail older than 24 hours was deleted unsent,
players who pressed "Resend" added copies to a queue that was already late, and the "queue growing" alert watched a
metric the relay never produced. Each test below names one rule that stops one of those.
"""
from __future__ import annotations

import sqlite3
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

import deploy.ops.mail.relay as relay

HOUR = 3600
DAY = 86400


def mail(to="player@example.org", subject="Welcome to L3m0nCTF", body="Hello", sender="no-reply@l3m0nctf.xyz") -> bytes:
    m = EmailMessage()
    m["From"] = f"L3m0nCTF <{sender}>"
    m["To"] = to
    m["Subject"] = subject
    m.set_content(body)
    return m.as_bytes()


def confirm_mail(to="player@example.org") -> bytes:
    return mail(to, "Confirm your account for L3m0nCTF", "Open https://play.l3m0nctf.xyz/confirm/abc.def to finish.")


def reset_mail(to="player@example.org") -> bytes:
    return mail(to, "Password Reset Request from L3m0nCTF", "Open https://play.l3m0nctf.xyz/reset_password/abc.def to choose a new one.")


class Wire:
    """Stands in for the network: records every send and can refuse a host."""

    def __init__(self):
        self.calls: list[tuple[str, list[str], bytes]] = []
        self.refuse: set[str] = set()

    def __call__(self, host, port, user, password, use_tls, use_ssl, sender, recipients, data):
        self.calls.append((host, recipients, data))
        if host in self.refuse:
            return False, "550 refused"
        return True, ""

    def hosts(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def box(tmp_path, monkeypatch):
    db = relay.SpoolDB(tmp_path / "spool.db")
    db.conn.execute("PRAGMA synchronous = OFF")  # tests only: a thousand commits to disk would make them slow, and the data is thrown away
    db.conn.execute("PRAGMA journal_mode = MEMORY")
    monkeypatch.setattr(relay, "spool", db)
    monkeypatch.setattr(relay, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr(relay, "DEFAULT_MODE", "auto")
    settings = dict(
        UPSTREAM_HOST="smtp-relay.brevo.com", UPSTREAM_USER="brevo-user",
        FALLBACK_HOST="smtp.gmail.com", FALLBACK_USER="backup@gmail.com",
        PRIMARY_DAILY_CAP=290, FALLBACK_DAILY_CAP=450, RATE_LIMIT_PER_DAY=780,
        RATE_LIMIT_PER_MINUTE=100000, SEND_PAUSE_SECONDS=0,
    )
    for name, value in settings.items():
        monkeypatch.setattr(relay, name, value, raising=False)
    monkeypatch.setattr(relay, "get_mail_password", lambda: "primary-secret")
    monkeypatch.setattr(relay, "get_fallback_password", lambda: "fallback-secret")
    monkeypatch.setattr(relay, "METRICS", {key: 0 for key in relay.METRICS})
    wire = Wire()
    monkeypatch.setattr(relay, "_send_smtp", wire)
    db.wire = wire
    return db


def drain(max_rounds=500):
    """Run the worker's pass until it has nothing more it may do."""
    for _ in range(max_rounds):
        if relay.deliver_once() > 0:
            return
    raise AssertionError("the worker never settled")


# ------------------------------------------------------------------ what counts as mail a player is waiting for

def test_confirmation_and_reset_mails_are_action_mail_by_their_link(box):
    assert relay.classify(confirm_mail())[0] == relay.PRIORITY_ACTION
    assert relay.classify(reset_mail())[0] == relay.PRIORITY_ACTION


def test_a_mail_with_a_renamed_subject_is_still_action_mail_by_its_link(box):
    data = mail(subject="Welcome aboard", body="Press https://play.l3m0nctf.xyz/confirm/zzz to start.")
    assert relay.classify(data)[0] == relay.PRIORITY_ACTION


def test_a_welcome_note_is_ordinary_mail(box):
    assert relay.classify(mail())[0] == relay.PRIORITY_ORDINARY


def test_action_mail_goes_out_before_older_ordinary_mail(box):
    first = box.enqueue("a@x", ["a@example.org"], mail("a@example.org"))
    second = box.enqueue("a@x", ["b@example.org"], confirm_mail("b@example.org"))
    assert [row[0] for row in box.get_pending(limit=5)] == [second, first]


# ------------------------------------------------------------------ resend clicks must not multiply the queue

def test_a_second_confirmation_for_the_same_address_replaces_the_one_still_waiting(box):
    first = box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    newer = mail("p@example.org", "Confirm your account for L3m0nCTF", "Open https://play.l3m0nctf.xyz/confirm/NEW to finish.")
    second = box.enqueue("a@x", ["p@example.org"], newer)
    assert second == first and box.queue_length() == 1
    assert b"/confirm/NEW" in box.get_pending(limit=5)[0][3]
    assert box.merged == 1


def test_confirmations_for_two_different_addresses_stay_two_messages(box):
    box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    box.enqueue("a@x", ["q@example.org"], confirm_mail("q@example.org"))
    assert box.queue_length() == 2


def test_a_confirmation_and_a_reset_for_the_same_address_are_not_merged(box):
    box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    box.enqueue("a@x", ["p@example.org"], reset_mail("p@example.org"))
    assert box.queue_length() == 2


def test_ordinary_mail_is_never_merged(box):
    box.enqueue("a@x", ["p@example.org"], mail("p@example.org"))
    box.enqueue("a@x", ["p@example.org"], mail("p@example.org"))
    assert box.queue_length() == 2


def test_a_confirmation_pressed_again_after_the_first_was_sent_is_a_new_message(box):
    first = box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    box.mark_success(first, "primary")
    box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    assert box.queue_length() == 1


def test_a_merged_confirmation_is_tried_again_at_once_even_if_the_old_one_was_backing_off(box):
    first = box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    box.mark_failure(first, 5, "timeout")
    assert box.get_pending(limit=5) == []
    box.enqueue("a@x", ["p@example.org"], confirm_mail("p@example.org"))
    assert [row[0] for row in box.get_pending(limit=5)] == [first]


# ------------------------------------------------------------------ one provider's limit must not stop the other

def log_sent(db, provider, count, age=0.0):
    """Write `count` earlier deliveries through `provider`, `age` seconds ago."""
    with db.conn:
        db.conn.executemany("INSERT INTO delivery_log (sent_at, provider) VALUES (?, ?)", [(time.time() - age, provider)] * count)


def test_when_brevo_is_full_the_mail_goes_through_gmail_without_trying_brevo(box):
    log_sent(box, "primary", 290)
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert ok and provider == "fallback"
    assert box.wire.hosts() == ["smtp.gmail.com"]


def test_when_gmail_is_full_brevo_still_sends(box):
    log_sent(box, "fallback", 450)
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert ok and provider == "primary"
    assert box.wire.hosts() == ["smtp-relay.brevo.com"]


def test_when_both_providers_are_full_nothing_is_sent(box):
    log_sent(box, "primary", 290)
    log_sent(box, "fallback", 450)
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert not ok and provider is None and "cap" in err.lower()
    assert box.wire.calls == []


def test_brevo_only_mode_never_uses_gmail_even_when_brevo_is_full(box, tmp_path):
    (tmp_path / "mode").write_text("brevo", encoding="utf-8")
    log_sent(box, "primary", 290)
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert not ok and box.wire.calls == []


def test_a_delivery_row_from_before_the_upgrade_counts_against_both_caps(box):
    with box.conn:
        box.conn.execute("INSERT INTO delivery_log (sent_at, provider) VALUES (?, '')", (time.time(),))
    assert box.deliveries_in_window(DAY, "primary") == 1
    assert box.deliveries_in_window(DAY, "fallback") == 1
    assert box.deliveries_in_window(DAY) == 1


def test_sends_older_than_a_day_stop_counting(box):
    log_sent(box, "primary", 290, age=DAY + 60)
    assert box.deliveries_in_window(DAY, "primary") == 0
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert ok and provider == "primary"


def test_the_overall_ceiling_still_applies_on_top_of_the_two_caps(box, monkeypatch):
    monkeypatch.setattr(relay, "RATE_LIMIT_PER_DAY", 100)
    log_sent(box, "primary", 60)
    log_sent(box, "fallback", 40)
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert not ok and box.wire.calls == []


def test_a_brevo_refusal_falls_back_to_gmail_and_is_counted_for_gmail(box):
    box.wire.refuse.add("smtp-relay.brevo.com")
    ok, err, provider = relay.dispatch_with_provider("a@x", ["p@example.org"], confirm_mail())
    assert ok and provider == "fallback"
    assert box.wire.hosts() == ["smtp-relay.brevo.com", "smtp.gmail.com"]


# ------------------------------------------------------------------ a day of mail, end to end

def test_a_busy_day_of_600_mails_is_sent_whole_and_split_by_the_two_caps(box):
    for n in range(600):
        box.enqueue("a@x", [f"p{n}@example.org"], confirm_mail(f"p{n}@example.org") if n % 3 else mail(f"p{n}@example.org"))
    drain()
    assert box.queue_length() == 0
    assert box.deliveries_in_window(DAY, "primary") == 290
    assert box.deliveries_in_window(DAY, "fallback") == 310
    assert relay.METRICS["failed_total"] == 0


def test_a_day_of_800_mails_sends_740_and_keeps_60_waiting_without_failing_any(box):
    for n in range(800):
        box.enqueue("a@x", [f"p{n}@example.org"], confirm_mail(f"p{n}@example.org"))
    drain()
    assert box.deliveries_in_window(DAY) == 740
    assert box.queue_length() == 60
    assert relay.METRICS["failed_total"] == 0
    assert all(row[5] == 0 for row in box.get_pending(limit=100))  # nobody burned an attempt on a cap


def test_the_per_minute_limit_still_slows_the_pass(box, monkeypatch):
    monkeypatch.setattr(relay, "RATE_LIMIT_PER_MINUTE", 3)
    for n in range(10):
        box.enqueue("a@x", [f"p{n}@example.org"], confirm_mail(f"p{n}@example.org"))
    drain()
    assert box.deliveries_in_window(60) == 3 and box.queue_length() == 7


def test_hold_mode_sends_nothing_and_asks_the_worker_to_wait(box, tmp_path):
    (tmp_path / "mode").write_text("hold", encoding="utf-8")
    box.enqueue("a@x", ["p@example.org"], confirm_mail())
    assert relay.deliver_once() > 0
    assert box.wire.calls == [] and box.queue_length() == 1


# ------------------------------------------------------------------ how long mail is kept

def age_everything(db, seconds):
    with db.conn:
        db.conn.execute("UPDATE queue SET created_at = created_at - ?", (seconds,))


def test_ordinary_mail_is_kept_72_hours_not_24(box):
    box.enqueue("a@x", ["p@example.org"], mail())
    age_everything(box, 25 * HOUR)
    assert box.purge_expired(relay.MAX_QUEUE_AGE_SECONDS, relay.MAX_QUEUE_AGE_ACTION_SECONDS) == 0
    age_everything(box, 48 * HOUR)  # 73 hours old
    assert box.purge_expired(relay.MAX_QUEUE_AGE_SECONDS, relay.MAX_QUEUE_AGE_ACTION_SECONDS) == 1


def test_a_confirmation_is_kept_while_its_link_still_works(box):
    box.enqueue("a@x", ["p@example.org"], confirm_mail())
    age_everything(box, 4 * DAY)  # CTFd's confirmation link lives 5 days
    assert box.purge_expired(relay.MAX_QUEUE_AGE_SECONDS, relay.MAX_QUEUE_AGE_ACTION_SECONDS) == 0
    age_everything(box, 2 * DAY)  # 6 days: the link is dead, sending it would only mislead
    assert box.purge_expired(relay.MAX_QUEUE_AGE_SECONDS, relay.MAX_QUEUE_AGE_ACTION_SECONDS) == 1


def test_the_defaults_are_72_hours_and_just_under_the_link_lifetime(box):
    assert relay.MAX_QUEUE_AGE_SECONDS == 72 * HOUR
    assert 4 * DAY < relay.MAX_QUEUE_AGE_ACTION_SECONDS < 5 * DAY


# ------------------------------------------------------------------ the spool already on the server

def test_a_spool_made_by_the_old_relay_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, recipients TEXT, data BLOB, created_at REAL, next_attempt REAL, attempts INTEGER DEFAULT 0, last_error TEXT DEFAULT '')")
    old.execute("CREATE TABLE delivery_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at REAL)")
    now = time.time()
    old.execute("INSERT INTO queue (sender, recipients, data, created_at, next_attempt) VALUES ('a@x', '[\"p@example.org\"]', ?, ?, ?)", (confirm_mail(), now, now))
    old.execute("INSERT INTO queue (sender, recipients, data, created_at, next_attempt) VALUES ('a@x', '[\"q@example.org\"]', ?, ?, ?)", (mail("q@example.org"), now, now))
    old.execute("INSERT INTO delivery_log (sent_at) VALUES (?)", (now,))
    old.commit()
    old.close()
    db = relay.SpoolDB(path)
    pending = db.get_pending(limit=5)
    assert len(pending) == 2
    assert pending[0][2] == '["p@example.org"]'  # the confirmation was classified and goes first
    assert db.deliveries_in_window(DAY, "primary") == 1 and db.deliveries_in_window(DAY, "fallback") == 1


def test_opening_the_same_spool_twice_is_harmless(tmp_path):
    relay.SpoolDB(tmp_path / "s.db")
    relay.SpoolDB(tmp_path / "s.db")


# ------------------------------------------------------------------ what the crew can see

def test_the_oldest_waiting_message_has_an_age(box):
    assert box.oldest_age() == 0
    box.enqueue("a@x", ["p@example.org"], mail())
    age_everything(box, 600)
    assert 590 < box.oldest_age() < 660


def test_the_metrics_page_shows_queue_age_and_both_providers_against_their_caps(box):
    log_sent(box, "primary", 5)
    log_sent(box, "fallback", 7)
    box.enqueue("a@x", ["p@example.org"], mail())
    age_everything(box, 120)
    text = relay.render_metrics()
    assert "l3mon_mail_queue_length 1" in text
    assert "l3mon_mail_queue_age_seconds 12" in text
    assert 'l3mon_mail_sent_24h{provider="brevo"} 5' in text
    assert 'l3mon_mail_sent_24h{provider="gmail"} 7' in text
    assert 'l3mon_mail_cap{provider="brevo"} 290' in text
    assert 'l3mon_mail_cap{provider="gmail"} 450' in text
    assert "l3mon_mail_merged_total" in text and "l3mon_mail_purged_total" in text


# ------------------------------------------------------------------ the Gmail password

def test_the_gmail_password_is_read_from_its_secret_file(tmp_path, monkeypatch):
    secret = tmp_path / "FALLBACK_MAIL_PASSWORD"
    secret.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_FILE", secret)
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_ENV", "")
    assert relay.get_fallback_password() == "from-the-file"


def test_the_secret_file_wins_over_an_old_environment_value(tmp_path, monkeypatch):
    secret = tmp_path / "FALLBACK_MAIL_PASSWORD"
    secret.write_text("from-the-file", encoding="utf-8")
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_FILE", secret)
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_ENV", "from-the-environment")
    assert relay.get_fallback_password() == "from-the-file"


def test_an_empty_secret_file_falls_back_to_the_environment_value_until_it_is_removed(tmp_path, monkeypatch):
    secret = tmp_path / "FALLBACK_MAIL_PASSWORD"
    secret.write_text("", encoding="utf-8")
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_FILE", secret)
    monkeypatch.setattr(relay, "FALLBACK_PASSWORD_ENV", "from-the-environment")
    assert relay.get_fallback_password() == "from-the-environment"


# ------------------------------------------------------------------ the sender name on the Gmail route

class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=15):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, data):
        FakeSMTP.sent.append((sender, recipients, data))

    def quit(self):
        pass


def test_the_gmail_route_shows_a_clear_sender_name_and_keeps_replies_going_to_the_real_address(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(relay.smtplib, "SMTP", FakeSMTP)
    original = mail("p@example.org", "Confirm your account for L3m0nCTF", "Open https://play.l3m0nctf.xyz/confirm/x")
    ok, err = relay._send_smtp("smtp.gmail.com", 587, "backup@gmail.com", "pw", True, False, "no-reply@l3m0nctf.xyz", ["p@example.org"], original)
    assert ok, err
    envelope, rcpts, sent = FakeSMTP.sent[0]
    text = sent.decode("utf-8")
    assert envelope == "backup@gmail.com"
    assert "From: L3m0nCTF <backup@gmail.com>" in text
    assert "Reply-To: no-reply@l3m0nctf.xyz" in text
    assert "/confirm/x" in text and "Subject: Confirm your account for L3m0nCTF" in text


def test_the_brevo_route_sends_the_message_exactly_as_it_came(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(relay.smtplib, "SMTP", FakeSMTP)
    original = mail()
    ok, err = relay._send_smtp("smtp-relay.brevo.com", 587, "brevo-user", "pw", True, False, "no-reply@l3m0nctf.xyz", ["p@example.org"], original)
    assert ok, err
    assert FakeSMTP.sent[0][2] == original


def test_a_message_the_sender_name_step_cannot_read_is_sent_untouched(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(relay.smtplib, "SMTP", FakeSMTP)
    broken = b"\xff\xfe not a mail at all"
    ok, err = relay._send_smtp("smtp.gmail.com", 587, "backup@gmail.com", "pw", True, False, "no-reply@l3m0nctf.xyz", ["p@example.org"], broken)
    assert ok, err
    assert FakeSMTP.sent[0][2] == broken
