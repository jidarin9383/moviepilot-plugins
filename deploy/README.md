# CloudShare Mac dispatcher

[中文配置说明](README.zh-CN.md)

The Swift helper handles Quark and Alipan manifests written by CloudShare to
the shared staging directory. It uses the official clients, not qBittorrent
or Transmission, for cloud-drive files.

## Build

On macOS with the Xcode command-line tools:

```sh
bash deploy/build_dispatch.sh
```

This builds `dist/CloudShare Dispatch.app` locally. It does not install or
start it, modify client preferences, or grant permissions.

## Configure and start

Mount your NAS share in Finder and configure the clients' default download
directory. Supply these environment values when starting the helper:

| Variable | Default | Meaning |
|---|---|---|
| `CLOUDSHARE_MAC_STAGING` | `/Volumes/Downloads/cloudshare-staging` | Mac path to the shared staging directory |
| `CLOUDSHARE_SMB_MOUNT` | `/Volumes/Downloads` | Expected mount root; must be a network volume |
| `CLOUDSHARE_SMB_URL` | unset | Optional `smb://nas.example/Downloads` reconnect URL without credentials |

For a first manual run, use the compiled executable with `service`:

```sh
export CLOUDSHARE_MAC_STAGING=/Volumes/Downloads/cloudshare-staging
export CLOUDSHARE_SMB_MOUNT=/Volumes/Downloads
"dist/CloudShare Dispatch.app/Contents/MacOS/quark-dispatch" service
```

Grant Accessibility to your locally built app through System Settings and
restart it after granting access. macOS may require the permission to be
renewed after replacing an ad-hoc-signed executable. For login persistence,
create a LaunchAgent appropriate to your installation, with the environment
values above and an absolute app path. No personal LaunchAgent is distributed.

The helper checks both manifests every 30 seconds, validates network-volume
paths, and uses private per-task claims to avoid duplicate dispatch. SMB
credentials stay in macOS/Finder rather than the reconnect URL. Existing
files are not removed to force a download; only an empty task directory may
be removed to avoid a client-created duplicate directory suffix.

Logs are under `~/Library/Logs/CloudShare/`. Quark actions wait for an idle,
unlocked desktop. Alipan confirmation requires a matching task ID and the
configured network staging path. See the plugin README for client-exit and
dialog limitations. This helper does not guarantee unattended recovery from
all official-client UI changes.
