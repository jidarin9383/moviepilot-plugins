# SPDX-License-Identifier: GPL-3.0-only
"""Discover and size downloadable media from PanSou's Quark results."""

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time

from .selection import ShareFile, select_files


QUARK_API = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage"
SHARE_URL = re.compile(r"https://pan\.quark\.cn/s/([A-Za-z0-9]+)(?:[/?#].*)?")
PAGE_SIZE = 100
MAX_ENTRIES = 2000
MAX_DEPTH = 8
CACHE_SECONDS = 600
TELEGRAM_CACHE_SECONDS = 60
ENRICHMENT_SECONDS = 8


class DiscoveryError(Exception):
    """A provider could not supply a usable response."""


class UnavailableShare(DiscoveryError):
    """Quark rejected the share or its access code."""


@dataclass(frozen=True)
class Candidate:
    share_id: str
    title: str
    passcode: str = field(repr=False)

    @property
    def url(self) -> str:
        return f"https://pan.quark.cn/s/{self.share_id}"


@dataclass(frozen=True)
class SearchResult:
    candidate: Candidate
    files: tuple[ShareFile, ...]

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)


async def _request(url, params, *, body=None, headers=None):
    from app.sdk.network import AsyncRequestUtils

    client = AsyncRequestUtils(
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://pan.quark.cn/", **(headers or {})},
        timeout=5, trust_env=False, verify=True,
    )
    if body is None:
        response = await client.get_res(url, params=params, allow_redirects=False)
    else:
        response = await client.post_res(url, params=params, json=body, allow_redirects=False)
    if response is None:
        raise DiscoveryError("Provider connection failed")
    try:
        if not 200 <= response.status_code < 300:
            raise DiscoveryError(f"Provider returned HTTP {response.status_code}")
        payload = response.json()
    except ValueError:
        raise DiscoveryError("Invalid provider JSON") from None
    finally:
        await response.aclose()
    if not isinstance(payload, dict):
        raise DiscoveryError("Invalid provider response")
    return payload


def _date(item):
    value = item.get("datetime")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
        except ValueError:
            pass
    return float("-inf")


