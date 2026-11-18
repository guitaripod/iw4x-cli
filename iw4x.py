#!/usr/bin/env python3
"""List IW4x servers and connect to one from a terminal.

The connect is handed to an already-running game over IW4x's own IPC pipe
(``iw4x://host:port`` parsed by iw4x.dll), so it never needs the in-game
server browser.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

STEAM_APPID = "10190"
GAME_FOLDER = "Call of Duty Modern Warfare 2"
MASTER_URL = "https://master.iw4x.io/v1/servers/iw4x?protocol=153"
DEFAULT_PORT = 28960

COLOR_CODES = re.compile(r"\^[0-9:;]")
NON_PRINTABLE = re.compile(r"[^\x20-\x7e]")
ADDRESS = re.compile(r"^[A-Za-z0-9._\[\]:-]+:\d{1,5}$")
BARE_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


class CliError(Exception):
    pass


def steam_root() -> Path:
    for candidate in (Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"):
        if (candidate / "steamapps").is_dir():
            return candidate.resolve()
    raise CliError("Steam install not found")


def library_roots() -> list[Path]:
    root = steam_root()
    roots = [root]
    folders = root / "steamapps" / "libraryfolders.vdf"
    if folders.is_file():
        for path in re.findall(r'"path"\s+"([^"]+)"', folders.read_text()):
            roots.append(Path(path))
    return [path for path in roots if path.is_dir()]


def game_dir() -> Path:
    for root in library_roots():
        candidate = root / "steamapps" / "common" / GAME_FOLDER
        if (candidate / "iw4x.exe").is_file():
            return candidate
    raise CliError(f"{GAME_FOLDER} with iw4x.exe not found in any Steam library")


def compat_data() -> Path:
    for root in library_roots():
        candidate = root / "steamapps" / "compatdata" / STEAM_APPID
        if (candidate / "pfx").is_dir():
            return candidate
    raise CliError(f"Proton prefix for app {STEAM_APPID} not found")


def proton_binary() -> Path:
    override = os.environ.get("IW4X_PROTON")
    if override:
        return Path(override)
    info = compat_data() / "config_info"
    if not info.is_file():
        raise CliError(f"{info} missing, cannot tell which Proton the game runs under")
    for line in info.read_text().splitlines():
        if "/files/" in line:
            files = Path(line.rstrip("/"))
            while files.name and files.name != "files":
                files = files.parent
            return files.parent / "proton"
    raise CliError("Proton path not found in config_info")


def fetch_servers() -> list[dict]:
    request = urllib.request.Request(MASTER_URL, headers={"User-Agent": "iw4x-cli"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())["servers"]


def clean(text: str) -> str:
    return NON_PRINTABLE.sub("", COLOR_CODES.sub("", text)).strip()


def address_of(server: dict) -> str:
    return f"{server['ip']}:{server['port']}"


def ranked(servers: list[dict], include_empty: bool, minimum: int) -> list[dict]:
    if include_empty:
        visible = list(servers)
    else:
        threshold = max(1, minimum)
        visible = [server for server in servers if server.get("clients", 0) >= threshold]
    return sorted(visible, key=lambda server: (-server.get("clients", 0), clean(server.get("hostname", "")).lower()))


def matches(server: dict, query: str) -> bool:
    haystack = " ".join(
        [
            clean(server.get("hostname", "")),
            clean(server.get("mapname_display", "") or server.get("mapname", "")),
            clean(server.get("gametype_display", "") or server.get("gametype", "")),
            address_of(server),
        ]
    ).lower()
    return all(token in haystack for token in query.lower().split())


def table(servers: list[dict]) -> str:
    rows = []
    for index, server in enumerate(servers, 1):
        players = f"{server.get('clients', 0)}/{server.get('sv_maxclients', 0)}"
        map_name = clean(server.get("mapname_display") or server.get("mapname", ""))[:16]
        gametype = clean(server.get("gametype_display") or server.get("gametype", ""))[:18]
        rows.append((str(index), players, map_name, gametype, clean(server.get("hostname", ""))))

    headers = ("#", "PLAYERS", "MAP", "MODE", "SERVER")
    widths = [max(len(row[column]) for row in (*rows, headers)) for column in range(5)]
    lines = ["  ".join(headers[column].ljust(widths[column]) for column in range(5)).rstrip()]
    for row in rows:
        lines.append("  ".join(row[column].ljust(widths[column]) for column in range(4)) + "  " + row[4])
    return "\n".join(lines)


def is_address(query: str) -> bool:
    if ADDRESS.match(query):
        return True
    return bool(BARE_IPV4.match(query))


def as_address(query: str) -> str:
    return query if ":" in query else f"{query}:{DEFAULT_PORT}"


def game_running() -> bool:
    probe = subprocess.run(["pgrep", "-af", r"iw4mp\.exe|iw4x\.exe"], capture_output=True, text=True)
    own = str(os.getpid())
    return any(own not in line for line in probe.stdout.splitlines())


def wine_handoff(connect_url: str) -> None:
    directory = game_dir()
    environment = dict(os.environ)
    environment.update(
        {
            "WINEDEBUG": "-all",
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_root()),
            "STEAM_COMPAT_DATA_PATH": str(compat_data()),
            "STEAM_COMPAT_MOUNTS": str(directory.parent.parent),
        }
    )
    print(f"handing {connect_url} to the running game…", flush=True)
    result = subprocess.run(
        [str(proton_binary()), "run", "iw4x.exe", connect_url],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise CliError("connect failed:\n" + "\n".join(details))
    print("sent. the game is joining.")


def steam_launch(connect_url: str) -> None:
    subprocess.Popen(
        ["steam", "-applaunch", STEAM_APPID, connect_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"launching the game with {connect_url}.")
    print("if it opens at the main menu instead, run the same command again.")


def command_list(arguments: argparse.Namespace) -> None:
    servers = fetch_servers()
    visible = ranked(servers, arguments.all, arguments.min_players)
    if arguments.target:
        visible = [server for server in visible if matches(server, arguments.target)]
    if arguments.json:
        print(json.dumps(visible, indent=1))
        return
    if not visible:
        raise CliError("no server matched")
    print(table(visible))


def command_join(arguments: argparse.Namespace) -> None:
    target = arguments.target.removeprefix("iw4x://").strip() if arguments.target else ""
    if not target:
        raise CliError("give a server name, an ip:port, or use the picker (no argument)")

    address = None
    if is_address(target):
        address = as_address(target)
    elif target.isdigit():
        populated = ranked(fetch_servers(), False, 1)
        index = int(target)
        if not 1 <= index <= len(populated):
            raise CliError(f"index {index} is out of range for the {len(populated)} populated servers")
        address = address_of(populated[index - 1])
    else:
        visible = [server for server in ranked(fetch_servers(), False, 1) if matches(server, target)]
        if len(visible) == 1:
            address = address_of(visible[0])
        elif not visible:
            raise CliError(f"nothing matched {target!r}")
        else:
            print(table(visible))
            raise CliError(f"{len(visible)} servers matched {target!r}, narrow it down or pass an ip:port")

    print(f"{address}  iw4x://{address}")
    if arguments.dry_run:
        return
    connect_url = f"iw4x://{address}"
    if game_running():
        wine_handoff(connect_url)
    else:
        steam_launch(connect_url)


def command_pick(arguments: argparse.Namespace) -> int:
    visible = ranked(fetch_servers(), False, 1)
    if not visible:
        raise CliError("no populated server right now")
    print(table(visible))
    if not sys.stdin.isatty():
        return 0
    choice = input("\njoin #> ").strip()
    if not choice:
        return 0
    if choice.isdigit() and 1 <= int(choice) <= len(visible):
        address = address_of(visible[int(choice) - 1])
        print(f"{address}  iw4x://{address}")
        connect_url = f"iw4x://{address}"
        if game_running():
            wine_handoff(connect_url)
        else:
            steam_launch(connect_url)
        return 0
    arguments.target = choice
    command_join(arguments)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iw4x", description="List and join IW4x servers from the terminal.")
    parser.add_argument("target", nargs="?", help="server name fragment or ip:port; omit for the picker")
    parser.add_argument("--list", "-l", action="store_true", help="list servers instead of joining")
    parser.add_argument("--all", "-a", action="store_true", help="include empty servers")
    parser.add_argument("--min-players", type=int, default=1, metavar="N", help="only servers with at least N players")
    parser.add_argument("--json", action="store_true", help="print the server list as JSON")
    parser.add_argument("--dry-run", action="store_true", help="print the connect target without launching anything")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.list or arguments.json:
            command_list(arguments)
        elif arguments.target:
            command_join(arguments)
        else:
            return command_pick(arguments)
    except CliError as error:
        print(f"iw4x: {error}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"iw4x: cannot reach the master server: {error.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
