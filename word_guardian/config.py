from copy import deepcopy
from datetime import date, time
from zoneinfo import ZoneInfo


DEFAULTS = {
    "enabled": False,
    "name": "单词打卡",
    "platform_id": "",
    "bot_id": "",
    "group_id": "",
    "user_ids": [],
    "session": "",
    "start_date": "",
    "timezone": "Asia/Shanghai",
    "new_goal": 20,
    "review_goal": 40,
    "weekdays": [5, 6],
    "statutory_holidays": True,
    "exclude_workdays": True,
    "date_overrides": {},
    "morning": "08:00",
    "evening": "21:30",
    "late": "22:30",
    "reminder_grace_minutes": 15,
    "carry_over": True,
    "carry_limit_days": 60,
    "max_daily_new": 200,
    "max_daily_review": 400,
    "provider_id": "",
    "feedback_provider_id": "",
    "auto_accept": True,
    "min_confidence": 0.90,
    "review_undated_images": True,
    "allow_text": True,
    "ai_feedback": False,
    "recognition_interval_seconds": 15,
    "retention_days": 180,
    "morning_template": "早上好！今天的单词小目标：新学 {new} 个，复习 {review} 个。分几次完成就好，加油！",
    "evening_template": "睡前温柔提醒：今天还差新学 {new} 个、复习 {review} 个。量力而行，22点前收尾，休息更重要。",
    "late_template": "看到你的努力啦，不过已经有点晚了。今天先好好休息，剩下的下次再慢慢来，晚安！",
    "feedback_style": "温柔具体，不比较、不讽刺、不羞辱、不制造焦虑；肯定努力和规律作息。",
}


def validate(raw, *, previous=None):
    if not isinstance(raw, dict):
        raise ValueError("配置必须是对象")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError("未知配置字段：" + ", ".join(sorted(unknown)))
    cfg = deepcopy(DEFAULTS)
    if previous:
        cfg.update(previous)
    cfg.update(raw)
    for key, default in DEFAULTS.items():
        if isinstance(default, bool) and type(cfg[key]) is not bool:
            raise ValueError(f"{key} 必须为布尔值")
        if isinstance(default, str) and not isinstance(cfg[key], str):
            raise ValueError(f"{key} 必须为字符串")
    for key, low, high in [
        ("new_goal", 0, 500),
        ("review_goal", 0, 1000),
        ("carry_limit_days", 1, 366),
        ("max_daily_new", 1, 2000),
        ("max_daily_review", 1, 4000),
        ("retention_days", 30, 730),
        ("reminder_grace_minutes", 1, 60),
        ("recognition_interval_seconds", 1, 600),
    ]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f"{key} 必须为 {low}–{high} 的整数")
    confidence = cfg["min_confidence"]
    if type(confidence) not in (float, int) or not 0.5 <= confidence <= 1:
        raise ValueError("识别置信度须在 0.5–1 之间")
    if cfg["max_daily_new"] < cfg["new_goal"] or cfg["max_daily_review"] < cfg["review_goal"]:
        raise ValueError("每日展示上限不能小于基础目标")
    for key in ("platform_id", "bot_id", "group_id", "session", "provider_id", "feedback_provider_id"):
        cfg[key] = cfg[key].strip()
        if len(cfg[key]) > 256 or any(ord(c) < 32 for c in cfg[key]):
            raise ValueError(f"{key} 格式无效")
    if not isinstance(cfg["user_ids"], list) or len(cfg["user_ids"]) > 10:
        raise ValueError("身份别名最多10个，填写 QQ号 / QQ UID / openid 字符串")
    if any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in cfg["user_ids"]):
        raise ValueError("成员身份须为非空字符串，不要转成数字")
    cfg["user_ids"] = list(dict.fromkeys(x.strip() for x in cfg["user_ids"]))
    if not isinstance(cfg["weekdays"], list) or any(type(x) is not int or x not in range(7) for x in cfg["weekdays"]):
        raise ValueError("weekdays 使用0=周一到6=周日")
    cfg["weekdays"] = sorted(set(cfg["weekdays"]))
    try:
        ZoneInfo(cfg["timezone"])
        if cfg["start_date"]:
            date.fromisoformat(cfg["start_date"])
        for key in ("morning", "evening", "late"):
            if len(cfg[key]) != 5:
                raise ValueError()
            time.fromisoformat(cfg[key])
        if not cfg["morning"] < cfg["evening"] < "22:00" <= cfg["late"]:
            raise ValueError("时间须满足：早晨 < 晚间 < 22:00 ≤ 太晚提醒")
    except (ValueError, KeyError) as exc:
        raise ValueError("检查时区、开始日期与提醒时间（HH:MM，晚间必须在22点前）") from exc
    if not isinstance(cfg["date_overrides"], dict) or len(cfg["date_overrides"]) > 1000:
        raise ValueError("日期例外必须是日期到布尔值的对象")
    for day, active in cfg["date_overrides"].items():
        date.fromisoformat(day)
        if type(active) is not bool:
            raise ValueError("日期例外 true=布置任务，false=免打卡")
    for key in ("morning_template", "evening_template", "late_template", "feedback_style", "name"):
        if len(cfg[key]) > 1000:
            raise ValueError(f"{key} 太长")
    if cfg["enabled"] and not all(
        cfg[k] for k in ("platform_id", "bot_id", "group_id", "user_ids", "session", "start_date")
    ):
        raise ValueError("启用前须绑定机器人、群聊、成员、会话及开始日期")
    if cfg["session"]:
        parts = cfg["session"].split(":", 2)
        if len(parts) != 3 or parts[0] != cfg["platform_id"] or parts[1] != "GroupMessage":
            raise ValueError("会话必须是所选适配器的群会话，请在目标群执行 /单词绑定")
        if parts[2] not in (cfg["group_id"], "0_" + cfg["group_id"]):
            raise ValueError("会话与群ID不一致，请重新在目标群绑定")
    return cfg
