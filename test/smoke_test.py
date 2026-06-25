"""End-to-end smoke tests against real HDR videos.

Unlike the unit/characterization suites (which mock ffmpeg), these exercise the
*actual* ffmpeg pipeline: probing, frame extraction, the tonemap filter chains,
and real HDR->SDR encodes. They prove the production command strings really work
against genuine HDR input (HEVC, bt2020/smpte2084 PQ), including the awkward
real-world cases: MKV containers carrying TrueHD audio and many subtitle streams.

Sample files (all gitignored, so absent on CI -> these tests skip, not fail):
  - ``drag multi bo6.mp4``      HEVC PQ 2560x1440, AAC, no subs
  - ``video.mkv``               HEVC PQ 3840x1632, TrueHD audio, PGS + SubRip subs
  - ``Shogun S01e01 Anjin.mkv`` HEVC PQ 1920x1080, AAC, ASS subs

The full-encode tests run on 2s stream-copied clips so the suite stays quick.
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from PIL import Image

from src.utils import (
    get_video_properties,
    get_maxfall,
    extract_frame,
    extract_frame_with_conversion,
    FFMPEG_EXECUTABLE,
    FFPROBE_EXECUTABLE,
)
from src.conversion import ConversionManager

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SAMPLE_VIDEO = os.path.join(PROJECT_ROOT, 'drag multi bo6.mp4')
MKV_VIDEO = os.path.join(PROJECT_ROOT, 'video.mkv')
SHOGUN_VIDEO = os.path.join(PROJECT_ROOT, 'Shōgun S01e01 Anjin.mkv')

_FFMPEG_OK = bool(FFMPEG_EXECUTABLE) and os.path.exists(FFMPEG_EXECUTABLE)
_BO6_OK = _FFMPEG_OK and os.path.exists(SAMPLE_VIDEO)
_MKV_OK = _FFMPEG_OK and os.path.exists(MKV_VIDEO)
_SHOGUN_OK = _FFMPEG_OK and os.path.exists(SHOGUN_VIDEO)

# Trimmed clips, built once in setUpModule (None until then / when unavailable).
_CLIP_DIR = None
_BO6_CLIP = None
_MKV_CLIP = None


def _build_clip(src, name, seconds=2):
    """Stream-copy the first ``seconds`` of ``src`` (keeps HDR side data; fast)."""
    dst = os.path.join(_CLIP_DIR, name)
    subprocess.run(
        [FFMPEG_EXECUTABLE, '-y', '-ss', '0', '-t', str(seconds), '-i', src,
         '-map', '0', '-c', 'copy', dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return dst


def setUpModule():
    global _CLIP_DIR, _BO6_CLIP, _MKV_CLIP
    if not _FFMPEG_OK:
        return
    _CLIP_DIR = tempfile.mkdtemp(prefix='hdr_smoke_')
    if _BO6_OK:
        _BO6_CLIP = _build_clip(SAMPLE_VIDEO, 'bo6.mp4')
    if _MKV_OK:
        _MKV_CLIP = _build_clip(MKV_VIDEO, 'clip.mkv')


def tearDownModule():
    if _CLIP_DIR and os.path.isdir(_CLIP_DIR):
        shutil.rmtree(_CLIP_DIR, ignore_errors=True)


def _probe_video(path):
    """Return (color_transfer, width, height, pix_fmt) for the first video stream."""
    out = subprocess.check_output(
        [FFPROBE_EXECUTABLE, '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=color_transfer,width,height,pix_fmt',
         '-of', 'json', path],
        stderr=subprocess.DEVNULL,
    )
    s = json.loads(out)['streams'][0]
    return s.get('color_transfer'), s.get('width'), s.get('height'), s.get('pix_fmt')


def _count_streams(path, codec_type):
    """Count streams of a given codec_type ('audio' | 'subtitle' | 'video')."""
    out = subprocess.check_output(
        [FFPROBE_EXECUTABLE, '-v', 'error', '-show_entries',
         'stream=codec_type', '-of', 'json', path],
        stderr=subprocess.DEVNULL,
    )
    return sum(1 for s in json.loads(out)['streams']
               if s.get('codec_type') == codec_type)


@unittest.skipUnless(_BO6_OK, "sample 'drag multi bo6.mp4' / ffmpeg not available")
class TestRealVideoProbing(unittest.TestCase):

    def test_get_video_properties_reads_real_hdr_metadata(self):
        props = get_video_properties(SAMPLE_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['width'], 2560)
        self.assertEqual(props['height'], 1440)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertGreater(props['duration'], 0)
        self.assertGreater(props['frame_rate'], 0)

    def test_get_maxfall_returns_float_or_none(self):
        # Returns a float when MAXFALL metadata is present, None when absent.
        # This sample carries no MAXFALL side data so None is expected here.
        value = get_maxfall(SAMPLE_VIDEO)
        self.assertTrue(value is None or isinstance(value, float))


@unittest.skipUnless(_BO6_OK, "sample 'drag multi bo6.mp4' / ffmpeg not available")
class TestRealFrameExtraction(unittest.TestCase):

    def test_extract_frame_returns_real_image(self):
        img = extract_frame(SAMPLE_VIDEO, time_position=1.0)
        self.assertIsInstance(img, Image.Image)
        self.assertGreater(img.width, 0)
        self.assertGreater(img.height, 0)

    def test_dynamic_filter_extraction_produces_image(self):
        img = extract_frame_with_conversion(
            SAMPLE_VIDEO, gamma=1.2,
            tonemapper='mobius', time_position=1.0,
        )
        self.assertIsInstance(img, Image.Image)
        self.assertEqual((img.width, img.height), (2560, 1440))


@unittest.skipUnless(_BO6_OK, "sample 'drag multi bo6.mp4' / ffmpeg not available")
class TestRealConversion(unittest.TestCase):
    """The full HDR->SDR encode using the production command string."""

    def test_dynamic_conversion_outputs_valid_sdr(self):
        self.assertIsNotNone(_BO6_CLIP, "trimmed clip was not created")
        props = get_video_properties(_BO6_CLIP)
        self.assertIsNotNone(props)

        out_path = os.path.join(_CLIP_DIR, 'out_dynamic.mp4')
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            _BO6_CLIP, out_path, gamma=1.0, properties=props,
            use_gpu=False, tonemapper='mobius',
        )

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(
            result.returncode, 0,
            msg=f"ffmpeg failed:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}",
        )
        self.assertTrue(os.path.exists(out_path))
        self.assertGreater(os.path.getsize(out_path), 0)

        transfer, width, height, pix_fmt = _probe_video(out_path)
        self.assertNotEqual(transfer, 'smpte2084')  # must be SDR, not PQ
        self.assertEqual((width, height), (2560, 1440))
        self.assertEqual(pix_fmt, 'yuv420p')


@unittest.skipUnless(_MKV_OK, "sample 'video.mkv' / ffmpeg not available")
class TestRealMkvWithTrueHDAndSubtitles(unittest.TestCase):
    """MKV carrying TrueHD audio and PGS + SubRip subtitle streams."""

    def test_properties_detect_truehd_and_subtitle_streams(self):
        props = get_video_properties(MKV_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['audio_codec'], 'truehd')
        # The file carries many subtitle tracks; detection must capture them.
        self.assertGreater(len(props['subtitle_streams']), 1)

    def test_mkv_to_mkv_preserves_truehd_and_subtitles(self):
        self.assertIsNotNone(_MKV_CLIP, "trimmed mkv clip was not created")
        props = get_video_properties(_MKV_CLIP)
        self.assertEqual(props['audio_codec'], 'truehd')
        self.assertGreater(len(props['subtitle_streams']), 1)

        out_path = os.path.join(_CLIP_DIR, 'out.mkv')
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            _MKV_CLIP, out_path, gamma=1.0, properties=props,
            use_gpu=False, tonemapper='mobius',
        )

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(
            result.returncode, 0,
            msg=f"mkv->mkv conversion failed:\n"
                f"{result.stderr.decode('utf-8', 'replace')[-2000:]}",
        )
        # Video is now SDR, and the copy-through audio + subtitle streams survive.
        transfer, _, _, pix_fmt = _probe_video(out_path)
        self.assertNotEqual(transfer, 'smpte2084')
        self.assertEqual(pix_fmt, 'yuv420p')
        self.assertGreaterEqual(_count_streams(out_path, 'audio'), 1)
        self.assertGreaterEqual(_count_streams(out_path, 'subtitle'), 1)

    def test_mp4_output_transcodes_audio_and_drops_image_subs(self):
        """Container-aware fallback: converting this TrueHD + PGS/SubRip MKV to an
        MP4 output must now succeed by transcoding TrueHD audio to AAC, converting
        text subtitles to mov_text, and dropping PGS image subtitles that no MP4
        codec can represent.
        """
        self.assertIsNotNone(_MKV_CLIP, "trimmed mkv clip was not created")
        props = get_video_properties(_MKV_CLIP)

        out_path = os.path.join(_CLIP_DIR, 'out_from_mkv.mp4')
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            _MKV_CLIP, out_path, gamma=1.0, properties=props,
            use_gpu=False, tonemapper='mobius',
        )

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(
            result.returncode, 0,
            msg=f"mkv->mp4 fallback conversion failed:\n"
                f"{result.stderr.decode('utf-8', 'replace')[-2000:]}",
        )
        self.assertTrue(os.path.exists(out_path))

        # SDR video, AAC audio, and only MP4-legal (text/mov_text) subtitles.
        out_streams = json.loads(subprocess.check_output(
            [FFPROBE_EXECUTABLE, '-v', 'error', '-show_entries',
             'stream=codec_type,codec_name', '-of', 'json', out_path],
            stderr=subprocess.DEVNULL,
        ))['streams']
        transfer, _, _, pix_fmt = _probe_video(out_path)
        self.assertNotEqual(transfer, 'smpte2084')
        self.assertEqual(pix_fmt, 'yuv420p')

        audio = [s for s in out_streams if s['codec_type'] == 'audio']
        self.assertTrue(audio and audio[0]['codec_name'] == 'aac')
        subs = [s for s in out_streams if s['codec_type'] == 'subtitle']
        # No image subtitles survived into the MP4.
        self.assertFalse(any(s['codec_name'] == 'hdmv_pgs_subtitle' for s in subs))


@unittest.skipUnless(_SHOGUN_OK, "sample 'Shōgun S01e01 Anjin.mkv' / ffmpeg not available")
class TestRealShogunSubtitles(unittest.TestCase):
    """MKV with ASS (text) subtitle tracks and AAC audio."""

    def test_properties_detect_ass_subtitle_streams(self):
        props = get_video_properties(SHOGUN_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['audio_codec'], 'aac')
        self.assertGreater(len(props['subtitle_streams']), 1)
        self.assertTrue(
            any(s.get('codec_name') == 'ass' for s in props['subtitle_streams'])
        )


if __name__ == '__main__':
    unittest.main()
