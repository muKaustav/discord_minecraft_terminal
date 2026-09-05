#!/usr/bin/env python3
"""Start/stop the RLCraft EC2 box the same way the local mcup alias does."""

from __future__ import annotations

import hmac
import os
import threading
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from mcstatus import JavaServer

INSTANCE_ID = os.environ.get("INSTANCE_ID", "i-050606336b43db88f")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")
MC_HOST = os.environ.get("MC_HOST", "13.205.240.170")
MC_PORT = int(os.environ.get("MC_PORT", "25565"))
POWER_PASSWORD = os.environ.get("POWER_PASSWORD", "")
WAIT_SECONDS = int(os.environ.get("MC_WAIT_SECONDS", "600"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

_lock = threading.Lock()
_job = {
    "action": None,
    "state": "idle",
    "message": "",
    "updated_at": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job(action: str, state: str, message: str) -> None:
    _job.update(action=action, state=state, message=message, updated_at=_now())


def _ec2():
    return boto3.client("ec2", region_name=REGION)


def _authed() -> bool:
    return bool(session.get("ok")) and bool(POWER_PASSWORD)


def _require_auth():
    if not _authed():
        return jsonify({"ok": False, "error": "login required"}), 401
    return None


def instance_state() -> str:
    resp = _ec2().describe_instances(InstanceIds=[INSTANCE_ID])
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


def minecraft_status() -> dict:
    try:
        status = JavaServer.lookup(f"{MC_HOST}:{MC_PORT}", timeout=3).status()
        players = status.players
        return {
            "online": True,
            "players": getattr(players, "online", 0) or 0,
            "max": getattr(players, "max", 0) or 0,
            "motd": str(getattr(status, "motd", "") or ""),
        }
    except Exception as exc:
        return {"online": False, "error": str(exc)}


def snapshot() -> dict:
    try:
        state = instance_state()
        aws_error = None
    except Exception as exc:
        state = "unknown"
        aws_error = str(exc)
    mc = minecraft_status() if state == "running" else {"online": False}
    return {
        "instance_id": INSTANCE_ID,
        "instance_state": state,
        "aws_error": aws_error,
        "connect": f"{MC_HOST}:{MC_PORT}",
        "minecraft": mc,
        "job": dict(_job),
    }


def start_like_mcup() -> None:
    """Match scripts/mc-up.sh: start if stopped, wait running, wait for Minecraft."""
    try:
        _set_job("start", "working", "Checking instance state")
        state = instance_state()
        if state == "stopping":
            _set_job("start", "working", "Waiting for instance to finish stopping")
            _ec2().get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
            state = "stopped"
        if state == "stopped":
            _set_job("start", "working", "Starting instance")
            _ec2().start_instances(InstanceIds=[INSTANCE_ID])
        if state != "running":
            _set_job("start", "working", "Waiting until instance is running")
            _ec2().get_waiter("instance_running").wait(InstanceIds=[INSTANCE_ID])
        deadline = time.time() + WAIT_SECONDS
        n = 0
        while time.time() < deadline:
            n += 1
            mc = minecraft_status()
            if mc.get("online"):
                _set_job(
                    "start",
                    "done",
                    f"RLCraft is up. Connect at {MC_HOST}:{MC_PORT}",
                )
                return
            _set_job("start", "working", f"Minecraft not ready yet (wait-{n})")
            time.sleep(10)
        _set_job(
            "start",
            "error",
            f"Instance is running at {MC_HOST}, but Minecraft did not answer in time",
        )
    except ClientError as exc:
        _set_job("start", "error", exc.response["Error"].get("Message", str(exc)))
    except Exception as exc:
        _set_job("start", "error", str(exc))


def stop_instance() -> None:
    try:
        _set_job("stop", "working", "Stopping instance")
        _ec2().stop_instances(InstanceIds=[INSTANCE_ID])
        _ec2().get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
        _set_job("stop", "done", "Instance is stopped")
    except ClientError as exc:
        _set_job("stop", "error", exc.response["Error"].get("Message", str(exc)))
    except Exception as exc:
        _set_job("stop", "error", str(exc))


@app.get("/")
def home():
    if not _authed():
        return render_template("login.html")
    return render_template("index.html", data=snapshot())


@app.post("/login")
def login():
    supplied = request.form.get("password", "")
    if POWER_PASSWORD and hmac.compare_digest(supplied, POWER_PASSWORD):
        session["ok"] = True
        return redirect(url_for("home"))
    return render_template("login.html", error="Wrong password"), 401


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/api/status")
def api_status():
    denied = _require_auth()
    if denied:
        return denied
    return jsonify(snapshot())


@app.post("/api/start")
def api_start():
    denied = _require_auth()
    if denied:
        return denied
    with _lock:
        if _job["state"] == "working":
            return jsonify({"ok": False, "error": "a job is already running", "job": _job}), 409
        _set_job("start", "working", "Queued")
        threading.Thread(target=start_like_mcup, daemon=True).start()
    return jsonify({"ok": True, "job": _job})


@app.post("/api/stop")
def api_stop():
    denied = _require_auth()
    if denied:
        return denied
    with _lock:
        if _job["state"] == "working":
            return jsonify({"ok": False, "error": "a job is already running", "job": _job}), 409
        _set_job("stop", "working", "Queued")
        threading.Thread(target=stop_instance, daemon=True).start()
    return jsonify({"ok": True, "job": _job})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
