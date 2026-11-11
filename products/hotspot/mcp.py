"""Run the Hotspot MCP stdio server or automated browser connect/skill CLI (`python -m products.hotspot.mcp`)."""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import webbrowser
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from products.hotspot.backend.mcp import run_stdio_server
from products.hotspot.backend.service import HotspotService


def _http_json(
    url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 15.0
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "hotspot-mcp/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(raw)
        except Exception:
            return err.code, {"status": 0, "error": raw or f"HTTP {err.code}"}


def _write_secure_file(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == "hotspot" or path.parent.name == ".hotspot":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ensure_gitignored(file_path: Path) -> None:
    resolved = file_path.resolve()
    cwd = Path.cwd().resolve()
    try:
        rel = resolved.relative_to(cwd).as_posix()
    except ValueError:
        return
    gitignore = cwd / ".gitignore"
    existing_lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    needed = rel if "/" not in rel else f"{rel.split('/')[0]}/"
    if rel not in existing_lines and needed not in existing_lines:
        prefix = "\n" if existing_lines and existing_lines[-1] != "" else ""
        with gitignore.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}{needed}\n")


def save_credentials_bundle(
    token_data: dict[str, Any],
    *,
    credentials_path: Path | None = None,
    env_path: Path | None = None,
    mcp_path: Path | None = None,
) -> dict[str, str]:
    """Persist credentials returned by `/hotspot/api/connect/token/` safely on disk."""
    saved: dict[str, str] = {}
    if credentials_path is not None:
        creds = token_data.get("credentials_json") or {
            "site_url": token_data.get("site_url"),
            "mcp_url": token_data.get("mcp_url"),
            "domain": token_data.get("domain"),
            "domains": token_data.get("domains") or [],
            "api_key": token_data.get("api_key"),
            "secret_key": token_data.get("secret_key"),
        }
        _write_secure_file(credentials_path, json.dumps(creds, indent=2) + "\n")
        _ensure_gitignored(credentials_path)
        saved["credentials"] = str(credentials_path)

    if env_path is not None:
        env_text = str(token_data.get("env_lines") or "").rstrip("\n") + "\n"
        _write_secure_file(env_path, env_text)
        _ensure_gitignored(env_path)
        saved["env"] = str(env_path)

    if mcp_path is not None:
        existing: dict[str, Any] = {}
        if mcp_path.exists():
            try:
                loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}
        servers = dict(existing.get("mcpServers") or {})
        incoming = dict((token_data.get("mcp_config") or {}).get("mcpServers") or {})
        servers.update(incoming)
        existing["mcpServers"] = servers
        _write_secure_file(mcp_path, json.dumps(existing, indent=2) + "\n")
        _ensure_gitignored(mcp_path)
        saved["mcp"] = str(mcp_path)

    return saved


