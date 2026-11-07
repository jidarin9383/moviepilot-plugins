# PublicBT

PublicBT integrates Prowlarr torrent searches into MoviePilot's normal resource
search and routes them to a separately configured downloader.

## Setup

1. Configure Prowlarr with public torrent indexers only.
2. Add an independent downloader in MoviePilot, such as `qb-public`.
3. Configure the corresponding NAS download and media-library directories in MoviePilot.
4. Open PublicBT settings, enter the Prowlarr base URL and API key, then set the
   exact downloader name and absolute download directory.
5. Enable the plugin, save, and use **View Data → Test Connection**.

Example service URL: `http://prowlarr:9696`. Example download directory:
`/downloads/bt`. Replace both with addresses and paths reachable by your host.

The settings page separates service connectivity, search rules, download
routing, and activity recording. Leaving the API key field blank preserves
the saved key; entering a new key replaces it. The key is stored as a private
`prowlarr.key` file in the plugin data directory and is not returned by the form.

## Search and routing

- Select indexer IDs with a comma-separated list. An empty list searches all
  indexers configured in this Prowlarr instance.
- Minimum seeders defaults to 1. Result limits accept 1–200, default 100.
- Movie and TV searches use categories 2000 and 5000 respectively.
- The search has a 20-second total deadline. Later host pages return no results.
- HTTP redirects are disabled; the key is sent in `X-Api-Key`, not a query parameter.
- Torrent-file URLs are preferred, with magnets used as fallback.
- Explicitly chosen downloaders and directories take priority.

When MoviePilot's manual endpoint clears the source downloader, the plugin
blocks a submission that could fall back to an unrelated default downloader.
Select the dedicated public-BT downloader explicitly in that dialog.

## Activity

View Data contains service state, a connection-test button, and paginated
activity with Refresh. The connection check verifies Prowlarr service access
and API authentication; it does not prove individual indexer health.

Activity covers search outcomes, connection checks, and pre-submit routing
decisions. A prepared handoff is not a successful or completed download.
Actual progress and media transfer remain in MoviePilot's native pages.

The private SQLite history defaults to 1,000 events, configurable from 1 to
5,000. Disabling recording retains existing events. Times display in UTC+08:00.
URLs, magnets, credential assignments, and raw exception messages are excluded.
No historical activity is reconstructed during installation.

The plugin does not manage router ports, VPNs, seeding policies, or existing
download tasks. Those remain under the downloader and host configuration.
