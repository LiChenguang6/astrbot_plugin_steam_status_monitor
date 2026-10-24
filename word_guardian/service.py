import asyncio
import hashlib
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .calendar import CalendarUnavailable, balance


def local_now(cfg, now=None):
    value = now or datetime.now(ZoneInfo(cfg["timezone"]))
    if value.tzinfo is None:
        raise ValueError("时钟必须带时区")
    return value.astimezone(ZoneInfo(cfg["timezone"]))


def matches(cfg, *, platform, bot, group, user):
    return cfg["enabled"] and all(
        (
            cfg["platform_id"] == str(platform),
            cfg["bot_id"] == str(bot),
            cfg["group_id"] == str(group),
            str(user) in cfg["user_ids"],
        )
    )


def digest(profile, day, text, image_hashes):
    # Images deduplicate across days; text check-ins are intentionally per day.
    payload = "images:" + ":".join(sorted(image_hashes)) if image_hashes else f"text:{day}:{text.strip()}"
    return hashlib.sha256((profile + ":" + payload).encode()).hexdigest()


def status(store, ident, cfg, now=None):
    today = local_now(cfg, now).date()
    return balance(today, cfg, store.totals(ident))


def record(store, ident, cfg, result, fingerprint, *, image=False, image_hashes=(), now=None):
    now = local_now(cfg, now)
    day = now.date().isoformat()
    if not result["relevant"] or (result["new"] == 0 and result["review"] == 0):
        return {"kind": "irrelevant"}
    reasons = []
    if not cfg["auto_accept"]:
        reasons.append("已启用人工审核")
    if result["confidence"] < cfg["min_confidence"]:
        reasons.append("识别置信度不足")
    if result["date"] and result["date"] != day:
        reasons.append("图片日期与今天不一致，需管理员确认后按今日计入")
    if image and not result["date"] and cfg["review_undated_images"]:
        reasons.append("截图无明确日期，需人工确认")
    accepted = not reasons
    reason = "; ".join(reasons) or ("图片识别通过置信度与日期校验" if image else "本人文字打卡声明（未验证图片）")
    ident_record = store.add(
        ident, day, result, fingerprint, "image" if image else "text", reason, accepted, image_hashes
    )
    if ident_record is None:
        return {"kind": "duplicate"}
    return {
        "kind": "accepted" if accepted else "pending",
        "id": ident_record,
        "late": now.time() >= time.fromisoformat(cfg["late"]),
        "reasons": reasons,
    }


def feedback(cfg, state, outcome):
    if outcome["kind"] == "duplicate":
        return "这份打卡已经记录过啦，不会重复累计。"
    if outcome["kind"] == "pending":
        text = f"收到学习打卡，已放入待审核（#{outcome['id']}），确认后再计入，避免记错。"
    else:
        text = f"已记录今日累计：新学{state['done_new']}个、复习{state['done_review']}个。"
        if not state["remaining_new"] and not state["remaining_review"]:
            text += "今天的目标完成啦，认真坚持很棒！"
        else:
            text += f"本次目标还差新学{state['remaining_new']}个、复习{state['remaining_review']}个。一步一步来就好。"
    if outcome.get("late"):
        text += "\n" + cfg["late_template"]
    return text


def due_phases(cfg, state, now):
    now = local_now(cfg, now)
    if not cfg["enabled"] or not state["required"]:
        return []
    if not state["remaining_new"] and not state["remaining_review"]:
        return []
    minute = now.hour * 60 + now.minute
    phases = []
    for key in ("morning", "evening"):
        h, m = map(int, cfg[key].split(":"))
        if 0 <= minute - (h * 60 + m) < cfg["reminder_grace_minutes"] and (key != "evening" or now.hour < 22):
            phases.append(key)
    return phases


class Scheduler:
    def __init__(self, store, send, locks):
        self.store, self.send, self.locks = store, send, locks
        self.errors = {}

    async def tick(self, now=None):
        for ident, cfg in self.store.profiles().items():
            if not cfg["enabled"]:
                continue
            async with self.locks.setdefault(ident, asyncio.Lock()):
                try:
                    stamp = local_now(cfg, now)
                    state = status(self.store, ident, cfg, stamp)
                    for phase in due_phases(cfg, state, stamp):
                        day = stamp.date().isoformat()
                        if self.store.claimed(ident, day, phase):
                            continue
                        # Only two documented placeholders; arbitrary format strings cannot introspect objects.
                        text = (
                            cfg[phase + "_template"]
                            .replace("{new}", str(state["remaining_new"]))
                            .replace("{review}", str(state["remaining_review"]))
                        )
                        await self.send(cfg, text)
                        self.store.mark_sent(ident, day, phase)
                    self.errors.pop(ident, None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # No URLs, tokens or private message bodies in diagnostics.
                    self.errors[ident] = type(exc).__name__ + (
                        ": " + str(exc)
                        if isinstance(exc, CalendarUnavailable)
                        else "：提醒发送失败，请检查连接/官方主动消息权限"
                    )
