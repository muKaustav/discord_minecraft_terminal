#!/usr/bin/env python3
"""Start/stop the Minecraft EC2 box the same way the local mcup alias does."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from mcstatus import JavaServer

from catalog import PackCatalog
from modpacks import CurseForgeClient, CurseForgeLinkError, parse_curseforge_link
from remote import SsmPackManager
from security import csrf_matches

INSTANCE_ID = os.environ.get("INSTANCE_ID", "i-050606336b43db88f")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")
MC_HOST = os.environ.get("MC_HOST", "13.205.240.170")
MC_PORT = int(os.environ.get("MC_PORT", "25565"))
MC_CONNECT = os.environ.get("MC_CONNECT", "13.205.240.170:25565")
POWER_PASSWORD = os.environ.get("POWER_PASSWORD", "")
WAIT_SECONDS = int(os.environ.get("MC_WAIT_SECONDS", "600"))
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "477327724152")
BUDGET_NAME = os.environ.get("BUDGET_NAME", "monthly-15-usd")
# Cost Explorer bills $0.01 per call. Cache hard so the 3s poll does not spend.
COST_CACHE_SECONDS = int(os.environ.get("COST_CACHE_SECONDS", "900"))
CURSEFORGE_API_KEY = os.environ.get("CURSEFORGE_API_KEY", "")
CATALOG_PATH = os.environ.get("MODPACK_CATALOG_PATH", "/opt/rlcraft-power/modpacks.sqlite3")
SSM_CONFIG = Config(retries={"total_max_attempts": 3, "mode": "adaptive"}, connect_timeout=5, read_timeout=15)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")
catalog = PackCatalog(CATALOG_PATH)

# Static files are only replaced when the service restarts, so a boot-time
# stamp is enough to stop browsers serving a stale panel.
ASSET_VERSION = str(int(time.time()))

_lock = threading.Lock()
_job = {
    "action": None,
    "state": "idle",
    "message": "",
    "started_at": None,
    "updated_at": None,
}

_cost_lock = threading.Lock()
_cost_cache = {"at": 0.0, "payload": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job(action: str, state: str, message: str) -> None:
    if _job["state"] != "working" and state == "working":
        _job["started_at"] = _now()
    _job.update(action=action, state=state, message=message, updated_at=_now())


@app.context_processor
def inject_asset_version():
    return {"asset_version": ASSET_VERSION, "csrf_token": _csrf_token()}


def _ec2():
    return boto3.client("ec2", region_name=REGION, config=SSM_CONFIG)


def _ssm():
    return boto3.client("ssm", region_name=REGION, config=SSM_CONFIG)


def _budgets():
    return boto3.client("budgets", region_name="us-east-1", config=SSM_CONFIG)


def _money(amount: float | None) -> str:
    if amount is None:
        return "—"
    return f"${amount:.2f}"


def _fetch_aws_cost() -> dict:
    """Read AWS Budgets CalculatedSpend. Do not estimate locally."""
    month = None
    budget = None
    forecast = None
    error = None

    try:
        b = _budgets().describe_budget(AccountId=ACCOUNT_ID, BudgetName=BUDGET_NAME)["Budget"]
        budget = float(b["BudgetLimit"]["Amount"])
        actual = b.get("CalculatedSpend", {}).get("ActualSpend", {})
        fc = b.get("CalculatedSpend", {}).get("ForecastedSpend", {})
        if actual.get("Amount") is not None:
            month = float(actual["Amount"])
        if fc.get("Amount") is not None:
            forecast = float(fc["Amount"])
    except Exception as exc:
        error = str(exc)

    pct = None
    if month is not None and budget:
        pct = min(100.0, (month / budget) * 100.0)

    return {
        "hourly": forecast,
        "hourly_label": _money(forecast),
        "month": month,
        "month_label": (
            "—" if month is None or budget is None else f"{_money(month)} / {_money(budget)}"
        ),
        "budget": budget,
        "remaining": None if month is None or budget is None else max(0.0, budget - month),
        "percent": pct,
        "forecast": forecast,
        "forecast_label": _money(forecast),
        "note": "AWS Budgets",
        "error": error,
    }


def cost_snapshot() -> dict:
    now = time.time()
    with _cost_lock:
        cached = _cost_cache["payload"]
        if cached is not None and now - _cost_cache["at"] < COST_CACHE_SECONDS:
            return cached
    payload = _fetch_aws_cost()
    with _cost_lock:
        _cost_cache.update(at=now, payload=payload)
    return payload


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _require_csrf():
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not csrf_matches(session.get("csrf_token"), supplied):
        return jsonify({"ok": False, "error": "Invalid request token. Refresh and try again."}), 400
    return None


def _authed() -> bool:
    return bool(session.get("ok")) and bool(POWER_PASSWORD)


def _require_auth():
    if not _authed():
        return jsonify({"ok": False, "error": "login required"}), 401
    return None


def _require_mutation():
    denied = _require_auth()
    return denied or _require_csrf()


def _curseforge() -> CurseForgeClient:
    return CurseForgeClient(CURSEFORGE_API_KEY)


def _remote() -> SsmPackManager:
    return SsmPackManager(_ssm(), INSTANCE_ID)


def _seed_existing_pack() -> None:
    """Register the known BMC6 install without touching its world or files."""
    if catalog.list_packs():
        return
    existing = catalog.upsert_pack(
        {
            "project_id": 0,
            "project_name": "Better MC [BMC6]",
            "slug": "better-mc-bmc6",
            "client_file_id": 8728668,
            "client_file_name": "Better MC [BMC6] v3",
            "client_url": "https://www.curseforge.com/minecraft/modpacks/better-mc-bmc6/files/8728668",
            "server_file_id": 8728685,
            "server_file_name": "BMC6 ServerPack v3",
            "minecraft_version": "26.1.2",
            "loader": "NeoForge",
        },
        state="ready",
        install_path="/opt/bmc6",
    )
    catalog.select_pack(existing["id"])


def instance_state() -> str:
    resp = _ec2().describe_instances(InstanceIds=[INSTANCE_ID])
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


def minecraft_status() -> dict:
    try:
        status = JavaServer.lookup(f"{MC_HOST}:{MC_PORT}", timeout=3).status()
        players = status.players
        motd_obj = getattr(status, "motd", "")
        motd = getattr(motd_obj, "to_plain", lambda: str(motd_obj))()
        if not isinstance(motd, str):
            motd = str(motd)
        return {
            "online": True,
            "players": getattr(players, "online", 0) or 0,
            "max": getattr(players, "max", 0) or 0,
            "motd": motd,
        }
    except Exception as exc:
        return {"online": False, "error": str(exc)}


def snapshot() -> dict:
    _seed_existing_pack()
    try:
        state = instance_state()
        aws_error = None
    except Exception as exc:
        state = "unknown"
        aws_error = str(exc)
    mc = minecraft_status() if state == "running" else {"online": False}
    busy = _job["state"] == "working"
    operation = catalog.current_operation()
    operation_busy = operation and operation["state"] == "working"
    return {
        "instance_id": INSTANCE_ID,
        "instance_state": state,
        "aws_error": aws_error,
        "connect": MC_CONNECT if MC_PORT == 25565 else f"{MC_CONNECT}:{MC_PORT}",
        "minecraft": mc,
        "job": dict(_job),
        "can_start": not busy and not operation_busy and state == "stopped",
        "can_stop": not busy and not operation_busy and state not in ("stopped", "unknown"),
        "cost": cost_snapshot(),
        "modpacks": {
            "active": catalog.active_pack(),
            "packs": catalog.list_packs(),
            "operation": operation,
        },
    }


def start_like_mcup() -> None:
    """Match scripts/mc-up.sh: start if stopped, wait running, wait for Minecraft."""
    try:
        _set_job("start", "working", "Checking AWS")
        state = instance_state()
        if state == "stopping":
            _set_job("start", "working", "Waiting for the last shutdown to finish")
            _ec2().get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
            state = "stopped"
        if state == "stopped":
            _set_job("start", "working", "Starting the box")
            _ec2().start_instances(InstanceIds=[INSTANCE_ID])
        if state != "running":
            _set_job("start", "working", "Waiting for the box to boot")
            _ec2().get_waiter("instance_running").wait(InstanceIds=[INSTANCE_ID])
        active_pack = catalog.active_pack()
        if not active_pack:
            raise RuntimeError("Select an installed modpack before starting the server.")
        _set_job("start", "working", f"Starting {active_pack['project_name']}")
        _remote().run(
            "activate",
            active_pack,
            lambda status: _set_job("start", "working", f"Starting {active_pack['project_name']} ({status})"),
        )
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            if minecraft_status().get("online"):
                _set_job("start", "done", "Server is ready")
                return
            _set_job("start", "working", "Loading mods")
            time.sleep(10)
        _set_job(
            "start",
            "error",
            "The box is running, but Minecraft never answered. Try again or check the server.",
        )
    except ClientError as exc:
        _set_job("start", "error", exc.response["Error"].get("Message", str(exc)))
    except Exception as exc:
        _set_job("start", "error", str(exc))


def stop_instance() -> None:
    try:
        _set_job("stop", "working", "Stopping the box")
        _ec2().stop_instances(InstanceIds=[INSTANCE_ID])
        _ec2().get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
        _set_job("stop", "done", "Box is stopped")
    except ClientError as exc:
        _set_job("stop", "error", exc.response["Error"].get("Message", str(exc)))
    except Exception as exc:
        _set_job("stop", "error", str(exc))


def _install_modpack(resolved: dict, pack_id: int, operation_id: int) -> None:
    started_for_import = False
    try:
        _set_job("import", "working", "Checking AWS")
        state = instance_state()
        if state == "stopped":
            started_for_import = True
            _set_job("import", "working", "Starting the box to install the server pack")
            _ec2().start_instances(InstanceIds=[INSTANCE_ID])
            _ec2().get_waiter("instance_running").wait(InstanceIds=[INSTANCE_ID])
        elif state != "running":
            raise RuntimeError(f"The box is {state}; wait for it to finish changing state.")

        _set_job("import", "working", "Downloading and checking the server pack")
        _remote().run(
            "install",
            resolved,
            lambda status: _set_job("import", "working", f"Installing {resolved['project_name']} ({status})"),
        )
        catalog.upsert_pack(
            resolved,
            state="ready",
            install_path=f"/opt/minecraft-booter/packs/{resolved['server_file_id']}",
        )
        if started_for_import:
            _set_job("import", "working", "Returning the box to stopped")
            _ec2().stop_instances(InstanceIds=[INSTANCE_ID])
            _ec2().get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
        catalog.update_operation(operation_id, "done", "Server pack installed")
        _set_job("import", "done", f"{resolved['project_name']} is ready to play")
    except Exception as exc:
        catalog.upsert_pack(resolved, state="error", error=str(exc))
        catalog.update_operation(operation_id, "error", str(exc))
        _set_job("import", "error", str(exc))


def _switch_modpack(pack: dict, previous: dict | None, operation_id: int) -> None:
    try:
        _set_job("switch", "working", f"Switching to {pack['project_name']}")
        _remote().run(
            "switch",
            pack,
            lambda status: _set_job("switch", "working", f"Switching modpack ({status})"),
        )
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            if minecraft_status().get("online"):
                catalog.select_pack(pack["id"])
                catalog.update_operation(operation_id, "done", "Modpack switched")
                _set_job("switch", "done", f"{pack['project_name']} is ready")
                return
            time.sleep(5)
        raise RuntimeError("The new modpack did not become ready in time.")
    except Exception as exc:
        recovery_error = ""
        if previous:
            try:
                _remote().run("switch", previous, lambda _status: None)
            except Exception as rollback_exc:
                recovery_error = f" The previous pack could not be restored: {rollback_exc}"
        message = f"{exc}{recovery_error}"
        catalog.update_operation(operation_id, "error", message)
        _set_job("switch", "error", message)


@app.get("/")
def home():
    if not _authed():
        return render_template("login.html")
    return render_template("index.html", data=snapshot())


@app.post("/login")
def login():
    denied = _require_csrf()
    if denied:
        return denied
    supplied = request.form.get("password", "")
    if POWER_PASSWORD and hmac.compare_digest(supplied, POWER_PASSWORD):
        session["ok"] = True
        _csrf_token()
        return redirect(url_for("home"))
    return render_template("login.html", error="Wrong password"), 401


@app.post("/logout")
def logout():
    denied = _require_csrf()
    if denied:
        return denied
    session.clear()
    return redirect(url_for("home"))


@app.get("/api/status")
def api_status():
    denied = _require_auth()
    if denied:
        return denied
    return jsonify(snapshot())


@app.post("/api/modpacks/resolve")
def api_resolve_modpack():
    denied = _require_mutation()
    if denied:
        return denied
    try:
        payload = request.get_json(silent=True) or {}
        resolved = _curseforge().resolve(parse_curseforge_link(payload.get("url", "")))
        return jsonify({"ok": True, "pack": resolved})
    except CurseForgeLinkError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        return jsonify({"ok": False, "error": "Could not reach CurseForge. Try again shortly."}), 502


@app.post("/api/modpacks/import")
def api_import_modpack():
    denied = _require_mutation()
    if denied:
        return denied
    try:
        with _lock:
            if _job["state"] == "working" or (catalog.current_operation() or {}).get("state") == "working":
                return jsonify({"ok": False, "error": "A server operation is already running."}), 409
        payload = request.get_json(silent=True) or {}
        resolved = _curseforge().resolve(parse_curseforge_link(payload.get("url", "")))
        existing = next((pack for pack in catalog.list_packs() if pack["server_file_id"] == resolved["server_file_id"]), None)
        if existing and existing["state"] == "ready":
            return jsonify({"ok": True, "pack": existing, "message": "That server pack is already installed."})
        pack = catalog.upsert_pack(resolved, state="installing")
        operation = catalog.start_operation("install", pack["id"], "Preparing server-pack installation")
        _set_job("import", "working", "Queued")
        threading.Thread(target=_install_modpack, args=(resolved, pack["id"], operation["id"]), daemon=True).start()
        return jsonify({"ok": True, "operation": operation, "pack": pack})
    except CurseForgeLinkError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception:
        return jsonify({"ok": False, "error": "Could not start the modpack import."}), 502


@app.post("/api/modpacks/select")
def api_select_modpack():
    denied = _require_mutation()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    try:
        pack = catalog.get_pack(int(payload.get("pack_id")))
    except (TypeError, ValueError):
        pack = None
    if not pack or pack["state"] != "ready":
        return jsonify({"ok": False, "error": "Choose an installed modpack."}), 400
    if _job["state"] == "working" or (catalog.current_operation() or {}).get("state") == "working":
        return jsonify({"ok": False, "error": "A server operation is already running."}), 409
    previous = catalog.active_pack()
    if previous and previous["id"] == pack["id"]:
        return jsonify({"ok": True, "pack": pack})
    state = instance_state()
    online = minecraft_status().get("online") if state == "running" else False
    if state == "stopped":
        catalog.select_pack(pack["id"])
        return jsonify({"ok": True, "pack": pack, "message": "Selected for the next start."})
    if online and not payload.get("confirm"):
        return jsonify({"ok": False, "confirmation_required": True, "error": "Players are online. Switching disconnects them after a 60-second countdown."}), 409
    try:
        operation = catalog.start_operation("switch", pack["id"], "Preparing modpack switch")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    _set_job("switch", "working", "Queued")
    threading.Thread(target=_switch_modpack, args=(pack, previous, operation["id"]), daemon=True).start()
    return jsonify({"ok": True, "operation": operation})


@app.post("/api/start")
def api_start():
    denied = _require_mutation()
    if denied:
        return denied
    with _lock:
        if _job["state"] == "working" or (catalog.current_operation() or {}).get("state") == "working":
            return jsonify({"ok": False, "error": "a job is already running", "job": _job}), 409
        _set_job("start", "working", "Queued")
        threading.Thread(target=start_like_mcup, daemon=True).start()
    return jsonify({"ok": True, "job": _job})


@app.post("/api/stop")
def api_stop():
    denied = _require_mutation()
    if denied:
        return denied
    with _lock:
        if _job["state"] == "working" or (catalog.current_operation() or {}).get("state") == "working":
            return jsonify({"ok": False, "error": "a job is already running", "job": _job}), 409
        _set_job("stop", "working", "Queued")
        threading.Thread(target=stop_instance, daemon=True).start()
    return jsonify({"ok": True, "job": _job})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
