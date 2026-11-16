(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
    return node;
  };
  const when = (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "-");

  let timer = null;
  function toast(text, error = false) {
    const bar = $("#activity");
    bar.hidden = false;
    bar.classList.toggle("error", error);
    $("#activityText").textContent = text;
    clearTimeout(timer);
    timer = setTimeout(() => (bar.hidden = true), 4000);
  }

  async function api(url, method = "GET", body) {
    const res = await fetch(url, {
      method,
      headers: { "X-Requested-With": "fetch", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  async function act(user, status, verb) {
    try {
      await api(`/api/admin/users/${user.id}/status`, "POST", { status });
      toast(`${user.name} ${verb}.`);
      load();
    } catch (err) { toast(err.message, true); }
  }

  async function remove(user) {
    if (!confirm(`Delete ${user.name} (${user.email}) and all of their saved resumes? This can't be undone.`)) return;
    try {
      await api(`/api/admin/users/${user.id}`, "DELETE");
      toast(`${user.name} deleted.`);
      load();
    } catch (err) { toast(err.message, true); }
  }

  const STATUS = { approved: "Active", pending: "Pending", rejected: "Rejected", blocked: "Access off" };

  function render(data) {
    const limit = data.ai_daily_limit;
    $("#limitInfo").textContent = `Each approved user can run ${limit ? `${limit} AI steps per day` : "unlimited AI steps"} on your API key. ` +
      (data.allow_signup ? "New requests are open." : "New requests are closed (ALLOW_SIGNUP=0).");

    const pending = data.users.filter((u) => u.status === "pending");
    const box = $("#pending");
    box.replaceChildren();
    if (!pending.length) box.append(el("div", { class: "card empty" }, "No pending requests."));
    for (const u of pending) {
      box.append(el("div", { class: "card request" },
        el("div", { class: "stack", style: "gap:2px;flex:1;min-width:220px" },
          el("strong", {}, u.name), el("span", { class: "small secondary" }, u.email),
          u.note ? el("span", { class: "small", style: "margin-top:6px" }, `“${u.note}”`) : null,
          el("span", { class: "tiny muted", style: "margin-top:4px" }, `Requested ${when(u.created_at)}`)),
        el("div", { class: "row" },
          el("button", { class: "btn primary sm", onclick: () => act(u, "approved", "approved") }, "Approve"),
          el("button", { class: "btn sm", onclick: () => act(u, "rejected", "rejected") }, "Reject")),
      ));
    }

    const table = $("#users");
    table.replaceChildren(el("thead", {}, el("tr", {},
      ["Name", "Email", "Status", "Last sign-in", "AI steps today", "Saved resumes", ""].map((h) => el("th", {}, h)))));
    const body = el("tbody");
    for (const u of data.users.filter((x) => x.status !== "pending")) {
      const actions = el("div", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" });
      if (!u.is_admin) {
        if (u.status === "approved") actions.append(el("button", { class: "btn sm", onclick: () => act(u, "blocked", "turned off") }, "Turn off access"));
        else actions.append(el("button", { class: "btn sm", onclick: () => act(u, "approved", "approved") }, "Approve"));
        actions.append(el("button", { class: "btn sm ghost", onclick: () => remove(u) }, "Delete"));
      }
      body.append(el("tr", {},
        el("td", {}, u.name, u.is_admin ? el("span", { class: "tag", style: "margin-left:6px" }, "you") : null),
        el("td", { class: "secondary" }, u.email),
        el("td", {}, el("span", { class: `status ${u.status}` }, STATUS[u.status] || u.status)),
        el("td", { class: "secondary small" }, when(u.last_login)),
        el("td", { class: "num" }, String(u.ai_calls_today), !u.is_admin && limit ? el("span", { class: "muted" }, ` / ${limit}`) : null),
        el("td", { class: "num" }, String(u.resumes)),
        el("td", {}, actions),
      ));
    }
    table.append(body);
  }

  async function load() {
    try { render(await api("/api/admin/users")); }
    catch (err) { toast(err.message, true); }
  }
  load();
  setInterval(load, 30000);
})();
