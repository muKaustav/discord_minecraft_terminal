#!/usr/bin/env node
"use strict";

const { execFile } = require("child_process");
const mcs = require("node-mcstatus");

const IDLE_MS = Number(process.env.IDLE_MINUTES || 30) * 60 * 1000;
const POLL_MS = Number(process.env.POLL_SECONDS || 60) * 1000;
const MC_PORT = Number(process.env.MC_PORT || 25565);

let emptySince = null;
let seenOnline = false;

function log(message) {
  console.log(new Date().toISOString(), message);
}

function metadataToken() {
  return fetch("http://169.254.169.254/latest/api/token", {
    method: "PUT",
    headers: { "X-aws-ec2-metadata-token-ttl-seconds": "21600" },
  }).then((res) => {
    if (!res.ok) throw new Error("IMDSv2 token failed");
    return res.text();
  });
}

async function publicIp() {
  if (process.env.MC_HOST) return process.env.MC_HOST;
  const token = await metadataToken();
  const res = await fetch("http://169.254.169.254/latest/meta-data/public-ipv4", {
    headers: { "X-aws-ec2-metadata-token": token },
  });
  if (!res.ok) throw new Error("public-ipv4 metadata failed");
  return (await res.text()).trim();
}

function poweroff() {
  log("No players for idle window. Stopping the VM.");
  execFile("/sbin/shutdown", ["-h", "now", "RLCraft idle timeout"], (err) => {
    if (err) {
      log("shutdown failed: " + err.message);
      process.exit(1);
    }
  });
}

async function tick() {
  let host;
  try {
    host = await publicIp();
  } catch (err) {
    log("Could not resolve public IP: " + err.message);
    return;
  }

  let result;
  try {
    result = await mcs.statusJava(host, MC_PORT, { query: false });
  } catch (err) {
    log("mcstatus service error, skipping tick: " + err.message);
    return;
  }

  if (!result.online) {
    if (!seenOnline) {
      log("Server not online yet, not counting idle time");
      return;
    }
    log("Server went offline after being up, counting as empty");
  } else {
    seenOnline = true;
  }

  const players = result.players && typeof result.players.online === "number"
    ? result.players.online
    : 0;

  if (players > 0) {
    emptySince = null;
    log("Players online=" + players + ", idle timer cleared");
    return;
  }

  if (emptySince === null) {
    emptySince = Date.now();
    log("Server empty, starting idle timer");
    return;
  }

  const elapsed = Date.now() - emptySince;
  const remainMin = Math.max(0, Math.ceil((IDLE_MS - elapsed) / 60000));
  log("Server empty for " + Math.floor(elapsed / 60000) + " min, shutdown in ~" + remainMin + " min");
  if (elapsed >= IDLE_MS) poweroff();
}

log("Idle stop watcher started, idle=" + IDLE_MS / 60000 + " min, poll=" + POLL_MS / 1000 + " s");
tick();
setInterval(tick, POLL_MS);
