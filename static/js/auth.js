/* Registration and forgot-password flows (Gmail -> OTP -> details/password). */
(function () {
  "use strict";
  var flow = document.getElementById("flow");
  if (!flow) return;
  var kind = flow.getAttribute("data-flow");
  var dots = flow.querySelectorAll(".step-dot");
  var errBox = document.getElementById("err");
  var otp = document.getElementById("otp");
  var digits = otp.querySelectorAll("input");
  var email = "", cooldownTimer = null;
  var $ = function (id) { return document.getElementById(id); };

  function show(step) {
    flow.querySelectorAll(".step-pane").forEach(function (p) { p.classList.toggle("hidden", p.getAttribute("data-step") !== String(step)); });
    dots.forEach(function (d, i) { d.classList.toggle("done", i < step - 1); d.classList.toggle("active", i === step - 1); });
    error("");
  }
  function error(msg) { errBox.textContent = msg; errBox.classList.toggle("hidden", !msg); }
  function busy(btn, on) { btn.classList.toggle("loading", on); btn.disabled = on; }
  function code() { return Array.prototype.map.call(digits, function (d) { return d.value; }).join(""); }

  digits.forEach(function (d, i) {
    d.addEventListener("input", function () {
      d.value = d.value.replace(/\D/g, "").slice(-1);
      otp.classList.remove("error");
      if (d.value && digits[i + 1]) digits[i + 1].focus();
      if (code().length === 6) $("verify").click();
    });
    d.addEventListener("keydown", function (e) { if (e.key === "Backspace" && !d.value && digits[i - 1]) digits[i - 1].focus(); });
    d.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "").slice(0, 6);
      if (!text) return;
      e.preventDefault();
      text.split("").forEach(function (c, k) { digits[k].value = c; });
      digits[Math.min(text.length, 5)].focus();
      if (text.length === 6) $("verify").click();
    });
  });

  function startCooldown(seconds) {
    var btn = $("resend"), note = $("resend-note");
    clearInterval(cooldownTimer);
    var left = seconds;
    btn.disabled = true;
    function tick() {
      if (left <= 0) { clearInterval(cooldownTimer); note.textContent = ""; btn.disabled = false; return; }
      note.textContent = "Resend in " + left + "s";
      left -= 1;
    }
    tick();
    cooldownTimer = setInterval(tick, 1000);
  }

  function sendCode(btn) {
    email = $("email").value.trim().toLowerCase();
    if (!email) { error("Please enter your Gmail address."); return; }
    busy(btn, true);
    window.api("/api/" + kind + "/send-otp", { body: { email: email } }).then(function (r) {
      busy(btn, false);
      if (!r.ok) { error(r.error || "Could not send the code."); return; }
      $("email-show").textContent = email;
      var box = $("dev-box");
      if (r.dev_otp) { $("dev-code").textContent = r.dev_otp; box.classList.remove("hidden"); } else box.classList.add("hidden");
      digits.forEach(function (d) { d.value = ""; });
      show(2);
      digits[0].focus();
      startCooldown(r.cooldown || 30);
    });
  }

  $("send").addEventListener("click", function () { sendCode($("send")); });
  $("email").addEventListener("keydown", function (e) { if (e.key === "Enter") $("send").click(); });
  $("resend").addEventListener("click", function () { sendCode($("resend")); });
  $("change").addEventListener("click", function () { show(1); });

  $("verify").addEventListener("click", function () {
    var c = code();
    if (c.length !== 6) { error("Please enter all 6 digits."); return; }
    var btn = $("verify");
    busy(btn, true);
    window.api("/api/" + kind + "/verify-otp", { body: { email: email, code: c } }).then(function (r) {
      busy(btn, false);
      if (!r.ok) {
        error(r.error || "Incorrect code.");
        otp.classList.add("error");
        return;
      }
      show(3);
      var first = flow.querySelector('[data-step="3"] input, [data-step="3"] select');
      if (first) first.focus();
    });
  });

  $("finish").addEventListener("click", function () {
    var btn = $("finish");
    var body = { password: $("password").value, confirm: $("confirm").value };
    if (kind === "register") {
      body.name = $("name").value; body.roll = $("roll").value; body.branch = $("branch").value; body.year = $("year").value;
    }
    busy(btn, true);
    window.api("/api/" + kind + (kind === "register" ? "/complete" : "/reset"), { body: body }).then(function (r) {
      if (!r.ok) { busy(btn, false); error(r.error || "Something went wrong."); return; }
      if (kind === "forgot") window.toast("Password changed. Please log in.", "ok");
      window.location.href = r.redirect || flow.getAttribute("data-login");
    });
  });
})();
