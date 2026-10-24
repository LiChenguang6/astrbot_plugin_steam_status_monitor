import asyncio
import io
import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage
from astrbot.api.message_components import Image
from astrbot.api.star import StarTools
from astrbot.api.web import PluginRequest, bind_request_context
from starlette.requests import Request


class Context:
    def __init__(self):
        self.routes = {}
        self.calls = []
        self.sent = []
        self.model_result = dict(
            relevant=True,
            new=20,
            review=40,
            confidence=0.98,
            date="",
            reason="已新学20/20、已复习40/40；2016/4440为总进度",
        )
        self.platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="qq-family", name="qq_official"),
            get_client=lambda: SimpleNamespace(api=SimpleNamespace(post_group_message=self.post_official)),
        )
        self.platform_manager = SimpleNamespace(platform_insts=[self.platform])
        self.reply = {"id": "confirmed-message"}

    def register_web_api(self, path, handler, methods, desc):
        self.routes[(methods[0], path)] = handler

    def get_all_providers(self):
        return [SimpleNamespace(meta=lambda: SimpleNamespace(id="vision-model"))]

    async def get_current_chat_provider_id(self, session):
        return "vision-model"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=json.dumps(self.model_result))

    def get_platform_inst(self, ident):
        return self.platform if ident == "qq-family" else None

    async def post_official(self, **kwargs):
        self.sent.append(kwargs)
        return self.reply


class Event:
    unified_msg_origin = "qq-family:GroupMessage:group-openid"

    def __init__(self, images=None, text="学习打卡", sender="sister-openid", group="group-openid"):
        self.images = images or []
        self.text = text
        self.sender = sender
        self.group = group
        self.replies = []
        self.stopped = False
        self.extras = {}
        self.is_at_or_wake_command = True

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def clear_result(self):
        pass

    def get_platform_id(self):
        return "qq-family"

    def get_self_id(self):
        return "bot-1"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.sender

    def get_platform_name(self):
        return "qq_official"

    def get_messages(self):
        return self.images

    def get_message_str(self):
        return self.text

    def plain_result(self, text):
        return text

    async def send(self, text):
        self.replies.append(text)


@pytest.fixture
def plugin(plugin_class, monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path)
    p = plugin_class(Context())
    pkg = plugin_class.__module__.rsplit(".", 1)[0]
    service = sys.modules[pkg + ".word_guardian.service"]
    original_now = service.local_now
    fixed = datetime.fromisoformat("2026-10-24T12:00:00+08:00")
    clock = lambda cfg, now=None: original_now(cfg, now or fixed)
    monkeypatch.setattr(service, "local_now", clock)
    monkeypatch.setattr(sys.modules[plugin_class.__module__], "local_now", clock)
    monkeypatch.setattr(sys.modules[pkg + ".word_guardian.web"], "local_now", clock)
    p.store.save_profile("family", cfg)
    yield p
    p.store.db.close()


async def call(plugin, path, body=None, *, username="admin", method="POST"):
    data = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}

    req = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
        },
        receive,
    )
    with bind_request_context(PluginRequest(req, username=username)):
        response = await plugin.context.routes[(method, "/qq_word_guardian/" + path)]()
    return response, json.loads(response.body)


def register(plugin):
    module = __import__(plugin.__module__.rsplit(".", 1)[0] + ".word_guardian.web", fromlist=["WebAPI"])
    module.WebAPI(plugin).register(plugin.context)


def screenshot():
    stream = io.BytesIO()
    PILImage.new("RGB", (50, 50), "white").save(stream, format="PNG")
    return Image.fromBytes(stream.getvalue())


async def test_actual_entry_unclear_image_to_pending_review_and_snapshot(plugin):
    plugin.context.model_result["confidence"] = 0.6
    e = Event([screenshot()])
    await plugin.ingest(e)
    rows = plugin.store.records()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert (rows[0]["learned"], rows[0]["reviewed"]) == (20, 40)
    assert "2016" not in str(plugin.store.totals("family"))
    assert plugin.context.calls[0]["image_urls"][0].startswith("data:image/png;base64,")
    assert "待审核" in e.replies[0]
    register(plugin)
    response, data = await call(plugin, "review", {"id": rows[0]["id"], "action": "accept"})
    assert response.status_code == 200
    response, data = await call(plugin, "snapshot", method="GET")
    assert data["profiles"][0]["today"]["done_new"] == 20
    assert data["providers"] == [{"id": "vision-model"}]


