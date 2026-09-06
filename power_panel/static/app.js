"use strict";

const $ = (id) => document.getElementById(id);
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

/* Password reveal, used on the login page. */
document.querySelectorAll("[data-reveal]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = $(button.dataset.reveal);
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    const eyeShow = button.querySelector("[data-eye-show]");
    const eyeHide = button.querySelector("[data-eye-hide]");
    if (eyeShow && eyeHide) {
      eyeShow.hidden = hidden;
      eyeHide.hidden = !hidden;
    } else {
      button.textContent = hidden ? "Hide" : "Show";
    }
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
    costHourly: $("cost-hourly"),
    costMonth: $("cost-month"),
    costBar: $("cost-bar"),
    costFill: $("cost-bar-fill"),
    jobPanel: $("job-panel"),
    jobMessage: $("job-message"),
    jobElapsed: $("job-elapsed"),
    jobBar: $("job-bar"),
    actions: $("actions"),
    start: $("start"),
    stop: $("stop"),
    error: $("error"),
    updated: $("updated"),
    packSelect: $("pack-select"),
    packVersion: $("pack-version"),
    clientDownload: $("client-download"),
    activePackSubtitle: $("active-pack-subtitle"),
    addPackForm: $("add-pack-form"),
    showAddPack: $("show-add-pack"),
    curseforgeUrl: $("curseforge-url"),
    resolvePack: $("resolve-pack"),
    installPack: $("install-pack"),
    resolvedPack: $("resolved-pack"),
  };

  let job = { state: "idle", started_at: null };
  let lastSuccess = Date.now();
  let resolvedPack = null;

  // The hint stays empty whenever the pill or the job panel already says it.
  const describe = (data) => {
    const busy = data.job && data.job.state === "working";
    const state = data.instance_state;
    if (data.minecraft && data.minecraft.online) {
      return { label: "Online", tone: "ok", hint: "" };
    }
    if (busy || state === "pending" || state === "stopping") {
      const stopping = (busy && data.job.action === "stop") || state === "stopping";
      return { label: stopping ? "Stopping" : "Starting", tone: "busy", hint: "" };
    }
    if (state === "running") {
      return { label: "Booting", tone: "busy", hint: "The box is up, Minecraft is still loading." };
    }
    if (state === "stopped") {
      return { label: "Offline", tone: "off", hint: "Press start, then give it about two minutes." };
    }
    return { label: state, tone: "off", hint: "" };
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
    els.hint.hidden = !status.hint;

    els.instance.textContent = data.instance_state;
    els.players.textContent = data.minecraft && data.minecraft.online
      ? `${data.minecraft.players} / ${data.minecraft.max}`
      : "—";
    els.connect.textContent = data.connect;
    if (data.cost) {
      els.costHourly.textContent = data.cost.hourly_label;
      els.costMonth.textContent = data.cost.month_label;
      const pct = data.cost.percent;
      els.costBar.hidden = pct == null || pct <= 0;
      if (pct != null) els.costFill.style.width = `${pct}%`;
    }

    renderModpacks(data.modpacks);

    job = data.job || { state: "idle", started_at: null };
    const working = job.state === "working";
    const failed = job.state === "error";
    // A finished job has nothing left to report that the pill doesn't show.
    els.jobPanel.hidden = !(working || failed);
    els.jobPanel.classList.toggle("job-error", failed);
    els.jobMessage.textContent = job.message || "";
    els.jobBar.hidden = failed;
    els.jobBar.classList.toggle("bar-active", working);
    els.jobElapsed.textContent = elapsed();

    const showStart = !working && data.can_start && !data.can_stop;
    const showStop = !working && data.can_stop;
    els.start.hidden = !showStart;
    els.stop.hidden = !showStop;
    els.start.disabled = !showStart;
    els.stop.disabled = !showStop;
    els.actions.hidden = !showStart && !showStop;

    if (data.aws_error) {
      showError(`AWS: ${data.aws_error}`);
    }
  };

  const renderModpacks = (modpacks) => {
    if (!modpacks || !els.packSelect) return;
    const active = modpacks.active;
    const operationBusy = modpacks.operation && modpacks.operation.state === "working";
    const selected = String(active?.id || "");
    const currentOptions = Array.from(els.packSelect.options).map((option) => option.value).join(",");
    const nextOptions = modpacks.packs.map((pack) => String(pack.id)).join(",");
    if (currentOptions !== nextOptions) {
      els.packSelect.replaceChildren();
      modpacks.packs.forEach((pack) => {
        const option = document.createElement("option");
        option.value = pack.id;
        option.textContent = `${pack.project_name}${pack.client_file_name ? ` · ${pack.client_file_name}` : ""}${pack.state === "ready" ? "" : ` (${pack.state})`}`;
        option.disabled = pack.state !== "ready";
        els.packSelect.appendChild(option);
      });
    }
    els.packSelect.value = selected;
    els.packSelect.disabled = Boolean(operationBusy);
    if (active) {
      els.activePackSubtitle.textContent = `${active.project_name} · Mumbai`;
      if (els.packVersion) els.packVersion.textContent = `${active.minecraft_version || "Unknown"} · ${active.loader || "Unknown"}`;
      if (els.clientDownload) els.clientDownload.href = active.client_url;
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
      els.updated.hidden = true;
      els.updated.textContent = "";
    } catch {
      const secs = Math.round((Date.now() - lastSuccess) / 1000);
      els.updated.hidden = false;
      els.updated.textContent = `Reconnecting… last update ${secs}s ago`;
    }
  };

  const post = async (url, payload) => {
    els.error.hidden = true;
    els.actions.hidden = true;
    els.start.hidden = true;
    els.stop.hidden = true;
    els.start.disabled = true;
    els.stop.disabled = true;
    try {
      const options = { method: "POST", headers: { "X-CSRF-Token": csrfToken } };
      if (payload !== undefined) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(payload);
      }
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok) showError(data.error || "Request failed.");
      return { ok: res.ok, data };
    } catch {
      showError("Could not reach the server.");
      return { ok: false, data: {} };
    }
    finally {
      await refresh();
    }
  };

  els.start.addEventListener("click", () => post("/api/start"));
  els.stop.addEventListener("click", () => {
    const online = els.players.textContent.trim();
    const playing = online !== "—" && !online.startsWith("0 ");
    const warn = playing ? "Players are online right now. Stop anyway?" : "Stop the server?";
    if (window.confirm(warn)) post("/api/stop");
  });

  els.showAddPack?.addEventListener("click", () => {
    els.addPackForm.hidden = !els.addPackForm.hidden;
    if (!els.addPackForm.hidden) els.curseforgeUrl.focus();
  });

  const resolvePack = async () => {
    const url = els.curseforgeUrl.value.trim();
    if (!url) return;
    const result = await post("/api/modpacks/resolve", { url });
    if (!result.ok) return;
    resolvedPack = result.data.pack;
    els.resolvedPack.textContent = `${resolvedPack.project_name} · ${resolvedPack.client_file_name || resolvedPack.minecraft_version} · ${resolvedPack.loader}. A matching server pack will be installed.`;
    els.resolvedPack.hidden = false;
    els.installPack.disabled = false;
  };

  els.resolvePack?.addEventListener("click", resolvePack);
  els.addPackForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!resolvedPack) {
      await resolvePack();
      return;
    }
    const result = await post("/api/modpacks/import", { url: els.curseforgeUrl.value.trim() });
    if (result.ok) {
      resolvedPack = null;
      els.installPack.disabled = true;
      els.addPackForm.hidden = true;
    }
  });

  els.packSelect?.addEventListener("change", async () => {
    const packId = Number(els.packSelect.value);
    let result = await post("/api/modpacks/select", { pack_id: packId });
    if (!result.ok && result.data.confirmation_required) {
      const confirmed = window.confirm("Players are online. Minecraft Booter will announce a 60-second countdown, disconnect them, and restart with this modpack. Continue?");
      if (confirmed) result = await post("/api/modpacks/select", { pack_id: packId, confirm: true });
    }
  });

  // navigator.clipboard only exists on HTTPS or localhost, and this panel is
  // served over plain HTTP, so fall back to the old execCommand path.
  const copyText = async (text) => {
    if (window.isSecureContext && navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch { /* fall through */ }
    }
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.setAttribute("readonly", "");
    helper.style.position = "fixed";
    helper.style.top = "-1000px";
    document.body.appendChild(helper);
    helper.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch { /* reported below */ }
    helper.remove();
    return copied;
  };

  const selectAddress = () => {
    const range = document.createRange();
    range.selectNodeContents(els.connect);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  };

  $("copy").addEventListener("click", async () => {
    const btn = $("copy");
    const idle = btn.querySelector("[data-copy-idle]");
    const ok = btn.querySelector("[data-copy-ok]");
    const done = await copyText(els.connect.textContent.trim());
    if (!done) selectAddress();
    idle.hidden = true;
    ok.hidden = false;
    btn.title = done ? "Copied" : "Press Ctrl+C";
    btn.setAttribute("aria-label", btn.title);
    setTimeout(() => {
      idle.hidden = false;
      ok.hidden = true;
      btn.title = "Copy address";
      btn.setAttribute("aria-label", "Copy address");
    }, 1800);
  });

  // Poll faster while a job runs so progress feels live.
  setInterval(() => refresh(), 3000);
  setInterval(() => { els.jobElapsed.textContent = elapsed(); }, 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
}
