const errorEl = document.getElementById("error");

function setBusy(busy) {
  document.getElementById("start").disabled = busy;
  document.getElementById("stop").disabled = busy;
}

function render(data) {
  document.getElementById("instance-state").textContent = data.instance_state;
  const mc = data.minecraft || {};
  document.getElementById("mc-state").textContent = mc.online
    ? `online (${mc.players}/${mc.max})`
    : "offline";
  document.getElementById("connect").textContent = data.connect;
  const job = data.job || {};
  document.getElementById("job").textContent = job.message || job.state || "";
  setBusy(job.state === "working");
}

async function refresh() {
  const res = await fetch("/api/status");
  if (!res.ok) return;
  render(await res.json());
}

async function post(url) {
  errorEl.hidden = true;
  const res = await fetch(url, { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    errorEl.hidden = false;
    errorEl.textContent = data.error || "request failed";
    return;
  }
  await refresh();
}

document.getElementById("start").addEventListener("click", () => post("/api/start"));
document.getElementById("stop").addEventListener("click", () => post("/api/stop"));
setInterval(refresh, 4000);
refresh();
