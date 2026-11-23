"""The channel bonus: a studio that solves EVERY programme of a channel is given a fixed number of TRP, once per channel (owner, 23 Nov 2026).

What the rules are, and why

- One fixed amount for every channel, a setting of the crew's (`l3mon_channel_bonus_trp`, default 100, 0 switches it off, at most 1000). A stored value that is not a
  whole number from 0 to 1000 reads as the default: a corrupted setting must never become a free bonus.
- A channel is complete when every programme the plan puts in it is solved by the studio, on air or not (a programme still withheld has to be solved when it comes
  on air); a channel with no programme is never complete. A sponsored channel counts like any other (its TRP counts in the main ranking).
- It is a plain bonus award on the studio's captain (the first member, if the studio has no captain), so it counts everywhere TRP counts, and the studio is told in
  its private lines. The award's own text is empty, as for every bonus: nothing about a programme can leak through it.
- Once per studio and channel, EVER: the table `l3mon_channel_bonus` has the key (team_id, channel_id). The award and its record are written in one savepoint, so
  when two workers finish the same channel for the same studio at once, the loser's award goes away with its refused record and nobody is paid twice.
- It is given when the last solve is recorded: a player's flag, the crew's "mark correct", a Restore, and at the latest by the minute check (`sweep`), which is
  what gives it to solves written straight into the database and to a studio that was complete while the amount was 0.
- The award is dated when the channel was COMPLETED (the date of the studio's latest solve of one of its programmes), never when it was written: CTFd breaks a tie
  by the date of a studio's latest solve or award, so an award dated 'now' would push a studio that is paid late (the amount switched on later, the minute check, a
  Restore) behind the studios it had beaten.
- A record that says `awarded` but has lost its award (the crew deleted the award, CTFd's member removal deleted it, the captain's account was deleted: the foreign
  key clears the link) is owed again, like a revoked one.
- An automatic award writes no audit line (500 studios times several channels would bury the crew's own lines): its record row and the studio's private line are
  the record. A withdrawal does, because a crew action caused it, and so does a change of the amount.
- It is NOT taken back because the crew adds a programme to the channel later: the studio earned it. It IS taken back when a Revoke (or any deletion of a solve that
  the ORM sees) sets aside a solve of that channel and the channel is no longer complete, and a Restore gives it again (the same record, a new award).
  A deletion that bypasses the ORM (CTFd deleting a user and their solves in bulk) leaves the bonus where it is.
- The functions add to the caller's transaction and commit it unless `commit=False` (Revoke and Restore commit their own).
"""
import datetime
import logging

from flask import g, has_request_context, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from CTFd.models import Awards, Solves, Teams, Users, db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.models import Bonus, Channel, ChannelBonus, Programme
from CTFd.plugins.l3mon_core.text import plain_text
from CTFd.plugins.l3mon_scoring import notes, values
from CTFd.plugins.l3mon_scoring.errors import Refused
from CTFd.utils import get_config, set_config

_log = logging.getLogger("l3mon")

KEY = "l3mon_channel_bonus_trp"
DEFAULT_TRP = 100
MAX_TRP = 1000
CATEGORY = "bonus"  # the same category as the crew's own bonus: the Guide's "bonus" total and the crew's list already show it


class _Lost(Exception):
    """Another worker changed this record first; the savepoint is undone and this studio's channel is left to it."""


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def amount() -> int:
    """The TRP a completed channel is worth: the setting if it is a whole number from 0 to 1000, otherwise the default."""
    value = get_config(KEY)
    if value is None or isinstance(value, bool):
        return DEFAULT_TRP
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_TRP
    return number if 0 <= number <= MAX_TRP else DEFAULT_TRP


def set_amount(trp, actor=None) -> dict:
    """The crew sets the amount (0 switches the bonus off). -> {"trp", "was"}. Raises Refused, and then changes nothing."""
    if not _whole(trp) or not (0 <= trp <= MAX_TRP):
        raise Refused({"trp": [f"must be a whole number from 0 to {MAX_TRP} (0 switches the channel bonus off)"]})
    was = amount()
    set_config(KEY, str(trp))
    audit.record("scoring.channel_bonus", "channel bonus", f"{was} -> {trp} TRP per completed channel", actor=actor)
    db.session.commit()
    return {"trp": trp, "was": was}


def title_for(trp: int) -> str:
    return f"Channel bonus {trp:+d} TRP"


# ---- what is complete ----------------------------------------------------------------------------------------------------------------

