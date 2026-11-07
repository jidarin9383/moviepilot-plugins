# Contributing

Keep PublicBT and CloudShare independently installable. Preserve explicit
downloader/path choices, private credential handling, resumable checkpoints,
and the distinction between cloud save, handoff, download, and media transfer.

Run the tests documented in the root README. New behavior needs a focused
regression test. Host-only and live-service checks must remain explicit;
normal CI must not need credentials or submit downloads.

Do not include personal service addresses, deployment records, media, or
runtime state in a change. Test fixtures should use synthetic names, keys,
and `.invalid`/`.example` addresses. Contributions are under GPL-3.0-only.
