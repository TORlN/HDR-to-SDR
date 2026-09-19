"""Regression checks for factual and roadmap website copy."""

from __future__ import annotations

import unittest
from pathlib import Path


SITE_HTML = Path(__file__).parents[1] / "HDR to SDR Website" / "index.html"


class TestWebsiteProductCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with SITE_HTML.open(encoding="utf-8") as site_file:
            cls.html = site_file.read()

    def test_describes_the_patched_ffmpeg_build(self) -> None:
        self.assertIn("custom patched FFmpeg and ffprobe binaries", self.html)
        self.assertNotIn("standard, unmodified FFmpeg", self.html)

    def test_names_current_hardware_encoder_families(self) -> None:
        self.assertIn("NVENC / AMF / QSV", self.html)
        self.assertNotIn("NVENC / VCE / QSV", self.html)

    def test_resolution_scaling_copy_matches_community_and_pro_tiers(self) -> None:
        self.assertIn("Preset LUTs", self.html)
        self.assertIn("Custom .cube LUTs", self.html)
        self.assertIn("Preset downscaling through 480p", self.html)
        self.assertIn("Preset upscaling through 8K", self.html)
        self.assertIn("Custom resolution", self.html)
        self.assertNotIn("Downsample to 480p only", self.html)
        self.assertNotIn("Audio Track Selection", self.html)
        self.assertNotIn("Saved Conversion Presets", self.html)


if __name__ == "__main__":
    unittest.main()