def _cells(channel_ids=None) -> dict:
    """channel id -> the set of the challenge ids of all its programmes, on air or not."""
    query = db.session.query(Programme.channel_id, Programme.challenge_id)
    if channel_ids is not None:
        query = query.filter(Programme.channel_id.in_(list(channel_ids)))
    cells = {}
    for channel_id, challenge_id in query.all():
        cells.setdefault(channel_id, set()).add(challenge_id)
    return cells


def _solved(team_id) -> set:
    return {cid for (cid,) in db.session.query(Solves.challenge_id).filter(Solves.team_id == team_id).all()}


def _existing(team_id) -> dict:
    """channel id -> the studio's record for it (awarded or revoked)."""
    return {row.channel_id: row for row in ChannelBonus.query.filter_by(team_id=team_id).all()}


_GIVEN = "l3mon_channel_bonus_given_in_this_request"  # on `g`: {(studio, channel): trp} given by this request


def told(team_id, channel_id) -> int:
    """What to tell the studio in the reply to the flag that completed `channel_id`: what this very request gave it, if the end-of-request step already ran, else
    what it will be given (`owed`). The two hooks that build the reply and give the bonus run in an order Flask chooses; either way the studio is told the truth."""
    if has_request_context():
        store = g.get(_GIVEN)
        if store is not None and store["request"] is request._get_current_object():  # `g` outlives a request when the app context was pushed earlier (the tests do)
            given = store["given"].get((team_id, channel_id))
            if given:
                return given
    return owed(team_id, channel_id)


def _is_paid(row) -> bool:
    """A record counts only while it is `awarded` AND still points at its award."""
    return row is not None and row.state == "awarded" and row.award_id is not None


def owed(team_id, channel_id) -> int:
    """What the studio would be given for completing this channel right now: the amount, unless it was paid already (or the bonus is off). The reply to a flag
    uses this, so that it never promises what the platform will not give."""
    trp = amount()
    if trp <= 0:
        return 0
    row = ChannelBonus.query.filter_by(team_id=team_id, channel_id=channel_id).first()
    return 0 if _is_paid(row) else trp


def _completed_at(team_id, challenge_ids):
    """The date of the studio's latest solve of one of these programmes: when the channel was completed."""
    return db.session.query(db.func.max(Solves.date)).filter(Solves.team_id == team_id, Solves.challenge_id.in_(list(challenge_ids))).scalar()


def _recipient(team):
    """The member the award is attached to: the captain, else the studio's first member. CTFd sums a studio's awards through its members."""
    if team.captain_id:
        captain = Users.query.filter_by(id=team.captain_id).first()
        if captain is not None and captain.team_id == team.id:
            return captain
    return Users.query.filter_by(team_id=team.id).order_by(Users.id).first()


# ---- giving it -----------------------------------------------------------------------------------------------------------------------

def award(team_id, channel_ids=None, commit=True) -> list:
    """Give the studio the bonus for every complete channel it has none for (or has lost). Safe to repeat and to run at once on several workers.
    -> [{"channel_id", "trp", "award_id"}]. Never takes anything back."""
    trp = amount()
    if trp <= 0 or not _whole(team_id):
        return []
    cells = _cells(channel_ids)
    if not cells:
        return []
    solved = _solved(team_id)
    complete = sorted(channel_id for channel_id, ids in cells.items() if ids and ids <= solved)
    if not complete:
        return []
    have = _existing(team_id)
    todo = [c for c in complete if not _is_paid(have.get(c))]
    if not todo:
        return []
    team = Teams.query.filter_by(id=team_id).first()
    member = _recipient(team) if team is not None else None
    if member is None:
        return []
    channels = {c.id: c for c in Channel.query.filter(Channel.id.in_(todo)).all()}
    given = []
    for channel_id in todo:
        channel = channels.get(channel_id)
        if channel is None:
            continue
        title = title_for(trp)
        message = f"You completed {plain_text(channel.name)}: every programme in it is solved."
        row = have.get(channel_id)
        try:
            with db.session.begin_nested():
                completed = _completed_at(team.id, cells[channel_id]) or datetime.datetime.utcnow()
                award_row = Awards(user_id=member.id, team_id=team.id, name=title, description="", value=trp, category=CATEGORY, date=completed)
                db.session.add(award_row)
                db.session.flush()
                db.session.add(Bonus(award_id=award_row.id, team_id=team.id, user_id=member.id, scope="team", message=message, given_by=None))
                if row is None:
                    db.session.add(ChannelBonus(team_id=team.id, channel_id=channel_id, award_id=award_row.id, state="awarded", trp=trp))
                    db.session.flush()  # the key (team_id, channel_id): a second record for the same pair is refused here
                else:
                    table = ChannelBonus.__table__
                    claimed = db.session.execute(
                        update(table)
                        .where(table.c.id == row.id, (table.c.state == "revoked") | ((table.c.state == "awarded") & table.c.award_id.is_(None)))
                        .values(state="awarded", award_id=award_row.id, trp=trp, awarded_at=datetime.datetime.utcnow(), revoked_at=None)
                    )
                    if claimed.rowcount != 1:
                        raise _Lost()
        except (IntegrityError, _Lost):
            continue  # another worker gave it first; its award stands and ours was undone with the savepoint
        notes.tell(team.id, title, message)
        if has_request_context():
            store = g.get(_GIVEN)
            if store is None or store["request"] is not request._get_current_object():
                g.pop(_GIVEN, None)  # `g` is not a dict: no item assignment, only attributes (get, pop and setdefault)
                store = g.setdefault(_GIVEN, {"request": request._get_current_object(), "given": {}})
            store["given"][(team.id, channel_id)] = trp
        given.append({"channel_id": channel_id, "trp": trp, "award_id": award_row.id})
    if given:
        values.mark_changed()  # CTFd's standings are stale: cleared after the commit
    if commit:
        db.session.commit()
    return given