async def test_actual_entry_clear_undated_image_automatically_records(plugin):
    e = Event([screenshot()])
    await plugin.ingest(e)
    row = plugin.store.records()[0]
    assert row["status"] == "accepted"
    assert (row["learned"], row["reviewed"]) == (20, 40)
    assert "已记录" in e.replies[0] and "待审核" not in e.replies[0]


@pytest.mark.parametrize("bot", ["qq_official", "unknown_selfid", ""])
async def test_official_unmentioned_image_with_placeholder_self_id_is_recorded(plugin, bot):
    e = Event([screenshot()])
    e.get_self_id = lambda: bot
    await plugin.watch(e)
    assert plugin.store.records()[0]["status"] == "accepted"
    assert e.stopped and len(e.replies) == 1


async def test_placeholder_identity_cannot_cross_platform_or_member_boundary(plugin):
    e = Event([screenshot()], sender="not-sister")
    e.get_self_id = lambda: "qq_official"
    await plugin.watch(e)
    e.sender = "sister-openid"
    e.get_platform_id = lambda: "different-bot-instance"
    await plugin.watch(e)
    assert not plugin.store.records() and not plugin.context.calls
    assert not e.stopped


async def test_placeholder_rebinding_keeps_original_bot_identity_and_history(plugin):
    await plugin.ingest(Event(text="/单词打卡 新学20 复习40"), explicit=True)
    e = Event(sender="admin")
    e.get_self_id = lambda: "qq_official"
    replies = [r async for r in plugin.bind(e, target="sister-openid")]
    assert "已绑定计划 family" in replies[0]
    assert plugin.store.profiles()["family"]["bot_id"] == "bot-1"
    assert len(plugin.store.profiles()) == 1


@pytest.mark.parametrize(
    "relevant,member,expect_claim",
    [(True, "sister-openid", True), (False, "sister-openid", False), (True, "someone-else", False)],
)
async def test_native_sdk_dispatch_prioritizes_checkin_over_proactive_reply(
    plugin, monkeypatch, relevant, member, expect_claim
):
    from dataclasses import replace
    from astrbot.core.pipeline.process_stage.stage import StarRequestSubStage
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import StarHandlerRegistry, star_handlers_registry

    actual = next(
        h for h in star_handlers_registry if h.handler_module_path == plugin.__module__ and h.handler_name == "watch"
    )
    assert actual.extras_configs["priority"] == 1000
    called = []

    async def proactive(event):
        called.append("proactive probability=1")

    proactive_md = replace(
        actual,
        handler_name="proactive",
        handler_full_name="test-proactive",
        handler=proactive,
        extras_configs={"priority": 0},
    )
    registry = StarHandlerRegistry()
    registry.append(proactive_md)
    registry.append(replace(actual, handler=plugin.watch))
    event = Event([screenshot()], sender=member)
    event.get_self_id = lambda: "qq_official"
    event.extras["activated_handlers"] = registry._handlers
    event.extras["handlers_parsed_params"] = {}
    monkeypatch.setitem(star_map, plugin.__module__, SimpleNamespace(name="qq_word_guardian"))
    plugin.context.model_result["relevant"] = relevant
    stage = StarRequestSubStage()
    [value async for value in stage.process(event)]
    assert bool(called) is (not expect_claim)
    assert event.stopped is expect_claim


async def test_delete_plan_requires_confirmation_and_retains_history_by_default(plugin):
    register(plugin)
    await plugin.ingest(Event(text="/单词打卡 新学20 复习40"), explicit=True)
    response, _ = await call(plugin, "profile/delete", {"id": "family", "confirm_id": "wrong"})
    assert response.status_code == 400 and "family" in plugin.store.profiles()
    response, data = await call(plugin, "profile/delete", {"id": "family", "confirm_id": "family"})
    assert response.status_code == 200 and data["history_retained"]
    assert not plugin.store.profiles() and plugin.store.archived()[0]["id"] == "family"
    assert plugin.store.totals("family")["2026-10-24"]["new"] == 20
    assert not plugin.targets(Event())
    assert "family" in plugin.store.reserved_ids()


