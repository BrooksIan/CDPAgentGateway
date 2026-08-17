const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtQuota(quota) {
  const calls = quota.daily_calls == null ? "∞" : quota.daily_calls;
  const submits = quota.daily_submits == null ? "∞" : quota.daily_submits;
  const suffix = quota.inherited ? " (default)" : "";
  return `${calls} calls / ${submits} submits${suffix}`;
}

function fmtCap(value) {
  return value == null ? "∞" : value;
}

function usagePct(used, cap) {
  if (cap == null || cap <= 0) return 0;
  return Math.min(100, Math.round((used / cap) * 100));
}

function resultPill(event) {
  if (event.kind === "denied") return '<span class="pill warn">quota 429</span>';
  if (event.ok === 0) return '<span class="pill bad">Livy error</span>';
  if (event.ok === 1) return '<span class="pill ok">ok</span>';
  return '<span class="pill">—</span>';
}

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

function selectedDay() {
  return $("day").value || utcToday();
}

function query(extra = {}) {
  const filters = $("event-filters");
  const params = new URLSearchParams();
  params.set("day", selectedDay());
  const sub = extra.sub ?? filters.sub.value.trim();
  const tool = extra.tool ?? filters.tool.value;
  const result = extra.result ?? filters.result.value;
  if (sub) params.set("sub", sub);
  if (tool) params.set("tool", tool);
  if (result) params.set("result", result);
  params.set("limit", "40");
  return params.toString();
}

function healthDot(state) {
  const tone = state === "ok" ? "ok" : "bad";
  return `<span class="dot ${tone}"></span>${esc(state)}`;
}

async function lookupAudit(requestId) {
  const status = $("audit-status");
  const result = $("audit-result");
  const form = $("audit-form");
  form.request_id.value = requestId;
  const response = await fetch(`/api/audit?request_id=${encodeURIComponent(requestId)}`);
  const payload = await response.json();
  status.hidden = false;
  if (!response.ok) {
    result.hidden = true;
    status.textContent = payload.error || "lookup failed";
    status.classList.add("bad");
    return;
  }
  status.classList.remove("bad");
  status.textContent = `Joined ${payload.audit.tool} for ${payload.audit.sub}`;
  result.hidden = false;
  result.textContent = JSON.stringify(payload.audit, null, 2);
  try {
    await navigator.clipboard.writeText(requestId);
  } catch {
    /* clipboard may be denied */
  }
}