class CloudShareDiscovery:
    def __init__(self, pansou_url, request=_request, *, telegram=None):
        self._search_url = pansou_url.rstrip("/") + "/api/search"
        self._request = request
        self._telegram = telegram
        self._cache = OrderedDict()

    async def candidates(self, keyword):
        if self._telegram is None:
            return await self._pansou(keyword)
        pansou, telegram = await asyncio.gather(
            self._pansou(keyword), self._telegram(keyword), return_exceptions=True,
        )
        if isinstance(pansou, DiscoveryError) and isinstance(telegram, DiscoveryError):
            raise pansou
        for result in (pansou, telegram):
            if isinstance(result, BaseException) and not isinstance(result, DiscoveryError):
                raise result
        combined = {item.share_id: item for item in pansou} if isinstance(pansou, list) else {}
        extra = []
        for item in telegram if isinstance(telegram, list) else ():
            existing = combined.get(item.share_id)
            if existing is None:
                extra.append(item)
            elif not existing.passcode and item.passcode:
                combined[item.share_id] = Candidate(existing.share_id, existing.title, item.passcode)
        # Reserve space for unique Telegram matches within the existing enrichment budget.
        extra = extra[:3]
        return list(combined.values())[:10 - len(extra)] + extra

    async def _pansou(self, keyword):
        payload = await self._request(self._search_url, {
            "kw": keyword, "res": "merge", "cloud_types": "quark",
        })
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise DiscoveryError("PanSou search failed")
        groups = data.get("merged_by_type") or {}
        links = groups.get("quark") if isinstance(groups, dict) else None
        if links is None:
            return []
        if not isinstance(links, list):
            raise DiscoveryError("Invalid PanSou results")
        candidates = {}
        for item in sorted((item for item in links if isinstance(item, dict)),
                           key=_date, reverse=True):
            match = SHARE_URL.fullmatch(str(item.get("url") or ""))
            if not match:
                continue
            share_id = match[1]
            title = " ".join(str(item.get("note") or item.get("title") or "").split())
            candidates.setdefault(share_id, Candidate(share_id, title or "Quark share",
                                                      str(item.get("password") or "")))
            if len(candidates) == 10:
                break
        return list(candidates.values())

    async def _quark(self, endpoint, params, *, body=None):
        payload = await self._request(QUARK_API + "/" + endpoint,
                                      {"pr": "ucpro", "fr": "pc", "uc_param_str": "", **params}, body=body)
        if payload.get("code") != 0:
            if endpoint == "token":
                raise UnavailableShare("Quark share unavailable")
            raise DiscoveryError("Quark listing failed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DiscoveryError("Invalid Quark response")
        return data, payload.get("metadata")

    async def _files(self, candidate):
        access, _ = await self._quark("token", {}, body={
            "pwd_id": candidate.share_id, "passcode": candidate.passcode,
        })
        token = access.get("stoken")
        if not isinstance(token, str) or not token:
            raise DiscoveryError("Quark token missing")

        pending = deque([("0", "", 0)])
        seen_ids, seen_paths, files = {"0"}, set(), []
        while pending:
            parent, prefix, depth = pending.popleft()
            page = listed = 0
            while True:
                page += 1
                data, metadata = await self._quark("detail", {
                    "pwd_id": candidate.share_id, "stoken": token, "pdir_fid": parent,
                    "_page": page, "_size": PAGE_SIZE, "_fetch_total": 1,
                })
                entries = data.get("list")
                total = metadata.get("_total") if isinstance(metadata, dict) else None
                if (not isinstance(entries, list) or len(entries) > PAGE_SIZE
                        or (total is not None and (type(total) is not int
                                                  or not listed + len(entries) <= total <= MAX_ENTRIES))):
                    raise DiscoveryError("Invalid or oversized Quark directory")
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise DiscoveryError("Invalid Quark entry")
                    file_id, name = entry.get("fid"), entry.get("file_name")
                    if (not isinstance(file_id, str) or not file_id or file_id in seen_ids
                            or len(seen_ids) > MAX_ENTRIES or not isinstance(name, str)):
                        raise DiscoveryError("Invalid or oversized Quark directory")
                    seen_ids.add(file_id)
                    if name in {"", ".", ".."} or any(char in name for char in "/\\:\x00"):
                        continue
                    path = prefix + name
                    if path in seen_paths:
                        raise DiscoveryError("Duplicate Quark path")
                    seen_paths.add(path)
                    if entry.get("dir"):
                        if depth >= MAX_DEPTH:
                            raise DiscoveryError("Quark directory is too deep")
                        pending.append((file_id, path + "/", depth + 1))
                    else:
                        size = entry.get("size")
                        if type(size) is int and size > 0:
                            files.append(ShareFile(file_id, path, size, parent))
                listed += len(entries)
                if (total is not None and listed == total) or (total is None and len(entries) < PAGE_SIZE):
                    break
                if not entries:
                    raise DiscoveryError("Incomplete Quark directory")
        return files

    async def resolve(self, candidate: Candidate, episodes: set[int] | None = None) -> SearchResult:
        """Read a fresh selection for an individual download."""
        files = await self._files(candidate)
        return SearchResult(candidate, tuple(select_files(files, episodes)))

    async def _enrich(self, candidate, episodes, slots):
        async with slots:
            try:
                result = await self.resolve(candidate, episodes)
            except UnavailableShare:
                return None, True
            except DiscoveryError:
                return None, False
            return (result if result.files else None), True

    async def search(self, keyword: str, episodes: set[int] | None = None) -> tuple[SearchResult, ...]:
        """Return enriched matches, ordered by publication date."""
        keyword = keyword.strip()
        if not keyword:
            return ()
        episodes = frozenset(episodes) if episodes is not None else None
        key = keyword, episodes
        cached = self._cache.pop(key, None)
        if cached is not None and time.monotonic() < cached[0]:
            self._cache[key] = cached
            return cached[1]

        candidates = await self.candidates(keyword)
        slots = asyncio.Semaphore(3)
        tasks = [asyncio.create_task(self._enrich(candidate, episodes, slots)) for candidate in candidates]
        done = set()
        try:
            if tasks:
                done, _ = await asyncio.wait(tasks, timeout=ENRICHMENT_SECONDS)
        finally:
            for task in tasks:
                if task not in done:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        outcomes = [task.result() for task in tasks if task in done]
        results = tuple(result for result, _ in outcomes if result is not None)
        if len(done) == len(tasks) and all(stable for _, stable in outcomes):
            ttl = CACHE_SECONDS if self._telegram is None else TELEGRAM_CACHE_SECONDS
            self._cache[key] = time.monotonic() + ttl, results
            if len(self._cache) > 64:
                self._cache.popitem(last=False)
        return results