def revoke_broken(team_id, challenge_ids, commit=True) -> list:
    """After solves of these challenges were set aside for this studio: take back the bonus of every channel that holds one of them and is no longer complete.
    -> [{"channel_id", "trp"}]. A channel that is still complete, or that has no bonus, is left alone."""
    wanted = {cid for cid in (challenge_ids or []) if cid is not None}
    if not wanted:
        return []
    cells = _cells()
    channels = {channel_id for channel_id, ids in cells.items() if ids & wanted}
    if not channels:
        return []
    solved = _solved(team_id)
    have = _existing(team_id)
    taken = []
    table = ChannelBonus.__table__
    team = Teams.query.filter_by(id=team_id).first()
    for channel_id in sorted(channels):
        row = have.get(channel_id)
        if row is None or row.state != "awarded" or cells[channel_id] <= solved:
            continue
        award_id, trp = row.award_id, row.trp
        claimed = db.session.execute(
            update(table).where(table.c.id == row.id, table.c.state == "awarded").values(state="revoked", award_id=None, revoked_at=datetime.datetime.utcnow())
        )
        if claimed.rowcount != 1:
            continue  # another worker took it back first
        if award_id is not None:
            Bonus.query.filter_by(award_id=award_id).delete(synchronize_session=False)
            Awards.query.filter_by(id=award_id).delete(synchronize_session=False)
        channel = Channel.query.filter_by(id=channel_id).first()
        name = plain_text(channel.name) if channel is not None else "a channel"
        notes.tell(team_id, "Channel bonus withdrawn", f"A solve in {name} was set aside, so the channel is not complete any more and its {trp} TRP bonus is withdrawn. It comes back when the solve does.")
        audit.record("scoring.channel_bonus", team.name if team is not None else str(team_id), f"{title_for(trp)} withdrawn: a solve in {name} was set aside", system=True)
        taken.append({"channel_id": channel_id, "trp": trp})
    if taken:
        values.mark_changed()
    if commit:
        db.session.commit()
    return taken


def sweep() -> int:
    """Give every studio the bonus it is owed, in one aggregate query for the whole platform (the minute check calls this). -> how many were given."""
    if amount() <= 0:
        return 0
    sizes = {}
    for channel_id, count in db.session.query(Programme.channel_id, db.func.count(Programme.id)).group_by(Programme.channel_id).all():
        sizes[channel_id] = count
    if not sizes:
        return 0
    solved = (
        db.session.query(Solves.team_id, Programme.channel_id, db.func.count(db.func.distinct(Solves.challenge_id)))
        .join(Programme, Programme.challenge_id == Solves.challenge_id)
        .group_by(Solves.team_id, Programme.channel_id)
        .all()
    )
    owed = {}
    for team_id, channel_id, count in solved:
        if team_id is not None and count >= sizes.get(channel_id, 1 << 30):
            owed.setdefault(team_id, set()).add(channel_id)
    if not owed:
        return 0
    paid = {(t, c) for t, c in db.session.query(ChannelBonus.team_id, ChannelBonus.channel_id).filter(ChannelBonus.state == "awarded", ChannelBonus.award_id.isnot(None)).all()}
    total = 0
    for team_id in sorted(owed):
        channels = {c for c in owed[team_id] if (team_id, c) not in paid}
        if not channels:
            continue
        total += len(award(team_id, channels, commit=True))
    return total


def given_count() -> int:
    """How many channel bonuses are in force now (the crew page shows it: the automatic awards are not in the list of bonuses the crew gave)."""
    return ChannelBonus.query.filter(ChannelBonus.state == "awarded", ChannelBonus.award_id.isnot(None)).count()
