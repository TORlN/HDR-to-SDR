"""End-to-end smoke tests against real HDR videos.

Unlike the unit/characterization suites (which mock ffmpeg), these exercise the
*actual* ffmpeg pipeline: probing, frame extraction, the tonemap filter chains,
and real HDR->SDR encodes. They prove the production command strings really work
against genuine HDR input (HEVC, bt2020/smpte2084 PQ).

All samples live in ``test/smoke_test_videos/`` -- small (each well under
500KB), committed to git specifically so CI exercises the real pipeline
instead of only the mocked unit tests, and never depend on any file outside
the repo. Covers the attribute matrix the app actually branches on: plain
SDR, HDR10 at 10-bit and 12-bit, Dolby Vision profile 8.1, an MKV carrying
TrueHD audio + an ASS subtitle track, and an MKV adding a real PGS (bitmap)
subtitle track on top of that (container-aware fallback, including the
drop-image-subtitle path that no synthesized bitmap sub can otherwise cover
-- ffmpeg has no PGS encoder, so ``hdr10_10bit_truehd_pgs.mkv`` was built by
extracting a few real PGS packets, stream-copied from a personal capture,
into an otherwise fully synthetic clip; see its recipe below).

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

  - ``hdr10_10bit_truehd_pgs.mkv``: the ``hdr10_10bit_truehd_ass.mkv`` recipe
    (same HDR10 video, same truehd audio, ``-i subs.ass -c:s:0 ass``), plus a
    fourth input mapped in as a second subtitle stream (``-c:s:1 copy``): a
    ~2s window stream-copied (``-ss <offset> -t <n>``) from a real capture's
    PGS track, picked at a timestamp known to contain actual subtitle packets
    (verify with ``ffprobe -select_streams s -show_entries packet=stream_index,pts_time``
    -- a naive ``-t N`` from the start can easily land on a stretch with none).
    ffmpeg has no PGS *encoder* (text-to-bitmap subtitle rasterization isn't
    supported), so this handful of real packets is the only way to exercise
    the app's drop-image-subtitle-on-MP4-export path against genuine PGS
    data; everything else in the file is synthetic.

All samples are small enough that the full-encode tests run directly against
them (no separate trimming step needed).
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
    vulkan_libplacebo_available,
    FFMPEG_EXECUTABLE,
    FFPROBE_EXECUTABLE,
    FFMPEG_CONVERT_FILTER,
    FFMPEG_FILTER_LEGACY_NO_LUT,
    get_lut_filter_path,
)
from src.conversion import ConversionManager

SMOKE_DIR = os.path.join(os.path.dirname(__file__), 'smoke_test_videos')

SDR_VIDEO = os.path.join(SMOKE_DIR, 'sdr_h264_8bit.mp4')
HDR10_10BIT_VIDEO = os.path.join(SMOKE_DIR, 'hdr10_10bit.mp4')
HDR10_12BIT_VIDEO = os.path.join(SMOKE_DIR, 'hdr10_12bit.mp4')
TRUEHD_ASS_MKV = os.path.join(SMOKE_DIR, 'hdr10_10bit_truehd_ass.mkv')
TRUEHD_PGS_MKV = os.path.join(SMOKE_DIR, 'hdr10_10bit_truehd_pgs.mkv')
DOVI_VIDEO = os.path.join(SMOKE_DIR, 'dovi_p8.mp4')

_FFMPEG_OK = bool(FFMPEG_EXECUTABLE) and os.path.exists(FFMPEG_EXECUTABLE)

_SDR_OK = _FFMPEG_OK and os.path.exists(SDR_VIDEO)
_HDR10_10BIT_OK = _FFMPEG_OK and os.path.exists(HDR10_10BIT_VIDEO)
_HDR10_12BIT_OK = _FFMPEG_OK and os.path.exists(HDR10_12BIT_VIDEO)
_TRUEHD_ASS_OK = _FFMPEG_OK and os.path.exists(TRUEHD_ASS_MKV)
_TRUEHD_PGS_OK = _FFMPEG_OK and os.path.exists(TRUEHD_PGS_MKV)
_DOVI_OK = _FFMPEG_OK and os.path.exists(DOVI_VIDEO)

_LIBPLACEBO_OK = _FFMPEG_OK and vulkan_libplacebo_available()


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


def _probe_color_primaries(path):
    """Return the color_primaries tag of the first video stream, or None."""
    out = subprocess.check_output(
        [FFPROBE_EXECUTABLE, '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=color_primaries',
         '-of', 'json', path],
        stderr=subprocess.DEVNULL,
    )
    return json.loads(out)['streams'][0].get('color_primaries')


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
            self.assertEqual(
                _probe_color_primaries(out_path), 'bt709',
                "converted output must be tagged bt709 primaries after the LUT gamut correction"
            )


@unittest.skipUnless(_HDR10_10BIT_OK, "sample 'smoke_test_videos/hdr10_10bit.mp4' / ffmpeg not available")
class TestLutReproducesLegacyGamutMath(unittest.TestCase):
    """Runs the same real HDR10 source through today's zscale-only gamut
    correction (FFMPEG_FILTER_LEGACY_NO_LUT) and the new LUT-based chain
    (FFMPEG_CONVERT_FILTER), then compares sampled pixels. This is the
    primary automated color-correctness safety net for the LUT color
    pipeline (see docs/superpowers/specs/2026-07-22-lut-color-pipeline-design.md) --
    everything else only checks that the filter graph runs without error,
    not that the math is right.

    TEMPORARY: this test (and FFMPEG_FILTER_LEGACY_NO_LUT, which it depends
    on) is removed in Task 9 of the LUT implementation plan once the LUT has
    been visually approved and the legacy chain deleted -- there is nothing
    left to compare against after that.
    """

    # Max allowed per-channel difference (0-255 scale) between the legacy
    # zscale-matrix chain and the new LUT chain on real photographic-like
    # content.
    #
    # This tolerance was originally guessed at 6 (a small margin over an
    # isolated synthetic-color check that showed 1-3/255 between CPU lut3d
    # and GPU libplacebo lut_type=2). This test itself then caught two real
    # problems that guess didn't anticipate: (1) the generator's EOTF/OETF
    # used the piecewise BT.709 camera curve instead of the pure gamma-2.4
    # curve zscale's zimg backend actually implements for "bt709" transfer
    # (confirmed empirically -- see _rec709_eotf's docstring in
    # tools/generate_lut.py) -- fixed, this was a real ~60/255 math bug, not
    # LUT-resolution noise; (2) even with the correct math, the gamut
    # correction's hard per-channel clamp at the BT.709 boundary is a genuine
    # kink (not a smooth curve), which any interpolated LUT rounds off to some
    # degree at saturated near-gamut-boundary colors -- this residual was
    # reduced (grid 33^3->65^3, interp=tetrahedral) but not eliminated: real
    # HDR10 content measured a worst case of 10/255 (mean ~2/255) after both
    # fixes. 12 keeps meaningful headroom above that measured worst case while
    # still well below the ~26-60/255 a real generator-math regression
    # produces -- this remains a safety net for a real bug, not a rubber stamp.
    _TOLERANCE = 12

    def test_lut_chain_matches_legacy_chain_within_tolerance(self):
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_lut_compare_') as tmpdir:
            legacy_out = os.path.join(tmpdir, 'legacy.png')
            lut_out = os.path.join(tmpdir, 'lut.png')

            legacy_filter = FFMPEG_FILTER_LEGACY_NO_LUT.format(
                gamma=1.0, tonemapper='reinhard', width='iw', height='ih')
            lut_filter = FFMPEG_CONVERT_FILTER.format(
                gamma=1.0, tonemapper='reinhard', lut_path=get_lut_filter_path())

            for filter_str, out_path in ((legacy_filter, legacy_out), (lut_filter, lut_out)):
                cmd = [
                    FFMPEG_EXECUTABLE, '-y', '-loglevel', 'error',
                    '-i', HDR10_10BIT_VIDEO, '-vf', filter_str,
                    '-vframes', '1', out_path,
                ]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(
                    result.returncode, 0,
                    msg=f"ffmpeg failed for {out_path}:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}"
                )

            legacy_img = Image.open(legacy_out).convert("RGB")
            lut_img = Image.open(lut_out).convert("RGB")
            self.assertEqual(legacy_img.size, lut_img.size)

            max_diff = 0
            w, h = legacy_img.size
            # Sample a grid rather than every pixel -- fast, and any gross
            # LUT error will show up across many sample points, not just one.
            for x in range(0, w, max(1, w // 20)):
                for y in range(0, h, max(1, h // 20)):
                    a = legacy_img.getpixel((x, y))
                    b = lut_img.getpixel((x, y))
                    diff = max(abs(ac - bc) for ac, bc in zip(a, b))
                    max_diff = max(max_diff, diff)

            self.assertLessEqual(
                max_diff, self._TOLERANCE,
                f"LUT chain differs from legacy zscale chain by up to {max_diff}/255 "
                f"(tolerance {self._TOLERANCE}) -- check the LUT generator math."
            )


@unittest.skipUnless(_LIBPLACEBO_OK, "Vulkan/libplacebo not available on this machine")
class TestRealGpuOnlyTonemappers(unittest.TestCase):
    """BT.2390 and Spline have no CPU/zscale implementation -- this proves the
    real libplacebo filter strings the app constructs actually work against
    genuine HDR input, covering the exact verification gap (mocked-only
    tests) that let the original zscale-based design go uncaught."""

    def _convert(self, tonemapper, out_name='out.mp4'):
        props = get_video_properties(HDR10_10BIT_VIDEO)
        with tempfile.TemporaryDirectory(prefix='hdr_smoke_gpu_only_') as tmpdir:
            out_path = os.path.join(tmpdir, out_name)
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                HDR10_10BIT_VIDEO, out_path, gamma=1.0, properties=props,
                use_gpu=True, tonemapper=tonemapper,
            )
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(
                result.returncode, 0,
                msg=f"ffmpeg failed:\n{result.stderr.decode('utf-8', 'replace')[-2000:]}",
            )
            transfer, _, _, _ = _probe_video(out_path)
            self.assertNotEqual(transfer, 'smpte2084')
            self.assertEqual(
                _probe_color_primaries(out_path), 'bt709',
                "GPU-converted output must be tagged bt709 primaries after the LUT gamut correction"
            )

    def test_bt2390_gpu_conversion_completes(self):
        self._convert('BT.2390')

    def test_spline_gpu_conversion_completes(self):
        self._convert('Spline')

    def test_gpu_preview_extraction_completes(self):
        from src.utils import extract_frame_with_gpu_conversion
        img = extract_frame_with_gpu_conversion(
            HDR10_10BIT_VIDEO, gamma=1.0, tonemapper='bt.2390', time_position=0.5)
        self.assertGreater(img.width, 0)
        self.assertGreater(img.height, 0)


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
    container-aware transcode/drop logic for text-only subtitle tracks."""

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