async def test_delete_with_purge_only_removes_selected_plan_data(plugin, cfg):
    register(plugin)
    plugin.store.save_profile("second", dict(cfg, user_ids=["second"]))
    for ident in ("family", "second"):
        plugin.store.add(
            ident, "2026-10-24", {"new": 20, "review": 40, "confidence": 1}, ident, "image", "confirmed", True, [ident]
        )
        plugin.store.mark_sent(ident, "2026-10-24", "morning")
    response, data = await call(
        plugin, "profile/delete", {"id": "family", "confirm_id": "family", "purge_history": True}
    )
    assert response.status_code == 200 and not data["history_retained"]
    assert not plugin.store.records("family")
    assert len(plugin.store.records("second")) == 1
    assert not plugin.store.db.execute("SELECT 1 FROM evidence WHERE profile='family'").fetchone()
    assert plugin.store.claimed("second", "2026-10-24", "morning")


async def test_queued_image_does_not_record_after_plan_deleted(plugin):
    lock = plugin.locks.setdefault("family", asyncio.Lock())
    await lock.acquire()
    event = Event([screenshot()])
    waiting = asyncio.create_task(plugin.ingest(event))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    plugin.store.delete_profile("family")
    lock.release()
    await waiting
    assert not plugin.store.records() and not plugin.context.calls
    with pytest.raises(ValueError, match="已归档"):
        plugin.store.save_profile("family", plugin.store.profiles().get("family", {}))


async def test_deleted_plan_id_is_not_reused_by_automatic_binding(plugin):
    plugin.store.delete_profile("family")
    replies = [r async for r in plugin.bind(Event(sender="admin"), target="sister-openid")]
    assert "已绑定计划 member_" in replies[0]
    assert "family" not in plugin.store.profiles()


async def test_official_placeholder_cannot_create_overlapping_active_plan_via_panel(plugin, cfg):
    register(plugin)
    response, _ = await call(plugin, "profile", {"id": "duplicate", "config": dict(cfg, bot_id="qq_official")})
    assert response.status_code == 400


async def test_nonofficial_or_real_different_bot_does_not_match_placeholder_exception(plugin):
    e = Event([screenshot()])
    e.get_self_id = lambda: "bot-other"
    assert not plugin.targets(e)
    e.get_self_id = lambda: "qq_official"
    e.get_platform_name = lambda: "aiocqhttp"
    assert not plugin.targets(e)


async def test_multiple_accounts_bind_without_reusing_family_and_keep_separate_ledgers(plugin):
    original = plugin.store.profiles()["family"]
    admin = Event(sender="admin")
    replies = [r async for r in plugin.bind(admin, target="second-member")]
    assert "已绑定计划 member_" in replies[0]
    plans = plugin.store.profiles()
    assert len(plans) == 2 and plans["family"] == original
    second = next(ident for ident in plans if ident != "family")
    await plugin.ingest(Event(text="/单词打卡 新学20 复习40"), explicit=True)
    await plugin.ingest(Event(text="/单词打卡 新学7 复习12", sender="second-member"), explicit=True)
    assert plugin.store.totals("family")["2026-10-24"] == {"new": 20, "review": 40}
    assert plugin.store.totals(second)["2026-10-24"] == {"new": 7, "review": 12}
    # Repeating the binding reuses this account's plan, not a fresh empty ledger.
    [r async for r in plugin.bind(admin, target="second-member")]
    assert set(plugin.store.profiles()) == set(plans)
    assert len(plugin.store.records()) == 2


async def test_explicit_existing_plan_cannot_overwrite_another_account_even_without_history(plugin):
    before = plugin.store.profiles()["family"]
    replies = [r async for r in plugin.bind(Event(sender="admin"), target="other-member", plan="family")]
    assert "不会覆盖" in replies[0]
    assert plugin.store.profiles()["family"] == before


