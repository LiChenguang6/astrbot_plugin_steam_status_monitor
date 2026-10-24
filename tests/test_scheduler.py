import asyncio
from datetime import datetime

import pytest

from word_guardian.service import Scheduler, due_phases, record, status


def stamp(day="2026-10-24", t="08:00"):
    return datetime.fromisoformat(f"{day}T{t}:00+08:00")


@pytest.mark.parametrize(
    "at,phase",
    [
        ("07:59", []),
        ("08:00", ["morning"]),
        ("08:14", ["morning"]),
        ("08:15", []),
        ("21:30", ["evening"]),
        ("22:00", []),
        ("22:31", []),
    ],
)
def test_due_windows(cfg, store, at, phase):
    assert due_phases(cfg, status(store, "p", cfg, stamp(t=at)), stamp(t=at)) == phase


async def test_restart_dedup_and_evening_only_if_incomplete(store, cfg):
    store.save_profile("p", cfg)
    sent = []

    async def send(c, t):
        sent.append((c["session"], t))

    scheduler = Scheduler(store, send, {})
    await scheduler.tick(stamp())
    await Scheduler(store, send, {}).tick(stamp(t="08:02"))
    assert len(sent) == 1 and "20" in sent[0][1]
    r = dict(relevant=True, new=20, review=40, confidence=1, date="2026-10-24", reason="完成")
    record(store, "p", cfg, r, "a", now=stamp(t="18:00"))
    await scheduler.tick(stamp(t="21:30"))
    assert len(sent) == 1


async def test_sunday_debt_message_and_no_friday_reminder(store, cfg):
    store.save_profile("p", cfg)
    sent = []

    async def send(c, t):
        sent.append(t)

    scheduler = Scheduler(store, send, {})
    await scheduler.tick(stamp(day="2026-10-23"))
    assert not sent
    await scheduler.tick(stamp(day="2026-10-25"))
    assert "新学 40" in sent[0] and "复习 80" in sent[0]


async def test_failed_send_is_not_marked_and_can_retry(store, cfg):
    store.save_profile("p", cfg)
    calls = []

    async def send(c, t):
        calls.append(t)
        if len(calls) == 1:
            raise RuntimeError("private remote detail must not leak")

    scheduler = Scheduler(store, send, {})
    await scheduler.tick(stamp())
    assert not store.claimed("p", "2026-10-24", "morning")
    assert "private" not in scheduler.errors["p"]
    await scheduler.tick(stamp(t="08:01"))
    assert store.claimed("p", "2026-10-24", "morning")
    assert not scheduler.errors


async def test_concurrent_ticks_do_not_duplicate(store, cfg):
    store.save_profile("p", cfg)
    sent = []

    async def send(c, t):
        await asyncio.sleep(0.01)
        sent.append(t)

    scheduler = Scheduler(store, send, {})
    await asyncio.gather(scheduler.tick(stamp()), scheduler.tick(stamp()))
    assert len(sent) == 1


async def test_unknown_year_pauses_and_reports_calendar(store, cfg):
    cfg["start_date"] = "2099-01-01"
    store.save_profile("p", cfg)

    async def send(c, t):
        raise AssertionError("must not send")

    scheduler = Scheduler(store, send, {})
    await scheduler.tick(stamp(day="2099-01-01"))
    assert "CalendarUnavailable" in scheduler.errors["p"]