def _say(msg: str) -> None:
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def run_connect_cli(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    status, start_data = _http_json(
        f"{base}/hotspot/api/connect/start/",
        method="POST",
        payload={
            "client_name": args.client_name,
            "domain": args.domain or "",
            "name": args.site_name or "",
        },
    )
    if status != 200 or not start_data.get("device_code"):
        _say(f"Failed to start connect session: {start_data.get('error', start_data)}")
        return 1

    verify_url = str(start_data["verification_uri"])
    user_code = str(start_data["user_code"])
    device_code = str(start_data["device_code"])
    interval = max(1, int(start_data.get("interval") or 2))
    deadline = time.monotonic() + max(30, int(start_data.get("expires_in") or 600))

    _say(f"\nOpen this URL to authorize Hotspot for domain access:\n  {verify_url}\n")
    _say(f"Confirm code matches: {user_code}\nWaiting for browser approval...")
    if not args.no_browser:
        try:
            webbrowser.open(verify_url)
        except Exception:
            pass

    while time.monotonic() < deadline:
        time.sleep(interval)
        poll_status, token_data = _http_json(
            f"{base}/hotspot/api/connect/token/",
            method="POST",
            payload={"device_code": device_code},
        )
        if poll_status == 202 and token_data.get("pending"):
            continue
        if poll_status == 200 and token_data.get("status") == 1:
            creds_file = Path(args.save_credentials).expanduser() if args.save_credentials else None
            if creds_file is None and not args.save_env:
                creds_file = Path.home() / ".config" / "hotspot" / "credentials.json"
            env_file = Path(args.save_env).expanduser() if args.save_env else None
            mcp_file = Path(args.save_mcp).expanduser() if args.save_mcp else None
            saved = save_credentials_bundle(
                token_data,
                credentials_path=creds_file,
                env_path=env_file,
                mcp_path=mcp_file,
            )
            domains_label = ", ".join(token_data.get("domains") or [str(token_data.get("domain"))])
            _say(f"\nAuthorized domain(s): {domains_label}")
            for kind, location in saved.items():
                _say(f"Saved {kind} (chmod 600): {location}")
            return 0
        _say(f"Connect failed ({poll_status}): {token_data.get('error', token_data)}")
        return 1

    _say("Timed out waiting for browser authorization.")
    return 1


def run_install_skill_cli(args: argparse.Namespace) -> int:
    url = f"{args.url.rstrip('/')}/skill.md"
    req = urllib.request.Request(
        url,
        headers={"Accept": "text/markdown, text/plain", "User-Agent": "hotspot-mcp/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        text = resp.read().decode("utf-8")
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    _say(f"Installed Hotspot skill to {out_path}")
    return 0


def _read_secret_from_credentials(path_str: str | None) -> str | None:
    if not path_str:
        return None
    p = Path(path_str).expanduser()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        sec = str(data.get("secret_key") or "").strip()
        return sec or None
    except Exception:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    arg_list = list(sys.argv[1:] if argv is None else argv)
    if arg_list and arg_list[0] == "connect":
        parser = argparse.ArgumentParser(
            prog="python -m products.hotspot.mcp connect",
            description="Authorize Hotspot via browser and save local credentials safely.",
        )
        parser.add_argument(
            "--url",
            default=os.environ.get("HOTSPOT_HOST", "https://hotspot.buxa.ai"),
            help="Hotspot server origin URL.",
        )
        parser.add_argument(
            "--client-name", default="Hotspot CLI", help="Client label displayed during browser approval."
        )
        parser.add_argument("--domain", default="", help="Optional domain pre-fill hint.")
        parser.add_argument("--site-name", default="", help="Optional site name hint when registering.")
        parser.add_argument(
            "--save-credentials",
            default="",
            help="JSON credentials file path (default ~/.config/hotspot/credentials.json).",
        )
        parser.add_argument("--save-env", default="", help="Optional .env file path (e.g. .env.hotspot.local).")
        parser.add_argument(
            "--save-mcp", default="", help="Optional .mcp.json path to merge Hotspot MCP server config."
        )
        parser.add_argument(
            "--no-browser", action="store_true", help="Print verification URL without launching a browser."
        )
        return run_connect_cli(parser.parse_args(arg_list[1:]))

    if arg_list and arg_list[0] == "install-skill":
        parser = argparse.ArgumentParser(
            prog="python -m products.hotspot.mcp install-skill",
            description="Download SKILL.md from a running Hotspot instance.",
        )
        parser.add_argument(
            "--url",
            default=os.environ.get("HOTSPOT_HOST", "https://hotspot.buxa.ai"),
            help="Hotspot server origin URL.",
        )
        parser.add_argument("--out", default=".agents/skills/hotspot/SKILL.md", help="Destination SKILL.md path.")
        return run_install_skill_cli(parser.parse_args(arg_list[1:]))

    parser = argparse.ArgumentParser(description="Hotspot MCP stdio server")
    parser.add_argument(
        "--secret-key",
        default=os.environ.get("HOTSPOT_MCP_SECRET_KEY", "").strip() or None,
        help="Optional site Secret Key (hs_sec_...) or admin password for scoped analytics access.",
    )
    parser.add_argument(
        "--credentials-file",
        default=os.environ.get("HOTSPOT_CREDENTIALS_FILE", "").strip() or None,
        help="Optional local credentials.json written by `python -m products.hotspot.mcp connect`.",
    )
    args = parser.parse_args(arg_list)
    secret = args.secret_key or _read_secret_from_credentials(args.credentials_file)
    service = HotspotService.from_env()
    run_stdio_server(service=service, default_secret_key=secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
