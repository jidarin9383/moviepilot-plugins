# SPDX-License-Identifier: GPL-3.0-only
"""Exercise Quark discovery without external services."""

import asyncio
from importlib.util import find_spec
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

plugins = Path(__file__).resolve().parents[3] / "plugins.v3"
sys.path.insert(0, str(plugins))
if find_spec("app") is None:
    package = ModuleType("cloudshare")
    package.__path__ = [str(plugins / "cloudshare")]
    sys.modules.setdefault("cloudshare", package)
from cloudshare import discovery


def file(fid, name, size=0, directory=False):
    return {"fid": fid, "file_name": name, "size": size, "dir": directory}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_nested_pages_and_caches_complete_results(self):
        search_calls = 0

        async def request(url, params, *, body=None):
            nonlocal search_calls
            if url.endswith("/api/search"):
                search_calls += 1
                self.assertEqual(params["cloud_types"], "quark")
                return {"code": 0, "data": {"merged_by_type": {"quark": [
                    {"url": "https://pan.quark.cn/s/good", "password": "secret"},
                    {"url": "https://pan.quark.cn/s/expired"},
                    {"url": "https://pan.quark.cn.example/s/invalid"},
                ]}}}
            if url.endswith("/token"):
                if body["pwd_id"] == "expired":
                    return {"code": 1}
                self.assertEqual(body["passcode"], "secret")
                return {"code": 0, "data": {"stoken": "token"}}
            pages = {
                ("0", 1): [file("folder", "Series", directory=True)],
                ("folder", 1): [file("video", "S01E02.mkv", 80_000_000)],
                ("folder", 2): [file("sub", "S01E02.srt", 800)],
            }
            parent = params["pdir_fid"]
            return {"code": 0, "data": {"list": pages[(parent, params["_page"])]},
                    "metadata": {"_total": 1 if parent == "0" else 2}}

        client = discovery.CloudShareDiscovery("http://pansou", request)
        with patch.object(discovery, "PAGE_SIZE", 1):
            results = await client.search("Series", {2})
            self.assertEqual(await client.search(" Series ", {2}), results)
        self.assertEqual(search_calls, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].size, 80_000_800)
        self.assertNotIn("secret", repr(results))
        self.assertNotIn("token", repr(results))

    async def test_deadline_keeps_completed_matches_without_caching_partial_results(self):
        client = discovery.CloudShareDiscovery("http://pansou")
        good = discovery.Candidate("good", "Good", "")
        slow = discovery.Candidate("slow", "Slow", "")
        result = discovery.SearchResult(good, (discovery.ShareFile("1", "Movie.mkv", 80_000_000),))
        cancelled = asyncio.Event()
        calls = 0

        async def candidates(_):
            nonlocal calls
            calls += 1
            return [good, slow]

        async def enrich(candidate, _, slots):
            if candidate == good:
                return result, True
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with patch.object(client, "candidates", candidates), patch.object(client, "_enrich", enrich):
            with patch.object(discovery, "ENRICHMENT_SECONDS", 0.05):
                self.assertEqual(await client.search("Movie"), (result,))
                self.assertEqual(await client.search("Movie"), (result,))
        self.assertTrue(cancelled.is_set())
        self.assertEqual(calls, 2)

    async def test_optional_source_merges_and_falls_back(self):
        async def request(*args, **kwargs):
            return {"code": 0, "data": {"merged_by_type": {"quark": [
                {"url": "https://pan.quark.cn/s/shared", "note": "PanSou"},
            ]}}}

        async def telegram(keyword):
            return [discovery.Candidate("shared", keyword, "code"),
                    discovery.Candidate("exclusive", keyword, "")]

        client = discovery.CloudShareDiscovery("http://pansou", request, telegram=telegram)
        results = await client.candidates("Movie")
        self.assertEqual([(item.share_id, item.passcode) for item in results],
                         [("shared", "code"), ("exclusive", "")])

        async def offline(_):
            raise discovery.DiscoveryError("Offline")

        client = discovery.CloudShareDiscovery("http://pansou", request, telegram=offline)
        self.assertEqual([item.share_id for item in await client.candidates("Movie")], ["shared"])

    async def test_telegram_remains_available_when_pansou_fails(self):
        async def offline(*args, **kwargs):
            raise discovery.DiscoveryError("Offline")

        async def telegram(keyword):
            return [discovery.Candidate("direct", keyword, "")]

        client = discovery.CloudShareDiscovery("http://pansou", offline, telegram=telegram)
        self.assertEqual([item.share_id for item in await client.candidates("Movie")], ["direct"])

    def test_inline_result_extracts_direct_links_and_keeps_passcode_private(self):
        from types import SimpleNamespace as Node
        from cloudshare.quarkrobot import inline_candidates

        result = Node(result=Node(title="Movie", send_message=Node(
            message="https://pan.quark.cn/s/direct https://pan.quark.cn.evil/s/bad",
            entities=[Node(url="https://pan.quark.cn/s/direct?pwd=secret")],
            reply_markup=Node(rows=[Node(buttons=[Node(url="https://pan.quark.cn/s/button")])]),
        )))
        matches = inline_candidates([result], "Movie")
        self.assertEqual([(item.share_id, item.passcode) for item in matches],
                         [("direct", "secret"), ("button", "")])
        self.assertNotIn("secret", repr(matches))
