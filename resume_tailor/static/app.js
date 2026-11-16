/* Resume Tailor front end - plain JS, no build step. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  };
  const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const fmt = (x) => (Math.round(x * 10) / 10).toFixed(1).replace(/\.0$/, "");

  const S = {
    sid: null,
    state: null,
    levers: null,
    viewing: 0,
    viewMode: "prev",
    cache: {},
    editing: false,
    decisions: {},
    texFile: null,
    resumeFile: null,
    jdFile: null,
    texView: "code",
    busy: false,
    health: null,
  };

  // ------------------------------------------------------------------ network
  const HDR = { "X-Requested-With": "fetch" };
  function signedOut(res) {
    if (res.status === 401) { location.href = "/login"; return true; }
    return false;
  }
  async function api(url, opts = {}) {
    const res = await fetch(url, { ...opts, headers: { ...HDR, ...(opts.headers || {}) } });
    if (signedOut(res)) throw new Error("Your sign-in expired. Redirecting…");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorText(data, res));
    return data;
  }
  function errorText(data, res) {
    if (typeof data.error === "string") return data.error;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
    if (typeof data.detail === "string") return data.detail;
    return `${res.status} ${res.statusText}`;
  }
  async function streamPost(url, body, onEvent) {
    const isForm = body instanceof FormData;
    const res = await fetch(url, {
      method: "POST",
      body: isForm ? body : JSON.stringify(body || {}),
      headers: isForm ? HDR : { ...HDR, "Content-Type": "application/json" },
    });
    if (signedOut(res)) throw new Error("Your sign-in expired. Redirecting…");
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(errorText(data, res));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let failure = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.event === "error") failure = new Error(ev.message);
          else onEvent(ev);
        }
      }
    }
    if (failure) throw failure;
  }

  // ------------------------------------------------------------------ activity bar
  let activityTimer = null;
  function activity(msg, kind = "busy") {
    const bar = $("#activity");
    clearTimeout(activityTimer);
    if (!msg) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.classList.toggle("error", kind === "error");
    $("#activitySpin").hidden = kind !== "busy";
    $("#activityText").textContent = msg;
    if (kind === "done") activityTimer = setTimeout(() => (bar.hidden = true), 6000);
  }
  $("#activityClose").addEventListener("click", () => activity(null));

  function setBusy(btn, on) {
    S.busy = on;
    document.querySelectorAll("button.btn").forEach((b) => {
      if (b.dataset.keepEnabled) return;
      if (on) { b.dataset.wasDisabled = b.disabled ? "1" : ""; b.disabled = true; }
      else { b.disabled = b.dataset.wasDisabled === "1"; }
    });
    if (btn) btn.classList.toggle("loading", on);
    if (!on) refreshControls();
  }

  async function guarded(btn, fn) {
    if (S.busy) return;
    setBusy(btn, true);
    try { await fn(); }
    catch (err) { console.error(err); activity(err.message || String(err), "error"); }
    finally { setBusy(btn, false); }
  }

  // ------------------------------------------------------------------ steps
  const STEPS = ["upload", "analysis", "tailor", "levers", "tex"];
  function unlock(upTo) {
    const idx = STEPS.indexOf(upTo);
    STEPS.forEach((step, i) => {
      const link = $(`#stepper a[data-step="${step}"]`);
      link.classList.toggle("locked", i > idx);
      $(`#step-${step}`).hidden = i > idx;
    });
  }
  function markDone() {
    const st = S.state;
    const done = {
      upload: !!st,
      analysis: !!(st && st.timeline.length),
      tailor: !!(st && st.timeline.length > 1),
      levers: !!(st && (st.confirmed.length || st.declined.length)),
      tex: !!(st && st.tex),
    };
    STEPS.forEach((s) => $(`#stepper a[data-step="${s}"]`).classList.toggle("done", done[s]));
  }
  const observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const step = e.target.id.replace("step-", "");
      document.querySelectorAll("#stepper a").forEach((a) => a.classList.toggle("active", a.dataset.step === step));
    }
  }, { rootMargin: "-45% 0px -50% 0px" });
  STEPS.forEach((s) => observer.observe($(`#step-${s}`)));

  // ------------------------------------------------------------------ upload
  function bindDrop(zoneSel, inputSel, onFile) {
    const zone = $(zoneSel), input = $(inputSel);
    input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]));
  }
  bindDrop("#resumeDrop", "#resumeFile", (f) => {
    S.resumeFile = f;
    $("#resumeDrop").classList.add("has-file");
    $("#resumeDropTitle").textContent = f.name;
    $("#resumeDropSub").textContent = `${Math.ceil(f.size / 1024)} KB · click to replace`;
  });
  bindDrop("#jdDrop", "#jdFile", async (f) => {
    S.jdFile = null;
    try {
      const fd = new FormData();
      fd.append("file", f);
      const data = await api("/api/extract", { method: "POST", body: fd });
      $("#jdText").value = data.text;
      $("#jdDrop").classList.add("has-file");
      $("#jdDropTitle").textContent = `Loaded ${f.name} - edit above if needed`;
    } catch (err) { activity(err.message, "error"); }
  });
  bindDrop("#texDrop", "#texFile", async (f) => {
    S.texFile = f;
    $("#texDrop").classList.add("has-file");
    $("#texDropTitle").textContent = f.name;
    $("#texText").value = await f.text();
  });

  $("#analyzeBtn").addEventListener("click", (e) => guarded(e.currentTarget, async () => {
    const fd = new FormData();
    if (S.resumeFile) fd.append("resume_file", S.resumeFile);
    else fd.append("resume_text", $("#resumeText").value);
    fd.append("jd_text", $("#jdText").value);
    activity("Reading your files…");
    const created = await api("/api/session", { method: "POST", body: fd });
    S.sid = created.id;
    S.cache = {};
    S.decisions = {};
    remember();
    await streamPost(`/api/session/${S.sid}/analyze`, null, (ev) => {
      if (ev.event === "status") activity(ev.message);
      if (ev.event === "done") applyState(ev.state);
    });
    activity(`ATS score: ${S.state.version.report.display_score}/100. Scroll down for the recruiter review and tailoring.`, "done");
    $("#step-analysis").scrollIntoView({ behavior: "smooth" });
  }));

  function remember() {
    try { localStorage.setItem("rt_sid", S.sid); } catch (_) { /* storage unavailable */ }
    history.replaceState(null, "", `#s=${S.sid}`);
  }

  $("#newBtn").addEventListener("click", () => {
    try { localStorage.removeItem("rt_sid"); } catch (_) { /* ignore */ }
    location.href = location.pathname;
  });

  // ------------------------------------------------------------------ state
  function applyState(state) {
    S.state = state;
    if (state.levers) S.levers = state.levers;
    S.viewing = state.current;
    if (state.version) S.cache[`${state.version.n}:prev`] = state.version;
    renderAll();
  }

  function renderAll() {
    const st = S.state;
    if (!st || !st.version) { unlock("upload"); return; }
    unlock("tex");
    markDone();
    renderAnalysis();
    renderTailor();
    renderLevers();
    renderTex();
    refreshControls();
  }

  function refreshControls() {
    const st = S.state;
    if (!st) return;
    const ai = st.ai_available;
    ["#autoBtn", "#stepBtn", "#recheckBtn", "#texBtn"].forEach((sel) => { if (!S.busy) $(sel).disabled = !ai; });
    $("#autoBtn").childNodes[1].textContent = `Auto-tailor to ${st.target.min}–${st.target.max}`;
    $("#targetLabel").textContent = `${st.target.min}–${st.target.max}`;
    if (!S.busy) $("#applyConfirmBtn").disabled = !ai || !Object.keys(S.decisions).length;
  }

  // ------------------------------------------------------------------ analysis
  function latestReview(upTo) {
    const reviews = S.state.reviews || {};
    const keys = Object.keys(reviews).map(Number).filter((n) => n <= upTo).sort((a, b) => b - a);
    return keys.length ? { n: keys[0], review: reviews[keys[0]] } : null;
  }

  function bandEl(band) {
    return el("span", { class: `band ${band.id}` }, el("span", { class: "dot" }), band.label);
  }

  function renderAnalysis() {
    const st = S.state;
    const v = st.version;
    const r = v.report;
    $("#analysisSub").textContent = v.n === 0
      ? "Your original resume against this JD."
      : `Showing version ${v.n} (${v.label}). Your original scored ${st.timeline[0].display_score}.`;

    const card = $("#atsCard");
    card.replaceChildren();
    const delta = r.score - st.timeline[0].score;
    card.append(
      el("div", { class: "eyebrow" }, "ATS match score", v.n ? ` · version ${v.n}` : " · original"),
      el("div", { class: "score-hero" },
        el("div", { class: "score-big num" }, String(r.display_score), el("small", {}, "/100")),
        el("div", { class: "stack", style: "gap:4px" },
          bandEl(r.band),
          v.n ? el("span", { class: "small secondary num" }, `${delta >= 0 ? "+" : ""}${fmt(delta)} vs original`) : el("span", { class: "small muted" }, `${st.profile.keywords.length} JD keywords · ${st.profile.source === "ai" ? "AI-extracted" : "offline extractor"}`),
        ),
      ),
    );
    const meter = el("div", { class: "meter" },
      el("div", { class: "target", style: `left:${st.target.min}%;width:${st.target.max - st.target.min}%`, title: "Target band" }),
      el("div", { class: "fill", style: `width:${Math.max(1, r.score)}%` }),
    );
    card.append(meter, el("div", { class: "meter-scale" }, el("span", {}, "0"), el("span", {}, `target ${st.target.min}–${st.target.max}`), el("span", {}, "100")));

    const comps = el("div", { class: "components" });
    for (const c of r.components) {
      if (c.id === "penalty") {
        if (c.score >= 0) continue;
        comps.append(el("div", { class: "comp penalty" },
          el("span", {}, c.label), el("span", { class: "val num" }, fmt(c.score)),
          el("span", { class: "detail" }, c.tips.join(" ") || c.detail)));
        continue;
      }
      comps.append(el("div", { class: "comp" },
        el("span", {}, c.label),
        el("span", { class: "val num secondary" }, `${fmt(c.score)} / ${c.max}`),
        el("div", { class: "bar" }, el("span", { style: `width:${Math.max(0, (c.score / c.max) * 100)}%` })),
        el("span", { class: "detail" }, c.detail),
      ));
    }
    card.append(comps);
    const notices = st.notices || [];
    if (st.profile.source !== "ai") notices.unshift("Offline mode: keywords come from a built-in skills list. Add an API key for AI extraction, tailoring and the recruiter review.");
    if (notices.length) card.append(el("div", { class: "notice", style: "margin-top:14px" }, notices.join(" ")));

    renderRecruiter();
    renderKeywords(r);
  }

  function renderRecruiter() {
    const card = $("#recruiterCard");
    card.replaceChildren();
    const st = S.state;
    const found = latestReview(st.current);
    card.append(el("div", { class: "eyebrow" }, "Senior recruiter review", found ? ` · version ${found.n}` : ""));
    if (!found) {
      card.append(el("p", { class: "secondary", style: "margin-top:8px" },
        st.ai_available ? "No review for this version yet." : "Add an Anthropic API key to get a senior recruiter's review (match score + top 5 missing keywords)."));
      if (st.ai_available) card.append(el("button", { class: "btn sm", style: "margin-top:10px", onclick: (e) => recheck(e.currentTarget) }, "Run recruiter review"));
      return;
    }
    const rv = found.review;
    card.append(
      el("div", { class: "recruiter-head", style: "margin-top:6px" },
        el("div", { class: "ring", style: `--p:${rv.match_score}` }, el("span", { class: "num" }, String(rv.match_score))),
        el("div", { class: "stack", style: "gap:4px;flex:1;min-width:200px" },
          el("span", { class: "verdict" }, rv.verdict, " ", el("span", { class: "small muted" }, rv.would_shortlist ? "· would shortlist" : "· would not shortlist yet")),
          el("span", { class: "small secondary" }, rv.summary),
        ),
      ),
      el("h3", { style: "margin-top:16px" }, "Top 5 missing keywords for ATS"),
    );
    const list = el("ol", { class: "missing-list" });
    for (const k of rv.top_missing_keywords || []) {
      list.append(el("li", {},
        el("span", { class: "kw" }, k.keyword),
        el("span", { class: "why" }, k.why_it_matters),
        el("span", { class: "where" }, "Where: ", k.where_to_add, k.evidence_in_resume && !/^none/i.test(k.evidence_in_resume) ? ` · Evidence: “${k.evidence_in_resume}”` : " · No evidence on your resume yet"),
      ));
    }
    card.append(list);
    const col = (title, items) => el("div", {}, el("h3", {}, title), el("ul", {}, (items || []).map((t) => el("li", {}, t))));
    card.append(el("div", { class: "cols-3" }, col("Strengths", rv.strengths), col("Concerns", rv.concerns), col("Quick wins", rv.quick_wins)));
    if (rv.seniority_fit) card.append(el("p", { class: "small muted", style: "margin-top:12px" }, "Seniority fit: ", rv.seniority_fit));
  }

  const IMP = { required: "req", preferred: "pref", nice_to_have: "nice" };
  function renderKeywords(report) {
    const box = $("#kwGroups");
    box.replaceChildren();
    const groups = [
      ["matched", "✓", "Matched in your experience"],
      ["skills_only", "◐", "Only in your skills list"],
      ["missing", "✕", "Missing"],
    ];
    const k = report.keywords;
    $("#kwSub").textContent = `${k.matched.length} matched · ${k.skills_only.length} skills-only · ${k.missing.length} missing`;
    for (const [id, icon, title] of groups) {
      const items = k[id];
      if (!items.length) continue;
      box.append(el("div", {},
        el("div", { class: "small secondary", style: "margin-bottom:6px;font-weight:600" }, title),
        el("div", { class: "chips" }, items.map((h) => el("span", { class: `chip ${id}`, title: `${h.importance.replace("_", " ")} · ${h.category.replace("_", " ")} · found ${h.count}x` },
          el("span", { class: "ic" }, icon), h.term, el("span", { class: "imp" }, IMP[h.importance] || ""))))));
    }
  }

  // ------------------------------------------------------------------ tailor: preview
  async function loadVersion(n, mode) {
    const compare = mode === "clean" ? "none" : mode;
    const key = `${n}:${compare}`;
    if (S.cache[key]) return S.cache[key];
    const v = await api(`/api/session/${S.sid}/version/${n}?compare=${compare}`);
    S.cache[key] = v;
    return v;
  }

  function keywordRegex() {
    const kws = (S.state.profile && S.state.profile.keywords) || [];
    const variants = new Set();
    for (const k of kws) {
      for (const t of [k.term, ...(k.aliases || [])]) {
        if (!t || t.length < 2) continue;
        variants.add(t);
        if (/\w[./-]\w/.test(t)) { variants.add(t.replace(/(\w)[./-](\w)/g, "$1$2")); variants.add(t.replace(/(\w)[./-](\w)/g, "$1 $2")); }
      }
    }
    if (!variants.size) return null;
    const esc = [...variants].sort((a, b) => b.length - a.length).map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "[\\s_/-]+"));
    return new RegExp(`(?<![A-Za-z0-9+#])(?:${esc.join("|")})(?:s|es)?(?![A-Za-z0-9+#])`, "gi");
  }

  function hl(text, re) {
    const frag = document.createDocumentFragment();
    if (!re || !$("#hlKeywords").checked) { frag.append(text); return frag; }
    let last = 0;
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) frag.append(text.slice(last, m.index));
      frag.append(el("mark", { class: "kw" }, m[0]));
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.append(text.slice(last));
    return frag;
  }

  const PREFIX = /^(#{1,3} |\s*[-•*]\s+)/;
  function classify(text) {
    if (/^# /.test(text)) return ["r-name", text.slice(2)];
    if (/^## /.test(text)) return ["r-section", text.slice(3)];
    if (/^### /.test(text)) return ["r-entry", text.slice(4)];
    if (/^\s*[-•*]\s+/.test(text)) return ["r-bullet", text.replace(/^\s*[-•*]\s+/, "")];
    if (!text.trim()) return ["r-gap", ""];
    return ["r-text", text];
  }

  function stripLead(ops, n) {
    const out = [];
    let rem = n;
    for (const op of ops) {
      if (rem > 0 && op.op === "delete" && /^[\s#*•-]+$/.test(op.text)) continue;
      if (rem > 0 && op.op !== "delete") {
        if (op.text.length <= rem) { rem -= op.text.length; continue; }
        out.push({ op: op.op, text: op.text.slice(rem) });
        rem = 0;
        continue;
      }
      out.push(op);
    }
    return out;
  }

  function renderPreview(v) {
    const paper = $("#paper");
    paper.replaceChildren();
    const re = keywordRegex();
    const rows = v.diff || v.text.split("\n").map((t) => ({ type: "equal", text: t }));
    let seenSection = false;
    let seenName = false;
    for (const row of rows) {
      const text = row.type === "replace" ? row.new : row.text;
      let [cls, body] = classify(text);
      if (cls === "r-name") seenName = true;
      if (cls === "r-section") seenSection = true;
      if (cls === "r-text" && seenName && !seenSection) cls = "r-contact";
      const line = el("div", { class: `${cls} line` });
      if (row.type === "insert" && text.trim()) line.classList.add("ins");
      if (row.type === "delete") { if (!text.trim()) continue; line.classList.add("del"); }
      if (row.type === "replace") {
        line.classList.add("rep");
        const prefixLen = (text.match(PREFIX) || [""])[0].length;
        for (const op of stripLead(row.words, prefixLen)) {
          if (op.op === "equal") line.append(hl(op.text, re));
          else if (op.op === "insert") line.append(el("ins", { class: "w" }, hl(op.text, re)));
          else line.append(el("del", { class: "w" }, op.text));
        }
      } else if (cls === "r-entry") {
        const parts = body.split(/\s+\|\s+/);
        line.append(el("span", {}, hl(parts[0], re)));
        parts.slice(1).forEach((p) => line.append(el("span", { class: "meta" }, hl(p, re))));
      } else {
        line.append(hl(body, re));
      }
      paper.append(line);
    }
  }

  async function showVersion(n) {
    if (S.editing) toggleEdit(false);
    S.viewing = n;
    const v = await loadVersion(n, S.viewMode);
    if (S.viewing !== n) return;
    $("#previewTitle").textContent = `v${v.n} · ${v.label}`;
    const ds = v.diff_stats;
    $("#previewMeta").textContent = `ATS ${v.report.display_score}` + (ds ? ` · ${ds.changed} edited, ${ds.added} added, ${ds.removed} removed lines` : "");
    renderPreview(v);
    $("#downloadMd").href = `/api/session/${S.sid}/resume/download?version=${n}`;
    $("#viewingNote").hidden = n === S.state.current;
    renderVersionChips();
    renderChart();
    renderSidePanels(v);
  }

  document.querySelectorAll("#viewMode button").forEach((b) => b.addEventListener("click", () => {
    S.viewMode = b.dataset.mode;
    document.querySelectorAll("#viewMode button").forEach((x) => x.classList.toggle("on", x === b));
    showVersion(S.viewing).catch((e) => activity(e.message, "error"));
  }));
  $("#hlKeywords").addEventListener("change", () => showVersion(S.viewing));

  function toggleEdit(on) {
    S.editing = on;
    const body = $("#previewBody");
    if (on) {
      const v = S.cache[`${S.viewing}:none`] || S.cache[`${S.viewing}:prev`] || S.cache[`${S.viewing}:original`];
      const ta = el("textarea", { class: "edit-area", id: "editArea", spellcheck: "true" });
      ta.value = v ? v.text : "";
      body.replaceChildren(ta);
      $("#editBtn").textContent = "Save as new version";
      $("#editBtn").classList.add("primary");
    } else {
      body.replaceChildren(el("div", { class: "paper", id: "paper" }));
      $("#editBtn").textContent = "Edit";
      $("#editBtn").classList.remove("primary");
    }
  }
  $("#editBtn").addEventListener("click", (e) => {
    if (!S.editing) { toggleEdit(true); return; }
    guarded(e.currentTarget, async () => {
      const text = $("#editArea").value;
      await api(`/api/session/${S.sid}/edit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      toggleEdit(false);
      await reloadState();
      activity(`Saved your edit as v${S.state.current} - ATS ${S.state.version.report.display_score}.`, "done");
    });
  });

  async function reloadState() {
    const state = await api(`/api/session/${S.sid}`);
    S.cache = {};
    applyState(state);
  }

  $("#revertBtn").addEventListener("click", (e) => guarded(e.currentTarget, async () => {
    const state = await api(`/api/session/${S.sid}/revert`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: S.viewing }) });
    S.cache = {};
    applyState(state);
    activity(`Continuing from v${state.current}. Later versions will be replaced by the next iteration.`, "done");
  }));

  // ------------------------------------------------------------------ tailor: chart
  function renderChart() {
    const st = S.state;
    const tl = st.timeline;
    const wrap = $("#chart");
    wrap.replaceChildren();
    const W = 520, H = 200, m = { l: 30, r: 58, t: 12, b: 24 };
    const scores = tl.map((t) => t.score);
    const recs = tl.map((t) => t.review_score).filter((x) => x != null);
    const lo = Math.min(...scores, ...recs, st.target.min);
    const yMin = Math.max(0, Math.floor((lo - 8) / 10) * 10);
    const x = (i) => m.l + (tl.length === 1 ? (W - m.l - m.r) / 2 : (i * (W - m.l - m.r)) / (tl.length - 1));
    const y = (v) => m.t + ((100 - v) / (100 - yMin)) * (H - m.t - m.b);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "ATS score by iteration" });

    for (let g = yMin; g <= 100; g += 10) {
      svg.append(svgEl("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(g), y2: y(g) }));
      const t = svgEl("text", { class: "axis-label", x: m.l - 6, y: y(g) + 4, "text-anchor": "end" });
      t.textContent = g;
      svg.append(t);
    }
    svg.append(svgEl("rect", { class: "band-rect", x: m.l, width: W - m.l - m.r, y: y(st.target.max), height: Math.max(2, y(st.target.min) - y(st.target.max)) }));
    const bl = svgEl("text", { class: "band-label", x: W - m.r + 6, y: y((st.target.min + st.target.max) / 2) + 4 });
    bl.textContent = "target";
    svg.append(bl);
    const step = Math.ceil(tl.length / 8);
    tl.forEach((t, i) => {
      if (i % step !== 0 && i !== tl.length - 1) return;
      const lab = svgEl("text", { class: "axis-label", x: x(i), y: H - 6, "text-anchor": "middle" });
      lab.textContent = `v${t.n}`;
      svg.append(lab);
    });

    const recPts = tl.map((t, i) => [i, t.review_score]).filter(([, v]) => v != null);
    if (recPts.length > 1) svg.append(svgEl("path", { class: "line-rec", d: recPts.map(([i, v], k) => `${k ? "L" : "M"}${x(i)},${y(v)}`).join(" ") }));
    if (tl.length > 1) svg.append(svgEl("path", { class: "line-ats", d: tl.map((t, i) => `${i ? "L" : "M"}${x(i)},${y(t.score)}`).join(" ") }));
    recPts.forEach(([i, v]) => svg.append(svgEl("rect", { class: "pt-rec", x: x(i) - 4.5, y: y(v) - 4.5, width: 9, height: 9, rx: 2 })));
    tl.forEach((t, i) => svg.append(svgEl("circle", { class: `pt-ats${t.n === S.viewing ? " pt-current" : ""}`, cx: x(i), cy: y(t.score), r: t.n === S.viewing ? 6 : 4.5 })));

    // Selective direct labels: first and last value only.
    const label = (i) => {
      const isLast = i === tl.length - 1 && tl.length > 1;
      // First value sits below its point (the line rises away from it); last sits to the right.
      const t = svgEl("text", { class: "value-label num", x: x(i) + (isLast ? 10 : 0), y: isLast ? y(tl[i].score) + 4 : y(tl[i].score) + 19, "text-anchor": isLast ? "start" : "middle" });
      t.textContent = tl[i].display_score;
      svg.append(t);
    };
    label(0);
    if (tl.length > 1) label(tl.length - 1);

    const tip = el("div", { class: "tooltip", hidden: true });
    tl.forEach((t, i) => {
      const hit = svgEl("circle", { class: "hit", cx: x(i), cy: y(t.score), r: 14 });
      hit.addEventListener("mouseenter", () => {
        const scale = wrap.clientWidth / W;
        tip.hidden = false;
        tip.textContent = `v${t.n} · ${t.label} · ATS ${t.display_score}` + (t.review_score != null ? ` · Recruiter ${t.review_score}` : "") + (t.flags ? ` · ${t.flags} check(s)` : "");
        tip.style.left = `${x(i) * scale}px`;
        tip.style.top = `${y(t.score) * scale}px`;
      });
      hit.addEventListener("mouseleave", () => (tip.hidden = true));
      hit.addEventListener("click", () => showVersion(t.n));
      svg.append(hit);
    });
    wrap.append(svg, tip);

    const legend = $("#chartLegend");
    legend.replaceChildren(
      el("span", {}, el("i", {}), "ATS score"),
      recPts.length ? el("span", {}, el("i", { class: "rec" }), "Recruiter score") : null,
      el("span", {}, el("i", { class: "band-sw" }), `Target ${st.target.min}–${st.target.max}`),
    );
    const first = tl[0].score, last = tl[tl.length - 1].score;
    $("#deltaLabel").textContent = tl.length > 1 ? `${tl[0].display_score} → ${tl[tl.length - 1].display_score} (${last - first >= 0 ? "+" : ""}${fmt(last - first)})` : "";
  }

  function renderVersionChips() {
    const box = $("#versions");
    box.replaceChildren();
    S.state.timeline.forEach((t, i) => {
      if (i) box.append(el("span", { class: "arrow muted" }, "→"));
      box.append(el("button", {
        class: `vchip${t.n === S.viewing ? " viewing" : ""}${t.n === S.state.current ? " current" : ""}`,
        title: t.label, onclick: () => showVersion(t.n),
      }, `v${t.n}`, el("span", { class: "muted" }, t.label.split(/[ -]/)[0]), el("b", {}, String(t.display_score))));
    });
  }

  // ------------------------------------------------------------------ tailor: side panels
  function renderSidePanels(v) {
    const qs = (v.questions || []);
    const qBox = $("#questions");
    qBox.replaceChildren();
    const open = qs.filter((q) => !q.answered);
    $("#qCount").textContent = open.length;
    $("#qCount").classList.toggle("hot", open.length > 0);
    if (!qs.length) qBox.append(el("div", { class: "empty" }, v.n === 0 ? "Questions appear here after an iteration, when a bullet needs a real metric." : "No open questions - nice."));
    for (const q of qs) {
      const input = el("input", { type: "text", "data-qid": q.id, placeholder: q.example ? `e.g. ${q.example}` : "Real number, or 'skip'", disabled: q.answered || v.n !== S.state.current });
      qBox.append(el("div", { class: `question${q.answered ? " answered" : ""}` },
        el("span", { class: "q" }, q.question),
        q.bullet ? el("span", { class: "b" }, q.bullet) : null,
        q.answered ? el("span", { class: "tiny muted" }, "Answered ✓") : input,
      ));
    }
    if (open.length) qBox.append(el("div", { class: "tiny muted" }, "Answers are saved as verified facts and used in the next iteration. Type 'skip' if you don't have a reliable number."));

    const fBox = $("#flags");
    fBox.replaceChildren();
    const flags = v.flags || [];
    $("#flagCount").textContent = flags.length;
    $("#flagCount").classList.toggle("warn", flags.length > 0);
    if (!flags.length) fBox.append(el("div", { class: "empty" }, "✓ Every number and new keyword traces back to your resume or your answers."));
    for (const f of flags) {
      fBox.append(el("div", { class: `flag ${f.severity}` }, el("span", { class: "ic" }, f.severity === "high" ? "!" : "?"), el("span", {}, f.message), el("span", { class: "ex" }, f.excerpt)));
    }
    if (flags.length) fBox.append(el("div", { class: "tiny muted" }, "The next iteration fixes these automatically, or answer the questions to verify a number."));

    const cBox = $("#changes");
    cBox.replaceChildren();
    $("#changeCount").textContent = (v.changes || []).length;
    if (!(v.changes || []).length) cBox.append(el("div", { class: "empty" }, "No changes recorded for this version."));
    for (const c of v.changes || []) {
      cBox.append(el("div", { class: "change" },
        el("div", { class: "tiny muted" }, c.section),
        c.before ? el("div", { class: "before" }, c.before) : null,
        el("div", { class: "after" }, c.after),
        el("div", { class: "why" }, c.reason)));
    }

    const kBox = $("#kwEvidence");
    kBox.replaceChildren();
    const added = v.keywords_added || [], skipped = v.keywords_skipped || [];
    $("#kwEvidenceCount").textContent = added.length + skipped.length;
    if (!added.length && !skipped.length) kBox.append(el("div", { class: "empty" }, "—"));
    if (added.length) kBox.append(el("div", { class: "small", style: "font-weight:600" }, "Added, with evidence from your resume"));
    for (const a of added) kBox.append(el("div", { class: "change" }, el("b", {}, a.keyword), el("div", { class: "why" }, `“${a.evidence_quote}”`)));
    if (skipped.length) kBox.append(el("div", { class: "small", style: "font-weight:600;margin-top:6px" }, "Skipped - no evidence (see Score levers)"));
    for (const s of skipped) kBox.append(el("div", { class: "change" }, el("b", {}, s.keyword), el("div", { class: "why" }, s.reason)));

    const sum = $("#phaseSummary");
    sum.hidden = !v.summary || v.n === 0;
    sum.textContent = v.summary || "";
  }

  function nextPhaseHint() {
    const st = S.state;
    const pending = st.facts.some((f) => f.kind === "confirmed_skill" && !f.applied);
    const order = [["alignment", "Alignment & keywords"], ["impact", "Impact (X-Y-Z bullets)"], ["polish", "Polish & naturalness"]];
    let next = pending ? "Apply confirmed skills" : null;
    if (!next) for (const [id, name] of order) if (!st.phases_done.includes(id)) { next = name; break; }
    if (!next) next = "Refine remaining gaps (or your instructions)";
    $("#nextPhaseHint").textContent = st.ai_available ? `Next iteration: ${next}` : "AI tailoring is off - set ANTHROPIC_API_KEY and restart the app.";
  }

  function renderTailor() {
    const st = S.state;
    if (!$("#instructions").value && st.instructions) $("#instructions").value = st.instructions;
    nextPhaseHint();
    showVersion(S.viewing).catch((e) => activity(e.message, "error"));
  }

  function collectAnswers() {
    return [...document.querySelectorAll("#questions input[data-qid]")]
      .map((i) => ({ id: i.dataset.qid, answer: i.value.trim() }))
      .filter((a) => a.answer);
  }

  async function iterate(btn, { mode, phase = null, instructions = null, note = null }) {
    await guarded(btn, async () => {
      if (S.editing) toggleEdit(false);
      const body = {
        mode,
        instructions: instructions != null ? instructions : $("#instructions").value,
        answers: collectAnswers(),
        note: note != null ? note : $("#factNote").value,
        phase: phase || $("#phaseSelect").value || null,
      };
      activity(mode === "auto" ? "Auto-tailoring… each pass is scored as it finishes." : "Tailoring…");
      $("#step-tailor").scrollIntoView({ behavior: "smooth" });
      await streamPost(`/api/session/${S.sid}/iterate`, body, (ev) => {
        if (ev.event === "status") activity(ev.message);
        if (ev.event === "iteration") {
          const v = ev.version;
          S.cache[`${v.n}:prev`] = v;
          for (const k of Object.keys(S.cache)) if (!k.endsWith(":prev") && Number(k.split(":")[0]) >= v.n) delete S.cache[k];
          S.state.timeline = ev.timeline;
          S.state.current = v.n;
          S.state.version = v;
          S.viewing = v.n;
          S.viewMode === "prev" || (S.viewMode = "prev");
          document.querySelectorAll("#viewMode button").forEach((x) => x.classList.toggle("on", x.dataset.mode === "prev"));
          renderAnalysis();
          showVersion(v.n);
          activity(`v${v.n} ${v.label}: ATS ${v.report.display_score}${v.flags.length ? ` · ${v.flags.length} check(s) to review` : ""}`);
        }
        if (ev.event === "done") {
          S.cache = {};
          applyState(Object.assign(ev.state, { levers: ev.levers }));
        }
      });
      $("#factNote").value = "";
      $("#phaseSelect").value = "";
      const v = S.state.version;
      activity(`Done - v${v.n} scores ${v.report.display_score}/100 (${v.report.band.label}).`, "done");
    });
  }
  $("#autoBtn").addEventListener("click", (e) => iterate(e.currentTarget, { mode: "auto" }));
  $("#stepBtn").addEventListener("click", (e) => iterate(e.currentTarget, { mode: "single" }));

  async function recheck(btn) {
    await guarded(btn, async () => {
      await streamPost(`/api/session/${S.sid}/recruiter?version=${S.state.current}`, null, (ev) => {
        if (ev.event === "status") activity(ev.message);
        if (ev.event === "done") {
          S.state.reviews = S.state.reviews || {};
          S.state.reviews[ev.version] = ev.review;
          S.state.timeline = ev.timeline;
          renderRecruiter();
          renderChart();
          activity(`Recruiter score for v${ev.version}: ${ev.review.match_score}/100 - ${ev.review.verdict}`, "done");
        }
      });
    });
  }
  $("#recheckBtn").addEventListener("click", (e) => recheck(e.currentTarget));

  // ------------------------------------------------------------------ levers
  function renderLevers() {
    const lv = S.levers;
    const box = $("#levers"), bBox = $("#bundles");
    box.replaceChildren();
    bBox.replaceChildren();
    if (!lv) return;
    const byId = Object.fromEntries(lv.levers.map((l) => [l.id, l]));
    for (const b of lv.bundles) {
      const names = b.lever_ids.map((id) => byId[id]).filter(Boolean);
      bBox.append(el("div", { class: "bundle" },
        el("span", {}, "To reach ", el("span", { class: "tgt" }, String(b.target)), ":"),
        names.length
          ? el("span", { class: "chips" }, names.map((l) => el("span", { class: "chip" }, l.needs_confirmation ? "⚑ " : "", l.keyword || l.title)))
          : el("span", { class: "secondary" }, b.summary),
        names.length ? el("span", { class: "num", style: "font-weight:700" }, `→ ${Math.round(b.projected)}`) : null,
        !b.reachable && names.length ? el("span", { class: "small muted" }, "(not fully reachable with these levers)") : null,
      ));
    }
    if (!lv.top5.length) {
      box.append(el("div", { class: "empty" }, "No remaining levers - your resume covers what this engine can measure. Move on to the LaTeX step."));
      return;
    }
    lv.top5.forEach((l, i) => {
      const card = el("div", { class: "card lever" },
        el("span", { class: "rank" }, `#${i + 1}`),
        el("div", { class: "gain" }, `+${fmt(l.delta)}`, el("small", {}, ` → ${Math.round(l.projected)}`)),
        el("h3", {}, l.title),
        el("span", { class: `tag${l.needs_confirmation ? " claim" : ""}` }, l.needs_confirmation ? "Your call" : "Rewording only"),
        el("p", {}, l.description),
      );
      if (l.kind === "keyword") card.append(decisionControls(l));
      else if (l.kind === "title") card.append(decisionControls(l, "Yes, this title fits me", "No, keep mine"));
      else if (l.kind === "impact") card.append(el("button", { class: "btn sm", onclick: () => { $("#qPanel").open = true; $("#qPanel").scrollIntoView({ behavior: "smooth", block: "center" }); } }, "Answer the metric questions"));
      else card.append(el("button", { class: "btn sm", onclick: (e) => iterate(e.currentTarget, { mode: "single", phase: "refine", instructions: `${$("#instructions").value}\nPriority: ${l.title}. ${l.description}`.trim() }) }, el("span", { class: "spinner spin" }), "Let AI apply this"));
      box.append(card);
    });
    refreshControls();
  }

  function decisionControls(l, yes = "I have this", no = "I don't") {
    const key = l.keyword || l.id;
    const name = `dec-${key.replace(/\W+/g, "_")}`;
    const prev = S.decisions[key];
    const ctx = el("input", { type: "text", placeholder: l.kind === "title" ? "Optional note" : "Where/how did you use it? (role, what you did)", value: prev ? prev.context : "" });
    const wrap = el("div", { class: "stack", style: "gap:8px" });
    const update = () => {
      const picked = wrap.querySelector(`input[name="${name}"]:checked`);
      if (!picked) { delete S.decisions[key]; }
      else S.decisions[key] = { lever: l, confirmed: picked.value === "yes", context: ctx.value.trim() };
      ctx.hidden = !(picked && picked.value === "yes");
      refreshControls();
    };
    const opt = (value, text) => el("label", {}, el("input", { type: "radio", name, value, checked: prev ? (prev.confirmed ? value === "yes" : value === "no") : false, onchange: update }), text);
    wrap.append(el("div", { class: "choice" }, opt("yes", yes), opt("no", no)), ctx);
    ctx.hidden = !(prev && prev.confirmed);
    ctx.addEventListener("input", update);
    return wrap;
  }

  $("#applyConfirmBtn").addEventListener("click", (e) => {
    const btn = e.currentTarget;
    const decisions = Object.values(S.decisions);
    const items = decisions.filter((d) => d.lever.kind === "keyword").map((d) => ({ keyword: d.lever.keyword, confirmed: d.confirmed, context: d.context }));
    const title = decisions.find((d) => d.lever.kind === "title");
    const notes = [];
    if (title && title.confirmed) notes.push(`Candidate confirms the target title "${S.state.profile.job_title}" honestly describes their work; it may be used in the headline/summary. ${title.context}`.trim());
    const anyConfirmed = items.some((i) => i.confirmed) || notes.length;
    let saved = false;
    guarded(btn, async () => {
      if (items.length) {
        const res = await api(`/api/session/${S.sid}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
        S.levers = res.levers;
        S.state.confirmed = res.confirmed;
        S.state.declined = res.declined;
      }
      S.decisions = {};
      saved = true;
      if (!anyConfirmed) { renderLevers(); activity("Saved. Declined items won't be suggested or added again.", "done"); }
    }).then(() => {
      if (saved && anyConfirmed) iterate(btn, { mode: "single", phase: items.some((i) => i.confirmed) ? "apply_confirmed" : "refine", note: notes.join("\n") });
    });
  });
  $("#toTexBtn").addEventListener("click", () => $("#step-tex").scrollIntoView({ behavior: "smooth" }));

  // ------------------------------------------------------------------ LaTeX
  function renderTex() {
    const st = S.state;
    if (st.has_tex && !$("#texText").value) {
      $("#texText").value = st.resume_tex;
      $("#texHint").textContent = "Pre-filled with the .tex you uploaded in step 1.";
    }
    const tex = st.tex;
    const box = $("#texResult");
    if (!tex) return;
    box.replaceChildren(el("h3", {}, "Result"));
    const r = tex.report;
    box.append(el("div", { class: "score-hero" },
      el("div", { class: "score-big num" }, String(r.display_score), el("small", {}, "/100")),
      el("div", { class: "stack", style: "gap:4px" }, bandEl(r.band),
        el("span", { class: "small secondary" }, `ATS score of the new .tex · tailored text (v${tex.version}) scored ${Math.round(tex.markdown_score)}`))));
    if (Math.abs(r.score - tex.markdown_score) >= 4) {
      box.append(el("div", { class: "notice warn" }, "The .tex scores differently from the tailored text - some content may not have carried over. Check the Changes view below."));
    }
    const checks = el("ul", { class: "checks" });
    for (const c of tex.validation) checks.append(el("li", {}, el("span", { class: c.ok ? "ok" : "bad" }, c.ok ? "✓" : "✕"), el("span", {}, c.check), c.ok ? null : el("span", { class: "d" }, c.detail)));
    box.append(el("h3", { style: "margin-top:6px" }, "Compile checks"), checks);
    if (tex.flags && tex.flags.length) {
      box.append(el("h3", {}, "Honesty checks"));
      for (const f of tex.flags) box.append(el("div", { class: `flag ${f.severity}` }, el("span", { class: "ic" }, "!"), el("span", {}, f.message), el("span", { class: "ex" }, f.excerpt)));
    }
    if (tex.notes && tex.notes.length) box.append(el("h3", {}, "Notes"), el("ul", { class: "small secondary", style: "margin:0;padding-left:18px" }, tex.notes.map((n) => el("li", {}, n))));
    box.append(el("p", { class: "small muted" }, "Next: in Overleaf, select all in main.tex, paste, and Recompile."));

    $("#texCodeCard").hidden = false;
    $("#downloadTex").href = `/api/session/${S.sid}/tex/download`;
    renderTexCode();
  }

  function renderTexCode() {
    const tex = S.state.tex;
    const pre = $("#texCode");
    pre.replaceChildren();
    if (S.texView === "code" || !tex.diff) { pre.textContent = tex.edited; return; }
    for (const row of tex.diff) {
      if (row.type === "equal") pre.append(row.text + "\n");
      else if (row.type === "insert") pre.append(el("span", { class: "ins" }, row.text || " "));
      else if (row.type === "delete") pre.append(el("span", { class: "del" }, row.text || " "));
      else { pre.append(el("span", { class: "del" }, row.old || " "), el("span", { class: "ins" }, row.new || " ")); }
    }
  }
  document.querySelectorAll("#texView button").forEach((b) => b.addEventListener("click", () => {
    S.texView = b.dataset.mode;
    document.querySelectorAll("#texView button").forEach((x) => x.classList.toggle("on", x === b));
    renderTexCode();
  }));
  $("#copyTex").dataset.keepEnabled = "1";
  $("#copyTex").addEventListener("click", async () => {
    const text = S.state.tex.edited;
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const ta = el("textarea", {}); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
    }
    activity("Copied - paste it into Overleaf's main.tex and hit Recompile.", "done");
  });

  $("#texBtn").addEventListener("click", (e) => guarded(e.currentTarget, async () => {
    const fd = new FormData();
    if (S.texFile) fd.append("tex_file", S.texFile);
    else fd.append("tex_text", $("#texText").value);
    await streamPost(`/api/session/${S.sid}/tex`, fd, (ev) => {
      if (ev.event === "status") activity(ev.message);
      if (ev.event === "done") { S.state.tex = ev.tex; renderTex(); markDone(); }
    });
    const ok = S.state.tex.validation.every((c) => c.ok);
    activity(ok ? `Your tailored .tex is ready - ATS ${S.state.tex.report.display_score}. Copy it into Overleaf.` : "Your .tex is ready, but some compile checks failed - see the result panel.", ok ? "done" : "error");
    $("#texResult").scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  // ------------------------------------------------------------------ account menus
  const when = (t) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  function closeMenus(except) {
    ["#historyMenu", "#userMenu"].forEach((sel) => { if (sel !== except) $(sel).hidden = true; });
  }
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu-wrap")) closeMenus(); });

  $("#historyBtn").addEventListener("click", async () => {
    const menu = $("#historyMenu");
    closeMenus("#historyMenu");
    if (!menu.hidden) { menu.hidden = true; return; }
    menu.hidden = false;
    menu.replaceChildren(el("div", { class: "small muted" }, "Loading…"));
    try {
      const { sessions } = await api("/api/sessions");
      const list = el("div", { class: "list" });
      if (!sessions.length) list.append(el("div", { class: "empty" }, "Nothing saved yet."));
      for (const item of sessions) {
        const open = el("button", { class: "item", onclick: () => openSession(item.id) },
          el("span", { class: "t", title: item.title }, item.title || "Untitled"),
          el("span", { class: "s" }, `Updated ${when(item.updated_at)}`, item.id === S.sid ? " · open now" : ""),
          el("span", { class: "sc" }, item.score != null ? String(Math.round(item.score)) : "-"));
        list.append(open);
      }
      menu.replaceChildren(el("div", { class: "small", style: "font-weight:650" }, "Your saved resumes"), list,
        S.sid ? el("button", { class: "btn sm ghost", onclick: deleteCurrent }, "Delete the one that's open") : null);
    } catch (err) { menu.replaceChildren(el("div", { class: "small" }, err.message)); }
  });

  async function openSession(id) {
    closeMenus();
    try {
      const state = await api(`/api/session/${id}`);
      S.sid = id;
      S.cache = {};
      S.decisions = {};
      remember();
      $("#jdText").value = state.jd_text || "";
      $("#texText").value = "";
      if (state.version) { applyState(state); $("#step-tailor").scrollIntoView({ behavior: "smooth" }); }
    } catch (err) { activity(err.message, "error"); }
  }

  async function deleteCurrent() {
    if (!S.sid || !confirm("Delete this resume session and all its versions? This can't be undone.")) return;
    try {
      await api(`/api/session/${S.sid}`, { method: "DELETE" });
      try { localStorage.removeItem("rt_sid"); } catch (_) { /* ignore */ }
      location.href = "/";
    } catch (err) { activity(err.message, "error"); }
  }

  $("#userBtn").addEventListener("click", () => {
    const m = $("#userMenu");
    closeMenus("#userMenu");
    m.hidden = !m.hidden;
    if (!m.hidden) api("/api/me").then(renderAccount).catch(() => {});  // fresh AI-usage count
  });
  $("#logoutBtn").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) { /* ignore */ }
    try { localStorage.removeItem("rt_sid"); } catch (_) { /* ignore */ }
    location.href = "/login";
  });

  function renderAccount(me) {
    const u = me.user;
    if (!me.local) {
      $("#userBtn").hidden = false;
      $("#userBtn").textContent = (u.name || u.email || "?").trim().charAt(0).toUpperCase();
      $("#userBtn").title = `${u.name} (${u.email})`;
      $("#userEmail").textContent = `${u.name} · ${u.email}`;
      $("#usageInfo").textContent = me.ai_daily_limit
        ? `AI steps today: ${me.ai_calls_today} of ${me.ai_daily_limit}`
        : `AI steps today: ${me.ai_calls_today} (no limit)`;
    }
    if (u.is_admin && !me.local) {
      $("#adminLink").hidden = false;
      $("#pendingCount").hidden = !me.pending_requests;
      $("#pendingCount").textContent = me.pending_requests;
    }
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    try {
      const me = await api("/api/me");
      S.health = me;
      renderAccount(me);
      const pill = $("#aiPill");
      pill.textContent = me.ai_available ? `Claude · ${me.model}` : "AI off - no API key";
      pill.className = `pill model ${me.ai_available ? "ok" : "off"}`;
      if (!me.ai_available) $("#uploadHint").textContent = "No ANTHROPIC_API_KEY on the server: you'll get the ATS score and score levers; tailoring, the recruiter review and LaTeX editing need a key.";
    } catch (err) { activity(err.message, "error"); return; }

    let sid = null;
    const m = location.hash.match(/s=([a-f0-9]{32})/);
    if (m) sid = m[1];
    else { try { sid = localStorage.getItem("rt_sid"); } catch (_) { sid = null; } }
    if (!sid) return;
    try {
      const state = await api(`/api/session/${sid}`);
      S.sid = sid;
      remember();
      $("#jdText").value = state.jd_text || "";
      if (!state.version) return;
      applyState(state);
    } catch (_) {
      try { localStorage.removeItem("rt_sid"); } catch (__) { /* ignore */ }
      history.replaceState(null, "", location.pathname);
    }
  }
  boot();
})();
