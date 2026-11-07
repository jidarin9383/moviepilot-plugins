# MoviePilot Plugins

Two independently installable plugins for MoviePilot V3.

| Plugin | Purpose | Status |
|---|---|---|
| PublicBT | Prowlarr search, dedicated downloader routing, and activity history | Tested with MoviePilot 3.2.4 |
| CloudShare | Quark/Alipan search, Telegram search sources, cloud saves, and NAS desktop handoff | Experimental; requires a logged-in Mac |

Both plugins currently declare MoviePilot `>=3.2.4,<3.3.0` compatibility.
They have separate settings and storage and do not require each other.

## Installation

Add this repository's published GitHub URL to MoviePilot's plugin repositories,
then install PublicBT and/or CloudShare from the plugin market. `package.v3.json`
and `plugins.v3/` follow the native MoviePilot repository layout. Development
and local-source installations depend on the repository support in your host.

- [PublicBT setup](plugins.v3/publicbt/README.md)
- [CloudShare setup and limitations](plugins.v3/cloudshare/README.md)
- [Mac dispatcher setup](deploy/README.md)

The repository includes source and tests, not credentials, desktop binaries,
personal deployment configurations, media, or runtime databases.

## Development

Use Python 3.11 or newer in an isolated environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest tests/v3/publicbt -q
python -m unittest discover -s tests/v3/cloudshare -q
```

Most tests run without MoviePilot, cloud accounts, Telegram, or network access.
CloudShare's host-dependent cases are explicitly skipped outside MoviePilot.
`tests/v3/publicbt/host_probe.py` is an isolated compatibility probe intended
for a MoviePilot container; it uses synthetic credentials and mocked HTTP.

The macOS dispatcher source can be compiled without running it:

```sh
swiftc -O deploy/quark_dispatch.swift -o /tmp/cloudshare-dispatch
```

Optional Telegram and Alipan dependencies are listed in separate requirement
files. They are not vendored and are not installed automatically by the plugins.

## Data handling

PublicBT stores its Prowlarr key privately and clears the normal configuration
field. CloudShare uses separate private credential files. Activity records
exclude request URLs, keys, cookies, sessions, and raw exception messages.
Download handoff, completed cloud saves, NAS file readiness, and library
transfer are distinct states; a handoff record is not a download-success claim.

See [SECURITY.md](SECURITY.md) before sharing logs or reproductions.

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).

Copyright (c) 2026 Darin Ji.
