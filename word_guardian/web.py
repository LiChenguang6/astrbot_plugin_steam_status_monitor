import asyncio
import csv
import io
from datetime import date

from astrbot.api.web import error_response, json_response, request

from .calendar import CalendarUnavailable, required
from .config import DEFAULTS
from .service import local_now, same_bot, status


class WebAPI:
    def __init__(self, plugin):
        self.plugin = plugin

    def register(self, context):
        endpoints = [
            ("GET", "snapshot", self.snapshot),
            ("POST", "profile", self.profile),
            ("POST", "profile/delete", self.delete_profile),
            ("POST", "review", self.review),
            ("POST", "manual", self.manual),
            ("POST", "test-send", self.test_send),
            ("GET", "export", self.export),
        ]
        for method, path, handler in endpoints:

            async def guarded(handler=handler):
                # The Page bridge supplies the authenticated dashboard identity, never a query password.
                if not request.username:
                    return error_response("请从已登录的 AstrBot 插件面板访问", status_code=401)
                try:
                    return await handler()
                except (ValueError, KeyError, TypeError) as exc:
                    return error_response(str(exc)[:200], status_code=400)
                except Exception:
                    return error_response("操作失败，请检查机器人连接、模型或主动消息权限", status_code=503)

            context.register_web_api(f"/qq_word_guardian/{path}", guarded, [method], "单词守护管理接口")

    async def snapshot(self):
        store = self.plugin.store
        plans = []
        for ident, cfg in store.profiles().items():
            row = {"id": ident, "config": cfg, "totals": store.totals(ident)}
            try:
                row["today"] = status(store, ident, cfg)
                row["calendar_error"] = ""
                now = local_now(cfg)
                row["local_date"] = now.date().isoformat()
                row["calendar"] = []
                import calendar

                for n in range(1, calendar.monthrange(now.year, now.month)[1] + 1):
                    day = date(now.year, now.month, n)
                    row["calendar"].append({"day": day.isoformat(), "required": required(day, cfg)})
            except CalendarUnavailable as exc:
                row["calendar_error"] = str(exc)
            row["error"] = self.plugin.errors.get(ident) or self.plugin.scheduler.errors.get(ident, "")
            plans.append(row)
        providers = [{"id": p.meta().id} for p in self.plugin.context.get_all_providers()]
        platforms = [
            {"id": p.meta().id, "type": p.meta().name} for p in self.plugin.context.platform_manager.platform_insts
        ]
        audit = [dict(r) for r in store.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
        return json_response(
            {
                "profiles": plans,
                "archived": store.archived(),
                "reserved_ids": sorted(store.reserved_ids()),
                "records": store.records(limit=500),
                "audit": audit,
                "defaults": DEFAULTS,
                "providers": providers,
                "platforms": platforms,
                "official_notice": "官方QQ通常只接收@机器人消息；定时提醒需官方主动群消息权限/额度。AppID/AppSecret在AstrBot机器人设置配置，插件不接管扫码登录或其他插件。",
            }
        )

    async def profile(self):
        body = await request.json()
        ident, cfg = body["id"], body["config"]
        async with self.plugin.locks.setdefault(ident, asyncio.Lock()):
            if body.get("create_only") is True and ident in self.plugin.store.profiles():
                raise ValueError("该计划ID已存在，请换一个ID新建；不会覆盖已有计划")
            # Recheck after acquiring the lock; another request may have saved while waiting.
            platform = self.plugin.context.get_platform_inst(cfg.get("platform_id", ""))
            official = platform is not None and platform.meta().name in (
                "qq_official",
                "qq_official_webhook",
                "qqofficial",
            )
            for other_id, other in self.plugin.store.profiles().items():
                if other_id == ident or not other["enabled"] or not cfg.get("enabled"):
                    continue
                if (
                    all(other[k] == cfg.get(k) for k in ("platform_id", "group_id"))
                    and same_bot(other["bot_id"], cfg.get("bot_id", ""), official=official)
                    and set(other["user_ids"]) & set(cfg.get("user_ids", []))
                ):
                    raise ValueError("同一机器人/群聊/成员只能启用一个计划")
            saved = self.plugin.store.save_profile(ident, cfg)
        return json_response({"config": saved})

    async def review(self):
        body = await request.json()
        self.plugin.store.decide(int(body["id"]), body["action"], body.get("new"), body.get("review"))
        return json_response({"ok": True})

    async def delete_profile(self):
        body = await request.json()
        ident = body["id"]
        if body.get("confirm_id") != ident:
            raise ValueError("请输入完整计划ID确认删除")
        purge = body.get("purge_history", False)
        if type(purge) is not bool:
            raise ValueError("删除历史开关必须为布尔值")
        async with self.plugin.locks.setdefault(ident, asyncio.Lock()):
            self.plugin.store.delete_profile(ident, purge_history=purge)
            self.plugin.last_attempt.pop(ident, None)
            self.plugin.errors.pop(ident, None)
            self.plugin.scheduler.errors.pop(ident, None)
        return json_response({"ok": True, "history_retained": not purge})

    async def manual(self):
        body = await request.json()
        ident = body["profile"]
        cfg = self.plugin.store.profiles()[ident]
        day = date.fromisoformat(body["day"])
        now = local_now(cfg).date()
        if not date.fromisoformat(cfg["start_date"] or now.isoformat()) <= day <= now:
            raise ValueError("仅允许补录计划开始日至今天的记录")
        n, r = body["new"], body["review"]
        if type(n) is not int or type(r) is not int or not 0 <= n <= 2000 or not 0 <= r <= 4000:
            raise ValueError("补录数量超出范围")
        # A repeat click is idempotent; corrected lower values require rejecting the old record.
        fingerprint = f"manual:{day}:{n}:{r}"
        result = {"new": n, "review": r, "confidence": 1}
        existing = self.plugin.store.db.execute(
            "SELECT id, status, learned, reviewed FROM records WHERE profile=? AND fingerprint=?", (ident, fingerprint)
        ).fetchone()
        if existing:
            record_id = existing["id"]
            if existing["status"] != "accepted" or existing["learned"] != n or existing["reviewed"] != r:
                self.plugin.store.decide(record_id, "accept", n, r)
        else:
            record_id = self.plugin.store.add(
                ident, day.isoformat(), result, fingerprint, "manual", "管理员手工确认的当日累计量", True
            )
        with self.plugin.store.db:
            self.plugin.store.audit("manual", ident, f"record={record_id}, day={day}, new={n}, review={r}")
        return json_response({"id": record_id})

    async def test_send(self):
        body = await request.json()
        ident = body["profile"]
        cfg = self.plugin.store.profiles()[ident]
        if not cfg["enabled"]:
            raise ValueError("计划未启用，请先在目标群完成绑定")
        day = local_now(cfg).date().isoformat()
        if self.plugin.store.claimed(ident, day, "test"):
            raise ValueError("每天最多发一次测试消息，避免打扰")
        async with self.plugin.locks.setdefault(ident, asyncio.Lock()):
            if self.plugin.store.claimed(ident, day, "test"):
                raise ValueError("今天已发送测试消息")
            await self.plugin.send_bound(cfg, "单词守护连接测试成功。这里只会发送本计划的学习提醒。")
            self.plugin.store.mark_sent(ident, day, "test")
        return json_response({"ok": True})

    async def export(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["记录ID", "计划", "日期", "新学", "复习", "状态", "来源", "置信度"])
        # Do not export model free text, message bodies, image links or credentials.
        for r in self.plugin.store.records(limit=10000):
            ident = r["profile"]
            if ident.startswith(("=", "+", "-", "@")):
                ident = "'" + ident
            writer.writerow(
                [r["id"], ident, r["day"], r["learned"], r["reviewed"], r["status"], r["source"], r["confidence"]]
            )
        return json_response({"filename": "word-guardian-records.csv", "csv": "\ufeff" + output.getvalue()})
