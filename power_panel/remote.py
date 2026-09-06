"""Run narrowly defined Minecraft Booter jobs on the EC2 instance through SSM."""

from __future__ import annotations

import base64
import json
import time


MANAGER = "/opt/minecraft-booter/pack_manager.py"


def pack_manager_command(action: str, manifest: dict) -> str:
    if action == "activate":
        return f"python3 {MANAGER} activate {int(manifest['server_file_id'])}"
    if action == "install":
        encoded = base64.b64encode(json.dumps(manifest, separators=(",", ":")).encode()).decode()
        return f"echo {encoded} | base64 --decode | python3 {MANAGER} install"
    if action == "switch":
        return f"python3 {MANAGER} switch {int(manifest['server_file_id'])}"
    raise ValueError("Unsupported pack-manager action.")


class SsmPackManager:
    def __init__(self, client, instance_id: str):
        self.client = client
        self.instance_id = instance_id

    def run(self, action: str, manifest: dict, progress) -> str:
        response = self.client.send_command(
            InstanceIds=[self.instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [pack_manager_command(action, manifest)]},
            CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
            TimeoutSeconds=3600,
        )
        command_id = response["Command"]["CommandId"]
        for _ in range(360):
            time.sleep(5)
            try:
                invocation = self.client.get_command_invocation(
                    CommandId=command_id, InstanceId=self.instance_id,
                )
            except self.client.exceptions.InvocationDoesNotExist:
                continue
            status = invocation["Status"]
            progress(invocation.get("StatusDetails", status))
            if status == "Success":
                return invocation.get("StandardOutputContent", "")
            if status in {"Cancelled", "TimedOut", "Failed", "Cancelling"}:
                detail = invocation.get("StandardErrorContent") or invocation.get("StatusDetails", status)
                raise RuntimeError(detail.strip())
        raise RuntimeError("The server operation timed out.")
