"use strict";
const bridge = window.AstrBotPluginPage;
let snapshot = null,
  page = "dashboard",
  selected = "",
  toastTimer;
const labels = {
  dashboard: "今日概览",
  plans: "计划与提醒",
  review: "打卡审核",
  calendar: "学习日历",
  history: "学习记录",
  connection: "机器人连接",
};
const $ = (s) => document.querySelector(s);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const dayISO = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
function toast(message, error = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = error ? "error" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 6000);
}
async function api(method, path, body) {
  if (!bridge)
    throw Error("请从 AstrBot 的插件详情页打开面板，普通本地页面无法连接。");
  const data = await bridge[method === "GET" ? "apiGet" : "apiPost"](
    path,
    body,
  );
  if (data?.status === "error") throw Error(data.message || "操作失败");
  return data;
}
async function refresh() {
  try {
    snapshot = await api("GET", "snapshot");
    if (!snapshot.profiles.some((p) => p.id === selected))
      selected = snapshot.profiles[0]?.id || "";
    $("#pending").textContent = snapshot.records.filter(
      (r) => r.status === "pending",
    ).length;
    render();
  } catch (e) {
    $("#content").innerHTML =
      `<div class="empty"><h2>暂时无法连接</h2><p>${esc(e.message)}</p><button class="secondary" data-action="refresh">重新连接</button></div>`;
  }
}
function title(name, sub, extra = "") {
  return `<div class="title"><div><h1>${name}</h1><p class="subtitle">${sub}</p></div>${extra}</div>`;
}
function selectPlan() {
  return `<select class="plan-select" id="select-plan" aria-label="选择学习计划">${snapshot.profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.config.name)} · ${esc(p.id)}</option>`).join("")}</select>`;
}
function empty() {
  return `<div class="empty"><h2>从一份小小的计划开始</h2><p>先让机器人进入目标QQ群，在群内由 AstrBot 管理员发送<br><strong>/单词绑定 @妹妹</strong><br>也可以先在面板建立草稿，再到群内绑定。</p><button class="primary" data-action="new-plan">新建学习计划</button></div>`;
}
function plan() {
  return snapshot.profiles.find((p) => p.id === selected);
}
function stateWarning(p) {
  return (
    (p?.calendar_error
      ? `<div class="notice">日历数据待更新：${esc(p.calendar_error)}。相关提醒会暂停，不会猜测节假日。</div>`
      : "") +
    (p?.error
      ? `<div class="notice">最近一次运行异常：${esc(p.error)}。请检查模型或机器人连接。</div>`
      : "")
  );
}
function metric(label, value, unit, note, pct) {
  return `<div class="card"><div class="metric-label">${label}</div><div class="metric">${esc(value)}<small>${unit}</small></div><div class="metric-note">${note}</div>${pct === undefined ? "" : `<div class="bar"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></div>`}</div>`;
}
function statusBadge(status) {
  const names = { accepted: "已计入", pending: "待审核", rejected: "不计入" };
  return `<span class="badge ${esc(status)}">${names[status] || esc(status)}</span>`;
}
function recordTable(records, actions = false) {
  if (!records.length)
    return `<div class="empty">暂时没有${actions ? "待审核" : "学习"}记录。每一份小小的努力，都会在这里留下足迹。</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>日期 / 记录</th><th>计划</th><th>新学 / 复习</th><th>状态</th><th>证据说明</th>${actions ? "<th>操作</th>" : ""}</tr></thead><tbody>${records.map((r) => `<tr><td>${esc(r.day)}<small>#${r.id} · ${{ image: "图片识别", text: "本人文字", manual: "手动补录" }[r.source] || esc(r.source)}</small></td><td>${esc(r.profile)}</td><td>${r.learned} / ${r.reviewed}</td><td>${statusBadge(r.status)}</td><td>${esc(r.reason)}<small>置信度 ${Math.round(r.confidence * 100)}%</small></td>${actions ? `<td><div class="actions"><button class="secondary" data-action="review-accept" data-id="${r.id}">确认</button><button class="quiet" data-action="review-edit" data-id="${r.id}">改数</button><button class="danger" data-action="review-reject" data-id="${r.id}">不计入</button></div></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}
function dashboard() {
  const p = plan();
  let h = title(
    "把坚持，变成一点点进步",
    "不急着追赶，一次认真打卡，就是一次积累。",
    snapshot.profiles.length ? selectPlan() : "",
  );
  if (!p) return h + empty();
  const c = p.config,
    s = p.today;
  h += stateWarning(p);
  if (!s) return h;
  const done = !s.remaining_new && !s.remaining_review;
  h += `<section class="hero"><div class="hero-copy"><div class="eyebrow">A LITTLE EVERY DAY</div><h2>${!c.enabled ? "计划已暂停，随时准备再出发" : done ? "今天的努力，已经好好记录啦" : s.required ? "今天也为自己，积累一点点" : "今天没有固定任务，好好休息也很重要"}</h2><p>${esc(c.name)} · ${esc(p.local_date || "")}<br>${c.enabled ? `早晨 ${esc(c.morning)} 温柔提醒，晚上 ${esc(c.evening)} 帮你收尾。` : "在计划设置中启用后，才会开始识别与提醒。"}</p></div><div class="hero-art" aria-hidden="true">Aa<span style="font-size:25px">✦</span></div></section>`;
  h += `<section class="stats">${metric("今日新学", s.done_new, "个", `当前目标 ${s.new} 个 · 累计快照`, (100 * s.done_new) / Math.max(1, s.new))}${metric("今日复习", s.done_review, "个", `当前目标 ${s.review} 个 · 新学与复习分开计算`, (100 * s.done_review) / Math.max(1, s.review))}${metric("待补新学", s.debt_new, "个", "仅任务日产生，下一任务日继续")}${metric("待补复习", s.debt_review, "个", `欠量保留 ${c.carry_limit_days} 天 · 不要求熬夜补齐`)}</section>`;
  const dates = Array.from({ length: 7 }, (_, i) => {
    const d = new Date((p.local_date || dayISO()) + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() - 6 + i);
    return d.toISOString().slice(0, 10);
  });
  const max = Math.max(
    1,
    ...dates.flatMap((d) => [p.totals[d]?.new || 0, p.totals[d]?.review || 0]),
  );
  h += `<section class="two-col"><div class="card"><div class="card-head"><h2>最近 7 天的积累</h2><span class="metric-note">每天取已确认记录最大值</span></div><div class="chart">${dates
    .map((d) => {
      const t = p.totals[d] || { new: 0, review: 0 };
      return `<div class="chart-day"><b>${t.new + t.review || ""}</b><div class="chart-bars"><i style="height:${(100 * t.new) / max}%" title="新学${t.new}"></i><i class="review" style="height:${(100 * t.review) / max}%" title="复习${t.review}"></i></div><small>${d.slice(5).replace("-", "/")}</small></div>`;
    })
    .join(
      "",
    )}</div><div class="legend"><span><i></i>新学</span><span><i class="orange"></i>复习</span></div></div><div class="card"><div class="card-head"><h2>有节奏，也有休息</h2><button class="link-button" data-action="edit-plan" data-id="${esc(p.id)}">调整计划 ↗</button></div>${[
    [c.morning, "开启今天的小目标", "任务日提醒，已完成则不打扰"],
    [c.evening, "睡前温柔提醒", "还没完成时提醒，22点前收尾"],
    [c.late, "努力之后，也要休息", "此时间后收到打卡，提示早点休息"],
  ]
    .map(
      ([t, a, b]) =>
        `<div class="schedule-row"><div class="schedule-time">${esc(t)}</div><div class="schedule-copy">${a}<small>${b}</small></div></div>`,
    )
    .join("")}</div></section>`;
  h += `<div class="card"><div class="card-head"><h2>最近的学习记录</h2><button class="link-button" data-action="navigate" data-page="history">查看全部 ↗</button></div>${recordTable(snapshot.records.filter((r) => r.profile === selected).slice(0, 5))}</div>`;
  return h;
}
function plans() {
  return (
    title(
      "给学习留一点自己的节奏",
      "支持多个QQ账号和多个机器人。每个人独立计划，学习量与欠量不混算。",
      `<button class="primary" data-action="new-plan">＋ 新建计划</button>`,
    ) +
    (snapshot.profiles.length
      ? `<div class="plan-list">${snapshot.profiles.map((p) => `<div class="card"><div class="card-head"><h2>${esc(p.config.name)}</h2><span class="badge ${p.config.enabled ? "" : "pending"}">${p.config.enabled ? "进行中" : "已暂停 / 草稿"}</span></div><p class="subtitle">${esc(p.id)} · ${esc(p.config.timezone)}</p><div class="plan-meta"><div><small>每日新学 / 复习</small>${p.config.new_goal} / ${p.config.review_goal} 个</div><div><small>提醒时间</small>${esc(p.config.morning)} · ${esc(p.config.evening)}</div><div><small>机器人 / 适配器</small>${esc(p.config.bot_id || "等待群内绑定")}<small>${esc(p.config.platform_id)}</small></div><div><small>群聊 / 成员</small>${esc(p.config.group_id || "尚未绑定")}<small>${esc(p.config.user_ids.join("、"))}</small></div></div><div class="actions"><button class="primary" data-action="edit-plan" data-id="${esc(p.id)}">管理计划</button><button class="secondary" data-action="copy-plan" data-id="${esc(p.id)}">复制为独立计划</button><button class="secondary" data-action="toggle-plan" data-id="${esc(p.id)}">${p.config.enabled ? "暂停" : "启用"}</button></div>${stateWarning(p)}</div>`).join("")}</div>`
      : empty())
  );
}
const fieldInfo = {
  name: ["计划名称", "陪伴计划的展示名称"],
  start_date: ["开始日期", "从此日开始计算任务与欠量"],
  timezone: ["时区", "默认 Asia/Shanghai"],
  new_goal: ["每日新学目标", "新词数量，与复习分开核算"],
  review_goal: ["每日复习目标", "复习词数量"],
  weekdays: ["固定任务星期", "0=周一，5=周六，6=周日，英文逗号分隔"],
  statutory_holidays: ["包含法定节假日", "中国大陆公布的放假安排"],
  exclude_workdays: ["排除调休上班日", "学校安排不同可在日期例外中覆盖"],
  date_overrides: [
    "日期例外（JSON）",
    '例如 {"2026-10-24":false,"2026-10-25":true}；false免打卡，true布置任务',
  ],
  morning: ["早晨提醒", "默认08:00；仅未完成的任务日发送"],
  evening: ["晚间提醒", "须早于22:00，默认21:30"],
  late: ["太晚打卡界限", "默认22:30，仅收到打卡时回复"],
  reminder_grace_minutes: [
    "提醒补发窗口（分钟）",
    "错过窗口不追发，避免重启刷屏",
  ],
  carry_over: ["漏学量顺延", "新学、复习欠量分别顺延"],
  carry_limit_days: ["欠量有效窗口（天）", "窗口外的欠量过期，默认60天"],
  max_daily_new: ["单次展示新学上限", "剩余欠量仍保留；不是删除欠量"],
  max_daily_review: ["单次展示复习上限", "避免一次催促过量"],
  provider_id: ["图片识别模型ID", "留空使用该群当前模型，必须支持视觉"],
  feedback_provider_id: ["鼓励模型ID", "留空复用识别模型；无需填写API密钥"],
  auto_accept: ["高置信度自动计入", "关闭后所有打卡先待审核"],
  min_confidence: ["自动计入最低置信度", "0.5–1，推荐0.9"],
  allow_text: ["允许本人文字打卡", "/单词打卡 新学20 复习40 为当天累计"],
  ai_feedback: ["启用AI鼓励", "仅向已接入模型发送学习数量与风格"],
  recognition_interval_seconds: [
    "识别冷却（秒）",
    "每个计划的消息处理间隔，节省API费用",
  ],
  retention_days: ["记录保留（天）", "至少覆盖欠量窗口；待审核记录不自动删除"],
  feedback_style: ["鼓励风格", "温柔具体，不比较、不羞辱、不鼓励熬夜"],
  morning_template: ["早晨提醒文案", "支持 {new}、{review} 两个剩余量占位符"],
  evening_template: ["晚间提醒文案", "支持 {new}、{review}；建议强调睡前收尾"],
  late_template: ["太晚打卡文案", "不催补量，优先休息"],
  platform_id: ["适配器实例ID", "由群内绑定捕获，不是适配器类型"],
  bot_id: ["机器人身份ID", "不是AppSecret；由绑定命令捕获"],
  group_id: ["群ID / group_openid", "官方QQ群通常不是数字群号"],
  user_ids: [
    "同一人的身份别名",
    "这里的QQ号/UID/openid共享账本；不同的人请各建一份独立计划",
  ],
  session: ["群会话路由", "在目标群运行绑定命令自动填入，勿猜官方会话"],
};
function field(key, cfg) {
  const [label, hint] = fieldInfo[key],
    value = cfg[key],
    def = snapshot.defaults[key];
  if (typeof def === "boolean")
    return `<label class="field check"><input type="checkbox" name="${key}" ${value ? "checked" : ""}><span>${label}<small style="display:block">${hint}</small></span></label>`;
  const area =
    key.endsWith("template") ||
    key === "feedback_style" ||
    key === "date_overrides";
  let val = Array.isArray(value)
    ? value.join(",")
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : value;
  const inputType =
    typeof def === "number"
      ? "number"
      : key === "start_date"
        ? "date"
        : ["morning", "evening", "late"].includes(key)
          ? "time"
          : "text";
  return `<label class="field ${area ? "full" : ""}"><span>${label}</span>${area ? `<textarea name="${key}" rows="3">${esc(val)}</textarea>` : `<input name="${key}" type="${inputType}" ${inputType === "number" ? 'min="0" step="any"' : ""} ${key.includes("provider_id") ? 'list="models"' : ""} value="${esc(val)}">`}<small>${hint}</small></label>`;
}
function nextPlanId(base = "member") {
  const ids = new Set(snapshot.profiles.map((p) => p.id));
  const stem = base.slice(0, 48);
  if (!ids.has(stem)) return stem;
  let n = 2;
  while (ids.has(`${stem}_${n}`)) n++;
  return `${stem}_${n}`;
}
function editPlan(ident, sourceId = "") {
  const p = snapshot.profiles.find((p) => p.id === ident);
  const source = snapshot.profiles.find((p) => p.id === sourceId);
  const c = structuredClone(p?.config || source?.config || snapshot.defaults);
  const newId = nextPlanId(
    source
      ? source.id + "_copy"
      : snapshot.profiles.length
        ? "member"
        : "family",
  );
  if (!p) {
    c.enabled = false;
    c.start_date = dayISO();
    c.user_ids = [];
    c.session = "";
    if (source) c.name = source.config.name + "（副本）";
  }
  const sections = [
    ["基础目标", ["name", "start_date", "timezone", "new_goal", "review_goal"]],
    [
      "上学与休息日",
      ["weekdays", "statutory_holidays", "exclude_workdays", "date_overrides"],
    ],
    [
      "温柔提醒与补量",
      [
        "morning",
        "evening",
        "late",
        "reminder_grace_minutes",
        "carry_over",
        "carry_limit_days",
        "max_daily_new",
        "max_daily_review",
      ],
    ],
    [
      "识别与鼓励",
      [
        "provider_id",
        "feedback_provider_id",
        "min_confidence",
        "auto_accept",
        "allow_text",
        "ai_feedback",
        "recognition_interval_seconds",
        "retention_days",
        "feedback_style",
      ],
    ],
    [
      "自定义提醒文案",
      ["morning_template", "evening_template", "late_template"],
    ],
    [
      "指定机器人与成员",
      ["platform_id", "bot_id", "group_id", "user_ids", "session"],
    ],
  ];
  $("#content").innerHTML =
    title(
      p ? "调整这份陪伴计划" : "新建学习计划",
      "建议先在目标群 /单词绑定 @妹妹；启用后只处理绑定成员的消息。",
    ) +
    `<div class="notice">多人打卡请各建一份计划，不要把不同人的账号放入同一份身份别名。官方QQ的QQ号、UID和openid无法自动互换。已有记录的身份与基础目标变更请新建计划。</div><form id="plan-form"><div class="card form-section"><div class="form-grid"><label class="field"><span>计划ID</span><input name="id" value="${esc(ident || newId)}" ${p ? "readonly" : ""} required pattern="[A-Za-z0-9_-]+"><small>不同账号使用不同计划ID；绑定时可指定此ID</small></label><label class="field check"><input name="enabled" type="checkbox" ${c.enabled ? "checked" : ""}>启用此计划（须先完成绑定）</label></div></div>${sections.map(([name, keys]) => `<section class="card form-section"><h2>${name}</h2><div class="form-grid">${keys.map((k) => field(k, c)).join("")}</div></section>`).join("")}<datalist id="models">${snapshot.providers.map((p) => `<option value="${esc(p.id)}"></option>`).join("")}</datalist><div class="form-actions"><button class="primary" type="submit">保存计划</button><button class="secondary" type="button" data-action="navigate" data-page="plans">取消</button></div></form>`;
  $("#plan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const cfg = {};
      for (const [key, def] of Object.entries(snapshot.defaults)) {
        const el = f.elements.namedItem(key);
        if (!el) {
          cfg[key] = c[key];
          continue;
        }
        cfg[key] =
          typeof def === "boolean"
            ? el.checked
            : typeof def === "number"
              ? Number(el.value)
              : Array.isArray(def)
                ? el.value
                    .split(/[,，\n]/)
                    .map((s) => s.trim())
                    .filter(Boolean)
                : typeof def === "object"
                  ? JSON.parse(el.value || "{}")
                  : el.value.trim();
        if (key === "weekdays") cfg[key] = cfg[key].map(Number);
      }
      await api("POST", "profile", {
        id: f.elements.id.value,
        config: cfg,
        create_only: !p,
      });
      toast("计划已保存");
      page = "plans";
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  });
}
function review() {
  return (
    title(
      "让每一份努力，都记得准确",
      "已完成数量模糊或识别置信度不足时先确认；未达到目标仍可直接记录部分进度。",
    ) +
    `<div class="notice soft">示例截图应识别为新学20、复习40；2016/4440是书本总进度。数量清晰就自动记录，不因缺少日期而要求审核。</div><div class="card">${recordTable(
      snapshot.records.filter((r) => r.status === "pending"),
      true,
    )}</div>`
  );
}
function calendarPage() {
  const p = plan();
  if (!p) return empty();
  const c = p.config,
    days = p.calendar || [],
    first = days[0]
      ? (new Date(days[0].day + "T12:00:00Z").getUTCDay() + 6) % 7
      : 0;
  return (
    title(
      "学习有安排，休息也有位置",
      "绿色为任务日；国家调休与学校日期例外共同决定。",
      selectPlan(),
    ) +
    stateWarning(p) +
    `<div class="card"><div class="card-head"><h2>${esc(p.local_date?.slice(0, 7) || "")} · 学习日历</h2><button class="link-button" data-action="edit-plan" data-id="${esc(p.id)}">设置调课 / 请假 ↗</button></div><div class="calendar-grid">${["一", "二", "三", "四", "五", "六", "日"].map((x) => `<div class="calendar-weekday">周${x}</div>`).join("")}${"<div></div>".repeat(first)}${days
      .map((d) => {
        const t = p.totals[d.day];
        return `<div class="calendar-day ${d.required ? "required" : ""} ${d.day === p.local_date ? "today" : ""}">${Number(d.day.slice(-2))}<small>${d.required ? "学习日" : "休息 / 上课"}${Object.hasOwn(c.date_overrides, d.day) ? " · 自定义" : ""}</small>${t ? `<b>新${t.new} · 复${t.review}</b>` : ""}</div>`;
      })
      .join(
        "",
      )}</div></div><div class="notice">周五晚可以自愿打卡，默认不增加任务。学校补课、周日提前返校、寒暑假请在日期例外设置；插件不会猜学校课程表。法定日历超出数据年份会明确提示并暂停相关提醒。</div>`
  );
}
function history() {
  return (
    title(
      "每一步，都有记录",
      "统计仅使用已确认记录。同一天多次打卡取累计最大值，不重复相加。",
      `<div class="actions"><button class="secondary" data-action="manual">＋ 手动补录</button><button class="primary" data-action="export">导出 CSV</button></div>`,
    ) +
    `<div class="card">${recordTable(snapshot.records, true)}</div><div class="notice">改小当天累计量：先将错误的大数记录“不计入”，再补录正确值。只改一条小数记录不会覆盖另一条已确认的大数记录。</div>`
  );
}
function connection() {
  return (
    title(
      "让陪伴，只到该到的地方",
      "本插件限制自身的收发范围，账号连接与其他插件开关由 AstrBot 管理。",
    ) +
    `<div class="notice">${esc(snapshot.official_notice)}</div><div class="two-col"><section class="card"><h2>QQ 官方机器人接入</h2><ol class="steps"><li>在 QQ 开放平台创建机器人，按平台要求配置群聊使用权限。</li><li>在 AstrBot「机器人」中添加 QQ 官方适配器，填写 AppID / AppSecret。</li><li>QQ 开放平台的扫码登录用于管理开发者账号，不是插件内扫码登录QQ。插件不收集扫码凭证或AppSecret。</li><li>在实际群聊中 @机器人，发送 <strong>/单词身份</strong> 确认收到消息。</li><li>AstrBot 管理员在目标群使用 <strong>/单词绑定 @妹妹</strong>，或传入实际成员ID。官方消息若不返回被@成员ID，可让妹妹先执行“单词身份”。</li><li>有权接收@消息不等于有权定时主动发送；请确认官方主动群消息权限和额度，再测试发送。</li></ol></section><section class="card"><h2>想实现完全独立的机器人？</h2><p class="subtitle">建议专用账号＋专用 AstrBot 实例，或在 AstrBot 中为该机器人禁用其他插件。本插件无法阻止其他插件推送，也不会修改它们的设置。</p><div class="notice soft">NapCat / OneBot：在适配器自己的受信管理端扫码，连接到 AstrBot 后群内绑定。面板不嵌入登录二维码，不要求暴露管理端口。</div><h3>当前适配器（仅显示非敏感信息）</h3>${snapshot.platforms.length ? snapshot.platforms.map((p) => `<div class="code">${esc(p.id)} · ${esc(p.type)}</div>`).join("") : "<p class='subtitle'>暂无连接的适配器</p>"}</section></div><section class="card"><h2>已绑定计划 · 发送测试</h2>${snapshot.profiles.map((p) => `<div class="schedule-row"><div style="flex:1"><strong>${esc(p.config.name)}</strong><p class="subtitle">${esc(p.config.platform_id || "未绑定")} / 群 ${esc(p.config.group_id || "未绑定")} / 机器人 ${esc(p.config.bot_id || "未绑定")}</p></div><button class="secondary" data-action="test-send" data-id="${esc(p.id)}" ${p.config.enabled ? "" : "disabled"}>向绑定群发测试消息</button></div>`).join("") || "<p class='subtitle'>先创建并绑定一个计划。</p>"}<p class="subtitle">每天每计划最多一次；失败会显示错误，不伪造发送成功。</p></section><section class="card" style="margin-top:20px"><h2>最近的管理操作</h2><div class="audit">${
      snapshot.audit
        .slice(0, 20)
        .map(
          (a) =>
            `<p>${esc(a.created.slice(0, 19))} · ${esc(a.profile)} · ${esc(a.action)} · ${esc(a.detail)}</p>`,
        )
        .join("") || "暂无操作"
    }</div></section>`
  );
}
function render() {
  if (!snapshot) return;
  $("#page-name").textContent = labels[page] || "计划设置";
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $("#content").innerHTML = (
    { dashboard, plans, review, calendar: calendarPage, history, connection }[
      page
    ] || dashboard
  )();
}
function manualForm(row) {
  const ident = row?.profile || selected || snapshot.profiles[0]?.id;
  if (!ident) {
    toast("请先创建计划", true);
    return;
  }
  const p = snapshot.profiles.find((p) => p.id === ident);
  $("#content").innerHTML =
    title(
      row ? `校正记录 #${row.id}` : "手动补录学习记录",
      "只填当天累计完成量。截图当天日期请人工核实。",
    ) +
    `<form id="manual-form" class="card"><div class="form-grid"><label class="field"><span>计划</span><select name="profile" ${row ? "disabled" : ""}>${snapshot.profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === ident ? "selected" : ""}>${esc(p.config.name)}</option>`).join("")}</select></label><label class="field"><span>日期</span><input type="date" name="day" value="${esc(row?.day || p?.local_date || dayISO())}" required ${row ? "readonly" : ""}></label><label class="field"><span>当日新学累计</span><input type="number" min="0" max="2000" name="new" value="${row?.learned || 0}" required></label><label class="field"><span>当日复习累计</span><input type="number" min="0" max="4000" name="review" value="${row?.reviewed || 0}" required></label></div><div class="actions" style="margin-top:22px"><button class="primary" type="submit">确认并计入</button><button class="secondary" type="button" data-action="navigate" data-page="history">取消</button></div></form>`;
  $("#manual-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const f = e.target.elements;
      await api(
        "POST",
        row ? "review" : "manual",
        row
          ? {
              id: row.id,
              action: "accept",
              new: Number(f.new.value),
              review: Number(f.review.value),
            }
          : {
              profile: f.profile.value,
              day: f.day.value,
              new: Number(f.new.value),
              review: Number(f.review.value),
            },
      );
      page = row ? "review" : "history";
      toast("已确认记录");
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  });
}
document.addEventListener("change", (e) => {
  if (e.target.id === "select-plan") {
    selected = e.target.value;
    render();
  }
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.matches("nav button")) {
    page = b.dataset.page;
    render();
    return;
  }
  const action = b.dataset.action;
  if (!action) return;
  b.disabled = true;
  try {
    if (action === "refresh") await refresh();
    else if (action === "navigate") {
      page = b.dataset.page;
      render();
    } else if (action === "new-plan") editPlan("");
    else if (action === "edit-plan") editPlan(b.dataset.id);
    else if (action === "copy-plan") editPlan("", b.dataset.id);
    else if (action === "toggle-plan") {
      const p = snapshot.profiles.find((p) => p.id === b.dataset.id);
      await api("POST", "profile", {
        id: p.id,
        config: { ...p.config, enabled: !p.config.enabled },
      });
      toast("计划状态已更新");
      await refresh();
    } else if (action === "review-edit")
      manualForm(snapshot.records.find((r) => r.id === Number(b.dataset.id)));
    else if (action.startsWith("review-")) {
      await api("POST", "review", {
        id: Number(b.dataset.id),
        action: action === "review-accept" ? "accept" : "reject",
      });
      toast("审核已保存");
      await refresh();
    } else if (action === "manual") manualForm();
    else if (action === "test-send") {
      await api("POST", "test-send", { profile: b.dataset.id });
      toast("测试消息已发送到绑定群");
      await refresh();
    } else if (action === "export") {
      const result = await api("GET", "export");
      const url = URL.createObjectURL(
        new Blob([result.csv], { type: "text/csv;charset=utf-8" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = result.filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  } catch (err) {
    toast(err.message, true);
  } finally {
    b.disabled = false;
  }
});
$("#refresh").addEventListener("click", refresh);
refresh();
