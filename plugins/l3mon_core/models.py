"""The tables of the SP3 design (section 4: five, and the audit trail of 3.2). Created by migrations/ on MariaDB and by create_all on SQLite (CTFd's own rule).

    l3mon_channel    a channel of the broadcast: its look, its storyline, its sponsor if it has one, and whether it is on air
    l3mon_programme  the place of one challenge in one channel (cell, number, address) and whether it is on air
    l3mon_void       a solve set aside by Revoke: who, when, why, and what happened to it since (nothing is deleted)
    l3mon_bonus      the private note behind a "Bonus" award: for the team or for one member
    l3mon_channel_bonus  the bonus a studio was given for completing a channel: one row per studio and channel (added by the sixth migration)
    l3mon_note       a private line for one studio (every void and bonus writes one)
    l3mon_audit      who did what to which target, and why: one line for every admin action (added by the second migration)

Times are naive UTC, like CTFd's. The words a column may hold are enforced by the database (CHECK), so no code path can store
a state that no query knows about. The migration (migrations/) must say exactly the same; test_migration_mariadb.py proves it.
"""
import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint

from CTFd.models import db


def _utcnow():
    return datetime.datetime.utcnow()


class Channel(db.Model):
    __tablename__ = "l3mon_channel"
    __table_args__ = (
        CheckConstraint("kind in ('standard', 'sponsored')", name="ck_l3mon_channel_kind"),
        CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_channel_release"),
    )
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False)
    synopsis = db.Column(db.String(200))  # the storyline caption, plain words, shown once the channel has something on air
    accent = db.Column(db.String(32))
    picture_key = db.Column(db.String(64))
    position = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(16), nullable=False, default="standard")
    sponsor_name = db.Column(db.String(80))
    sponsor_logo = db.Column(db.String(128))
    release_state = db.Column(db.String(16), nullable=False, default="withheld")  # off air until the crew puts it on
    release_at = db.Column(db.DateTime)


DIFFICULTIES = ("warmup", "easy", "medium", "hard", "insane")  # the author kit's words (private repo, challenges/l3mon/schema.py)
DELIVERIES = ("static_per_participant", "static_shared", "live_single", "live_multi")
LIVE_DELIVERIES = ("live_single", "live_multi")


class Programme(db.Model):
    __tablename__ = "l3mon_programme"
    __table_args__ = (
        UniqueConstraint("channel_id", "cell", name="uq_l3mon_programme_cell"),
        CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_programme_release"),
        CheckConstraint("difficulty in ('warmup', 'easy', 'medium', 'hard', 'insane')", name="ck_l3mon_programme_difficulty"),
        CheckConstraint("delivery in ('static_per_participant', 'static_shared', 'live_single', 'live_multi')", name="ck_l3mon_programme_delivery"),
    )
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, unique=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("l3mon_channel.id", ondelete="CASCADE"), nullable=False)
    cell = db.Column(db.Integer, nullable=False)
    number = db.Column(db.Integer, nullable=False, unique=True)
    slug = db.Column(db.String(48), nullable=False, unique=True)
    release_state = db.Column(db.String(16), nullable=False, default="withheld")  # off air until the crew puts it on
    release_at = db.Column(db.DateTime)
    difficulty = db.Column(db.String(16), nullable=False, default="medium", server_default="medium")  # the tile and the filters show it (third revision)
    delivery = db.Column(db.String(24), nullable=False, default="static_shared", server_default="static_shared")  # how the player gets it; live ones have instances (SP4)
    story = db.Column(db.String(280), nullable=False, default="", server_default="")  # the programme's story piece (Stream E)


class Void(db.Model):
    __tablename__ = "l3mon_void"
    __table_args__ = (CheckConstraint("outcome in ('open', 'restored', 'skipped', 'superseded')", name="ck_l3mon_void_outcome"),)
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id", ondelete="SET NULL"))
    solved_at = db.Column(db.DateTime)
    voided_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    voided_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reason = db.Column(db.String(500), nullable=False)
    outcome = db.Column(db.String(16), nullable=False, default="open")
    restored_at = db.Column(db.DateTime)
    restored_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))


