"""Tests for Discord first, second, and third blood notifications (Embed format)."""
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

    # Crucial regression test for 1stth bug:
    assert blood.ordinal("1st") == "1st"
    assert blood.ordinal("2nd") == "2nd"
    assert blood.ordinal("3rd") == "3rd"
    assert blood.ordinal("4th") == "4th"
    assert blood.ordinal("35th") == "35th"
    assert blood.ordinal("1stth") == "1st"


def test_create_blood_embed():
    # First Blood - exactly matching screenshot
    fb = blood.create_blood_embed(
        blood_index=0,
        team_name="Hydra",
        challenge_name="Physics Trauma",
        rank="2nd",
        score=19215,
    )
    assert fb["title"] == "First Blood!"
    assert "🩸" not in fb["title"]
    assert "🩸" not in fb["description"]
    assert fb["description"] == "Hydra got first blood on Physics Trauma and is now in 2nd place with 19215 points!"
    assert fb["color"] == 16765503

    # Second Blood - exactly matching screenshot
    sb = blood.create_blood_embed(
        blood_index=1,
        team_name="Null Hunters",
        challenge_name="The Cicada Archives",
        rank=35,
        score=5985,
    )
    assert sb["title"] == "Second Blood!"
    assert "🩸" not in sb["title"]
    assert "🩸" not in sb["description"]
    assert sb["description"] == "Null Hunters got second blood on The Cicada Archives and is now in 35th place with 5985 points!"
    assert sb["color"] == 14866672

    # Third Blood
    tb = blood.create_blood_embed(
        blood_index=2,
        team_name="AutoBots",
        challenge_name="Echoes of the Abyss",
        rank="1st",
        score=2900,
    )
    assert tb["title"] == "Third Blood!"
    assert "🩸" not in tb["title"]
    assert "🩸" not in tb["description"]
    assert tb["description"] == "AutoBots got third blood on Echoes of the Abyss and is now in 1st place with 2900 points!"
    assert "1stth" not in tb["description"]
    assert tb["color"] == 13467442

    # 4th solve produces empty dict
    assert blood.create_blood_embed(3, "T", "C", 5, 100) == {}


def test_notify_sends_webhook_embed():
    chal = SimpleNamespace(id=1, name="Physics Trauma", value=500)
    team = SimpleNamespace(id=10, name="Hydra", hidden=False, banned=False, get_score=lambda **_: 19215, get_place=lambda **_: 2)
    solve = SimpleNamespace(id=999)

    sent = []

    def mock_send(url, payload):
        sent.append((url, payload))

    with patch.object(blood, "_send_webhook", side_effect=mock_send):
        # 1st blood (earlier_count = 0)
        blood.notify(chal, team, solve, earlier_count=0)
        assert len(sent) == 1
        payload = sent[0][1]
        assert "embeds" in payload
        assert payload["embeds"][0]["title"] == "First Blood!"
        assert "Hydra got first blood on Physics Trauma and is now in 2nd place with 19215 points!" in payload["embeds"][0]["description"]

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
    test_create_blood_embed()
    test_notify_sends_webhook_embed()
    print("ALL BLOOD TESTS PASSED!")