async function load() {
  if (!$("day").value) $("day").value = utcToday();
  const qs = query();
  const day = selectedDay();
  const [status, overview, users, events] = await Promise.all([
    fetch("/api/status").then((r) => r.json()),
    fetch(`/api/overview?day=${encodeURIComponent(day)}`).then((r) => r.json()),
    fetch(`/api/users?day=${encodeURIComponent(day)}`).then((r) => r.json()),
    fetch(`/api/events?${qs}`).then((r) => r.json()),
  ]);

  const burst = status.burst || {};
  const health = status.health || {};
  $("status-strip").innerHTML = [
    `<div><span class="pill ok">quotas enforcing</span> fail-open if this service stops</div>`,
    `<div><span class="pill warn">burst ${esc(burst.count)}/${esc(burst.window)}s</span> on ${esc(burst.route)} · not in sqlite</div>`,
    `<div>mode <code>${esc(status.mode)}</code> · host <code>${esc(status.upstream_host)}</code></div>`,
    `<div>admin ${healthDot(health.admin)} · mcp-spark ${healthDot(health.mcp_spark)} · mcp-hive ${healthDot(health.mcp_hive)} · apisix ${healthDot(health.apisix)}</div>`,
  ].join("");

  $("stats").innerHTML = [
    ["Users", overview.users],
    ["Calls", overview.calls],
    ["Submits", overview.submits],
    ["Quota 429s", overview.denied],
    ["Livy errors", overview.errors],
  ]
    .map(
      ([label, value]) =>
        `<div class="stat"><div class="value">${esc(value)}</div><div class="label">${esc(label)} · ${esc(overview.day)}</div></div>`
    )
    .join("");

  const quotaBody = $("quota-rows");
  const quotas = users.quotas || [];
  if (!quotas.length) {
    quotaBody.innerHTML = `<tr><td class="empty" colspan="4">No quotas stored.</td></tr>`;
  } else {
    quotaBody.innerHTML = quotas
      .map((row) => {
        const label = row.sub === "*" ? "* (default)" : row.sub;
        return `<tr data-sub="${esc(row.sub)}">
          <td><code>${esc(label)}</code></td>
          <td>${esc(fmtCap(row.daily_calls))}</td>
          <td>${esc(fmtCap(row.daily_submits))}</td>
          <td>${esc(row.updated_at || "—")}</td>
        </tr>`;
      })
      .join("");
  }

  quotaBody.querySelectorAll("tr[data-sub]").forEach((row) => {
    row.addEventListener("click", () => {
      const item = quotas.find((q) => q.sub === row.dataset.sub);
      if (!item) return;
      const form = $("quota-form");
      form.sub.value = item.sub;
      form.daily_calls.value = item.daily_calls ?? "";
      form.daily_submits.value = item.daily_submits ?? "";
    });
  });

  const body = $("users");
  if (!users.users.length) {
    body.innerHTML = `<tr><td class="empty" colspan="8">No usage on ${esc(overview.day)}. Calls through /mcp/spark appear here by Knox user.</td></tr>`;
  } else {
    body.innerHTML = users.users
      .map((row) => {
        const cap = row.quota.daily_submits ?? row.quota.daily_calls;
        const used = row.quota.daily_submits != null ? row.usage.submits : row.usage.calls;
        const pct = usagePct(used, cap);
        return `<tr data-sub="${esc(row.sub)}">
          <td><code>${esc(row.sub)}</code></td>
          <td>${esc(row.usage.calls)}</td>
          <td>${esc(row.usage.submits)}</td>
          <td>${esc(row.usage.denied)}</td>
          <td>${esc(row.usage.errors)}</td>
          <td>
            ${esc(fmtQuota(row.quota))}
            <div class="bar" title="${pct}% of capped metric"><span style="width:${pct}%"></span></div>
          </td>
          <td>${esc(row.last_tool || "—")}</td>
          <td>${esc(row.last_seen || "—")}</td>
        </tr>`;
      })
      .join("");
  }

  body.querySelectorAll("tr[data-sub]").forEach((row) => {
    row.addEventListener("click", () => {
      const user = users.users.find((item) => item.sub === row.dataset.sub);
      if (!user) return;
      const form = $("quota-form");
      form.sub.value = user.sub;
      form.daily_calls.value = user.quota.inherited ? "" : user.quota.daily_calls ?? "";
      form.daily_submits.value = user.quota.inherited ? "" : user.quota.daily_submits ?? "";
      $("event-filters").sub.value = user.sub;
      load();
    });
  });

  const eventsBody = $("events");
  if (!events.events.length) {
    eventsBody.innerHTML = `<tr><td class="empty" colspan="6">No events for this day and filter.</td></tr>`;
  } else {
    eventsBody.innerHTML = events.events
      .map(
        (event) => `<tr>
          <td>${esc(event.ts)}</td>
          <td><code class="click-sub" data-sub="${esc(event.sub)}">${esc(event.sub)}</code></td>
          <td><code>${esc(event.token_id || "—")}</code></td>
          <td><code class="click-tool" data-tool="${esc(event.tool)}">${esc(event.tool)}</code></td>
          <td>${resultPill(event)}</td>
          <td>${
            event.request_id
              ? `<code class="click-rid" data-rid="${esc(event.request_id)}" title="Join and copy">${esc(event.request_id)}</code>`
              : "—"
          }</td>
        </tr>`
      )
      .join("");
  }

  eventsBody.querySelectorAll(".click-rid").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      lookupAudit(el.dataset.rid);
    });
  });
  eventsBody.querySelectorAll(".click-sub").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      $("event-filters").sub.value = el.dataset.sub;
      load();
    });
  });
  eventsBody.querySelectorAll(".click-tool").forEach((el) => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      $("event-filters").tool.value = el.dataset.tool;
      load();
    });
  });
}

function showStatus(text, bad = false) {
  const el = $("quota-status");
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("bad", bad);
}

$("quota-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const sub = form.sub.value.trim() || "*";
  const body = {
    daily_calls: form.daily_calls.value === "" ? null : Number(form.daily_calls.value),
    daily_submits: form.daily_submits.value === "" ? null : Number(form.daily_submits.value),
  };
  const response = await fetch(`/api/quotas/${encodeURIComponent(sub)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    showStatus(payload.error || "save failed", true);
    return;
  }
  showStatus(`Saved quota for ${payload.quota.sub}`);
  await load();
});

$("audit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await lookupAudit(event.target.request_id.value.trim());
});

$("event-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  load();
});

$("clear-filters").addEventListener("click", () => {
  const form = $("event-filters");
  form.sub.value = "";
  form.tool.value = "";
  form.result.value = "";
  load();
});

$("day").addEventListener("change", () => load());

$("delete-quota").addEventListener("click", async () => {
  const sub = $("quota-form").sub.value.trim();
  if (!sub || sub === "*") {
    showStatus("Pick a user override to remove.", true);
    return;
  }
  const response = await fetch(`/api/quotas/${encodeURIComponent(sub)}`, { method: "DELETE" });
  const payload = await response.json();
  if (!response.ok) {
    showStatus(payload.error || "delete failed", true);
    return;
  }
  showStatus(`Removed override for ${sub}; default quota applies.`);
  await load();
});

$("day").value = utcToday();
load();
setInterval(load, 8000);