class Bonus(db.Model):
    __tablename__ = "l3mon_bonus"
    __table_args__ = (CheckConstraint("scope in ('team', 'member')", name="ck_l3mon_bonus_scope"),)
    id = db.Column(db.Integer, primary_key=True)
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"), nullable=False, unique=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope = db.Column(db.String(8), nullable=False)
    message = db.Column(db.String(200), nullable=False)
    given_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    given_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class ChannelBonus(db.Model):
    """The bonus a studio was given for completing a channel (plugins/l3mon_scoring/channel_bonus.py): one row per studio and channel, ever.

    The key (team_id, channel_id) is what makes a second payment impossible even when two workers finish the same channel for the same studio at the same
    moment. `state` is `awarded` while the bonus counts and `revoked` after a Revoke set aside a solve of the channel; a Restore makes the same row `awarded`
    again, with a new award. `award_id` is NULL while it is revoked."""
    __tablename__ = "l3mon_channel_bonus"
    __table_args__ = (
        UniqueConstraint("team_id", "channel_id", name="uq_l3mon_channel_bonus_team_channel"),
        CheckConstraint("state in ('awarded', 'revoked')", name="ck_l3mon_channel_bonus_state"),
    )
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    channel_id = db.Column(db.Integer, db.ForeignKey("l3mon_channel.id", ondelete="CASCADE"), nullable=False)
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="SET NULL"))
    state = db.Column(db.String(8), nullable=False, default="awarded")
    trp = db.Column(db.Integer, nullable=False)
    awarded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    revoked_at = db.Column(db.DateTime)


class Note(db.Model):
    __tablename__ = "l3mon_note"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class Audit(db.Model):
    __tablename__ = "l3mon_audit"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    actor_name = db.Column(db.String(80), nullable=False)  # kept when the account is deleted later
    action = db.Column(db.String(40), nullable=False)
    target = db.Column(db.String(100), nullable=False)
    detail = db.Column(db.String(500), nullable=False, default="")


class Activity(db.Model):
    """The activity ledger (docs/runbooks/17-activity-log.md): one row for everything a participant did, append-only and chained.

    No foreign keys on purpose: deleting an account or a studio must never change or cascade into the ledger, so the person's name is copied onto the row and
    the ids are plain numbers. The database refuses UPDATE and DELETE (triggers: created by the migration where the database user may, and by
    tools/ops/activity-triggers.sh as the database's root user where it may not: MariaDB with binary logging refuses CREATE TRIGGER to anyone without SUPER). Every
    row holds the hash of the row before it, so a changed or missing row is found by activity.verify_chain(). Written by plugins/l3mon_core/activity.py only."""

    __tablename__ = "l3mon_activity"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False)
    kind = db.Column(db.String(40), nullable=False)
    actor_id = db.Column(db.Integer)
    actor_name = db.Column(db.String(80), nullable=False)
    team_id = db.Column(db.Integer)
    target = db.Column(db.String(100), nullable=False, default="")
    detail = db.Column(db.String(1000), nullable=False, default="")
    ip = db.Column(db.String(45), nullable=False, default="")
    user_agent = db.Column(db.String(200), nullable=False, default="")
    request_id = db.Column(db.String(64), nullable=False, default="")
    prev_hash = db.Column(db.String(64), nullable=False)
    hash = db.Column(db.String(64), nullable=False)
    __table_args__ = (
        db.Index("ix_l3mon_activity_at", "at"),
        db.Index("ix_l3mon_activity_kind_at", "kind", "at"),
        db.Index("ix_l3mon_activity_team_id", "team_id", "id"),
        db.Index("ix_l3mon_activity_actor_id", "actor_id", "id"),
    )


class Removal(db.Model):
    """A captain removed a member from a studio. While `lifted_at` is empty the same person cannot join the same studio again (an old invite link would
    otherwise bring them straight back); the crew lifts it. The member's solves, hint unlocks and awards are NOT touched: the studio earned them."""

    __tablename__ = "l3mon_removal"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    removed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    removed_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    recent_activity = db.Column(db.SmallInteger, nullable=False, default=0)  # 0 or 1: the member had a solve or a hint unlock in the 24 hours before (the "lend my flag, then leave" pattern)
    lifted_at = db.Column(db.DateTime)
    lifted_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (db.Index("ix_l3mon_removal_team_user", "team_id", "user_id"), db.Index("ix_l3mon_removal_team_at", "team_id", "removed_at"))


def _append_only_triggers_for_sqlite():
    """SQLite (the tests, and development) gets the triggers with the table. MariaDB does not: see Activity."""
    from sqlalchemy import DDL, event

    for verb in ("UPDATE", "DELETE"):
        event.listen(
            Activity.__table__,
            "after_create",
            DDL(f"CREATE TRIGGER l3mon_activity_no_{verb.lower()} BEFORE {verb} ON l3mon_activity BEGIN SELECT RAISE(ABORT, 'l3mon_activity is append-only'); END").execute_if(dialect="sqlite"),
        )


_append_only_triggers_for_sqlite()
