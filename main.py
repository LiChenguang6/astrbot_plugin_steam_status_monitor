"""QQ 单词守护的 AstrBot 入口；所有处理器直接定义，支持框架原生权限。"""

import asyncio
import base64
import hashlib
import io
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star, StarTools
from PIL import Image as PILImage

from .word_guardian.config import DEFAULTS
from .word_guardian.calendar import CalendarUnavailable
from .word_guardian.recognition import explicit_text, recognize
from .word_guardian.service import Scheduler, digest, feedback, local_now, matches, record, status
from .word_guardian.store import Store
from .word_guardian.web import WebAPI


class Main(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.store = Store(StarTools.get_data_dir("qq_word_guardian") / "guardian.sqlite3")
        self.locks = {}
        self.capacity = asyncio.Semaphore(2)
        self.last_attempt = {}
        self.errors = {}
        self.scheduler = Scheduler(self.store, self.send_bound, self.locks)
        self.task = None
        self.pruned_day = ""
        self.active = set()

    async def initialize(self):
        WebAPI(self).register(self.context)
        self.store.prune(self.store.profiles())
        self.task = asyncio.create_task(self.run_clock(), name="qq-word-guardian-clock")

    async def run_clock(self):
        while True:
            try:
                today = time.strftime("%Y-%m-%d", time.gmtime())
                if today != self.pruned_day:
                    self.store.prune(self.store.profiles())
                    self.pruned_day = today
                await self.scheduler.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[单词守护] 调度失败：%s", type(exc).__name__)
            await asyncio.sleep(30)

    async def send_bound(self, cfg, text):
        platform = self.context.get_platform_inst(cfg["platform_id"])
        if platform is None:
            raise RuntimeError("bound platform unavailable")
        if platform.meta().name == "qq_official":
            # Do NOT reuse an expired passive msg_id: fixed-time reminders are proactive messages.
            # The QQ platform must grant proactive group messaging permission/quota.
            response = await platform.get_client().api.post_group_message(
                group_openid=cfg["group_id"],
                msg_type=0,
                content=text,
            )
            if not isinstance(response, dict) or not response.get("id"):
                raise RuntimeError("official proactive message not confirmed")
            return
        # Only use the observed adapter-owned session, never a guessed QQ official session.
        chain = MessageChain([Plain(text)])
        sent = await self.context.send_message(cfg["session"], chain)
        if sent is False:
            raise RuntimeError("send rejected")

    def targets(self, event):
        return [
            (ident, cfg)
            for ident, cfg in self.store.profiles().items()
            if matches(
                cfg,
                platform=event.get_platform_id(),
                bot=event.get_self_id(),
                group=event.get_group_id(),
                user=event.get_sender_id(),
            )
        ]

    async def ingest(self, event, *, explicit=False):
        task = asyncio.create_task(self._ingest(event, explicit=explicit))
        self.active.add(task)
        try:
            return await task
        finally:
            self.active.discard(task)

    async def _ingest(self, event, *, explicit=False):
        targets = self.targets(event)
        if not targets:
            return
        # Multiple plans for one identity would duplicate encouragement and accounting.
        ident, cfg = targets[0]
        async with self.locks.setdefault(ident, asyncio.Lock()):
            if time.monotonic() - self.last_attempt.get(ident, -100000) < cfg["recognition_interval_seconds"]:
                if explicit or any(isinstance(c, Image) for c in event.get_messages()):
                    await event.send(event.plain_result("刚刚处理过一次打卡，请稍后再试。"))
                return
            self.last_attempt[ident] = time.monotonic()
            try:
                images, image_hashes = [], []
                components = [c for c in event.get_messages() if isinstance(c, Image)]
                text = event.get_message_str()
                if not explicit and not components and not cfg["allow_text"]:
                    return
                if len(components) > 3:
                    await event.send(event.plain_result("一次最多发3张学习截图，请分开发送。"))
                    return
                async with self.capacity:
                    for component in components:
                        encoded = await asyncio.wait_for(component.convert_to_base64(), timeout=30)
                        if len(encoded) > 12 * 1024 * 1024:
                            raise ValueError("图片超过8MB，请压缩后重试")
                        data = base64.b64decode(encoded, validate=True)
                        if len(data) > 8 * 1024 * 1024:
                            raise ValueError("图片超过8MB，请压缩后重试")
                        with PILImage.open(io.BytesIO(data)) as picture:
                            if picture.width * picture.height > 20_000_000 or picture.format not in (
                                "PNG",
                                "JPEG",
                                "WEBP",
                            ):
                                raise ValueError("请发不超过2000万像素的PNG/JPEG/WebP图片")
                            mime = PILImage.MIME[picture.format]
                            picture.verify()
                        images.append(f"data:{mime};base64,{encoded}")
                        image_hashes.append(hashlib.sha256(data).hexdigest())
                    if any(
                        self.store.db.execute(
                            "SELECT 1 FROM evidence WHERE profile=? AND digest=?", (ident, h)
                        ).fetchone()
                        for h in image_hashes
                    ):
                        await event.send(event.plain_result("这份截图已经记录过啦，不会重复累计。"))
                        return
                    if explicit:
                        if not cfg["allow_text"]:
                            raise ValueError("此计划已关闭文字打卡，请发学习截图")
                        parts = text.split(maxsplit=1)
                        body = parts[1] if len(parts) == 2 else ""
                        result = explicit_text(body)
                    else:
                        result = await asyncio.wait_for(
                            recognize(self.context, cfg, event.unified_msg_origin, text, images), timeout=60
                        )
                now = local_now(cfg)
                fingerprint = digest(ident, now.date().isoformat(), text, image_hashes)
                outcome = record(
                    self.store, ident, cfg, result, fingerprint, image=bool(images), image_hashes=image_hashes, now=now
                )
                if outcome["kind"] == "irrelevant":
                    return
                calendar_warning = ""
                try:
                    state = status(self.store, ident, cfg, now)
                except CalendarUnavailable:
                    today = self.store.totals(ident).get(now.date().isoformat(), {"new": 0, "review": 0})
                    state = {
                        "done_new": today["new"],
                        "done_review": today["review"],
                        "remaining_new": 0,
                        "remaining_review": 0,
                    }
                    calendar_warning = "\n日历尚未覆盖该年份；已保存打卡，目标计算与提醒暂停，请管理员更新日历。"
                reply = feedback(cfg, state, outcome)
                if calendar_warning and outcome["kind"] == "accepted":
                    reply = f"已记录今日累计：新学{state['done_new']}个、复习{state['done_review']}个。"
                # Late messages always use the rest reminder and never ask for immediate catch-up.
                if outcome.get("late") and outcome["kind"] == "accepted":
                    reply = (
                        f"已记录今日累计：新学{state['done_new']}个、复习{state['done_review']}个。\n"
                        + cfg["late_template"]
                    )
                elif cfg["ai_feedback"] and outcome["kind"] == "accepted":
                    try:
                        provider = (
                            cfg["feedback_provider_id"]
                            or cfg["provider_id"]
                            or await self.context.get_current_chat_provider_id(event.unified_msg_origin)
                        )
                        response = await asyncio.wait_for(
                            self.context.llm_generate(
                                chat_provider_id=provider,
                                system_prompt="为学生写一句不超过60字的温柔鼓励，不比较、不羞辱、不诊断，不编造数量，不施压熬夜。风格偏好："
                                + cfg["feedback_style"],
                                prompt=f"今天完成新学{state['done_new']}，复习{state['done_review']}。请肯定努力与规律作息。",
                            ),
                            timeout=20,
                        )
                        if response.completion_text.strip():
                            reply += "\n" + response.completion_text.strip()[:180]
                    except Exception:
                        pass  # Keep the factual deterministic response if the optional model fails.
                reply += calendar_warning
                self.errors.pop(ident, None)
                await event.send(event.plain_result(reply))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors[ident] = type(exc).__name__
                logger.warning("[单词守护] 识别未完成：%s", type(exc).__name__)
                message = (
                    str(exc)
                    if isinstance(exc, ValueError) and str(exc).startswith(("格式：", "图片超过", "请发", "此计划"))
                    else "识别暂时失败，未计入完成量。请检查面板的模型配置，或让管理员手动补录。"
                )
                await event.send(event.plain_result(message))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def watch(self, event: AstrMessageEvent):
        text = event.get_message_str().lstrip()
        first = text.split(maxsplit=1)
        commands = {"单词打卡", "单词身份", "单词绑定", "单词进度", "单词确认", "单词帮助"}
        if text.startswith("/") or (first and first[0] in commands):
            return
        await self.ingest(event)

    @filter.command("单词打卡")
    async def checkin(self, event: AstrMessageEvent):
        if not self.targets(event):
            yield event.plain_result("当前机器人/群聊/成员尚未绑定到启用中的计划，请联系管理员。")
            return
        await self.ingest(event, explicit=True)

    @filter.command("单词身份")
    async def identity(self, event: AstrMessageEvent):
        yield event.plain_result(
            f"适配器ID：{event.get_platform_id()}\n机器人ID：{event.get_self_id()}\n群ID：{event.get_group_id()}\n你的成员ID：{event.get_sender_id()}\n官方QQ请用此处实际ID，不要猜QQ号与openid映射。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("单词绑定")
    async def bind(self, event: AstrMessageEvent, target: str = "", plan: str = "family"):
        if not event.get_group_id():
            yield event.plain_result("请在目标QQ群内绑定。")
            return
        if event.get_platform_name() not in ("aiocqhttp", "qq_official", "qqofficial", "qq_official_webhook"):
            yield event.plain_result("当前适配器不是已支持的QQ适配器。")
            return
        mentions = [
            str(c.qq)
            for c in event.get_messages()
            if isinstance(c, At) and str(c.qq) not in (str(event.get_self_id()), "all", "0")
        ]
        user = mentions[0] if mentions else target.strip()
        if not user:
            yield event.plain_result("用法：/单词绑定 @妹妹（或她在此适配器的成员ID）。绑定前请告诉她会记录学习打卡。")
            return
        cfg = self.store.profiles().get(plan, dict(DEFAULTS))
        cfg = dict(
            cfg,
            enabled=True,
            platform_id=str(event.get_platform_id()),
            bot_id=str(event.get_self_id()),
            group_id=str(event.get_group_id()),
            user_ids=[user],
            session=event.unified_msg_origin,
        )
        cfg["start_date"] = cfg["start_date"] or local_now(cfg).date().isoformat()
        try:
            for other_id, other in self.store.profiles().items():
                if other_id != plan and matches(
                    other, platform=cfg["platform_id"], bot=cfg["bot_id"], group=cfg["group_id"], user=user
                ):
                    raise ValueError("此成员已绑定其他启用中的计划，请先停用旧计划")
            self.store.save_profile(plan, cfg)
            yield event.plain_result(
                f"已绑定计划 {plan}。仅此机器人、此群、成员 {user} 会被记录。默认新学20/复习40；请到插件面板调整。无日期截图默认待审核。"
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @filter.command("单词进度")
    async def progress(self, event: AstrMessageEvent):
        for ident, cfg in self.targets(event):
            try:
                row = status(self.store, ident, cfg)
                yield event.plain_result(
                    f"{cfg['name']}：今日新学{row['done_new']}，复习{row['done_review']}；待完成新学{row['remaining_new']}、复习{row['remaining_review']}。历史欠量新学{row['debt_new']}、复习{row['debt_review']}。非任务日不自动催促。"
                )
            except ValueError as exc:
                yield event.plain_result(str(exc))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("单词确认")
    async def confirm(self, event: AstrMessageEvent, record_id: int):
        row = next((r for r in self.store.records(limit=1000) if r["id"] == record_id), None)
        cfg = self.store.profiles().get(row["profile"]) if row else None
        if not cfg or not (
            cfg["platform_id"] == str(event.get_platform_id())
            and cfg["bot_id"] == str(event.get_self_id())
            and cfg["group_id"] == str(event.get_group_id())
        ):
            yield event.plain_result("记录不存在或不属于当前机器人与群聊，请在管理面板审核。")
            return
        self.store.decide(record_id, "accept")
        yield event.plain_result(f"记录 #{record_id} 已确认，按记录当天累计最大值计入，不会重复相加。")

    @filter.command("单词帮助")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "单词守护：发送学习截图自动识别；/单词打卡 新学20 复习40 为当天累计量；/单词进度；/单词身份。管理员：/单词绑定 @成员、/单词确认 记录ID。计划、审核、节假日例外、提醒与统计在 AstrBot 插件面板。"
        )

    async def terminate(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        tasks = list(self.active)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.db.close()
