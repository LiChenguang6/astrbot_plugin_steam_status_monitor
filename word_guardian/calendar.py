from datetime import date, timedelta

import chinese_calendar as cn


class CalendarUnavailable(ValueError):
    pass


def required(day: date, cfg: dict) -> bool:
    """Explicit school leave/makeup overrides win over the national calendar."""
    if day.isoformat() in cfg["date_overrides"]:
        return cfg["date_overrides"][day.isoformat()]
    if not cfg["statutory_holidays"] and not cfg["exclude_workdays"]:
        return day.weekday() in cfg["weekdays"]
    try:
        workday = cn.is_workday(day)
        holiday, name = cn.get_holiday_detail(day)
    except NotImplementedError as exc:
        raise CalendarUnavailable(f"节假日库未覆盖{day.year}年；请升级 chinese-calendar 或为该日期设置例外") from exc
    if cfg["exclude_workdays"] and workday:
        return False
    return day.weekday() in cfg["weekdays"] or bool(cfg["statutory_holidays"] and holiday and name)


def balance(day, cfg, totals):
    start = date.fromisoformat(cfg["start_date"] or day.isoformat())
    if day < start:
        return {
            "required": False,
            "new": 0,
            "review": 0,
            "debt_new": 0,
            "debt_review": 0,
            "remaining_new": 0,
            "remaining_review": 0,
            "done_new": 0,
            "done_review": 0,
        }
    active = required(day, cfg)
    debt_new = debt_review = 0
    # Rolling carry window is explicit; old debt expires, it is never multiplied by calendar days.
    first = max(start, day - timedelta(days=cfg["carry_limit_days"]))
    cursor = first
    while cursor < day:
        row = totals.get(cursor.isoformat(), {"new": 0, "review": 0})
        if required(cursor, cfg):
            debt_new += cfg["new_goal"]
            debt_review += cfg["review_goal"]
        debt_new = max(0, debt_new - row["new"])
        debt_review = max(0, debt_review - row["review"])
        cursor += timedelta(days=1)
    if not cfg["carry_over"]:
        debt_new = debt_review = 0
    new = (cfg["new_goal"] if active else 0) + debt_new
    review = (cfg["review_goal"] if active else 0) + debt_review
    today = totals.get(day.isoformat(), {"new": 0, "review": 0})
    return {
        "required": active,
        "new": min(new, cfg["max_daily_new"]),
        "review": min(review, cfg["max_daily_review"]),
        "debt_new": debt_new,
        "debt_review": debt_review,
        "remaining_new": max(0, min(new, cfg["max_daily_new"]) - today["new"]),
        "remaining_review": max(0, min(review, cfg["max_daily_review"]) - today["review"]),
        "done_new": today["new"],
        "done_review": today["review"],
        "uncapped_new": new,
        "uncapped_review": review,
    }
