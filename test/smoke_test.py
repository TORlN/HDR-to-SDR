"""End-to-end smoke tests against real HDR videos.

Unlike the unit/characterization suites (which mock ffmpeg), these exercise the
*actual* ffmpeg pipeline: probing, frame extraction, the tonemap filter chains,
and real HDR->SDR encodes. They prove the production command strings really work
against genuine HDR input (HEVC, bt2020/smpte2084 PQ).

Two tiers of sample:

1. ``test/smoke_test_videos/`` -- small (each well under 500KB), synthetic clips
   built with plain ffmpeg (+ dovi_tool for the Dolby Vision one) and committed
   to git specifically so CI exercises the real pipeline instead of only the
   mocked unit tests. Covers the attribute matrix the app actually branches on:
   plain SDR, HDR10 at 10-bit and 12-bit, Dolby Vision profile 8.1, and an MKV
   carrying TrueHD audio + an ASS subtitle track (container-aware fallback).

2. Large real-world captures in the project root (gitignored, so present only
   on a dev machine -- these tests skip, not fail, on CI):
     - ``drag multi bo6.mp4``      HEVC PQ 2560x1440, AAC, no subs
     - ``video.mkv``               HEVC PQ 3840x1632, TrueHD audio, PGS + SubRip subs
     - ``Shogun S01e01 Anjin.mkv`` HEVC PQ 1920x1080, AAC, ASS subs
   These stay because they cover real-world muxing quirks (PGS bitmap subs,
   multi-GB files) that aren't practical to synthesize small.

Regenerating ``smoke_test_videos/*`` (all built with plain lavfi sources; the
critical gotcha is that ``testsrc2`` produces ordinary Rec.709-range pixel
values with no color tags -- tagging the *container* as bt2020/smpte2084
without actually running the pixels through a PQ OETF makes every tonemap
wash out to near-white, since decoders read normal video-range values as if
they were real PQ light levels. ``zscale`` needs the input tagged explicitly
before it will do that conversion; a single zscale call with in-place
``primariesin``/``transferin``/``matrixin`` options fails with "no path
between colorspaces" on this ffmpeg build, so tag via ``setparams`` first):

    ffmpeg -y -f lavfi -i "testsrc2=size=WxH:rate=24:duration=Ns" [-f lavfi -i "<audio>"] \\
      -vf "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,\\
           zscale=t=linear:npl=100,zscale=p=bt2020:t=smpte2084:m=bt2020nc,format=yuv420p10le" \\
      -c:v libx265 -crf 24 -preset medium \\
      -x265-params "hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:\\
                    master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400" \\
      -c:a aac -tag:v hvc1 out.mp4

  - ``sdr_h264_8bit.mp4``: same testsrc2 source, no zscale/HDR tagging at all,
    ``-c:v libx264``.
  - ``hdr10_10bit.mp4``: the HDR10 recipe above at 960x540, AAC audio.
  - ``hdr10_12bit.mp4``: HDR10 recipe with ``format=yuv420p12le`` (moved in from
    a prior manual build rather than regenerated here -- kept as-is).
  - ``hdr10_10bit_truehd_ass.mkv``: HDR10 recipe, ``-i subs.ass -c:s ass``,
    audio via ``-c:a truehd -strict -2 -ar 48000 -ac 6`` (the native truehd
    *encoder* is marked experimental and refuses to open without ``-strict -2``;
    this has no bearing on decoding/copying truehd, which the app itself does).
  - ``dovi_p8.mp4``: the HDR10 10-bit recipe above encoded to a raw ``.hevc``
    elementary stream (``bframes=0`` -- B-frames break PTS assignment when a
    raw stream is muxed straight into MP4/MKV via stream copy), then:

        echo '{"cm_version":"V29","profile":"8.1","length":<nframes>,"level6":{"max_display_mastering_luminance":1000,"min_display_mastering_luminance":1,"max_content_light_level":1000,"max_frame_average_light_level":400}}' > rpu.json
        dovi_tool generate -j rpu.json -o rpu.bin
        dovi_tool inject-rpu -i base.hevc --rpu-in rpu.bin -o dovi.hevc
        ffmpeg -y -r 24 -i dovi.hevc -f lavfi -i "anoisesrc=d=Ns:c=pink" \\
          -c:v copy -bsf:v dovi_rpu -tag:v hvc1 -strict unofficial -c:a eac3 -ac 6 -t Ns \\
          dovi_p8.mp4

    ``-bsf:v dovi_rpu`` makes ffmpeg parse the injected RPU and emit a DOVI
    configuration record; ``-strict unofficial`` is required to actually
    persist it into the MP4's dvcC box (otherwise it's computed but silently
    dropped). ``-t Ns`` (not ``-shortest``) avoids truncating the audio to
    zero, since the raw ``.hevc`` input reports an unknown duration.
    dovi_tool: https://github.com/quietvoid/dovi_tool (must be on PATH).

The full-encode tests against the large gitignored root samples run on 2s
stream-copied clips so the suite stays quick.
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
    get_maxcll,
    extract_frame,
    extract_frame_with_conversion,
    FFMPEG_EXECUTABLE,
    FFPROBE_EXECUTABLE,
)
from src.conversion import ConversionManager

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SMOKE_DIR = os.path.join(os.path.dirname(__file__), 'smoke_test_videos')

SAMPLE_VIDEO = os.path.join(PROJECT_ROOT, 'drag multi bo6.mp4')
MKV_VIDEO = os.path.join(PROJECT_ROOT, 'video.mkv')
SHOGUN_VIDEO = os.path.join(PROJECT_ROOT, 'Shōgun S01e01 Anjin.mkv')

SDR_VIDEO = os.path.join(SMOKE_DIR, 'sdr_h264_8bit.mp4')
HDR10_10BIT_VIDEO = os.path.join(SMOKE_DIR, 'hdr10_10bit.mp4')
HDR10_12BIT_VIDEO = os.path.join(SMOKE_DIR, 'hdr10_12bit.mp4')
TRUEHD_ASS_MKV = os.path.join(SMOKE_DIR, 'hdr10_10bit_truehd_ass.mkv')
DOVI_VIDEO = os.path.join(SMOKE_DIR, 'dovi_p8.mp4')

_FFMPEG_OK = bool(FFMPEG_EXECUTABLE) and os.path.exists(FFMPEG_EXECUTABLE)
_BO6_OK = _FFMPEG_OK and os.path.exists(SAMPLE_VIDEO)
_MKV_OK = _FFMPEG_OK and os.path.exists(MKV_VIDEO)
_SHOGUN_OK = _FFMPEG_OK and os.path.exists(SHOGUN_VIDEO)

_SDR_OK = _FFMPEG_OK and os.path.exists(SDR_VIDEO)
_HDR10_10BIT_OK = _FFMPEG_OK and os.path.exists(HDR10_10BIT_VIDEO)
_HDR10_12BIT_OK = _FFMPEG_OK and os.path.exists(HDR10_12BIT_VIDEO)
_TRUEHD_ASS_OK = _FFMPEG_OK and os.path.exists(TRUEHD_ASS_MKV)
_DOVI_OK = _FFMPEG_OK and os.path.exists(DOVI_VIDEO)


def _x265_supports_12bit():
    """Some distro ffmpeg builds ship libx265 without high-bit-depth support;
    probe rather than assume, so the 12-bit encode test skips cleanly instead
    of failing on such a build."""
    if not _FFMPEG_OK:
        return False
    try:
        out = subprocess.check_output(
            [FFMPEG_EXECUTABLE, '-hide_banner', '-h', 'encoder=libx265'],
            stderr=subprocess.STDOUT,
        ).decode('utf-8', 'replace')
    except subprocess.SubprocessError:
        return False
    return 'yuv420p12le' in out


_X265_12BIT_OK = _HDR10_12BIT_OK and _x265_supports_12bit()

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


@unittest.skipUnless(_SDR_OK, "sample 'smoke_test_videos/sdr_h264_8bit.mp4' / ffmpeg not available")
class TestRealSdrBaseline(unittest.TestCase):
    """Plain SDR H.264 input -- confirms non-HDR sources are correctly
    identified (no PQ/DoVi flags) and still convert cleanly end to end."""

    def test_properties_detect_non_hdr(self):
        props = get_video_properties(SDR_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'h264')
        self.assertNotEqual(props['color_transfer'], 'smpte2084')
        self.assertFalse(props['is_dolby_vision'])
        self.assertEqual(props['bit_depth'], 8)

    def test_conversion_of_sdr_source_completes(self):
        props = get_video_properties(SDR_VIDEO)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_sdr_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mp4')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                SDR_VIDEO, out_path, gamma=1.0, properties=props,
                use_gpu=False, tonemapper='reinhard',
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"ffmpeg failed:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
            self.assertGreater(os.path.getsize(out_path), 0)


@unittest.skipUnless(_HDR10_10BIT_OK, "sample 'smoke_test_videos/hdr10_10bit.mp4' / ffmpeg not available")
class TestRealHdr10TenBit(unittest.TestCase):
    """Genuine HDR10 (bt2020/smpte2084, properly PQ-mastered pixel data) at
    the free-tier default 10-bit depth, run through the real tonemap chain."""

    def test_properties_detect_hdr10_not_dolby_vision(self):
        props = get_video_properties(HDR10_10BIT_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['color_transfer'], 'smpte2084')
        self.assertEqual(props['color_primaries'], 'bt2020')
        self.assertEqual(props['bit_depth'], 10)
        self.assertFalse(props['is_dolby_vision'])
        self.assertIsNone(props['dovi_profile'])

    def test_conversion_outputs_valid_sdr(self):
        props = get_video_properties(HDR10_10BIT_VIDEO)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_10bit_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mp4')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                HDR10_10BIT_VIDEO, out_path, gamma=1.0, properties=props,
                use_gpu=False, tonemapper='mobius',
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"ffmpeg failed:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
            transfer, _, _, pix_fmt = _probe_video(out_path)
            self.assertNotEqual(transfer, 'smpte2084')
            self.assertEqual(pix_fmt, 'yuv420p')


@unittest.skipUnless(_HDR10_12BIT_OK, "sample 'smoke_test_videos/hdr10_12bit.mp4' / ffmpeg not available")
class TestRealHdr10TwelveBitProbing(unittest.TestCase):
    """Genuine 12-bit HDR10 source -- properties detection only (the encode
    path is covered separately, gated on the local ffmpeg actually supporting
    a 12-bit libx265 output, which not every distro build does)."""

    def test_properties_detect_twelve_bit_hdr10(self):
        props = get_video_properties(HDR10_12BIT_VIDEO)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['color_transfer'], 'smpte2084')
        self.assertEqual(props['color_primaries'], 'bt2020')
        self.assertEqual(props['bit_depth'], 12)


@unittest.skipUnless(_X265_12BIT_OK, "libx265 12-bit support / sample not available")
class TestRealHdr10TwelveBitConversion(unittest.TestCase):
    """Pro-tier 12-bit output: the CPU-only libx265 Main12 encode path."""

    def test_twelve_bit_conversion_outputs_valid_sdr_12bit(self):
        props = get_video_properties(HDR10_12BIT_VIDEO)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_12bit_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mkv')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                HDR10_12BIT_VIDEO, out_path, gamma=1.0, properties=props,
                use_gpu=False, tonemapper='reinhard', bit_depth=12,
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"ffmpeg failed:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
            transfer, _, _, pix_fmt = _probe_video(out_path)
            self.assertNotEqual(transfer, 'smpte2084')
            self.assertEqual(pix_fmt, 'yuv420p12le')


@unittest.skipUnless(_TRUEHD_ASS_OK, "sample 'smoke_test_videos/hdr10_10bit_truehd_ass.mkv' / ffmpeg not available")
class TestRealTrueHdAssMkv(unittest.TestCase):
    """Small MKV carrying TrueHD audio and an ASS subtitle track -- covers the
    same container-aware transcode/drop logic as the (gitignored, large)
    real-world MKV/Shogun samples, but small enough to run in CI."""

    def test_properties_detect_truehd_and_ass_subtitle(self):
        props = get_video_properties(TRUEHD_ASS_MKV)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['audio_codec'], 'truehd')
        self.assertTrue(
            any(s.get('codec_name') == 'ass' for s in props['subtitle_streams'])
        )

    def test_mkv_to_mkv_preserves_truehd_and_ass(self):
        props = get_video_properties(TRUEHD_ASS_MKV)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_truehd_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mkv')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                TRUEHD_ASS_MKV, out_path, gamma=1.0, properties=props,
                use_gpu=False, tonemapper='mobius',
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"mkv->mkv conversion failed:\n"
                    f"{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
            transfer, _, _, pix_fmt = _probe_video(out_path)
            self.assertNotEqual(transfer, 'smpte2084')
            self.assertEqual(pix_fmt, 'yuv420p')
            self.assertGreaterEqual(_count_streams(out_path, 'audio'), 1)
            self.assertGreaterEqual(_count_streams(out_path, 'subtitle'), 1)

    def test_mp4_output_transcodes_audio_and_subs(self):
        """Container-aware fallback: MP4 can't carry TrueHD or ASS, so the
        pipeline must transcode audio to AAC and subtitles to mov_text."""
        props = get_video_properties(TRUEHD_ASS_MKV)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_truehd_mp4_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mp4')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                TRUEHD_ASS_MKV, out_path, gamma=1.0, properties=props,
                use_gpu=False, tonemapper='mobius',
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"mkv->mp4 fallback conversion failed:\n"
                    f"{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
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
            self.assertTrue(subs and subs[0]['codec_name'] == 'mov_text')


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

    def test_get_maxcll_returns_float_or_none(self):
        # Returns a float when MaxCLL metadata is present, None when absent.
        # This sample carries no MaxCLL side data so None is expected here.
        value = get_maxcll(SAMPLE_VIDEO)
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


@unittest.skipUnless(_DOVI_OK, "sample 'smoke_test_videos/dovi_p8.mp4' / ffmpeg not available")
class TestRealDolbyVisionDetection(unittest.TestCase):
    """Real Dolby Vision profile 8.1 metadata, read by the actual ffprobe/utils
    pipeline (not mocked JSON) -- proves the 'DOVI configuration record' side
    data shape assumed by _parse_dovi() really is what ffprobe emits."""

    def test_properties_flag_dolby_vision_and_profile(self):
        props = get_video_properties(DOVI_VIDEO)
        self.assertIsNotNone(props)
        self.assertTrue(props['is_dolby_vision'])
        self.assertEqual(props['dovi_profile'], 8)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['color_transfer'], 'smpte2084')
        self.assertEqual(props['color_primaries'], 'bt2020')
        self.assertEqual(props['bit_depth'], 10)
        self.assertEqual(props['audio_codec'], 'eac3')


@unittest.skipUnless(_DOVI_OK, "sample 'smoke_test_videos/dovi_p8.mp4' / ffmpeg not available")
class TestRealDolbyVisionTierConversion(unittest.TestCase):
    """The Pro-passthrough / Free-stereo-downmix audio split, run through the
    real ffmpeg pipeline against a genuine Dolby Vision file rather than a
    mocked command-string assertion."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix='hdr_smoke_dovi_')
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def _convert(self, licensed, out_name='out.mkv'):
        props = get_video_properties(DOVI_VIDEO)
        out_path = os.path.join(self._tmpdir, out_name)
        manager = ConversionManager()
        cmd = manager.construct_ffmpeg_command(
            DOVI_VIDEO, out_path, gamma=1.0, properties=props,
            use_gpu=False, tonemapper='reinhard', licensed=licensed,
        )
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(
            result.returncode, 0,
            msg=f"DoVi conversion (licensed={licensed}) failed:\n"
                f"{result.stderr.decode('utf-8', 'replace')[-2000:]}",
        )
        return out_path

    @staticmethod
    def _audio_stream(path):
        out = subprocess.check_output(
            [FFPROBE_EXECUTABLE, '-v', 'error', '-select_streams', 'a',
             '-show_entries', 'stream=codec_name,channels', '-of', 'json', path],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(out)['streams'][0]

    def test_pro_conversion_preserves_full_multichannel_audio(self):
        out_path = self._convert(licensed=True)
        audio = self._audio_stream(out_path)
        self.assertEqual(audio['codec_name'], 'eac3')
        self.assertEqual(audio['channels'], 6)

    def test_free_conversion_forces_two_channel_aac(self):
        out_path = self._convert(licensed=False)
        audio = self._audio_stream(out_path)
        self.assertEqual(audio['codec_name'], 'aac')
        self.assertEqual(audio['channels'], 2)

    def test_both_tiers_produce_sdr_video(self):
        """The tier split is audio-only -- both outputs must still be
        properly tonemapped to SDR."""
        pro_path = self._convert(licensed=True, out_name='pro.mkv')
        free_path = self._convert(licensed=False, out_name='free.mkv')
        for path in (pro_path, free_path):
            transfer, _, _, pix_fmt = _probe_video(path)
            self.assertNotEqual(transfer, 'smpte2084')
            self.assertEqual(pix_fmt, 'yuv420p')


if __name__ == '__main__':
    unittest.main()
