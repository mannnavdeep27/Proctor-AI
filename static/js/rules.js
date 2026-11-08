/* Rules page: enable "Begin Exam" after the box is ticked and the minimum reading time has passed. */
(function () {
  "use strict";
  var form = document.getElementById("begin-form");
  if (!form) return;
  var btn = document.getElementById("begin-btn"), agree = document.getElementById("agree"), note = document.getElementById("read-timer");
  var left = parseInt(form.getAttribute("data-min"), 10) || 0;
  function update() {
    var ready = left <= 0 && agree.checked;
    btn.disabled = !ready;
    if (left > 0) note.textContent = "Please read the rules. You can begin in " + left + "s.";
    else if (!agree.checked) note.textContent = "Tick the box above to continue.";
    else note.textContent = "Your timer starts when you press Begin Exam.";
  }
  var modal = document.getElementById("rules-modal"), open = document.getElementById("open-rules");
  if (open) open.addEventListener("click", function () { modal.classList.add("show"); });
  agree.addEventListener("change", update);
  update();
  if (left > 0) {
    var t = setInterval(function () { left -= 1; update(); if (left <= 0) clearInterval(t); }, 1000);
  }
})();