async def test_rebinding_existing_alias_preserves_aliases_and_history(plugin):
    await plugin.ingest(Event(text="/单词打卡 新学20 复习40"), explicit=True)
    original = plugin.store.profiles()["family"]
    plugin.store.save_profile("family", dict(original, enabled=False))
    replies = [r async for r in plugin.bind(Event(sender="admin"), target="u_alias")]
    assert "已绑定计划 family" in replies[0]
    assert plugin.store.profiles()["family"]["user_ids"] == original["user_ids"]
    assert plugin.store.profiles()["family"]["enabled"]
    assert plugin.store.totals("family")["2026-10-24"]["new"] == 20


async def test_same_member_can_have_separate_plans_on_different_bot_accounts(plugin):
    other_bot = Event(sender="admin")
    other_bot.get_platform_id = lambda: "qq-other"
    other_bot.get_self_id = lambda: "bot-2"
    other_bot.unified_msg_origin = "qq-other:GroupMessage:group-openid"
    replies = [r async for r in plugin.bind(other_bot, target="sister-openid")]
    assert "已绑定计划 member_" in replies[0]
    assert len(plugin.store.profiles()) == 2
    assert len(plugin.targets(Event())) == 1


async def test_plan_list_is_scoped_to_current_bot_and_group(plugin, cfg):
    plugin.store.save_profile("elsewhere", dict(cfg, group_id="elsewhere", session="qq-family:GroupMessage:elsewhere"))
    replies = [r async for r in plugin.list_plans(Event(sender="admin"))]
    assert "family" in replies[0] and "elsewhere" not in replies[0]


async def test_create_only_profile_request_never_overwrites_existing_config(plugin, cfg):
    register(plugin)
    response, data = await call(
        plugin, "profile", {"id": "family", "config": dict(cfg, name="replaced"), "create_only": True}
    )
    assert response.status_code == 400
    assert plugin.store.profiles()["family"]["name"] == cfg["name"]


async def test_unbound_sender_or_group_never_invokes_model(plugin):
    await plugin.ingest(Event([screenshot()], sender="someone-else"))
    await plugin.ingest(Event([screenshot()], group="another-group"))
    assert not plugin.context.calls and not plugin.store.records()


async def test_admin_binding_from_framework_isolated_group_session(plugin):
    e = Event(sender="admin-openid")
    e.unified_msg_origin = "qq-family:GroupMessage:admin-openid_group-openid"
    replies = [reply async for reply in plugin.bind(e, target="sister-openid", plan="isolated")]
    # The pre-existing active plan would conflict; pause it before binding.
    assert "已绑定其他" in replies[0]
    previous = plugin.store.profiles()["family"]
    plugin.store.save_profile("family", dict(previous, enabled=False))
    replies = [reply async for reply in plugin.bind(e, target="sister-openid", plan="isolated")]
    assert "已绑定计划 isolated" in replies[0]
    saved = plugin.store.profiles()["isolated"]
    assert saved["session"] == e.unified_msg_origin
    assert saved["group_id"] == "group-openid"
    assert saved["user_ids"] == ["sister-openid"]


async def test_text_command_uses_persons_cumulative_not_model(plugin):
    e = Event(text="/单词打卡 新学20 复习40")
    await plugin.ingest(e, explicit=True)
    assert not plugin.context.calls
    assert plugin.store.records()[0]["status"] == "accepted"
    assert "已记录" in e.replies[0]


async def test_model_failure_does_not_invent_progress(plugin):
    async def fail(**kwargs):
        raise RuntimeError("test")

    plugin.context.llm_generate = fail
    e = Event([screenshot()])
    await plugin.ingest(e)
    assert not plugin.store.records()
    assert "未计入" in e.replies[0]


async def test_irrelevant_chat_no_record(plugin):
    plugin.context.model_result["relevant"] = False
    e = Event(text="今天吃什么")
    await plugin.ingest(e)
    assert not plugin.store.records() and not e.replies


async def test_natural_word_checkin_not_mistaken_for_a_command(plugin):
    e = Event(text="单词背完啦，今天新学20，复习40")
    await plugin.watch(e)
    assert len(plugin.context.calls) == 1
    assert plugin.store.records()[0]["status"] == "accepted"


