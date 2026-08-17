const $ = (id) => document.getElementById(id);

function fmtQuota(quota) {
  const calls = quota.daily_calls == null ? "∞" : quota.daily_calls;
  const submits = quota.daily_submits == null ? "∞" : quota.daily_submits;
  const suffix = quota.inherited ? " (default)" : "";
  return `${calls} calls / ${submits} submits${suffix}`;
}

function usagePct(used, cap) {
  if (cap == null || cap <= 0) return 0;
  return Math.min(100, Math.round((used / cap) * 100));
}

function resultPill(event) {
  if (event.kind === "denied") return '<span class="pill warn">quota</span>';
  if (event.ok === 0) return '<span class="pill bad">error</span>';
  if (event.ok === 1) return '<span class="pill ok">ok</span>';
  return '<span class="pill">—</span>';
}

async function load() {
  const [overview, users, events] = await Promise.all([
    fetch("/api/overview").then((r) => r.json()),
    fetch("/api/users").then((r) => r.json()),
    fetch("/api/events?limit=40").then((r) => r.json()),
  ]);

  $("day-label").textContent = `UTC day ${overview.day}`;
  $("stats").innerHTML = [
    ["Users", overview.users],
    ["Calls", overview.calls],
    ["Submits", overview.submits],
    ["Quota denials", overview.denied],
    ["Errors", overview.errors],
  ]
    .map(
      ([label, value]) =>
        `<div class="stat"><div class="value">${value}</div><div class="label">${label} today</div></div>`
    )
    .join("");

  const body = $("users");
  if (!users.users.length) {
    body.innerHTML = `<tr><td class="empty" colspan="8">No usage yet. Calls through /mcp/spark will appear here by Knox user.</td></tr>`;
  } else {
    body.innerHTML = users.users
      .map((row) => {
        const cap = row.quota.daily_submits ?? row.quota.daily_calls;
        const used = row.quota.daily_submits != null ? row.usage.submits : row.usage.calls;
        const pct = usagePct(used, cap);
        return `<tr data-sub="${row.sub}">
          <td><code>${row.sub}</code></td>
          <td>${row.usage.calls}</td>
          <td>${row.usage.submits}</td>
          <td>${row.usage.denied}</td>
          <td>${row.usage.errors}</td>
          <td>
            ${fmtQuota(row.quota)}
            <div class="bar" title="${pct}% of capped metric"><span style="width:${pct}%"></span></div>
          </td>
          <td>${row.last_tool || "—"}</td>
          <td>${row.last_seen || "—"}</td>
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
    });
  });

  const eventsBody = $("events");
  if (!events.events.length) {
    eventsBody.innerHTML = `<tr><td class="empty" colspan="6">No events recorded.</td></tr>`;
  } else {
    eventsBody.innerHTML = events.events
      .map(
        (event) => `<tr>
          <td>${event.ts}</td>
          <td><code>${event.sub}</code></td>
          <td><code>${event.token_id || "—"}</code></td>
          <td>${event.tool}</td>
          <td>${resultPill(event)}</td>
          <td><code>${event.request_id || "—"}</code></td>
        </tr>`
      )
      .join("");
  }
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
  const requestId = event.target.request_id.value.trim();
  const status = $("audit-status");
  const result = $("audit-result");
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
});

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

load();
setInterval(load, 8000);
