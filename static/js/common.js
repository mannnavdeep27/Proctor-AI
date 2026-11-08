/* Shared helpers: CSRF-aware fetch, toasts, loading buttons, local times, copy, password toggle. */
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.content : "";

  window.api = function (url, opts) {
    opts = opts || {};
    var init = { method: opts.method || "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": CSRF, "Accept": "application/json" } };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    if (opts.keepalive) init.keepalive = true;
    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return { ok: false, error: "Unexpected server response." }; })
        .then(function (data) { data._status = res.status; return data; });
    }).catch(function () {
      return { ok: false, error: "Network problem. Please check your connection.", _network: true, _status: 0 };
    });
  };

  window.toast = function (msg, kind) {
    var host = document.getElementById("toasts");
    if (!host) return;
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () { el.style.transition = "opacity .3s"; el.style.opacity = "0"; setTimeout(function () { el.remove(); }, 300); }, 3500);
  };

  // Loading state on form submit
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    if (form.hasAttribute("data-loading")) {
      var btn = e.submitter || form.querySelector("button:not([type=button])");
      if (btn) setTimeout(function () { btn.classList.add("loading"); }, 0);
    }
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".btn.loading").forEach(function (b) { b.classList.remove("loading"); });
  });

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-toggle-pw]");
    if (t) {
      var input = document.querySelector(t.getAttribute("data-toggle-pw"));
      if (input) input.type = input.type === "password" ? "text" : "password";
    }
    var c = e.target.closest("[data-copy]");
    if (c) {
      var text = c.getAttribute("data-copy");
      var done = function () { window.toast("Link copied", "ok"); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { window.toast("Copy failed", "err"); });
      else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (x) { window.toast("Copy failed", "err"); } ta.remove(); }
    }
  });

  // Local time display
  document.querySelectorAll(".js-time").forEach(function (el) {
    var ts = parseInt(el.getAttribute("data-ts"), 10);
    if (!ts) return;
    var full = el.hasAttribute("data-full");
    el.textContent = new Date(ts * 1000).toLocaleString(undefined, full
      ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  });

  // Count-up numbers
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-count]").forEach(function (el) {
    var target = parseInt(el.getAttribute("data-count"), 10) || 0;
    if (reduce || target === 0) return;
    var start = performance.now(), dur = 800;
    (function step(now) {
      var p = Math.min(1, (now - start) / dur);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    })(start);
  });
})();
