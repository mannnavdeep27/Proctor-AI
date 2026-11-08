/* Exam runner: one question per page, autosave, server-side timer, window-switch detection. */
(function () {
  "use strict";
  var root = document.getElementById("runner");
  if (!root) return;
  var BASE = root.getAttribute("data-base"), DONE = root.getAttribute("data-done");
  var $ = function (id) { return document.getElementById(id); };
  var S = { qs: [], answers: {}, i: 0, deadline: 0, offset: 0, total: 1, cfg: {}, finished: false, awayOn: false, checkTimer: null };

  var LS_KEY = "proctorai_ans_" + root.getAttribute("data-user") + "_" + root.getAttribute("data-id");
  function backup() { try { localStorage.setItem(LS_KEY, JSON.stringify(S.answers)); } catch (e) {} }
  function restore() { try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}"); } catch (e) { return {}; } }
  function now() { return Date.now() / 1000 + S.offset; }
  function finish(url) { if (S.finished) return; try { localStorage.removeItem(LS_KEY); } catch (e) {} S.finished = true; window.location.replace(url || DONE); }

  function applyStatus(r) {
    if (!r || !r.ok) return;
    if (r.server_now) S.offset = r.server_now - Date.now() / 1000;
    if (r.deadline) S.deadline = r.deadline;
    if (r.status === "submitted") finish(r.redirect);
  }

  // ---------------- rendering
  function answeredCount() { return Object.keys(S.answers).length; }
  function renderProgress() {
    var n = S.qs.length, a = answeredCount();
    $("bar").style.width = (n ? (a / n) * 100 : 0) + "%";
    $("answered").textContent = a + " of " + n + " answered";
    $("q-counter").textContent = "Question " + (S.i + 1) + " of " + n + " · " + a + " answered";
    $("prev").disabled = S.i === 0;
    $("next").classList.toggle("hidden", S.i >= n - 1);
    var pal = $("palette"); pal.innerHTML = "";
    S.qs.forEach(function (q, k) {
      var b = document.createElement("button");
      b.type = "button"; b.textContent = k + 1;
      b.className = "dot" + (S.answers[q.id] !== undefined ? " answered" : "") + (k === S.i ? " current" : "");
      b.addEventListener("click", function () { go(k); });
      pal.appendChild(b);
    });
  }

  function renderQuestion(dir) {
    var q = S.qs[S.i], card = $("qcard");
    card.innerHTML = "";
    var head = document.createElement("div");
    head.innerHTML = '<span class="q-num">Question ' + (S.i + 1) + '</span><span class="q-marks">' + q.marks + ' mark' + (q.marks == 1 ? "" : "s") + "</span>";
    var text = document.createElement("div"); text.className = "q-text"; text.textContent = q.text;
    var opts = document.createElement("div"); opts.className = "opts";
    q.options.forEach(function (label, k) {
      var b = document.createElement("button"); b.type = "button";
      b.className = "opt" + (S.answers[q.id] === k ? " selected" : "");
      var key = document.createElement("span"); key.className = "key"; key.textContent = "ABCDEF"[k];
      var t = document.createElement("span"); t.textContent = label;
      b.appendChild(key); b.appendChild(t);
      b.addEventListener("click", function (e) { choose(q, k, b, e); });
      opts.appendChild(b);
    });
    card.appendChild(head); card.appendChild(text); card.appendChild(opts);
    card.classList.remove("slide-in"); void card.offsetWidth; if (dir !== 0) card.classList.add("slide-in");
    renderProgress();
  }

  function go(k) {
    if (k < 0 || k >= S.qs.length) return;
    var dir = k === S.i ? 0 : 1; S.i = k; renderQuestion(dir); window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------------- answers
  var saveEl = $("save"), pending = 0;
  function saveState(kind, msg) { saveEl.className = "save-state " + kind; saveEl.lastChild.textContent = " " + msg; }
  function choose(q, k, btn, ev) {
    var clear = S.answers[q.id] === k;
    if (clear) delete S.answers[q.id]; else S.answers[q.id] = k;
    backup();
    var r = document.createElement("span"); r.className = "ripple";
    var rect = btn.getBoundingClientRect(); r.style.width = r.style.height = "40px";
    r.style.left = (ev.clientX - rect.left - 20) + "px"; r.style.top = (ev.clientY - rect.top - 20) + "px";
    btn.appendChild(r); setTimeout(function () { r.remove(); }, 600);
    renderQuestion(0);
    send(q.id, clear ? null : k, 0);
  }
  function send(qid, choice, tries) {
    pending++; saveState("", "Saving…");
    window.api(BASE + "/answer", { body: { qid: qid, choice: choice } }).then(function (r) {
      pending--;
      applyStatus(r);
      if (r.ok) { if (!pending) saveState("ok", "Saved"); return; }
      if (tries < 4 && r._status !== 400) { saveState("err", "Retrying…"); setTimeout(function () { send(qid, choice, tries + 1); }, 1500 * (tries + 1)); }
      else saveState("err", "Not saved - check your connection");
    });
  }

  // ---------------- timer
  function tick() {
    if (S.finished) return;
    var left = Math.max(0, Math.ceil(S.deadline - now()));
    var m = Math.floor(left / 60), s = left % 60;
    $("time").textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    $("ring").style.strokeDashoffset = 144.5 * (1 - Math.min(1, left / S.total));
    var t = $("timer"); t.classList.toggle("warn", left <= 300 && left > 60); t.classList.toggle("danger", left <= 60);
    if (left <= 0) { window.api(BASE + "/ping").then(function (r) { applyStatus(r); if (!S.finished) setTimeout(tick, 1500); }); return; }
    setTimeout(tick, 500);
  }

  // ---------------- cheating / window-switch detection
  var banner = $("warn"), bannerTimer = null;
  function warn(msg) { banner.textContent = msg; banner.classList.add("show"); clearTimeout(bannerTimer); bannerTimer = setTimeout(function () { banner.classList.remove("show"); }, 7000); }
  function away(action) {
    return window.api(BASE + "/away", { body: { action: action }, keepalive: true }).then(function (r) {
      applyStatus(r);
      if (r.ok && r.violation && !S.finished) {
        var w = r.warnings || {};
        warn("Warning: you left the exam window. " + (w.allowed ? "Warning " + w.used + " of " + w.allowed + ". The next time your exam will be submitted automatically." : ""));
      }
      return r;
    });
  }
  function isAway() { return document.hidden || !document.hasFocus(); }
  function sync() {
    if (S.finished) return;
    var out = isAway();
    if (out && !S.awayOn) {
      S.awayOn = true; away("start");
      var grace = (S.cfg.grace_seconds || 0) * 1000 + 600;
      clearTimeout(S.checkTimer); S.checkTimer = setTimeout(function () { if (S.awayOn) away("check"); }, grace);
    } else if (!out && S.awayOn) {
      S.awayOn = false; clearTimeout(S.checkTimer); away("end");
    }
  }
  ["visibilitychange", "blur", "focus", "pagehide", "pageshow"].forEach(function (ev) {
    (ev === "visibilitychange" ? document : window).addEventListener(ev, function () { setTimeout(sync, 30); });
  });
  setInterval(function () { sync(); if (S.awayOn) away("check"); }, 2000);

  function report(type) { if (!S.finished) window.api(BASE + "/event", { body: { type: type } }); }
  document.addEventListener("copy", function (e) { report("copy"); if (S.cfg.block_copy) e.preventDefault(); });
  document.addEventListener("cut", function (e) { report("cut"); if (S.cfg.block_copy) e.preventDefault(); });
  document.addEventListener("contextmenu", function (e) { report("contextmenu"); if (S.cfg.block_copy) e.preventDefault(); });
  document.addEventListener("keydown", function (e) {
    var k = (e.key || "").toLowerCase();
    if (k === "f12" || (e.ctrlKey && e.shiftKey && "ijc".indexOf(k) > -1) || (e.ctrlKey && k === "u")) { e.preventDefault(); report("devtools"); }
  });

  // ---------------- buttons
  $("prev").addEventListener("click", function () { go(S.i - 1); });
  $("next").addEventListener("click", function () { go(S.i + 1); });
  $("finish").addEventListener("click", function () {
    var un = S.qs.length - answeredCount();
    $("confirm-text").textContent = un ? "You have " + un + " unanswered question" + (un > 1 ? "s" : "") + ". You cannot change answers after submitting." : "You answered every question. You cannot change answers after submitting.";
    $("confirm").classList.add("show");
  });
  $("cancel").addEventListener("click", function () { $("confirm").classList.remove("show"); });
  $("yes").addEventListener("click", function () {
    var b = $("yes"); b.classList.add("loading");
    window.api(BASE + "/submit").then(function (r) { if (r.ok) { applyStatus(r); finish(r.redirect); } else { b.classList.remove("loading"); window.toast(r.error || "Could not submit. Try again.", "err"); } });
  });
  document.addEventListener("keydown", function (e) {
    if ($("confirm").classList.contains("show")) return;
    if (e.key === "ArrowRight") go(S.i + 1); else if (e.key === "ArrowLeft") go(S.i - 1);
  });

  // ---------------- start
  window.api(root.getAttribute("data-api"), { method: "GET" }).then(function (r) {
    if (!r.ok) { $("qcard").textContent = r.error || "Could not load the exam. Please refresh."; return; }
    applyStatus(r);
    if (S.finished) return;
    S.qs = r.questions; S.cfg = r.cfg;
    Object.keys(r.answers).forEach(function (qid) { S.answers[qid] = r.answers[qid]; });
    // crash recovery: answers kept in this browser but missing on the server are sent again
    var local = restore();
    S.qs.forEach(function (q) { if (S.answers[q.id] === undefined && local[q.id] !== undefined && local[q.id] < q.options.length) { S.answers[q.id] = local[q.id]; send(q.id, local[q.id], 0); } });
    backup();
    S.total = Math.max(1, S.deadline - now());
    if (r.cfg.block_copy) document.body.classList.add("no-copy");
    if (!S.qs.length) { $("qcard").textContent = "This exam has no questions."; return; }
    renderQuestion(0); tick(); sync();
    setInterval(function () { window.api(BASE + "/ping").then(applyStatus); }, 15000);
  });
})();