@unittest.skipUnless(_TRUEHD_PGS_OK, "sample 'smoke_test_videos/hdr10_10bit_truehd_pgs.mkv' / ffmpeg not available")
class TestRealTrueHdPgsMkv(unittest.TestCase):
    """MKV carrying TrueHD audio and both an ASS (text) and a real PGS
    (bitmap) subtitle track -- covers the drop-image-subtitle-on-MP4-export
    path against genuine PGS data, which no synthesized fixture can exercise
    since ffmpeg has no PGS encoder."""

    def test_properties_detect_truehd_and_subtitle_streams(self):
        props = get_video_properties(TRUEHD_PGS_MKV)
        self.assertIsNotNone(props)
        self.assertEqual(props['codec_name'], 'hevc')
        self.assertEqual(props['audio_codec'], 'truehd')
        self.assertEqual(len(props['subtitle_streams']), 2)
        self.assertTrue(
            any(s.get('codec_name') == 'hdmv_pgs_subtitle' for s in props['subtitle_streams'])
        )

    def test_mkv_to_mkv_preserves_truehd_and_subtitles(self):
        props = get_video_properties(TRUEHD_PGS_MKV)
        self.assertEqual(props['audio_codec'], 'truehd')
        self.assertEqual(len(props['subtitle_streams']), 2)

        with tempfile.TemporaryDirectory(prefix='hdr_smoke_pgs_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out.mkv')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                TRUEHD_PGS_MKV, out_path, gamma=1.0, properties=props,
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
            self.assertEqual(_count_streams(out_path, 'subtitle'), 2)

    def test_mp4_output_transcodes_audio_and_drops_image_subs(self):
        """Container-aware fallback: converting this TrueHD + ASS/PGS MKV to an
        MP4 output must succeed by transcoding TrueHD audio to AAC, converting
        the ASS text subtitle to mov_text, and dropping the PGS image subtitle
        that no MP4 codec can represent.
        """
        props = get_video_properties(TRUEHD_PGS_MKV)

        with tempfile.TemporaryDirectory(prefix='hdr_smoke_pgs_mp4_') as tmpdir:
            out_path = os.path.join(tmpdir, 'out_from_mkv.mp4')
            manager = ConversionManager()
            cmd = manager.construct_ffmpeg_command(
                TRUEHD_PGS_MKV, out_path, gamma=1.0, properties=props,
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
            self.assertTrue(any(s['codec_name'] == 'mov_text' for s in subs))
            # No image subtitles survived into the MP4.
            self.assertFalse(any(s['codec_name'] == 'hdmv_pgs_subtitle' for s in subs))


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
