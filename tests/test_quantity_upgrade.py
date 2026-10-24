from word_guardian.store import Store


def test_upgrade_only_releases_clear_date_blocked_records_once(tmp_path, cfg):
    path = tmp_path / "legacy.sqlite3"
    old = Store(path)
    old.save_profile("family", dict(cfg, review_undated_images=True))
    old.save_profile("manual", dict(cfg, auto_accept=False))
    clear = {"new": 20, "review": 40, "confidence": 0.98}
    blurry = dict(clear, confidence=0.3)
    old.add("family", "2026-10-24", clear, "a", "image", "截图无明确日期，需人工确认", False)
    old.add("family", "2026-10-24", blurry, "b", "image", "截图无明确日期，需人工确认", False)
    old.add("manual", "2026-10-24", clear, "c", "image", "截图无明确日期，需人工确认", False)
    old.add("family", "2026-10-24", clear, "d", "image", "识别置信度不足", False)
    with old.db:
        old.db.execute("DELETE FROM migrations")  # Simulate the pre-1.0.2 database.
    old.db.close()
    upgraded = Store(path)
    rows = {row["fingerprint"]: row for row in upgraded.records()}
    assert rows["a"]["status"] == "accepted"
    assert all(rows[k]["status"] == "pending" for k in ("b", "c", "d"))
    assert (rows["a"]["day"], rows["a"]["learned"], rows["a"]["reviewed"]) == ("2026-10-24", 20, 40)
    upgraded.decide(rows["a"]["id"], "reject")
    upgraded.db.close()
    restarted = Store(path)
    assert next(r for r in restarted.records() if r["fingerprint"] == "a")["status"] == "rejected"
    restarted.db.close()
