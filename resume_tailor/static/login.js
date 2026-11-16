(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const msg = $("#authMsg");

  function show(text, kind = "info") {
    msg.hidden = !text;
    msg.textContent = text || "";
    msg.className = `auth-msg ${kind}`;
  }

  function tab(name) {
    document.querySelectorAll(".auth-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    $("#loginForm").hidden = name !== "login";
    $("#registerForm").hidden = name !== "register";
    show("");
  }
  document.querySelectorAll(".auth-tabs button").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.error === "string" ? data.error : "Something went wrong. Try again.");
    return data;
  }

  async function submit(form, fn) {
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.classList.add("loading");
    try { await fn(Object.fromEntries(new FormData(form))); }
    catch (err) { show(err.message, "error"); }
    finally { btn.disabled = false; btn.classList.remove("loading"); }
  }

  $("#loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    submit(e.currentTarget, async (data) => {
      await post("/api/auth/login", { email: data.email, password: data.password });
      location.href = "/";
    });
  });

  $("#registerForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    submit(form, async (data) => {
      await post("/api/auth/register", data);
      form.reset();
      tab("login");
      show("Request sent. You can sign in as soon as the owner approves it.", "ok");
    });
  });

  fetch("/api/auth/config").then((r) => r.json()).then((cfg) => {
    if (cfg.problem) {
      $("#configProblem").hidden = false;
      $("#configProblem").textContent = cfg.problem;
      $("#authCard").hidden = true;
    }
    if (!cfg.allow_signup) $("#registerTab").hidden = true;
  }).catch(() => {});
})();
