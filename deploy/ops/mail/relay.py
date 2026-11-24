#!/usr/bin/env python3
"""Internal Queueing SMTP Relay for L3m0nCTF 2026.

Accepts unauthenticated, plain SMTP traffic from CTFd on the internal network.
Persists outgoing messages in a durable SQLite spool.
Enforces per-minute and daily rate limits, size limits, and access controls.
Relays upstream via STARTTLS/TLS using Docker secret MAIL_PASSWORD.
Exports Prometheus metrics and health status on /metrics.
"""
from __future__ import annotations

import asyncio
import http.server
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default
from email.utils import formataddr, parseaddr
from pathlib import Path
import tempfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mail-relay")

SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9025"))
SPOOL_DIR = Path(os.environ.get("SPOOL_DIR", "/var/spool/l3mon-mail"))
try:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    test_f = SPOOL_DIR / ".probe"
    test_f.touch()
    test_f.unlink()
except (PermissionError, OSError):
    SPOOL_DIR = Path(tempfile.gettempdir()) / "l3mon-mail"
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)

MAX_MESSAGE_BYTES = int(os.environ.get("MAX_MESSAGE_BYTES", "10485760"))  # 10 MB
# How long a message that could not be sent yet is kept. Ordinary mail: 72 hours. Mail a player is waiting for to finish
# something (a confirmation or a password reset): CTFd's links live 5 days (432000 s), so keep it until just before its
# link dies; deleting it earlier loses a mail that would still have worked (18 Nov 2026: the old 24-hour purge did this).
MAX_QUEUE_AGE_SECONDS = int(os.environ.get("MAX_QUEUE_AGE_SECONDS", str(72 * 3600)))
MAX_QUEUE_AGE_ACTION_SECONDS = int(os.environ.get("MAX_QUEUE_AGE_ACTION_SECONDS", str(5 * 86400 - 3600)))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))
# Daily limits over a rolling 24 hours. Each provider has its own (so one provider being full never stops the other);
# RATE_LIMIT_PER_DAY stays as an overall ceiling on top of both.
RATE_LIMIT_PER_DAY = int(os.environ.get("RATE_LIMIT_PER_DAY", "780"))
PRIMARY_DAILY_CAP = int(os.environ.get("PRIMARY_DAILY_CAP", "290"))    # Brevo's free plan allows 300 a day
FALLBACK_DAILY_CAP = int(os.environ.get("FALLBACK_DAILY_CAP", "450"))  # a personal Gmail account allows about 500 a day
SEND_PAUSE_SECONDS = float(os.environ.get("SEND_PAUSE_SECONDS", "0.5"))

PRIORITY_ACTION = 0    # confirmation and password-reset mail: a player is waiting to finish something
PRIORITY_ORDINARY = 1  # everything else
PROVIDER_PRIMARY = "primary"
PROVIDER_FALLBACK = "fallback"

UPSTREAM_HOST = os.environ.get("UPSTREAM_SMTP_HOST", "").strip()
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_SMTP_PORT", "587"))
UPSTREAM_USER = os.environ.get("UPSTREAM_SMTP_USER", "").strip()
UPSTREAM_TLS = os.environ.get("UPSTREAM_SMTP_TLS", "true").lower() in ("true", "1", "yes")
UPSTREAM_SSL = os.environ.get("UPSTREAM_SMTP_SSL", "false").lower() in ("true", "1", "yes")
PASSWORD_FILE = Path(os.environ.get("MAIL_PASSWORD_FILE", "/run/secrets/MAIL_PASSWORD"))

