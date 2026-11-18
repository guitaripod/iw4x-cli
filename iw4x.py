#!/usr/bin/env python3
"""List IW4x servers and connect to one from a terminal.

The connect is handed to an already-running game over IW4x's own IPC pipe
(``iw4x://host:port`` parsed by iw4x.dll), so it never needs the in-game
server browser. Both the shortcut to launch and the Proton prefix to talk to
are read from the Steam non-Steam shortcut that runs iw4x.exe.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

MASTER_URL = "https://master.iw4x.io/v1/servers/iw4x?protocol=153"
DEFAULT_PORT = 28960
SHORTCUT_EXE = "iw4x.exe"

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


def read_binary_vdf(data: bytes, offset: int = 0) -> tuple[dict, int]:
    fields: dict = {}
    while offset < len(data):
        kind = data[offset]
        if kind == 0x08:
            return fields, offset + 1
        if kind not in (0x00, 0x01, 0x02, 0x07):
            raise CliError(f"unexpected shortcuts.vdf byte 0x{kind:02x} at {offset}")
        end = data.index(b"\x00", offset + 1)
        key = data[offset + 1 : end].decode("utf-8", "replace")
        offset = end + 1
        if kind == 0x00:
            fields[key], offset = read_binary_vdf(data, offset)
        elif kind == 0x01:
            end = data.index(b"\x00", offset)
            fields[key] = data[offset:end].decode("utf-8", "replace")
            offset = end + 1
        elif kind == 0x02:
            fields[key] = struct.unpack("<I", data[offset : offset + 4])[0]
            offset += 4
        else:
            fields[key] = struct.unpack("<Q", data[offset : offset + 8])[0]
            offset += 8
    return fields, offset


def shortcut_entries(node: dict) -> list[dict]:
    found = []
    for value in node.values():
        if isinstance(value, dict):
            if value.get("appid") and value.get("Exe"):
                found.append(value)
            found.extend(shortcut_entries(value))
    return found


def shortcuts() -> list[dict]:
    entries = []
    for userdata in sorted(steam_root().glob("userdata/*/config/shortcuts.vdf")):
        try:
            parsed, _ = read_binary_vdf(userdata.read_bytes())
        except (CliError, ValueError, IndexError):
            continue
        entries.extend(shortcut_entries(parsed))
    return entries


def clean_path(value: str) -> Path:
    return Path(value.strip().strip('"').strip())


def iw4x_shortcut() -> dict:
    candidates = [
        entry
        for entry in shortcuts()
        if clean_path(entry.get("Exe", "")).name.lower() == SHORTCUT_EXE
    ]
    preferred = [entry for entry in candidates if "iw4x" in entry.get("AppName", "").lower()]
    chosen = (preferred or candidates)[:1]
    if not chosen:
        raise CliError(f"no Steam shortcut runs {SHORTCUT_EXE}; add one in Steam first")
    return chosen[0]


def game_dir() -> Path:
    directory = clean_path(iw4x_shortcut()["Exe"]).parent
    if not (directory / SHORTCUT_EXE).is_file():
        raise CliError(f"{directory} has no {SHORTCUT_EXE}")
    return directory


def compat_data(appid: int) -> Path:
    for root in library_roots():
        candidate = root / "steamapps" / "compatdata" / str(appid)
        if (candidate / "pfx").is_dir():
            return candidate
    raise CliError(f"no Proton prefix for app {appid}; launch the IW4x shortcut once")


def proton_binary(appid: int) -> Path:
    override = os.environ.get("IW4X_PROTON")
    if override:
        return Path(override)
    info = compat_data(appid) / "config_info"
    if not info.is_file():
        raise CliError(f"{info} missing, cannot tell which Proton the shortcut uses")
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
    return bool(ADDRESS.match(query) or BARE_IPV4.match(query))


def as_address(query: str) -> str:
    return query if ":" in query else f"{query}:{DEFAULT_PORT}"


def game_running() -> bool:
    probe = subprocess.run(["pgrep", "-af", r"iw4x\.exe|iw4mp\.exe"], capture_output=True, text=True)
    own = str(os.getpid())
    return any(own not in line for line in probe.stdout.splitlines())


def wine_handoff(connect_url: str) -> None:
    shortcut = iw4x_shortcut()
    appid = shortcut["appid"]
    directory = game_dir()
    environment = dict(os.environ)
    environment.update(
        {
            "WINEDEBUG": "-all",
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_root()),
            "STEAM_COMPAT_DATA_PATH": str(compat_data(appid)),
            "STEAM_COMPAT_MOUNTS": str(directory),
        }
    )
    print(f"handing {connect_url} to the running game…", flush=True)
    result = subprocess.run(
        [str(proton_binary(appid)), "run", SHORTCUT_EXE, connect_url],
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
    appid = iw4x_shortcut()["appid"]
    subprocess.Popen(
        ["steam", "-applaunch", str(appid), connect_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"launching the IW4x shortcut with {connect_url}.")
    print("if it opens at the main menu instead, run the same command again once the game is up.")


def connect(address: str, dry_run: bool) -> None:
    print(f"{address}  iw4x://{address}")
    if dry_run:
        return
    connect_url = f"iw4x://{address}"
    if game_running():
        wine_handoff(connect_url)
    else:
        steam_launch(connect_url)


def command_list(arguments: argparse.Namespace) -> None:
    visible = ranked(fetch_servers(), arguments.all, arguments.min_players)
    if arguments.target:
        visible = [server for server in visible if matches(server, arguments.target)]
    if arguments.json:
        print(json.dumps(visible, indent=1))
        return
    if not visible:
        raise CliError("no server matched")
    print(table(visible))


def resolve(target: str) -> str:
    if is_address(target):
        return as_address(target)
    if target.isdigit():
        populated = ranked(fetch_servers(), False, 1)
        index = int(target)
        if not 1 <= index <= len(populated):
            raise CliError(f"index {index} is out of range for the {len(populated)} populated servers")
        return address_of(populated[index - 1])
    visible = [server for server in ranked(fetch_servers(), False, 1) if matches(server, target)]
    if len(visible) == 1:
        return address_of(visible[0])
    if not visible:
        raise CliError(f"nothing matched {target!r}")
    print(table(visible))
    raise CliError(f"{len(visible)} servers matched {target!r}, narrow it down or pass an ip:port")


def command_join(arguments: argparse.Namespace) -> None:
    target = arguments.target.removeprefix("iw4x://").strip() if arguments.target else ""
    if not target:
        raise CliError("give a server name, an ip:port, or omit the argument for the picker")
    connect(resolve(target), arguments.dry_run)


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
    address = address_of(visible[int(choice) - 1]) if choice.isdigit() and 1 <= int(choice) <= len(visible) else None
    connect(address or resolve(choice), arguments.dry_run)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iw4x", description="List and join IW4x servers from the terminal.")
    parser.add_argument("target", nargs="?", help="server name fragment, list index or ip:port; omit for the picker")
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