async def test_late_checkin_prioritizes_rest_and_skips_ai_encouragement(plugin, monkeypatch):
    module = sys.modules[plugin.__module__]
    monkeypatch.setattr(module, "local_now", lambda cfg: datetime.fromisoformat("2026-10-24T22:31:00+08:00"))
    cfg = plugin.store.profiles()["family"]
    plugin.store.save_profile("family", dict(cfg, ai_feedback=True))
    e = Event(text="/单词打卡 新学10 复习20")
    await plugin.ingest(e, explicit=True)
    assert not plugin.context.calls
    assert "晚安" in e.replies[0]
    assert "还差" not in e.replies[0]


async def test_unload_cancels_inflight_model_before_closing_database(plugin_class, monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path)
    p = plugin_class(Context())
    p.store.save_profile("family", cfg)
    entered = asyncio.Event()

    async def waiting_model(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    p.context.llm_generate = waiting_model
    ingestion = asyncio.create_task(p.ingest(Event([screenshot()])))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await p.terminate()
    with pytest.raises(asyncio.CancelledError):
        await ingestion


async def test_official_reminder_calls_proactive_api_without_expired_msgid(plugin, cfg):
    await plugin.send_bound(cfg, "早上好")
    assert plugin.context.sent == [{"group_openid": "group-openid", "msg_type": 0, "content": "早上好"}]
    plugin.context.reply = {"code": 123, "message": "denied"}
    with pytest.raises(RuntimeError):
        await plugin.send_bound(cfg, "must not claim success")


@pytest.mark.parametrize(
    "path,method",
    [
        ("snapshot", "GET"),
        ("profile", "POST"),
        ("profile/delete", "POST"),
        ("review", "POST"),
        ("manual", "POST"),
        ("test-send", "POST"),
        ("export", "GET"),
    ],
)
async def test_all_web_endpoints_require_dashboard_auth(plugin, path, method):
    register(plugin)
    response, data = await call(plugin, path, username=None, method=method)
    assert response.status_code == 401


async def test_manual_correction_export_and_history(plugin):
    register(plugin)
    response, data = await call(plugin, "manual", {"profile": "family", "day": "2026-10-24", "new": 30, "review": 40})
    assert response.status_code == 200
    ident = data["id"]
    response, data = await call(plugin, "review", {"id": ident, "action": "accept", "new": 20, "review": 40})
    assert response.status_code == 200
    assert plugin.store.totals("family")["2026-10-24"]["new"] == 20
    response, data = await call(plugin, "export", method="GET")
    assert "20,40,accepted,manual" in data["csv"]
    assert "fingerprint" not in data["csv"]
    # Re-entering the same manual total must reactivate/correct an existing row,
    # not report success while leaving it rejected or with stale corrected counts.
    await call(plugin, "review", {"id": ident, "action": "reject"})
    response, data = await call(plugin, "manual", {"profile": "family", "day": "2026-10-24", "new": 30, "review": 40})
    assert response.status_code == 200 and data["id"] == ident
    assert plugin.store.totals("family")["2026-10-24"]["new"] == 30
    assert len(plugin.store.records()) == 1


async def test_duplicate_active_plan_rejected_and_test_send_limited(plugin, cfg):
    register(plugin)
    response, data = await call(plugin, "profile", {"id": "second", "config": cfg})
    assert response.status_code == 400
    response, data = await call(plugin, "test-send", {"profile": "family"})
    assert response.status_code == 200
    response, data = await call(plugin, "test-send", {"profile": "family"})
    assert response.status_code == 400
    assert len(plugin.context.sent) == 1


async def test_no_false_success_on_official_permission_failure(plugin):
    register(plugin)
    plugin.context.reply = {"code": 123}
    response, data = await call(plugin, "test-send", {"profile": "family"})
    assert response.status_code == 503
    assert not list(plugin.store.db.execute("SELECT * FROM notifications"))


async def test_scheduler_task_is_cancelled_on_reload(plugin_class, monkeypatch, tmp_path):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path)
    p = plugin_class(Context())
    await p.initialize()
    await asyncio.sleep(0)
    assert not p.task.done()
    await p.terminate()
    assert p.task.cancelled()