# Fallback / Secondary Upstream (e.g. Gmail SMTP)
FALLBACK_HOST = os.environ.get("FALLBACK_SMTP_HOST", "").strip()
FALLBACK_PORT = int(os.environ.get("FALLBACK_SMTP_PORT", "587"))
FALLBACK_USER = os.environ.get("FALLBACK_SMTP_USER", "").strip()
FALLBACK_TLS = os.environ.get("FALLBACK_SMTP_TLS", "true").lower() in ("true", "1", "yes")
FALLBACK_SSL = os.environ.get("FALLBACK_SMTP_SSL", "false").lower() in ("true", "1", "yes")
FALLBACK_PASSWORD_ENV = os.environ.get("FALLBACK_SMTP_PASSWORD", "").strip()
FALLBACK_PASSWORD_FILE = Path(os.environ.get("FALLBACK_MAIL_PASSWORD_FILE", "/run/secrets/FALLBACK_MAIL_PASSWORD"))

# Runtime mode control: 'auto' (fallback on error), 'brevo' (primary only), 'gmail' (secondary only), 'hold' (pause)
MODE_FILE = SPOOL_DIR / "mode"
DEFAULT_MODE = os.environ.get("MAIL_UPSTREAM_MODE", "auto").strip().lower()

# Allowed client CIDRs (Docker internal networks and localhost)
ALLOWED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# Metrics counters
METRICS = {
    "sent_total": 0,
    "sent_primary_total": 0,
    "sent_fallback_total": 0,
    "fallback_triggered_total": 0,
    "failed_total": 0,
    "rate_limited_total": 0,
    "received_total": 0,
    "rejected_unauthorized": 0,
    "holding": 0,
}


_ACTION_LINK = re.compile(rb"/(?:confirm|reset_password)/", re.IGNORECASE)
_ACTION_SUBJECT = re.compile(r"\b(?:confirm|verify|verification|reset)\b", re.IGNORECASE)


def classify(data: bytes) -> tuple[int, str]:
    """Return (priority, subject) for a raw message. A message is action mail when it carries CTFd's confirmation or
    password-reset link (the reliable sign: the subject is a setting the crew can rename), or when its subject says so."""
    subject = ""
    text = b""
    try:
        msg = BytesParser(policy=default).parsebytes(data)
        subject = " ".join(str(msg.get("Subject", "")).split())
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                try:
                    text += part.get_content().encode("utf-8", "replace")
                except Exception:
                    text += part.get_payload(decode=True) or b""
    except Exception:
        pass
    if _ACTION_LINK.search(text) or _ACTION_LINK.search(data) or _ACTION_SUBJECT.search(subject):
        return PRIORITY_ACTION, subject
    return PRIORITY_ORDINARY, subject


def merge_key(recipients: list[str], priority: int, subject: str) -> str | None:
    """Action mail for the same address with the same subject is one message while it is still waiting (a player who
    presses Resend because nothing arrived must not add a copy to a queue that is already late)."""
    if priority != PRIORITY_ACTION:
        return None
    return json.dumps([sorted(r.strip().lower() for r in recipients), subject.lower()])


class SpoolDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.merged = 0  # resends folded into a message that was still waiting (since this start)
        self.purged = 0  # messages deleted unsent because they got too old (since this start)
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT,
                    recipients TEXT,
                    data BLOB,
                    created_at REAL,
                    next_attempt REAL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    priority INTEGER,
                    merge_key TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS delivery_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at REAL,
                    provider TEXT DEFAULT ''
                )
            """)
            self._upgrade_old_tables()
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_next_attempt ON queue(next_attempt)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sent_at ON delivery_log(sent_at)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_merge ON queue(merge_key)")

    def _upgrade_old_tables(self):
        """A spool made by the earlier relay has no priority, merge key or provider: add them in place and classify what
        is already waiting (the spool lives in a volume and survives restarts, so the old rows are real mail)."""
        queue_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(queue)")}
        if "priority" not in queue_cols:
            self.conn.execute("ALTER TABLE queue ADD COLUMN priority INTEGER")
        if "merge_key" not in queue_cols:
            self.conn.execute("ALTER TABLE queue ADD COLUMN merge_key TEXT")
        log_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(delivery_log)")}
        if "provider" not in log_cols:
            # rows from before the upgrade keep '' and count against BOTH daily limits until they are a day old
            self.conn.execute("ALTER TABLE delivery_log ADD COLUMN provider TEXT DEFAULT ''")
        for msg_id, recipients, data in self.conn.execute("SELECT id, recipients, data FROM queue WHERE priority IS NULL").fetchall():
            priority, subject = classify(bytes(data or b""))
            self.conn.execute("UPDATE queue SET priority = ?, merge_key = ? WHERE id = ?", (priority, merge_key(json.loads(recipients or "[]"), priority, subject), msg_id))

    def enqueue(self, sender: str, recipients: list[str], data: bytes) -> int:
        now = time.time()
        priority, subject = classify(data)
        key = merge_key(recipients, priority, subject)
        with self.lock, self.conn:
            if key:
                waiting = self.conn.execute("SELECT id FROM queue WHERE merge_key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
                if waiting:
                    # keep the newest copy (its link is the freshest) and try it at once, even if the old one was backing off
                    self.conn.execute("UPDATE queue SET sender = ?, data = ?, created_at = ?, next_attempt = ? WHERE id = ?", (sender, data, now, now, waiting[0]))
                    self.merged += 1
                    return waiting[0]
            cur = self.conn.execute(
                "INSERT INTO queue (sender, recipients, data, created_at, next_attempt, attempts, priority, merge_key) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (sender, json.dumps(recipients), data, now, now, priority, key),
            )
            return cur.lastrowid

    def get_pending(self, limit: int = 10) -> list[tuple]:
        now = time.time()
        with self.lock:
            cur = self.conn.execute(
                "SELECT id, sender, recipients, data, created_at, attempts FROM queue WHERE next_attempt <= ? ORDER BY COALESCE(priority, 1) ASC, id ASC LIMIT ?",
                (now, limit),
            )
            return cur.fetchall()

    def mark_success(self, msg_id: int, provider: str = ""):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM queue WHERE id = ?", (msg_id,))
            self.conn.execute("INSERT INTO delivery_log (sent_at, provider) VALUES (?, ?)", (now, provider))

    def mark_failure(self, msg_id: int, attempts: int, error: str):
        now = time.time()
        backoff = min(3600, 2 * (2 ** min(attempts, 6)))
        next_attempt = now + backoff
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE queue SET attempts = ?, next_attempt = ?, last_error = ? WHERE id = ?",
                (attempts + 1, next_attempt, error, msg_id),
            )

    def purge_expired(self, max_age: float, action_max_age: float | None = None) -> int:
        """Delete unsent mail that is too old. Action mail (confirmation, reset) has its own, longer limit."""
        now = time.time()
        if action_max_age is None:
            action_max_age = max_age
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM queue WHERE (COALESCE(priority, 1) = ? AND created_at < ?) OR (priority = ? AND created_at < ?)",
                (PRIORITY_ORDINARY, now - max_age, PRIORITY_ACTION, now - action_max_age),
            )
            # also trim delivery_log older than 48 hours
            self.conn.execute("DELETE FROM delivery_log WHERE sent_at < ?", (now - 172800,))
            self.purged += cur.rowcount
            return cur.rowcount

    def queue_length(self, priority: int | None = None) -> int:
        with self.lock:
            if priority is None:
                cur = self.conn.execute("SELECT COUNT(*) FROM queue")
            else:
                cur = self.conn.execute("SELECT COUNT(*) FROM queue WHERE COALESCE(priority, 1) = ?", (priority,))
            return cur.fetchone()[0]

    def oldest_age(self) -> float:
        """Seconds the longest-waiting message has been in the queue (0 when it is empty)."""
        with self.lock:
            oldest = self.conn.execute("SELECT MIN(created_at) FROM queue").fetchone()[0]
        return max(0.0, time.time() - oldest) if oldest else 0.0

    def deliveries_in_window(self, seconds: float, provider: str | None = None) -> int:
        """Deliveries in the last `seconds`; for one provider, rows written before the upgrade (no provider noted) count too."""
        cutoff = time.time() - seconds
        with self.lock:
            if provider is None:
                cur = self.conn.execute("SELECT COUNT(*) FROM delivery_log WHERE sent_at >= ?", (cutoff,))
            else:
                cur = self.conn.execute(
                    "SELECT COUNT(*) FROM delivery_log WHERE sent_at >= ? AND (provider = ? OR provider = '' OR provider IS NULL)",
                    (cutoff, provider),
                )
            return cur.fetchone()[0]


spool = SpoolDB(SPOOL_DIR / "spool.db")


def is_ip_allowed(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in ALLOWED_NETWORKS)
    except ValueError:
        return False


def get_mail_password() -> str:
    if PASSWORD_FILE.is_file():
        return PASSWORD_FILE.read_text(encoding="utf-8").strip()
    return ""


def get_fallback_password() -> str:
    """The Gmail app password. Its home is the Docker secret file (.secrets/FALLBACK_MAIL_PASSWORD); a value in the
    environment is still accepted so a server that has not moved it yet keeps working, but the file wins."""
    if FALLBACK_PASSWORD_FILE.is_file():
        try:
            value = FALLBACK_PASSWORD_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    return FALLBACK_PASSWORD_ENV


def get_active_mode() -> str:
    """Return active upstream mode: 'auto', 'brevo', 'gmail', or 'hold'."""
    candidate_paths = [
        MODE_FILE,
        Path("/var/spool/l3mon-mail/mode"),
        Path("/tmp/l3mon-mail/mode"),
    ]
    for p in candidate_paths:
        if p.is_file():
            try:
                val = p.read_text(encoding="utf-8").strip().lower()
                if val in ("auto", "both", "brevo", "primary", "gmail", "fallback", "hold", "off", "pause"):
                    return val
            except OSError:
                pass
    return DEFAULT_MODE


# ------------------------------------------------------------- SMTP Server Implementation
class SmtpSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.peer_ip = self.peer[0] if self.peer else "0.0.0.0"
        self.helo = ""
        self.sender = ""
        self.recipients: list[str] = []

    async def send_line(self, line: str):
        self.writer.write((line + "\r\n").encode("utf-8"))
        await self.writer.drain()

    async def handle(self):
        # Access control check
        if not is_ip_allowed(self.peer_ip):
            logger.warning("Rejected unauthorized client connection from %s", self.peer_ip)
            METRICS["rejected_unauthorized"] += 1
            await self.send_line("550 5.7.1 Relaying denied: untrusted network")
            self.writer.close()
            await self.writer.wait_closed()
            return

        await self.send_line("220 l3mon-mail-relay ESMTP ready")

        while True:
            line_bytes = await self.reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", "replace").strip()
            if not line:
                continue

            cmd = line.split(None, 1)[0].upper()
            arg = line[len(cmd):].strip()

            if cmd in ("HELO", "EHLO"):
                self.helo = arg
                await self.send_line("250-l3mon-mail-relay")
                await self.send_line("250-SIZE 10485760")
                await self.send_line("250 8BITMIME")

            elif cmd == "MAIL":
                m = re.search(r"FROM:\s*<([^>]*)>", arg, re.IGNORECASE)
                if not m:
                    await self.send_line("501 5.5.4 Syntax error in parameters")
                else:
                    self.sender = m.group(1).strip()
                    self.recipients = []
                    await self.send_line("250 2.1.0 Sender ok")

            elif cmd == "RCPT":
                if not self.sender:
                    await self.send_line("503 5.5.1 Need MAIL before RCPT")
                    continue
                m = re.search(r"TO:\s*<([^>]*)>", arg, re.IGNORECASE)
                if not m:
                    await self.send_line("501 5.5.4 Syntax error in parameters")
                else:
                    rcpt = m.group(1).strip()
                    self.recipients.append(rcpt)
                    await self.send_line("250 2.1.5 Recipient ok")

            elif cmd == "DATA":
                if not self.sender or not self.recipients:
                    await self.send_line("503 5.5.1 Need MAIL and RCPT before DATA")
                    continue
                await self.send_line("354 Start mail input; end with <CRLF>.<CRLF>")

                data_chunks = []
                total_bytes = 0
                overflow = False
                buf = b""

                while True:
                    chunk = await self.reader.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    total_bytes += len(chunk)
                    if total_bytes > MAX_MESSAGE_BYTES:
                        overflow = True

                    if not overflow:
                        data_chunks.append(chunk)

                    if b"\r\n.\r\n" in buf or b"\n.\n" in buf or buf.endswith(b"\r\n.") or buf == b".\r\n":
                        break
                    # Keep only last 10 bytes in buf to check for boundary
                    if len(buf) > 10:
                        buf = buf[-10:]

                if overflow:
                    await self.send_line("552 5.3.4 Message size exceeds maximum limit")
                    self.sender = ""
                    self.recipients = []
                    continue

                full_data = b"".join(data_chunks)
                # Strip trailing terminator
                if full_data.endswith(b"\r\n.\r\n"):
                    full_data = full_data[:-3]
                elif full_data.endswith(b"\n.\n"):
                    full_data = full_data[:-2]

                msg_id = spool.enqueue(self.sender, self.recipients, full_data)
                METRICS["received_total"] += 1
                logger.info("Queued message #%d from <%s> to %d recipients (%d bytes)", msg_id, self.sender, len(self.recipients), len(full_data))
                await self.send_line("250 2.0.0 Queued as %d" % msg_id)
                self.sender = ""
                self.recipients = []

            elif cmd == "RSET":
                self.sender = ""
                self.recipients = []
                await self.send_line("250 2.0.0 Reset ok")

            elif cmd == "NOOP":
                await self.send_line("250 2.0.0 OK")

            elif cmd == "QUIT":
                await self.send_line("221 2.0.0 Bye")
                break
            else:
                await self.send_line("500 5.5.2 Command unrecognized")

        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


async def start_smtp_server():
    server = await asyncio.start_server(
        lambda r, w: SmtpSession(r, w).handle(),
        SMTP_HOST,
        SMTP_PORT,
    )
    logger.info("SMTP relay listening on %s:%d", SMTP_HOST, SMTP_PORT)
    async with server:
        await server.serve_forever()


# -------------------------------------------------------- Outgoing Delivery Worker
def gmail_sender_name(data: bytes, account: str) -> bytes:
    """For the Gmail route: show the sender as `<the From name> <the Gmail account>` and send replies to the address CTFd
    wrote. Gmail will not send as an address that is not a verified alias of the account, so a mail whose From says
    no-reply@l3m0nctf.xyz reaches the inbox with a different, unexplained sender. Only the header block is touched; the body
    is passed on byte for byte. Anything the step cannot read is sent exactly as it came."""
    try:
        positions = [p for p in (data.find(b"\r\n\r\n"), data.find(b"\n\n")) if p >= 0]
        if not positions:
            return data
        cut = min(positions)
        sep = b"\r\n\r\n" if data.startswith(b"\r\n\r\n", cut) else b"\n\n"
        eol = b"\r\n" if sep == b"\r\n\r\n" else b"\n"
        head, body = data[:cut], data[cut + len(sep):]
        groups: list[list[bytes]] = []
        for line in head.split(eol):
            if line[:1] in (b" ", b"\t") and groups:
                groups[-1].append(line)
            else:
                groups.append([line])
        names = [g[0].split(b":", 1)[0].strip().lower() for g in groups]
        if b"from" not in names:
            return data
        index = names.index(b"from")
        original = b" ".join(part.strip() for part in groups[index]).split(b":", 1)[1].decode("utf-8").strip()
        display, address = parseaddr(original)
        if not address or address.lower() == account.lower():
            return data
        groups[index] = [b"From: " + formataddr((display or "L3m0nCTF", account)).encode("ascii")]
        if b"reply-to" not in names:
            groups.append([b"Reply-To: " + address.encode("ascii")])
        return eol.join(line for group in groups for line in group) + sep + body
    except Exception:
        return data


def _send_smtp(host: str, port: int, user: str, password: str, use_tls: bool, use_ssl: bool, sender: str, recipients: list[str], data: bytes) -> tuple[bool, str]:
    try:
        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        if user and password:
            server.login(user, password)

        # For providers enforcing authenticated envelope sender (like Gmail): the envelope is the account itself, and the
        # From line is rewritten to name it clearly (the account's own address under the L3m0nCTF name).
        via_gmail = "gmail" in host.lower() and bool(user)
        env_sender = user if via_gmail else sender
        if via_gmail:
            data = gmail_sender_name(data, user)
        server.sendmail(env_sender, recipients, data)
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass
        return True, ""
    except Exception as e:
        return False, str(e)


def _under_cap(provider: str) -> bool:
    cap = PRIMARY_DAILY_CAP if provider == PROVIDER_PRIMARY else FALLBACK_DAILY_CAP
    return spool.deliveries_in_window(86400, provider) < cap


def dispatch_with_provider(sender: str, recipients: list[str], data: bytes) -> tuple[bool, str, str | None]:
    """Send one message through the provider(s) the mode allows, each only while under its own daily cap.

    Returns (ok, error, provider). `provider` is "primary" or "fallback" when the message was sent that way, "" when a
    provider was tried and failed (the message should back off), and None when nothing was tried because the mode, a
    missing credential or a daily cap said no (the message must wait without losing an attempt)."""
    mode = get_active_mode()
    if mode in ("hold", "pause", "off"):
        return False, "Holding mode: upstream delivery paused by operator", None

    primary_pw = get_mail_password()
    fallback_pw = get_fallback_password()
    primary_ready = bool(UPSTREAM_HOST and primary_pw)
    fallback_ready = bool(FALLBACK_HOST and FALLBACK_USER and fallback_pw)
    gmail_only = mode in ("gmail", "fallback")
    brevo_only = mode in ("brevo", "primary")

    candidates = []  # in the order they are tried
    if not gmail_only and primary_ready:
        candidates.append((PROVIDER_PRIMARY, (UPSTREAM_HOST, UPSTREAM_PORT, UPSTREAM_USER, primary_pw, UPSTREAM_TLS, UPSTREAM_SSL)))
    if not brevo_only and fallback_ready:
        candidates.append((PROVIDER_FALLBACK, (FALLBACK_HOST, FALLBACK_PORT, FALLBACK_USER, fallback_pw, FALLBACK_TLS, FALLBACK_SSL)))
    if not candidates:
        if gmail_only:
            return False, "Fallback (Gmail) mode selected but fallback credentials not ready", None
        if brevo_only:
            return False, "Primary (Brevo) mode selected but primary credentials not ready", None
        return False, "No upstream SMTP provider configured or ready", None

    if spool.deliveries_in_window(86400) >= RATE_LIMIT_PER_DAY:
        return False, "Daily cap reached (all providers together)", None
    open_candidates = [c for c in candidates if _under_cap(c[0])]
    if not open_candidates:
        return False, "Daily cap reached on every provider the current mode may use", None

    errors = []
    for position, (name, (host, port, user, password, use_tls, use_ssl)) in enumerate(open_candidates):
        ok, err = _send_smtp(host, port, user, password, use_tls, use_ssl, sender, recipients, data)
        if ok:
            METRICS["sent_primary_total" if name == PROVIDER_PRIMARY else "sent_fallback_total"] += 1
            if position > 0:
                logger.info("Delivered message via fallback upstream (%s) to %s", host, recipients)
            return True, "", name
        errors.append((name, err))
        if position + 1 < len(open_candidates):
            logger.warning("Primary upstream (%s) delivery failed: %s. Falling back to %s...", host, err, open_candidates[position + 1][1][0])
            METRICS["fallback_triggered_total"] += 1
    if len(errors) == 1:
        return False, errors[0][1], ""
    return False, f"Primary error: {errors[0][1]} | Fallback error: {errors[1][1]}", ""


def dispatch_upstream(sender: str, recipients: list[str], data: bytes) -> tuple[bool, str]:
    ok, err, _provider = dispatch_with_provider(sender, recipients, data)
    return ok, err


def send_upstream(sender: str, recipients: list[str], data: bytes, password: str = "") -> tuple[bool, str]:
    return dispatch_upstream(sender, recipients, data)


def deliver_once() -> float:
    """One pass of the delivery worker: drop what is too old, then send up to five waiting messages, action mail first.
    Returns how many seconds the caller should wait before the next pass (0 = more work may be ready right now)."""
    before = spool.purged
    spool.purge_expired(MAX_QUEUE_AGE_SECONDS, MAX_QUEUE_AGE_ACTION_SECONDS)
    if spool.purged > before:
        logger.warning("Purged %d expired messages from spool (too old to send)", spool.purged - before)

    mode = get_active_mode()
    primary_pw = get_mail_password()
    fallback_pw = get_fallback_password()
    primary_ready = bool(UPSTREAM_HOST and primary_pw and mode not in ("gmail", "fallback", "hold", "off", "pause"))
    fallback_ready = bool(FALLBACK_HOST and FALLBACK_USER and fallback_pw and mode not in ("brevo", "primary", "hold", "off", "pause"))
    if mode in ("hold", "pause", "off") or not (primary_ready or fallback_ready):
        # Holding mode: no upstream ready or paused by operator
        METRICS["holding"] = 1
        return 3.0
    METRICS["holding"] = 0

    pending = spool.get_pending(limit=5)
    if not pending:
        return 1.0

    for msg_id, sender, rcpts_json, data, created_at, attempts in pending:
        if spool.deliveries_in_window(60) >= RATE_LIMIT_PER_MINUTE:
            METRICS["rate_limited_total"] += 1
            return 2.0
        recipients = json.loads(rcpts_json)
        success, err, provider = dispatch_with_provider(sender, recipients, data)
        if provider is None:
            # nothing was tried (a daily cap, or a mode that cannot send): the message waits and loses no attempt
            METRICS["rate_limited_total"] += 1
            return 10.0
        if success:
            spool.mark_success(msg_id, provider)
            METRICS["sent_total"] += 1
            logger.info("Delivered message #%d to %s", msg_id, recipients)
        else:
            spool.mark_failure(msg_id, attempts, err)
            METRICS["failed_total"] += 1
            logger.warning("Delivery failed for message #%d: %s", msg_id, err)
        if SEND_PAUSE_SECONDS:
            time.sleep(SEND_PAUSE_SECONDS)
    return 0.0


def delivery_worker():
    logger.info("Delivery worker thread started.")
    while True:
        try:
            wait = deliver_once()
        except Exception as e:
            logger.error("Error in delivery worker loop: %s", e)
            wait = 2.0
        if wait:
            time.sleep(wait)


# ------------------------------------------------------------- Metrics HTTP Server
def render_metrics() -> str:
    q_len = spool.queue_length()
    return (
        "# HELP l3mon_mail_queue_length Current number of queued emails.\n"
        "# TYPE l3mon_mail_queue_length gauge\n"
        f"l3mon_mail_queue_length {q_len}\n"
        "# HELP l3mon_mail_action_queue_length Queued confirmation and password-reset emails (a player is waiting).\n"
        "# TYPE l3mon_mail_action_queue_length gauge\n"
        f"l3mon_mail_action_queue_length {spool.queue_length(PRIORITY_ACTION)}\n"
        "# HELP l3mon_mail_queue_age_seconds Age of the longest-waiting queued email (0 when the queue is empty).\n"
        "# TYPE l3mon_mail_queue_age_seconds gauge\n"
        f"l3mon_mail_queue_age_seconds {int(spool.oldest_age())}\n"
        "# HELP l3mon_mail_sent_24h Emails delivered in the last 24 hours, by provider (a delivery recorded before the upgrade counts for both).\n"
        "# TYPE l3mon_mail_sent_24h gauge\n"
        f'l3mon_mail_sent_24h{{provider="brevo"}} {spool.deliveries_in_window(86400, PROVIDER_PRIMARY)}\n'
        f'l3mon_mail_sent_24h{{provider="gmail"}} {spool.deliveries_in_window(86400, PROVIDER_FALLBACK)}\n'
        "# HELP l3mon_mail_cap Daily delivery limit of each provider.\n"
        "# TYPE l3mon_mail_cap gauge\n"
        f'l3mon_mail_cap{{provider="brevo"}} {PRIMARY_DAILY_CAP}\n'
        f'l3mon_mail_cap{{provider="gmail"}} {FALLBACK_DAILY_CAP}\n'
        "# HELP l3mon_mail_merged_total Resends folded into a message that was still waiting.\n"
        "# TYPE l3mon_mail_merged_total counter\n"
        f"l3mon_mail_merged_total {spool.merged}\n"
        "# HELP l3mon_mail_purged_total Messages deleted unsent because they were too old.\n"
        "# TYPE l3mon_mail_purged_total counter\n"
        f"l3mon_mail_purged_total {spool.purged}\n"
        "# HELP l3mon_mail_received_total Emails accepted from the platform into the queue.\n"
        "# TYPE l3mon_mail_received_total counter\n"
        f"l3mon_mail_received_total {METRICS['received_total']}\n"
        "# HELP l3mon_mail_sent_total Total delivered emails.\n"
        "# TYPE l3mon_mail_sent_total counter\n"
        f"l3mon_mail_sent_total {METRICS['sent_total']}\n"
        "# HELP l3mon_mail_sent_primary_total Total emails delivered through primary upstream.\n"
        "# TYPE l3mon_mail_sent_primary_total counter\n"
        f"l3mon_mail_sent_primary_total {METRICS['sent_primary_total']}\n"
        "# HELP l3mon_mail_sent_fallback_total Total emails delivered through fallback upstream.\n"
        "# TYPE l3mon_mail_sent_fallback_total counter\n"
        f"l3mon_mail_sent_fallback_total {METRICS['sent_fallback_total']}\n"
        "# HELP l3mon_mail_fallback_triggered_total Count of fallback delivery triggers.\n"
        "# TYPE l3mon_mail_fallback_triggered_total counter\n"
        f"l3mon_mail_fallback_triggered_total {METRICS['fallback_triggered_total']}\n"
        "# HELP l3mon_mail_failed_total Total failed email delivery attempts.\n"
        "# TYPE l3mon_mail_failed_total counter\n"
        f"l3mon_mail_failed_total {METRICS['failed_total']}\n"
        "# HELP l3mon_mail_rate_limited_total Count of times a rate limit or daily cap held delivery back.\n"
        "# TYPE l3mon_mail_rate_limited_total counter\n"
        f"l3mon_mail_rate_limited_total {METRICS['rate_limited_total']}\n"
        "# HELP l3mon_mail_holding Whether relay is in holding mode (missing password or upstream).\n"
        "# TYPE l3mon_mail_holding gauge\n"
        f"l3mon_mail_holding {METRICS['holding']}\n"
        "# HELP l3mon_mail_rejected_unauthorized_total Connections rejected from non-allowed networks.\n"
        "# TYPE l3mon_mail_rejected_unauthorized_total counter\n"
        f"l3mon_mail_rejected_unauthorized_total {METRICS['rejected_unauthorized']}\n"
    )


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/metrics", "/metrics/"):
            body = render_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path in ("/healthz", "/readyz"):
            body = b'{"status": "ok", "service": "mail-relay"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # suppress default access logs
        return


def start_metrics_server():
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    logger.info("Metrics HTTP server listening on :%d", METRICS_PORT)
    httpd.serve_forever()


def main():
    threading.Thread(target=start_metrics_server, daemon=True).start()
    threading.Thread(target=delivery_worker, daemon=True).start()
    asyncio.run(start_smtp_server())


if __name__ == "__main__":
    main()
