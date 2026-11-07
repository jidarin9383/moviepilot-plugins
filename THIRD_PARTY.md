# Upstream components

This repository contains plugin adapters and a locally built helper. It does
not bundle MoviePilot, the official cloud clients, dependency source trees,
or pre-authenticated accounts. Each upstream component retains its own license.

| Component | Relationship | Upstream |
|---|---|---|
| MoviePilot | Host SDK and native plugin/component contracts | https://github.com/jxxghp/MoviePilot |
| Prowlarr | PublicBT search/status API | https://github.com/Prowlarr/Prowlarr |
| PanSou | CloudShare search API | https://github.com/fish2018/pansou |
| aligo | Optional Alipan save subprocess, tested at 6.2.8 | https://github.com/foyoux/aligo |
| Telethon | Optional Telegram user-session inline queries, tested at 1.45.0 | https://github.com/LonamiWebs/Telethon |
| python-socks | Optional Telegram proxy support | https://github.com/romis2012/python-socks |

MoviePilot and aligo source distributions examined during preparation include
the GNU GPL version 3 license text. This repository uses GPL-3.0-only. Dependency
installation and redistribution must retain each dependency's upstream notices.

Quark and Alipan are third-party services and clients. Their names are used to
identify integration targets; this project is not an official client or endorsement.
