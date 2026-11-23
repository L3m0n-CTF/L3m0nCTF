"""How long an instance lives: 15 minutes, once, and then the studio starts a new one (owner's rule of 23 Nov 2026).

Before: every challenge named its own lifetime (the samples 45 minutes), and Renew gave the full time again, up to a 3-hour ceiling, so a studio could keep one
instance for hours. The owner found 45 minutes too long and wants every instance to live only 15 minutes: when the time is up the participant starts a new
one. So now: (1) a host-wide ceiling on the lifetime (INSTANCER_MAX_TTL_MINUTES, default 15) that applies to every challenge, whatever its manifest says;
(2) renewal is off (INSTANCER_MAX_RENEWALS, default 0) and a refused renewal says so plainly with its own code, `renew_limit`; (3) an instance that ran out its
time is removed without the 60-second restart lockout that a deliberate Stop leaves, because the studio has done nothing wrong and must be able to start again
at once.
"""
import logging

import pytest

from services.instancer.lifetime import DEFAULT_MAX_RENEWALS, DEFAULT_MAX_TTL_MINUTES, RenewLimitError, read_instance_lifetime
from services.instancer.manager import InstanceManager
from services.instancer.manifest import DEFAULT_TTL_MINUTES, load_manifest_from_string
from services.instancer.tests.test_instancer_core import FLAG_EXAMPLE, MANIFEST_TOML, MockDockerClient


def manager(now, **kw):
    """`now` is a one-element list so a test can move the clock."""
    return InstanceManager(
        manifest=load_manifest_from_string(MANIFEST_TOML, dev_mode=False), docker_client=MockDockerClient(),
        instance_domain="inst.example.com", time_fn=lambda: now[0], **kw,
    )


def test_the_defaults_are_fifteen_minutes_and_no_renewal():
    assert DEFAULT_MAX_TTL_MINUTES == 15
    assert DEFAULT_MAX_RENEWALS == 0
    assert DEFAULT_TTL_MINUTES == 15, "a challenge that names no lifetime gets the ceiling, not 45 minutes"


def test_a_challenge_that_asks_for_45_minutes_gets_15():
    now = [1_700_000_000]
    mgr = manager(now)  # the manifest says 45 for demo-web
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert inst["expires_at"] == now[0] + 15 * 60


def test_a_challenge_that_asks_for_less_keeps_its_own_shorter_lifetime():
    toml = MANIFEST_TOML.replace("ttl_minutes = 45", "ttl_minutes = 5")
    now = [1_700_000_000]
    mgr = InstanceManager(manifest=load_manifest_from_string(toml, dev_mode=False), docker_client=MockDockerClient(), instance_domain="inst.example.com", time_fn=lambda: now[0])
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert inst["expires_at"] == now[0] + 5 * 60


def test_the_ceiling_can_be_changed_by_the_operator():
    now = [1_700_000_000]
    mgr = manager(now, max_ttl_minutes=30)
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert inst["expires_at"] == now[0] + 30 * 60


def test_a_challenge_that_asks_for_more_than_the_ceiling_is_logged_once_at_start(caplog):
    caplog.set_level(logging.WARNING, logger="l3mon.instancer")
    manager([1_700_000_000])
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "demo-web" in text and "45" in text and "15" in text, text


def test_renewal_is_refused_by_default_with_its_own_code_and_a_plain_message():
    now = [1_700_000_000]
    mgr = manager(now)
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    with pytest.raises(RenewLimitError) as caught:
        mgr.renew_instance(inst["id"])
    message = str(caught.value)
    assert "15 minutes" in message and "new one" in message.lower()
    # the instance is untouched: still the original expiry, no renewal counted
    assert mgr.get_instance(inst["id"])["expires_at"] == now[0] + 15 * 60 and mgr.get_instance(inst["id"])["renewals"] == 0


def test_a_renew_limit_is_a_value_error_so_that_older_callers_still_catch_it():
    assert issubclass(RenewLimitError, ValueError)


def test_renewals_can_be_allowed_by_the_operator_up_to_the_number_given():
    now = [1_700_000_000]
    mgr = manager(now, max_renewals=1)
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    now[0] += 120
    renewed = mgr.renew_instance(inst["id"])
    assert renewed["renewals"] == 1 and renewed["expires_at"] == now[0] + 15 * 60
    now[0] += 120
    with pytest.raises(RenewLimitError):
        mgr.renew_instance(inst["id"])


def test_the_three_hour_ceiling_still_holds_when_renewals_are_allowed():
    now = [1_700_000_000]
    mgr = manager(now, max_renewals=100, max_ttl_minutes=180)
    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    now[0] += 3 * 3600
    with pytest.raises(ValueError, match=r"(?i)absolute"):
        mgr.renew_instance(inst["id"])


def test_an_instance_that_ran_out_its_time_can_be_started_again_at_once():
    now = [1_700_000_000]
    mgr = manager(now)
    first, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    now[0] += 15 * 60 + 1
    assert mgr.reap_expired() == [first["id"]]
    again, created = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)  # no CooldownError
    assert created is True and again["id"] != first["id"]
    assert again["expires_at"] == now[0] + 15 * 60


def test_a_deliberate_stop_still_leaves_the_restart_cooldown():
    from services.instancer.manager import CooldownError

    now = [1_700_000_000]
    mgr = manager(now)
    first, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert mgr.stop_instance(first["id"]) is True
    with pytest.raises(CooldownError):
        mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)


# -- the settings --------------------------------------------------------------------------------------------------------------

def test_the_settings_default_and_read_the_environment():
    assert read_instance_lifetime({}) == (15, 0)
    assert read_instance_lifetime({"INSTANCER_MAX_TTL_MINUTES": "30", "INSTANCER_MAX_RENEWALS": "2"}) == (30, 2)
    assert read_instance_lifetime({"INSTANCER_MAX_TTL_MINUTES": " ", "INSTANCER_MAX_RENEWALS": ""}) == (15, 0)


@pytest.mark.parametrize("name,value", [
    ("INSTANCER_MAX_TTL_MINUTES", "0"), ("INSTANCER_MAX_TTL_MINUTES", "-5"), ("INSTANCER_MAX_TTL_MINUTES", "181"), ("INSTANCER_MAX_TTL_MINUTES", "abc"),
    ("INSTANCER_MAX_RENEWALS", "-1"), ("INSTANCER_MAX_RENEWALS", "101"), ("INSTANCER_MAX_RENEWALS", "1.5"),
])
def test_a_setting_that_makes_no_sense_stops_the_agent_at_start(name, value):
    with pytest.raises(ValueError, match=name):
        read_instance_lifetime({name: value})


@pytest.mark.parametrize("kw", [{"max_ttl_minutes": 0}, {"max_ttl_minutes": 181}, {"max_renewals": -1}])
def test_the_manager_refuses_a_ceiling_that_makes_no_sense(kw):
    with pytest.raises(ValueError):
        manager([1_700_000_000], **kw)
