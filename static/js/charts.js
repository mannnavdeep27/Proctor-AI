/* Dashboard charts (Chart.js is loaded from cdnjs; the page still works if it is blocked). */
(function () {
  "use strict";
  var holder = document.getElementById("chart-data");
  if (!holder || typeof Chart === "undefined") return;
  var d = JSON.parse(holder.textContent);
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = "#6b7280";
  var labels = d.exams.map(function (e) { return e.title.length > 18 ? e.title.slice(0, 17) + "…" : e.title; });
  new Chart(document.getElementById("chart-exams"), {
    type: "bar",
    data: { labels: labels, datasets: [{ data: d.exams.map(function (e) { return e.n; }), backgroundColor: "#6366f1", borderRadius: 8, maxBarThickness: 38 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#eef0f6" } }, x: { grid: { display: false } } } }
  });
  var v = d.verdicts;
  new Chart(document.getElementById("chart-verdicts"), {
    type: "doughnut",
    data: { labels: ["Clean", "Suspicious", "High risk"], datasets: [{ data: [v["CLEAN"], v["SUSPICIOUS"], v["HIGH RISK"]], backgroundColor: ["#16a34a", "#d97706", "#dc2626"], borderWidth: 3, borderColor: "#fff" }] },
    options: { maintainAspectRatio: false, cutout: "66%", plugins: { legend: { position: "bottom" } } }
  });
})();
