"""Add the activity ledger and the removal records (l3mon_activity, l3mon_removal)

Revision ID: e9a4b2c5f306
Revises: d8f3a1b4e205
Create Date: 2026-11-20

The owner's rule of 19 November 2026: everything a participant does is logged, and a captain can remove a member. Two additive tables, nothing existing is
touched. Safe to run again and safe when two web workers run it at the same moment (the tables are checked first, and "already exists" from a worker that
got there between the check and the create is accepted as long as the table is there afterwards).

THE TRIGGERS. l3mon_activity must refuse UPDATE and DELETE. On MariaDB with binary logging switched on (production is), CREATE TRIGGER is refused to a user
without the SUPER privilege (ERROR 1419), and the platform's database user has none. So this migration TRIES to create them and, when the database says no,
logs a warning and carries on: the platform must never fail to start because of it. The triggers are then created as the database's root user by
tools/ops/activity-triggers.sh (idempotent; run at every deploy), and activity.triggers_installed() says whether they are there. The ledger is chained and echoed to
the log either way; the triggers are the third layer. models.py must say exactly the same tables (tests/test_migration_mariadb.py).

Rolling back is manual (the runner only goes forward): run `downgrade` of this file, then set the setting `l3mon_core_alembic_version` back to the previous
revision (d8f3a1b4e205). Dropping the ledger drops its triggers with it.
"""
import logging

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "e9a4b2c5f306"
down_revision = "d8f3a1b4e205"
branch_labels = None
depends_on = None

_log = logging.getLogger("l3mon")

MARIADB_TRIGGERS = [
    f"CREATE TRIGGER IF NOT EXISTS l3mon_activity_no_{verb.lower()} BEFORE {verb} ON l3mon_activity FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'l3mon_activity is append-only'"
    for verb in ("UPDATE", "DELETE")
]


def _create(op, table, *items):
    if table in set(get_all_tables(op)):
        return
    try:
        op.create_table(table, *items)
    except (OperationalError, ProgrammingError):
        if table not in get_all_tables(op):
            raise


def upgrade(op=None):
    _create(
        op,
        "l3mon_activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=80), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("target", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=200), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("ix_l3mon_activity_at", "at"),
        sa.Index("ix_l3mon_activity_kind_at", "kind", "at"),
        sa.Index("ix_l3mon_activity_team_id", "team_id", "id"),
        sa.Index("ix_l3mon_activity_actor_id", "actor_id", "id"),
    )
    _create(
        op,
        "l3mon_removal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("removed_by", sa.Integer(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=False),
        sa.Column("recent_activity", sa.SmallInteger(), nullable=False),
        sa.Column("lifted_at", sa.DateTime(), nullable=True),
        sa.Column("lifted_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["removed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lifted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("ix_l3mon_removal_team_user", "team_id", "user_id"),
        sa.Index("ix_l3mon_removal_team_at", "team_id", "removed_at"),
    )
    _try_the_triggers(op)


def _try_the_triggers(op):
    """MariaDB only (SQLite gets them with the table in models.py). A refusal is a warning, never a failure."""
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for statement in MARIADB_TRIGGERS:
        try:
            op.execute(statement)
        except DBAPIError as err:
            _log.warning(
                "l3mon: the append-only triggers on l3mon_activity were NOT created by the migration (%s). Run tools/ops/activity-triggers.sh as the database's root user; "
                "until then the ledger is chained and logged but the database does not refuse a change.",
                str(getattr(err, "orig", err))[:200],
            )
            return


def downgrade(op=None):
    for table in ("l3mon_removal", "l3mon_activity"):
        if table in set(get_all_tables(op)):
            op.drop_table(table)
