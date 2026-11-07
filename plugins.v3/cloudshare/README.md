# CloudShare

**Experimental.** CloudShare combines PanSou and Telegram search with Quark
and Alipan cloud-save workflows. Official desktop clients on a logged-in,
awake Mac write directly to a NAS SMB staging directory. MoviePilot then
checks completed files and uses its recorded media identity for transfer.

Cloud saves, desktop downloads, and library transfers are separate stages.
The Mac dispatcher is required for the desktop workflow; a NAS-only deployment
does not replace the official clients.

## Prerequisites

- MoviePilot V3 3.2.4 with a writable persistent configuration directory.
- A reachable PanSou instance, normally `http://pansou:8888`.
- A NAS download root, mounted into MoviePilot at the same absolute path.
- Official Quark and/or Alipan Mac clients signed in to the receiving accounts.
- A privately mounted SMB share and macOS Accessibility permission for the dispatcher.

Configure these environment values in the MoviePilot container as needed:

| Variable | Default | Meaning |
|---|---|---|
| `CLOUDSHARE_MEDIA_ROOT` | `/downloads` | NAS-visible media/download root |
| `CLOUDSHARE_PANSOU_URL` | `http://pansou:8888` | PanSou address reachable from MoviePilot |
| `CLOUDSHARE_DOWNLOAD_DIR` | `/downloads/cloud` | Files presented to MoviePilot's transfer workflow |

The plugin form separately configures provider switches, Telegram search,
desktop staging/import directories, Alipan automatic dispatch, and history.
The default staging directory is `/downloads/cloudshare-staging`.

## Private credentials

Quark uses `/config/cloudshare/cookie`, a private regular file owned by the
MoviePilot runtime user with mode 0600. Populate it directly in your own
terminal. Do not put its contents in a repository, issue, log, or chat.

Optional Telegram search uses `@QuarkRobot` and `@AliYunPanBot`. Install the
Telegram requirements in the host's Python environment and run the interactive
setup in your own TTY:

```sh
python deploy/telegram_setup.py
# If required, add --proxy with a NAS-reachable HTTP or SOCKS5 proxy URL.
```

The script prompts without echo, stores a dedicated user session privately at
`/config/cloudshare/telegram.json`, and does not overwrite an existing login.
Reload the plugin after initial setup. Bot names, proxy settings, and search
switches are editable in the form; login secrets are not echoed there.
Queries use Telegram inline search without posting messages to chats.

Optional Alipan automatic saving requires `aligo==6.2.8` and its dependencies
under `/config/cloudshare/aliyun/vendor`, plus a receiving-account login at
`/config/cloudshare/aliyun/cloudshare.json`. Provision that login through
aligo's own interactive workflow in the private directory. This repository
does not include an Alipan first-run login UI or automatically install packages.
Keep the directory private and the login file mode 0600, owned by the runtime user.

## Desktop and media transfer

See [Mac dispatcher instructions](../../deploy/README.md). Set both official
clients to use the SMB staging directory as their default download location.
The NAS path and the Mac-mounted path refer to the same shared directory.

MoviePilot imports complete, stable files using its download history's media
identity. Uncertain identities, ambiguous movie selections, and episodes with
insufficient episode metadata stay for review. Renaming alone is not proof
of media identity. The usage page distinguishes these states and shows NAS-side progress.

## Known limitations

- Initialization currently still needs a Quark receiving-account cookie,
  including when only Alipan search is enabled afterward.
- The Mac must be awake and logged in. Quark must already be running for
  dispatch. Full recovery after either client exits mid-download is incomplete.
- Alipan can be launched through its official handler. Download confirmation
  behavior varies by client state; some visible dialogs are missing from the
  client's accessibility tree. Keep the default path enabled. Unmatched dialogs
  are not clicked automatically.
- Telegram bots, PanSou sources, and share links can become unavailable.
  The search pipeline verifies candidates and retains fallback results.
- Paths, SMB credentials, client accounts, and library rules need local setup.

History is independent of the queue, private, bounded, and retained when tasks
are removed. Existing tasks are labelled as snapshots rather than invented
original events. Use only resources you are authorized to download.
