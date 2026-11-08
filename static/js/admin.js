/* Admin helpers: mobile menu, exam form local-time handling. */
(function () {
  "use strict";
  var menu = document.getElementById("menu-btn"), side = document.getElementById("sidebar");
  if (menu && side) menu.addEventListener("click", function () { side.classList.toggle("open"); });

  var form = document.getElementById("exam-form");
  if (!form) return;
  var offset = new Date().getTimezoneOffset();      // minutes: UTC - local
  document.getElementById("tz_offset").value = offset;
  function toLocalInput(utcSeconds) { return new Date(utcSeconds * 1000 - offset * 60000).toISOString().slice(0, 16); }
  var fields = form.querySelectorAll(".js-local-dt");
  var any = false;
  fields.forEach(function (f) {
    var utc = parseInt(f.getAttribute("data-utc"), 10);
    if (utc) { f.value = toLocalInput(utc); any = true; }
    if (f.value) any = true;
  });
  if (!any) {
    var now = Math.floor(Date.now() / 1000);
    fields[0].value = toLocalInput(now);
    fields[1].value = toLocalInput(now + 2 * 86400);
  }
})();
