# SPDX-License-Identifier: GPL-3.0-only
"""验证夸克分享的下载范围。"""

import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).resolve().parents[3] / "plugins.v3/cloudshare/selection.py"
spec = importlib.util.spec_from_file_location("cloudshare_selection", path)
selection = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selection
spec.loader.exec_module(selection)
ShareFile = selection.ShareFile


class SelectionTests(unittest.TestCase):
    def test_only_selected_episodes_and_matching_subtitles(self):
        files = [
            ShareFile("1", "Series/S01E01.mkv", 80_000_000),
            ShareFile("2", "Series/S01E02.mkv", 80_000_000),
            ShareFile("3", "Series/S01E02.zh-Hans.ass", 800),
            ShareFile("4", "Series/S01E02E03.mkv", 100_000_000),
            ShareFile("5", "Series/S01E02-E03.mkv", 100_000_000),
            ShareFile("6", "Other/S01E02.srt", 800),
        ]
        self.assertEqual([f.file_id for f in selection.select_files(files, {2})], ["2", "3"])
        self.assertEqual(selection.select_files(files, set()), [])

    def test_rejects_unusable_or_unsafe_files(self):
        files = [
            ShareFile("good", "Film/Movie.MKV", 50_000_000),
            ShareFile("sample", "Film/Sample/Movie.mkv", 80_000_000),
            ShareFile("small", "Film/Trailer.mp4", 49_999_999),
            ShareFile("archive", "Film/Movie.zip", 80_000_000),
            ShareFile("escape", "Film/../Movie.mkv", 80_000_000),
            ShareFile("absolute", "/Film/Movie.mkv", 80_000_000),
            ShareFile("exe", "Film/Movie.exe", 80_000_000),
        ]
        self.assertEqual([f.file_id for f in selection.select_files(files)], ["good"])
