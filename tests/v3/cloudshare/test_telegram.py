# SPDX-License-Identifier: GPL-3.0-only
"""Check Telegram result routing without a Telegram account."""

from types import SimpleNamespace as Node, ModuleType
import unittest
from unittest.mock import patch

from test_discovery import discovery
from cloudshare.aliyun import AliyunDiscovery
from cloudshare.quarkrobot import inline_candidates


def result(text, *, entities=(), buttons=()):
    return Node(result=Node(title="Test movie", send_message=Node(
        message=text, entities=list(entities),
        reply_markup=Node(rows=[Node(buttons=list(buttons))]))))


class TelegramTests(unittest.TestCase):
    def test_aliyun_links_are_routed_separately_and_keep_access_codes(self):
        results = [result(
            "https://pan.quark.cn/s/quark https://www.alipan.com/s/ali\n提取码：a1b2",
            entities=[Node(url="https://www.aliyundrive.com/s/ali")],
            buttons=[Node(url="https://www.alipan.com/s/button?pwd=c3d4")])]
        matches = inline_candidates(results, "Movie", provider="aliyun")
        self.assertEqual([(x.share_id, x.passcode) for x in matches],
                         [("ali", "a1b2"), ("button", "c3d4")])
        self.assertEqual(matches[0].url, "https://www.alipan.com/s/ali")
        self.assertNotIn("a1b2", repr(matches))
        self.assertEqual([x.share_id for x in inline_candidates(results, "Movie")], ["quark"])

    def test_rejects_lookalike_domains_and_does_not_guess_ambiguous_codes(self):
        rows = [result("https://www.alipan.com.evil/s/bad "
                       "https://evil.test/https://www.alipan.com/s/nested "
                       "https://www.alipan.com/s/good\n提取码: ab12\n提取码: cd34")]
        self.assertEqual([(x.share_id, x.passcode) for x in
                          inline_candidates(rows, "Movie", provider="aliyun")], [("good", "")])


class AliyunTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_telegram_provider_does_not_read_login_or_query(self):
        from cloudshare.quarkrobot import TelegramSource

        source = TelegramSource("unused", providers={"aliyun"})
        with patch.object(source, "_config", side_effect=AssertionError("Unexpected login read")):
            self.assertEqual(await source("Movie"), [])

    async def test_session_queries_only_the_configured_provider_bot(self):
        from cloudshare.quarkrobot import TelegramSource

        calls = []

        class Client:
            def __init__(self, *args, **kwargs):
                self.options = kwargs

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def inline_query(self, bot, keyword, *, entity):
                calls.append((bot, keyword, entity))
                return [result("https://www.alipan.com/s/ali https://pan.quark.cn/s/quark")]

        telethon = ModuleType("telethon")
        telethon.TelegramClient = Client
        sessions = ModuleType("telethon.sessions")
        sessions.StringSession = lambda value: value
        source = TelegramSource("unused")
        config = {"api_id": 123, "api_hash": "fake", "session": "fake",
                  "bots": {"quark": "QuarkRobot", "aliyun": "AliYunPanBot"}}
        with patch.dict("sys.modules", {"telethon": telethon, "telethon.sessions": sessions}), \
                patch.object(source, "_config", return_value=config):
            ali = await source.for_provider("aliyun")("Movie")
            quark = await source("Movie")
        self.assertEqual([x.share_id for x in ali], ["ali"])
        self.assertEqual([x.share_id for x in quark], ["quark"])
        self.assertEqual(calls, [("AliYunPanBot", "Movie", "me"), ("QuarkRobot", "Movie", "me")])

    async def test_telegram_aliyun_results_survive_pansou_failure(self):
        from cloudshare.aliyun import AliyunCandidate

        async def offline(*args, **kwargs):
            raise discovery.DiscoveryError("Offline")

        async def telegram(keyword):
            return [AliyunCandidate("direct", keyword, "private")]

        client = AliyunDiscovery("http://pansou", offline, telegram=telegram)
        self.assertEqual([x.share_id for x in await client.candidates("Movie")], ["direct"])

    async def test_merges_aliyun_codes_and_falls_back_when_telegram_fails(self):
        from cloudshare.aliyun import AliyunCandidate

        async def pansou(*args, **kwargs):
            return {"code": 0, "data": {"merged_by_type": {"aliyun": [
                {"url": "https://www.alipan.com/s/shared", "note": "PanSou"}]}}}

        async def telegram(keyword):
            return [AliyunCandidate("shared", keyword, "private"),
                    AliyunCandidate("extra", keyword, "")]

        client = AliyunDiscovery("http://pansou", pansou, telegram=telegram)
        self.assertEqual([(x.share_id, x.passcode) for x in await client.candidates("Movie")],
                         [("shared", "private"), ("extra", "")])

        async def offline(keyword):
            raise discovery.DiscoveryError("Offline")

        client = AliyunDiscovery("http://pansou", pansou, telegram=offline)
        self.assertEqual([x.share_id for x in await client.candidates("Movie")], ["shared"])
