"""Local SQLite ledger. No screenshots, model prompts, or image URLs are persisted."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import validate


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(
          id INTEGER PRIMARY KEY, profile TEXT NOT NULL, day TEXT NOT NULL,
          learned INTEGER NOT NULL, reviewed INTEGER NOT NULL,
          status TEXT NOT NULL, confidence REAL NOT NULL, source TEXT NOT NULL,
          reason TEXT NOT NULL, fingerprint TEXT NOT NULL, created TEXT NOT NULL,
          UNIQUE(profile, fingerprint));
        CREATE TABLE IF NOT EXISTS notifications(
          profile TEXT, day TEXT, phase TEXT, sent TEXT,
          PRIMARY KEY(profile,day,phase));
        CREATE TABLE IF NOT EXISTS evidence(
          profile TEXT, digest TEXT, record_id INTEGER,
          PRIMARY KEY(profile,digest));
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY, created TEXT, action TEXT, profile TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS archived_profiles(id TEXT PRIMARY KEY, config TEXT NOT NULL, deleted TEXT NOT NULL);
        """)
        self.db.commit()
        self.upgrade_quantity_review()

    def upgrade_quantity_review(self):
        """Release only date-blocked evidence already meeting the quantity threshold."""
        marker = "quantity_review_1.0.2"
        if self.db.execute("SELECT 1 FROM migrations WHERE name=?", (marker,)).fetchone():
            return
        date_reasons = ("截图无明确日期，需人工确认", "图片日期与今天不一致，需管理员确认后按今日计入")
        with self.db:
            for ident, cfg in self.profiles().items():
                if not cfg.get("auto_accept", True):
                    continue
                updated = self.db.execute(
                    "UPDATE records SET status='accepted', reason=? WHERE profile=? AND status='pending' "
                    "AND source='image' AND confidence>=? AND reason IN (?,?)",
                    (
                        "升级：已完成数量清晰，取消仅因日期产生的审核",
                        ident,
                        cfg.get("min_confidence", 0.9),
                        *date_reasons,
                    ),
                ).rowcount
                if updated:
                    self.audit("quantity_review_upgrade", ident, f"accepted={updated}; original dates/counts preserved")
            self.db.execute("INSERT INTO migrations VALUES(?)", (marker,))

    def profiles(self):
        return {r["id"]: json.loads(r["config"]) for r in self.db.execute("SELECT * FROM profiles ORDER BY id")}

    def archived(self):
        return [
            {"id": r["id"], "name": json.loads(r["config"])["name"], "deleted": r["deleted"]}
            for r in self.db.execute("SELECT * FROM archived_profiles ORDER BY deleted DESC")
        ]

    def reserved_ids(self):
        return {r["id"] for r in self.db.execute("SELECT id FROM profiles UNION SELECT id FROM archived_profiles")}

    def delete_profile(self, ident, *, purge_history=False):
        row = self.db.execute("SELECT config FROM profiles WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("计划不存在或已经删除")
        with self.db:
            self.db.execute(
                "INSERT INTO archived_profiles VALUES(?,?,?)",
                (ident, row["config"], datetime.now(timezone.utc).isoformat()),
            )
            self.db.execute("DELETE FROM profiles WHERE id=?", (ident,))
            if purge_history:
                self.db.execute("DELETE FROM records WHERE profile=?", (ident,))
                self.db.execute("DELETE FROM evidence WHERE profile=?", (ident,))
                self.db.execute("DELETE FROM notifications WHERE profile=?", (ident,))
                self.db.execute("DELETE FROM audit WHERE profile=?", (ident,))
            self.audit("profile_deleted", ident, "history purged" if purge_history else "history retained")

    def save_profile(self, ident, raw):
        if (
            not isinstance(ident, str)
            or not ident
            or len(ident) > 64
            or not all(c.isalnum() or c in "-_" for c in ident)
        ):
            raise ValueError("计划ID仅支持字母、数字、横线和下划线，最多64字")
        cfg = validate(raw)
        if self.db.execute("SELECT 1 FROM archived_profiles WHERE id=?", (ident,)).fetchone():
            raise ValueError("该计划ID已归档，请使用新ID，避免混入旧记录")
        previous = self.profiles().get(ident)
        # Goals/calendar are historical accounting inputs. Prevent silent retroactive rewrites.
        historical = (
            "start_date",
            "timezone",
            "new_goal",
            "review_goal",
            "weekdays",
            "statutory_holidays",
            "exclude_workdays",
            "platform_id",
            "bot_id",
            "group_id",
            "user_ids",
        )
        if previous and self.db.execute("SELECT 1 FROM records WHERE profile=? LIMIT 1", (ident,)).fetchone():
            if any(previous[k] != cfg[k] for k in historical):
                raise ValueError("此计划已有记录，目标、日历规则及身份变更请新建计划并停用旧计划，避免改写历史")
        with self.db:
            self.db.execute(
                "INSERT INTO profiles VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (ident, json.dumps(cfg, ensure_ascii=False)),
            )
            self.audit("profile_saved", ident, "settings updated")
        return cfg

    def audit(self, action, profile, detail):
        self.db.execute(
            "INSERT INTO audit(created,action,profile,detail) VALUES(?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), action, profile, detail[:200]),
        )

    def totals(self, profile):
        # A screenshot is a daily cumulative snapshot; repeated screenshots cannot add words.
        rows = self.db.execute(
            "SELECT day,MAX(learned) n,MAX(reviewed) r FROM records WHERE profile=? AND status='accepted' GROUP BY day",
            (profile,),
        )
        return {r["day"]: {"new": r["n"], "review": r["r"]} for r in rows}

    def records(self, profile=None, limit=200):
        if profile:
            rows = self.db.execute("SELECT * FROM records WHERE profile=? ORDER BY id DESC LIMIT ?", (profile, limit))
        else:
            rows = self.db.execute("SELECT * FROM records ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def add(self, profile, day, result, fingerprint, source, reason, accepted, image_hashes=()):
        try:
            with self.db:
                cur = self.db.execute(
                    "INSERT INTO records(profile,day,learned,reviewed,status,confidence,source,reason,fingerprint,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile,
                        day,
                        result["new"],
                        result["review"],
                        "accepted" if accepted else "pending",
                        result["confidence"],
                        source,
                        reason[:200],
                        fingerprint,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                for image_hash in set(image_hashes):
                    self.db.execute("INSERT INTO evidence VALUES(?,?,?)", (profile, image_hash, cur.lastrowid))
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def decide(self, ident, action, new=None, review=None):
        row = self.db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("记录不存在")
        if action not in ("accept", "reject"):
            raise ValueError("审核操作必须为 accept 或 reject")
        n = row["learned"] if new is None else new
        r = row["reviewed"] if review is None else review
        if type(n) is not int or type(r) is not int or not 0 <= n <= 2000 or not 0 <= r <= 4000:
            raise ValueError("审核数量超出范围")
        with self.db:
            self.db.execute(
                "UPDATE records SET status=?, learned=?, reviewed=? WHERE id=?",
                ("accepted" if action == "accept" else "rejected", n, r, ident),
            )
            self.audit("review_" + action, row["profile"], f"record={ident}, new={n}, review={r}")

    def claimed(self, profile, day, phase):
        return (
            self.db.execute(
                "SELECT 1 FROM notifications WHERE profile=? AND day=? AND phase=?", (profile, day, phase)
            ).fetchone()
            is not None
        )

    def mark_sent(self, profile, day, phase):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO notifications VALUES(?,?,?,?)",
                (profile, day, phase, datetime.now(timezone.utc).isoformat()),
            )

    def prune(self, cfgs):
        # Prune only settled history outside BOTH retention and the accounting window.
        with self.db:
            for ident, cfg in cfgs.items():
                days = max(cfg["retention_days"], cfg["carry_limit_days"] + 2)
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
                self.db.execute("DELETE FROM records WHERE profile=? AND day<? AND status!='pending'", (ident, cutoff))
                self.db.execute("DELETE FROM notifications WHERE profile=? AND day<?", (ident, cutoff))
            self.db.execute("DELETE FROM evidence WHERE record_id NOT IN (SELECT id FROM records)")
            self.db.execute(
                "DELETE FROM audit WHERE created<?", ((datetime.now(timezone.utc) - timedelta(days=730)).isoformat(),)
            )
