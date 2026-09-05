"use strict";

const $ = (id) => document.getElementById(id);

/* Password reveal, used on the login page. */
document.querySelectorAll("[data-reveal]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = $(button.dataset.reveal);
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    button.textContent = hidden ? "Hide" : "Show";
    button.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
    input.focus();
  });
});

const panel = $("status-pill");
if (panel) {
  const els = {
    pill: panel,
    label: $("status-label"),
    hint: $("status-hint"),
    instance: $("instance-state"),
    players: $("mc-state"),
    connect: $("connect"),
    jobPanel: $("job-panel"),
    jobMessage: $("job-message"),
    jobElapsed: $("job-elapsed"),
    jobBar: $("job-bar"),
    start: $("start"),
    stop: $("stop"),
    error: $("error"),
    updated: $("updated"),
  };

  let job = { state: "idle", started_at: null };
  let lastSuccess = Date.now();

  const describe = (data) => {
    const busy = data.job && data.job.state === "working";
    const state = data.instance_state;
    if (data.minecraft && data.minecraft.online) {
      return { label: "Online", tone: "ok", hint: "Server is accepting players." };
    }
    if (busy || state === "pending" || state === "stopping") {
      return { label: busy && data.job.action === "stop" ? "Stopping" : "Starting", tone: "busy", hint: "This takes about two minutes." };
    }
    if (state === "running") {
      return { label: "Booting", tone: "busy", hint: "Box is up, Minecraft is still loading." };
    }
    if (state === "stopped") {
      return { label: "Offline", tone: "off", hint: "Press start and wait about two minutes." };
    }
    return { label: state, tone: "off", hint: "Waiting on AWS." };
  };

  const elapsed = () => {
    if (job.state !== "working" || !job.started_at) return "";
    const secs = Math.max(0, Math.round((Date.now() - Date.parse(job.started_at)) / 1000));
    const mins = Math.floor(secs / 60);
    return mins ? `${mins}m ${secs % 60}s` : `${secs}s`;
  };

  const render = (data) => {
    const status = describe(data);
    els.pill.className = `pill pill-${status.tone}`;
    els.label.textContent = status.label;
    els.hint.textContent = status.hint;

    els.instance.textContent = data.instance_state;
    els.players.textContent = data.minecraft && data.minecraft.online
      ? `${data.minecraft.players} / ${data.minecraft.max}`
      : "—";
    els.connect.textContent = data.connect;

    job = data.job || { state: "idle", started_at: null };
    const working = job.state === "working";
    els.jobPanel.hidden = !job.message;
    els.jobMessage.textContent = job.message || "";
    els.jobBar.classList.toggle("bar-active", working);
    els.jobElapsed.textContent = elapsed();

    els.start.disabled = !data.can_start;
    els.stop.disabled = !data.can_stop;

    if (data.aws_error) {
      showError(`AWS: ${data.aws_error}`);
    }
  };

  const showError = (message) => {
    els.error.textContent = message;
    els.error.hidden = false;
  };

  const refresh = async () => {
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (res.status === 401) {
        window.location.reload();
        return;
      }
      if (!res.ok) throw new Error(`status ${res.status}`);
      render(await res.json());
      lastSuccess = Date.now();
      els.updated.textContent = "Live";
    } catch {
      const secs = Math.round((Date.now() - lastSuccess) / 1000);
      els.updated.textContent = `Reconnecting… last update ${secs}s ago`;
    }
  };

  const post = async (url) => {
    els.error.hidden = true;
    els.start.disabled = true;
    els.stop.disabled = true;
    try {
      const res = await fetch(url, { method: "POST" });
      const data = await res.json();
      if (!res.ok) showError(data.error || "Request failed.");
    } catch {
      showError("Could not reach the server.");
    }
    await refresh();
  };

  els.start.addEventListener("click", () => post("/api/start"));
  els.stop.addEventListener("click", () => {
    const online = els.players.textContent.trim();
    const playing = online !== "—" && !online.startsWith("0 ");
    const warn = playing ? "Players are online right now. Stop anyway?" : "Stop the server?";
    if (window.confirm(warn)) post("/api/stop");
  });

  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(els.connect.textContent.trim());
      const label = $("copy-label");
      label.textContent = "Copied";
      setTimeout(() => { label.textContent = "Copy"; }, 1500);
    } catch {
      showError("Clipboard blocked. Copy the address manually.");
    }
  });

  // Poll faster while a job runs so progress feels live.
  setInterval(() => refresh(), 3000);
  setInterval(() => { els.jobElapsed.textContent = elapsed(); }, 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
}
