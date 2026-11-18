# iw4x-cli

Join IW4x servers from a terminal, without the in-game server browser.

```sh
iw4x                       # picker: populated servers, type a number to join
iw4x --list                # same list, no prompt
iw4x --list tdm --all      # filter by name, map or gametype; --all includes empty servers
iw4x sesh                  # join the one server that matches "sesh"
iw4x 5.196.78.5:27018      # join an address
iw4x iw4x://5.196.78.5:27018
iw4x arn --dry-run         # print the target, launch nothing
```

## How the connect works

IW4x registers an `iw4x://host:port` protocol. A second `iw4x.exe` started with
one of those URLs does not open a game: `ConnectProtocol` writes `connect
host:port` over the IW4x IPC pipe to the instance that is already running and
exits. `iw4x` reuses exactly that path, running the client inside the game's
own Proton prefix so both processes share a wineserver.

When the game is not running, the URL is handed to Steam instead:

```sh
steam -applaunch 10190 iw4x://host:port
```

If Steam opens the game at the main menu rather than joining, run the same
`iw4x` command again once the game is up: the second call finds the running
instance and hands the connect over.

## Servers

The list comes from the IW4x master server
(`master.iw4x.io/v1/servers/iw4x?protocol=153`), the same feed the in-game
browser uses, so `iw4x --list` is a complete, live list of what the game would
show you.

## Install

```sh
./install.sh              # ~/.local/bin/iw4x
./install.sh --protocol   # also make iw4x:// links in a browser open this CLI
```

Requires Python 3.10+ and a Steam install of Call of Duty: Modern Warfare 2
(app 10190) with IW4x. Override the Proton binary with `IW4X_PROTON` if the
`config_info` lookup misses.

## Licence

GPL-3.0.
