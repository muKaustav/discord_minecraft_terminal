"""CurseForge link resolution and safe server-pack metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CURSEFORGE_HOSTS = {"www.curseforge.com", "curseforge.com"}
MODPACK_PREFIX = "/minecraft/modpacks/"


class CurseForgeLinkError(ValueError):
    """The supplied URL is not a CurseForge Minecraft modpack link."""


@dataclass(frozen=True)
class CurseForgeLink:
    slug: str
    file_id: int | None
    url: str


def parse_curseforge_link(value: str) -> CurseForgeLink:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in CURSEFORGE_HOSTS:
        raise CurseForgeLinkError("Use an HTTPS CurseForge Minecraft modpack link.")
    path = parsed.path.rstrip("/")
    if not path.startswith(MODPACK_PREFIX):
        raise CurseForgeLinkError("The link must point to a CurseForge Minecraft modpack.")
    parts = path[len(MODPACK_PREFIX) :].split("/")
    if not parts or not parts[0] or len(parts) > 3:
        raise CurseForgeLinkError("The CurseForge modpack link is not valid.")
    slug = parts[0]
    if len(parts) == 1:
        return CurseForgeLink(slug=slug, file_id=None, url=value.strip())
    if len(parts) != 3 or parts[1] != "files" or not parts[2].isdigit():
        raise CurseForgeLinkError("Use a modpack project link or a specific CurseForge file link.")
    file_id = int(parts[2])
    if file_id <= 0:
        raise CurseForgeLinkError("The CurseForge file ID is not valid.")
    return CurseForgeLink(slug=slug, file_id=file_id, url=value.strip())


def choose_server_file(files: list[dict]) -> dict:
    """Return the newest stable client file that has a matching server pack."""
    candidates = [
        file
        for file in files
        if file.get("releaseType") == 1 and file.get("serverPackFileId")
    ]
    if not candidates:
        raise CurseForgeLinkError("This modpack has no published stable server pack.")
    return max(candidates, key=lambda file: file.get("dateReleased", ""))


def safe_archive_member(name: str) -> bool:
    """Allow only archive paths that remain inside the staging directory."""
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


class CurseForgeClient:
    """Small wrapper around the official CurseForge API used by the panel."""

    def __init__(self, api_key: str, fetch_json: Callable[[str], dict] | None = None):
        if not api_key:
            raise CurseForgeLinkError("CurseForge API access is not configured.")
        self.api_key = api_key
        self.fetch_json = fetch_json or self._fetch_json

    def _fetch_json(self, path: str) -> dict:
        import json

        request = Request(
            f"https://api.curseforge.com{path}",
            headers={"Accept": "application/json", "x-api-key": self.api_key},
        )
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def _project_for_slug(self, slug: str) -> dict:
        payload = self.fetch_json(f"/v1/mods/search?gameId=432&classId=4471&slug={slug}")
        matches = [project for project in payload.get("data", []) if project.get("slug") == slug]
        if not matches:
            raise CurseForgeLinkError("That CurseForge modpack could not be found.")
        return matches[0]

    def _file(self, project_id: int, file_id: int) -> dict:
        payload = self.fetch_json(f"/v1/mods/{project_id}/files/{file_id}")
        file = payload.get("data")
        if not file:
            raise CurseForgeLinkError("That CurseForge release could not be found.")
        return file

    def _download_url(self, project_id: int, file: dict) -> str:
        url = file.get("downloadUrl")
        if url:
            return url
        payload = self.fetch_json(f"/v1/mods/{project_id}/files/{file['id']}/download-url")
        url = payload.get("data")
        if not url:
            raise CurseForgeLinkError("CurseForge does not permit this server pack download.")
        return url

    def resolve(self, link: CurseForgeLink) -> dict:
        project = self._project_for_slug(link.slug)
        project_id = project["id"]
        if link.file_id is None:
            files = self.fetch_json(f"/v1/mods/{project_id}/files").get("data", [])
            client_file = choose_server_file(files)
            server_file = self._file(project_id, client_file["serverPackFileId"])
        else:
            selected = self._file(project_id, link.file_id)
            if selected.get("isServerPack"):
                files = self.fetch_json(f"/v1/mods/{project_id}/files").get("data", [])
                client_file = next(
                    (file for file in files if file.get("serverPackFileId") == selected["id"]),
                    None,
                )
                if client_file is None:
                    raise CurseForgeLinkError("This server pack has no matching client release.")
                server_file = selected
            else:
                client_file = selected
                server_file_id = client_file.get("serverPackFileId")
                if not server_file_id:
                    raise CurseForgeLinkError("This release has no published server pack.")
                server_file = self._file(project_id, server_file_id)

        game_versions = client_file.get("gameVersions", [])
        minecraft_version = next(
            (version for version in game_versions if re.match(r"^\d+\.\d+(?:\.\d+)?$", version)),
            "Unknown",
        )
        loader = next(
            (version for version in game_versions if version.lower() in {"forge", "neoforge", "fabric"}),
            "Unknown",
        )
        return {
            "project_id": project_id,
            "project_name": project.get("name", link.slug),
            "slug": link.slug,
            "client_file_id": client_file["id"],
            "client_file_name": client_file.get("displayName") or client_file.get("fileName"),
            "client_url": f"https://www.curseforge.com/minecraft/modpacks/{link.slug}/files/{client_file['id']}",
            "server_file_id": server_file["id"],
            "server_file_name": server_file.get("fileName", "server-pack.zip"),
            "server_download_url": self._download_url(project_id, server_file),
            "minecraft_version": minecraft_version,
            "loader": loader,
        }
