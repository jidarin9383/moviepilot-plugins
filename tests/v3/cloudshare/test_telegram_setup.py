# SPDX-License-Identifier: GPL-3.0-only
"""Validate international phone input without sending a login request."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

path = Path(__file__).resolve().parents[3] / "deploy/telegram_setup.py"
spec = importlib.util.spec_from_file_location("telegram_setup", path)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PhoneInputTests(unittest.TestCase):
    def test_accepts_formatted_and_fullwidth_international_numbers(self):
        for value in ["+1 (202) 555-0123", "００１２０２５５５０１２３", "+1\u00a0202 555 0123"]:
            self.assertEqual(setup.normalize_phone(value), "+12025550123")

    def test_does_not_guess_country_or_accept_other_characters(self):
        for value in ["2025550123", "+12025550123abc", "+123", "+" + "1" * 16, ""]:
            with self.assertRaises(ValueError):
                setup.normalize_phone(value)


class LoginRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_finishes_one_datacenter_migration(self):
        class Migration(Exception):
            pass

        client = type("Client", (), {})()
        client.send_code_request = AsyncMock(side_effect=[Migration(), "sent"])
        self.assertEqual(await setup.request_login_code(client, "+12025550123", (Migration,)), "sent")
        self.assertEqual(client.send_code_request.await_count, 2)

    async def test_stops_after_second_migration_or_other_error(self):
        class Migration(Exception):
            pass

        for errors, expected in [([Migration(), Migration()], 2), ([RuntimeError()], 1)]:
            client = type("Client", (), {})()
            client.send_code_request = AsyncMock(side_effect=errors)
            with self.assertRaises((Migration, RuntimeError)):
                await setup.request_login_code(client, "+12025550123", (Migration,))
            self.assertEqual(client.send_code_request.await_count, expected)
