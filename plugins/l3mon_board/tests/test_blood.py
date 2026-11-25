"""Tests for Discord first, second, and third blood notifications."""
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

try:
    from CTFd.plugins.l3mon_board import blood
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import blood


def test_ordinal():
    assert blood.ordinal(1) == "1st"
    assert blood.ordinal(2) == "2nd"
    assert blood.ordinal(3) == "3rd"
    assert blood.ordinal(4) == "4th"
    assert blood.ordinal(11) == "11th"
    assert blood.ordinal(12) == "12th"
    assert blood.ordinal(13) == "13th"
    assert blood.ordinal(21) == "21st"
    assert blood.ordinal(22) == "22nd"
    assert blood.ordinal(23) == "23rd"
    assert blood.ordinal(34) == "34th"
    assert blood.ordinal(45) == "45th"
    assert blood.ordinal(101) == "101st"


def test_format_blood_message():
    # First Blood
    fb = blood.format_blood_message(
        blood_index=0,
        team_name="5n1ck3rD00dl3",
        challenge_name="Echoes of the Abyss",
        rank=34,
        score=6100,
        challenge_value=500,
    )
    assert "🩸 First Blood" in fb
    assert "First Blood claimed!" in fb
    assert "Congratulations to 5n1ck3rD00dl3 for being the first to solve Echoes of the Abyss!" in fb
    assert "They now hold 34th place on the leaderboard with 6,100 points." in fb

    # Second Blood
    sb = blood.format_blood_message(
        blood_index=1,
        team_name="Bro_Code",
        challenge_name="Shattered Echoes",
        rank=4,
        score=16965,
        challenge_value=300,
    )
    assert "🩸 Second Blood" in sb
    assert ("Bro_Code" in sb) and ("Shattered Echoes" in sb)
    assert ("4th place" in sb) and ("16,965 points" in sb)

    # Third Blood
    tb = blood.format_blood_message(
        blood_index=2,
        team_name="AutoBots",
        challenge_name="Echoes of the Abyss",
        rank=45,
        score=2900,
        challenge_value=200,
    )
    assert "🩸 Third Blood" in tb
    assert "Third Blood claimed!" in tb
    assert "The race continues! AutoBots was the third team to solve Echoes of the Abyss, earning 200 points and securing 45th place on the leaderboard." in tb

    # 4th solve produces empty
    assert blood.format_blood_message(3, "T", "C", 5, 100, 100) == ""


def test_notify_sends_webhook_for_first_second_third_blood():
    chal = SimpleNamespace(id=1, name="Echoes of the Abyss", value=500)
    team = SimpleNamespace(id=10, name="5n1ck3rD00dl3", hidden=False, banned=False, get_score=lambda **_: 6100, get_place=lambda **_: 34)
    solve = SimpleNamespace(id=999)

    sent = []

    def mock_send(url, content):
        sent.append((url, content))

    with patch.object(blood, "_send_webhook", side_effect=mock_send):
        # 1st blood (earlier_count = 0)
        blood.notify(chal, team, solve, earlier_count=0)
        assert len(sent) == 1
        assert "🩸 First Blood" in sent[0][1]

        # Duplicate solve ID should not send again
        blood.notify(chal, team, solve, earlier_count=0)
        assert len(sent) == 1

        # 4th solve (earlier_count = 3) should not send
        solve4 = SimpleNamespace(id=1000)
        blood.notify(chal, team, solve4, earlier_count=3)
        assert len(sent) == 1

        # Hidden team should not send
        hidden_team = SimpleNamespace(id=11, name="Admin", hidden=True, banned=False, get_score=lambda **_: 0, get_place=lambda **_: 1)
        solve_hidden = SimpleNamespace(id=1001)
        blood.notify(chal, hidden_team, solve_hidden, earlier_count=0)
        assert len(sent) == 1


if __name__ == "__main__":
    test_ordinal()
    test_format_blood_message()
    test_notify_sends_webhook_for_first_second_third_blood()
    print("ALL BLOOD TESTS PASSED!")
