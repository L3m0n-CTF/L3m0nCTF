"""A submitted flag is not written to the application log (see log_redaction.py).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import logging

import pytest

from CTFd.plugins.l3mon_core.log_redaction import redact
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, login_as_user, register_user

FLAG = "wombat-the-real-one-9f3a"


@pytest.mark.parametrize("tag", ["CORRECT", "WRONG", "TOO FAST", "ALREADY SOLVED"])
def test_every_kind_of_attempt_line_loses_the_text_and_keeps_the_rest(tag):
    line = f"[11/15/2026 10:02:11] Ravi submitted b'{FLAG}' on 7 with kpm 1 [{tag}]"
    assert redact(line) == f"[11/15/2026 10:02:11] Ravi submitted [not logged] on 7 with kpm 1 [{tag}]"


def test_quotes_and_the_word_on_inside_the_answer_do_not_end_the_redaction_early():
    line = "[d] Ravi submitted b\"it's on 5 with kpm 9 wombat{x}\" on 7 with kpm 1 [WRONG]"
    out = redact(line)
    assert "wombat" not in out and "it's" not in out
    assert out.endswith("on 7 with kpm 1 [WRONG]")


def test_a_line_that_is_not_an_attempt_is_untouched():
    for line in ("[d] Ravi logged in", "something else entirely", "[d] Ravi submitted nothing"):
        assert redact(line) == line


class Collect(logging.Handler):
    """CTFd's submissions logger does not pass its records on to the root logger, so pytest's caplog cannot see them: a handler on
    the logger itself collects exactly what its own handlers (the output, the file) receive."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def test_a_real_attempt_leaves_no_flag_in_the_log():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        from CTFd.models import db

        challenge = gen_challenge(db, value=100)
        challenge_id = challenge.id
        gen_flag(db, challenge_id, content=FLAG)
        register_user(app, name="ravi", email="ravi@example.com")
        client = login_as_user(app, "ravi")
        collected = Collect()
        logger = logging.getLogger("submissions")
        logger.addHandler(collected)
        try:
            wrong = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": "wombat-not-it-1234"})
            right = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": FLAG})
        finally:
            logger.removeHandler(collected)
        assert wrong.status_code == 200 and right.status_code == 200
        text = " | ".join(collected.lines)
        assert "[WRONG]" in text and "[CORRECT]" in text and "ravi submitted" in text  # the lines are still written
        assert "the-real-one" not in text and "not-it-1234" not in text
    destroy_ctfd(app)
