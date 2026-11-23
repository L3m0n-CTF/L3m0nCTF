"""Add the channel bonus table (l3mon_channel_bonus)

Revision ID: f1b6c3d7a407
Revises: e9a4b2c5f306
Create Date: 2026-11-23

A studio that solves every programme of a channel is given a fixed number of TRP, once per channel (plugins/l3mon_scoring/channel_bonus.py). This table is the record
of it: one row per studio and channel, EVER (the unique key (team_id, channel_id) is what makes a second payment impossible when two workers finish the same channel
for the same studio at the same moment), with the state `awarded` or `revoked` (a Revoke set aside a solve of the channel; a Restore makes the same row `awarded`
again). `award_id` points at the plain bonus award that carries the TRP and is NULL while the bonus is revoked.

Run by CTFd's plugin migration runner when the plugin loads, on MariaDB (SQLite uses create_all). Like the earlier revisions it is safe to run again and safe when
two web workers run it at the same moment: the table is checked first, and "already exists" from a worker that got there between the check and the create is
accepted as long as the table is there afterwards. models.py must say exactly the same (tests/test_migration_mariadb.py).

Rolling back is manual (the runner only goes forward): run `downgrade` of this file, then set the setting `l3mon_core_alembic_version` back to the previous
revision (e9a4b2c5f306). The awards the bonus created stay in CTFd's own `awards` table; the downgrade removes only the record.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "f1b6c3d7a407"
down_revision = "e9a4b2c5f306"
branch_labels = None
depends_on = None

TABLE = "l3mon_channel_bonus"


def upgrade(op=None):
    if TABLE in set(get_all_tables(op)):
        return
    try:
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("channel_id", sa.Integer(), nullable=False),
            sa.Column("award_id", sa.Integer(), nullable=True),
            sa.Column("state", sa.String(length=8), nullable=False),
            sa.Column("trp", sa.Integer(), nullable=False),
            sa.Column("awarded_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("state in ('awarded', 'revoked')", name="ck_l3mon_channel_bonus_state"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["channel_id"], ["l3mon_channel.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["award_id"], ["awards.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("team_id", "channel_id", name="uq_l3mon_channel_bonus_team_channel"),
        )
    except (OperationalError, ProgrammingError):
        if TABLE not in get_all_tables(op):
            raise


def downgrade(op=None):
    if TABLE in set(get_all_tables(op)):
        op.drop_table(TABLE)
