"""Decide the ffmpeg command for one conversion (audit item 6).

Split out of ConversionManager.construct_ffmpeg_command, which was a
253-line, complexity-45 function doing six separable jobs. Pure functions
only -- no tkinter, no gui, no conversion.py -- so each is testable with no
ConversionView or test double. build() is the one impure piece: it owns
the ConversionView and decides notice-emission order.

Three sharp edges, each documented at the code that resolves them:
  - HEVC-swap ordering: active_encoder can be reassigned by the H.264->HEVC
    preservation swap. CodecPlan.produces_hevc is computed at the
    point of the swap so no caller has to reconstruct it from a
    possibly-stale value.
  - GPU-probe laziness: a 12-bit request forces use_gpu off before any GPU
    vendor probe would run, so resolve_gpu_encoder must be a lazy
    callable, not a pre-resolved value -- calling it unconditionally would
    add a probe subprocess call on a path that has none today.
  - This module never imports vulkan_libplacebo_available or
    vulkan_cuda_interop_available from utils, even though it is the only
    code that calls them. Both are always parameters (see Probes). Two
    reasons, not one: it preserves their laziness exactly like
    resolve_gpu_encoder above, and -- the one that actually bit this refactor
    during implementation -- conversion.py's own `from utils import X`
    creates an independent name binding per importing module, so
    test/conversion_test.py's and the golden master's
    patch('src.conversion.vulkan_libplacebo_available', ...) mocks would
    silently stop affecting anything the moment this module imported and
    called its own copy directly. conversion.py keeps the real imports and
    passes them through unchanged; that is what keeps ~30 pre-existing test
    mocks working without editing a single one of them. Do not "simplify"
    this module by importing these two directly -- confirmed by reproducing
    the failure: 13 golden-master subtests and 8 conversion_test.py tests
    broke on the first attempt. platform.system() has no such treatment
    because `import platform` (not `from platform import system`) shares
    one singleton module object across every importer, so a patch on it
    from anywhere stays globally effective regardless of which module's
    code calls platform.system().
"""
from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from conversion_view import ConversionView, Notice
from utils import (VULKAN_DEVICE_ARGS, VULKAN_CUDA_DEVICE_ARGS,
                   build_libplacebo_filter, is_gpu_only_tonemapper,
                   FFMPEG_CONVERT_FILTER, get_lut_filter_path,
                   FFMPEG_EXECUTABLE)


class RequestLike(Protocol):
    """The subset of ConversionRequest this module needs, described
    structurally so this module never imports conversion.py -- conversion.py
    imports this module, and the layer table forbids a cycle.
    ConversionRequest, being a plain dataclass with matching attribute
    names, satisfies this automatically.

    Declared as read-only @property members, not plain annotations: plain
    Protocol attributes are structurally writable, but ConversionRequest is
    a frozen dataclass (no setter) -- pyright flags that mismatch as a
    reportArgumentType error at the one real call site (conversion.py:172's
    `ffmpeg_command.build(request, properties, probes, view)`) otherwise."""
    @property
    def input_path(self) -> str: ...
    @property
    def output_path(self) -> str: ...
    @property
    def gamma(self) -> float: ...
    @property
    def use_gpu(self) -> bool: ...
    @property
    def tonemapper(self) -> str: ...
    @property
    def quality(self) -> int: ...
    @property
    def quality_mode(self) -> str: ...
    @property
    def bit_depth(self) -> int: ...
    @property
    def licensed(self) -> bool: ...
    @property
    def lut_enabled(self) -> bool: ...


@dataclass(frozen=True)
class TonemapPlan:
    use_gpu: bool
    dovi_needs_rpu: bool
    use_libplacebo: bool
    notices: 'list[Notice]'


def _tonemap_plan(request: RequestLike, properties: 'dict[str, Any]',
                  resolve_libplacebo_available: 'Callable[[], bool]') -> TonemapPlan:
    """12-bit forces the CPU pipeline outright -- no hardware encoder (any
    vendor) has a 12-bit HEVC profile in its API, a fixed silicon
    limitation. Dolby Vision profile 5 has no HDR10-compatible base layer,
    so the CPU zscale chain decodes to wrong colors (green/purple cast);
    libplacebo applies the DoVi RPU during tonemapping, so profile 5 forces
    libplacebo even on an otherwise-CPU run -- only the tonemap step moves
    to the GPU, encoding still follows the vendor dispatch in
    _gpu_device_args. Profiles 7/8 carry an HDR10-compatible base layer and
    need no special handling here.

    resolve_libplacebo_available is a parameter, not a direct call to
    utils.vulkan_libplacebo_available, so this module never imports a
    function that reaches a real ffmpeg subprocess -- see this module's
    top-of-file docstring for why that matters for the existing test suite's
    mocks, not just for purity's sake."""
    use_gpu = request.use_gpu
    if request.bit_depth >= 12:
        use_gpu = False

    dovi_needs_rpu = bool(properties.get('is_dolby_vision')
                         and properties.get('dovi_profile') == 5)
    use_libplacebo = (use_gpu or dovi_needs_rpu) and resolve_libplacebo_available()

    notices: 'list[Notice]' = []
    if dovi_needs_rpu and not use_libplacebo:
        notices.append(Notice.warning(
            "Warning",
            "This Dolby Vision (profile 5) source has no HDR10-compatible "
            "base layer and requires GPU tonemapping to render correctly, "
            "which isn't available on this system. The output colors may "
            "look wrong (green/purple cast)."))
    elif dovi_needs_rpu and not use_gpu:
        # use_gpu is False here, so without this notice the override is
        # silent -- a user who unchecked "Enable GPU Acceleration" expecting
        # a pure-CPU run has no way to know GPU tonemapping ran anyway (only
        # the tonemap step; encoding still follows the vendor dispatch, so
        # it stays on the CPU encoder).
        notices.append(Notice.info(
            "Dolby Vision Profile 5",
            "This Dolby Vision (profile 5) source has no HDR10-compatible "
            "base layer, so GPU tonemapping is being used to render its "
            "colors correctly even though \"Enable GPU Acceleration\" is "
            "unchecked. Encoding itself still runs on the CPU."))

    return TonemapPlan(use_gpu=use_gpu, dovi_needs_rpu=dovi_needs_rpu,
                       use_libplacebo=use_libplacebo, notices=notices)


@dataclass(frozen=True)
class GpuPlan:
    active_encoder: 'str | None'
    pre_input_args: 'list[str]'
    use_cuda_interop: bool
    notices: 'list[Notice]'


_UNSUPPORTED_PLATFORM_NOTICE = Notice.warning(
    "Warning", "GPU acceleration is not supported on this platform.")


def _gpu_device_args(plan: TonemapPlan,
                     resolve_gpu_encoder: 'Callable[[], str | None]',
                     resolve_cuda_interop_available: 'Callable[[], bool]') -> GpuPlan:
    """GPU vendor dispatch plus the device-setup args that must precede -i.

    plan.use_gpu already reflects the 12-bit override _tonemap_plan applies
    -- a 12-bit GPU-requested conversion reaches here with use_gpu already
    False, so resolve_gpu_encoder (which may run a real detect_gpu_encoder()
    subprocess probe) is never called on that path. See the module
    docstring's GPU-probe-laziness note.

    resolve_cuda_interop_available is a parameter for the same reason
    resolve_gpu_encoder is -- see the module docstring -- and its
    call stays exactly where the original inline code's
    vulkan_cuda_interop_available() call was, inside the same short-circuit
    expression, so it is still skipped whenever plan.use_libplacebo is
    False or active_encoder isn't 'h264_nvenc'."""
    current_platform = platform.system().lower()
    active_encoder = None
    pre_input_args: 'list[str]' = []
    notices: 'list[Notice]' = []

    if plan.use_gpu:
        active_encoder = resolve_gpu_encoder()

        if active_encoder == 'h264_nvenc':
            if current_platform in ["windows", "linux"]:
                if not plan.use_libplacebo:
                    pre_input_args = ['-hwaccel', 'cuda', '-hwaccel_device', '0']
            else:
                notices.append(_UNSUPPORTED_PLATFORM_NOTICE)
                active_encoder = None
        elif active_encoder == 'h264_qsv':
            if current_platform in ["windows", "linux"]:
                if not plan.use_libplacebo:
                    pre_input_args = ['-hwaccel', 'qsv']
            else:
                notices.append(_UNSUPPORTED_PLATFORM_NOTICE)
                active_encoder = None
        elif active_encoder == 'h264_amf':
            if current_platform not in ["windows", "linux"]:
                notices.append(_UNSUPPORTED_PLATFORM_NOTICE)
                active_encoder = None
            # AMF needs no separate hwaccel flag
        elif active_encoder is not None:
            notices.append(_UNSUPPORTED_PLATFORM_NOTICE)
            active_encoder = None

    # NVIDIA fast path: use CUDA->Vulkan interop so NVDEC handles decode on
    # the GPU and feeds frames directly into libplacebo without a CPU detour.
    # Other vendors (AMF, QSV) don't have CUDA; they fall back to CPU decode.
    use_cuda_interop = (
        plan.use_libplacebo
        and active_encoder == 'h264_nvenc'
        and resolve_cuda_interop_available()
    )

    # Device args go before -i. Interop path sets up linked cuda+vulkan
    # devices and enables -hwaccel cuda; plain Vulkan path sets up vulkan only.
    if use_cuda_interop:
        pre_input_args = list(VULKAN_CUDA_DEVICE_ARGS)
    elif plan.use_libplacebo:
        pre_input_args = list(VULKAN_DEVICE_ARGS)

    return GpuPlan(active_encoder=active_encoder, pre_input_args=pre_input_args,
                   use_cuda_interop=use_cuda_interop, notices=notices)


def _filter_args(request: RequestLike, plan: TonemapPlan, gpu: GpuPlan) -> str:
    """The tonemap filter chain body, without the [0:v:0]...[vout] wrapper
    -- build() owns that, since it also owns the -filter_complex
    flag itself.

    Raises ValueError for a GPU-only tonemapper (e.g. bt.2390) forced onto
    the CPU zscale path, which has no equivalent -- unchanged from the
    pre-split function."""
    tonemapper = request.tonemapper.lower()
    if plan.use_libplacebo:
        return build_libplacebo_filter(
            request.gamma, tonemapper, cuda_input=gpu.use_cuda_interop,
            lut_enabled=request.lut_enabled)
    if is_gpu_only_tonemapper(tonemapper):
        raise ValueError(
            f"{tonemapper} requires GPU tonemapping; this item's "
            "settings force CPU processing — change the tonemapper "
            "or output bit depth."
        )
    return FFMPEG_CONVERT_FILTER.format(
        gamma=request.gamma, tonemapper=tonemapper, lut_path=get_lut_filter_path())


_FREE_DOVI_AUDIO_ARGS = ['-c:a', 'aac', '-ac', '2', '-b:a', '192k']

# Audio/subtitle codecs the MP4-family containers (.mp4/.m4v/.mov) accept
# via stream copy. Anything else must be transcoded or dropped.
_MP4_AUDIO_OK = {'aac', 'ac3', 'eac3', 'mp3', 'alac'}
_TEXT_SUBTITLES = {'subrip', 'srt', 'ass', 'ssa', 'text', 'mov_text', 'webvtt'}
_MP4_FAMILY = {'mp4', 'm4v', 'mov'}


def _container_stream_args(
    output_path: str, properties: 'dict[str, Any]'
) -> 'tuple[list[str], list[str], list[str]]':
    """Decide subtitle mapping and audio/subtitle codecs for the output
    container.

    Prefer lossless stream copy. For MP4-family containers, which can't copy
    TrueHD/DTS audio or ASS/PGS subtitles, fall back to transcoding audio to
    AAC and text subtitles to mov_text, and drop image subtitles (e.g. PGS)
    that no MP4 codec can represent. Non-MP4 containers (notably MKV) keep
    the original copy-everything behavior.

    Returns (subtitle_map_args, audio_codec_args, subtitle_codec_args).
    """
    ext = os.path.splitext(output_path)[1].lower().lstrip('.')
    if ext not in _MP4_FAMILY:
        return (['-map', '0:s?'], ['-c:a', 'copy'], ['-c:s', 'copy'])

    audio_codec = (properties.get('audio_codec') or '').lower()
    if audio_codec and audio_codec not in _MP4_AUDIO_OK:
        bit_rate = properties.get('audio_bit_rate') or 0
        target_rate = str(min(int(bit_rate), 384000)) if bit_rate else '192k'
        audio_codec_args = ['-c:a', 'aac', '-b:a', target_rate]
    else:
        audio_codec_args = ['-c:a', 'copy']

    subtitle_map_args = []
    for stream in properties.get('subtitle_streams', []):
        if (stream.get('codec_name') or '').lower() in _TEXT_SUBTITLES:
            subtitle_map_args += ['-map', f"0:{stream['index']}"]
    subtitle_codec_args = ['-c:s', 'mov_text'] if subtitle_map_args else []

    return (subtitle_map_args, audio_codec_args, subtitle_codec_args)


@dataclass(frozen=True)
class StreamArgs:
    map_args: 'list[str]'
    audio_codec_args: 'list[str]'
    subtitle_codec_args: 'list[str]'


def _stream_map_args(request: RequestLike, properties: 'dict[str, Any]') -> StreamArgs:
    """Stream mapping plus the free/Pro Dolby Vision audio tier split.

    Pro keeps the container-aware passthrough _container_stream_args decides
    (lossless copy wherever the container allows, full multi-channel layout
    always preserved). Free is restricted to the first audio stream,
    downmixed to 2-channel stereo AAC, regardless of container."""
    subtitle_map_args, audio_codec_args, subtitle_codec_args = \
        _container_stream_args(request.output_path, properties)

    if properties.get('is_dolby_vision') and not request.licensed:
        audio_map_args = ['-map', '0:a:0?']
        audio_codec_args = list(_FREE_DOVI_AUDIO_ARGS)
    else:
        audio_map_args = ['-map', '0:a?']

    return StreamArgs(map_args=audio_map_args + subtitle_map_args,
                      audio_codec_args=audio_codec_args,
                      subtitle_codec_args=subtitle_codec_args)


# Hardware H.264 encoders can't do 10-bit at all; their HEVC counterparts
# can. Also used to preserve an already-HEVC source's codec on GPU, not
# just the mandatory 10-bit case.
_HW_HEVC_ENCODER_MAP = {
    'h264_nvenc': 'hevc_nvenc',
    'h264_amf':   'hevc_amf',
    'h264_qsv':   'hevc_qsv',
}


@dataclass(frozen=True)
class CodecPlan:
    codec: str
    pix_fmt: str
    produces_hevc: bool


def _codec_and_pix_fmt(request: RequestLike, properties: 'dict[str, Any]',
                       active_encoder: 'str | None') -> CodecPlan:
    """Pixel format, the two H.264->HEVC preservation/mandatory swaps, and
    codec selection -- including produces_hevc, computed here rather than
    left for a caller to reconstruct from active_encoder plus want_libx265,
    since active_encoder can be reassigned inside this same function by the
    swap below and a caller holding the pre-swap value would compute the
    wrong hvc1-tag decision (see the module docstring's HEVC-swap-ordering
    note)."""
    bit_depth = request.bit_depth
    codec_name = (properties.get('codec_name') or '').lower()
    source_is_hevc = codec_name == 'hevc'

    # Output Color Depth: 10-bit (free) / 12-bit (Pro, CPU-only) avoids the
    # banding that gradient-heavy HDR sources produce once crushed down to
    # 8-bit.
    if bit_depth >= 12:
        pix_fmt = 'yuv420p12le'
    elif bit_depth == 10:
        pix_fmt = 'yuv420p10le'
    else:
        pix_fmt = 'yuv420p'

    # GPU: H.264 hardware encoders can't do 10-bit at all (mandatory swap);
    # an already-HEVC source also swaps at 8-bit purely to preserve the
    # source codec, since libx264/h264_* could otherwise handle 8-bit fine.
    if (bit_depth >= 10 or source_is_hevc) and active_encoder in _HW_HEVC_ENCODER_MAP:
        active_encoder = _HW_HEVC_ENCODER_MAP[active_encoder]
        if bit_depth == 10:
            pix_fmt = 'p010le'

    # CPU: libx264 tops out at 10-bit, so 12-bit must switch to libx265; an
    # already-HEVC source also switches (preservation) even at 8/10-bit,
    # where libx264 could otherwise still handle the bit depth.
    want_libx265 = bit_depth >= 12 or source_is_hevc
    codec = active_encoder or ('libx265' if want_libx265 else 'libx264')

    produces_hevc = (
        active_encoder in _HW_HEVC_ENCODER_MAP.values()
        or (active_encoder is None and want_libx265)
    )

    return CodecPlan(codec=codec, pix_fmt=pix_fmt, produces_hevc=produces_hevc)


def _encoder_rate_args(request: RequestLike, properties: 'dict[str, Any]',
                       codec: str) -> 'list[str]':
    """The -c:v flag plus every quality/rate-control arg for *codec*.

    quality is CRF (libx264/5) or CQ/global_quality/QP (GPU encoders) in
    Constant Quality mode; the user's chosen kbps value in Target Bitrate
    mode, where -b:v/-maxrate/-bufsize are the standard "target average,
    capped burst" args, added on every encoder here (GPU or CPU)."""
    quality = str(request.quality)
    bitrate_rc_args: 'list[str]' = []
    if request.quality_mode == 'bitrate':
        target_bv = int(quality) * 1000
        bitrate_rc_args = ['-b:v', str(target_bv),
                           '-maxrate', str(int(target_bv * 1.5)),
                           '-bufsize', str(target_bv * 2)]

    # MKV containers often report bit_rate=0; fall back to 8 Mbps so
    # nvenc/qsv never receive -b:v 0 / -maxrate 0 / -bufsize 0.
    _bv = properties['bit_rate'] or 8_000_000

    # Per encoder: (args shared by both modes, Constant-Quality-only args,
    # Target-Bitrate-only args). The command is always
    # -c:v <codec> + shared + (bitrate-only + bitrate_rc_args | cq-only), so
    # a change to one mode can't silently skip the other. The key is the
    # vendor suffix: an unrecognised encoder is already nulled to None by
    # the vendor dispatch in _gpu_device_args, so this lookup is total.
    encoder_args = {
        'nvenc': (['-preset', 'p4', '-tune', 'hq', '-rc', 'vbr'],
                  ['-cq', quality, '-b:v', str(_bv),
                   '-maxrate', str(_bv), '-bufsize', str(_bv * 2)],
                  []),
        'amf': (['-quality', 'balanced'],
                ['-rc', 'cqp', '-qp_i', quality,
                 '-qp_p', quality, '-qp_b', quality],
                ['-rc', 'vbr_peak']),
        'qsv': ([], ['-global_quality', quality, '-b:v', str(_bv)], []),
        # libx265 has no 'film' tune -- x264's -tune film fails x265 init.
        'libx265': (['-preset', 'veryfast'], ['-crf', quality], []),
        # No -b:v in CRF mode: libx264 ignores a target bitrate there.
        'libx264': (['-preset', 'veryfast', '-tune', 'film'],
                    ['-crf', quality], []),
    }
    shared, cq_args, bitrate_args = encoder_args[codec.rsplit('_', 1)[-1]]
    return ['-c:v', codec] + shared + (
        bitrate_args + bitrate_rc_args if request.quality_mode == 'bitrate' else cq_args)


@dataclass(frozen=True)
class Probes:
    """The three lazy capability checks build() needs, bundled so its own
    signature doesn't re-explode into the kind of parameter list this
    refactor exists to eliminate.

    Each is a zero-arg callable the caller supplies -- this module never
    imports vulkan_libplacebo_available, vulkan_cuda_interop_available, or
    calls detect_gpu_encoder itself. Two reasons: it preserves their exact
    pre-split call laziness (see the module docstring's GPU-probe-laziness
    note -- eagerly resolving any of these would add a probe call, possibly
    a real subprocess, on a path that has none today), and it is what keeps
    the pre-existing test suite's patch('src.conversion.vulkan_libplacebo_available',
    ...)-style mocks (roughly 30 call sites across test/conversion_test.py
    and test/ffmpeg_command_golden_test.py) working unmodified -- conversion.py
    still owns the real `from utils import ...` bindings those mocks target,
    and merely passes the (possibly-mocked) names through as arguments. See
    this module's top-of-file docstring for the full story, including why
    platform.system() needs no equivalent field here."""
    resolve_gpu_encoder: 'Callable[[], str | None]'
    resolve_libplacebo_available: 'Callable[[], bool]'
    resolve_cuda_interop_available: 'Callable[[], bool]'


def build(request: RequestLike, properties: 'dict[str, Any]',
         probes: Probes, view: ConversionView) -> 'list[str]':
    """Construct the ffmpeg argv for one conversion.

    The one impure function in this module: it owns view and decides
    notice-emission order. Notices from _tonemap_plan and _gpu_device_args
    are delivered immediately after each of those calls returns -- before
    _filter_args runs, since _filter_args is the only helper that can raise
    and a raise must never suppress a notice that a pre-split caller would
    already have seen (the pre-split function emitted these notices inline,
    textually before the equivalent of the _filter_args call)."""
    tone = _tonemap_plan(request, properties, probes.resolve_libplacebo_available)
    for notice in tone.notices:
        view.notify(notice)

    gpu = _gpu_device_args(tone, probes.resolve_gpu_encoder,
                           probes.resolve_cuda_interop_available)
    for notice in gpu.notices:
        view.notify(notice)

    filter_str = _filter_args(request, tone, gpu)  # may raise ValueError

    cmd = [FFMPEG_EXECUTABLE, '-loglevel', 'info']
    cmd += gpu.pre_input_args
    cmd += ['-i', os.path.normpath(request.input_path)]
    cmd += [
        '-filter_complex', f'[0:v:0]{filter_str}[vout]',
        '-map', '[vout]',
    ]

    streams = _stream_map_args(request, properties)
    cmd += streams.map_args

    codec_plan = _codec_and_pix_fmt(request, properties, gpu.active_encoder)
    cmd += _encoder_rate_args(request, properties, codec_plan.codec)

    # HEVC in MP4/MOV must be tagged 'hvc1': ffmpeg's default sample entry
    # is 'hev1', which QuickTime/Apple devices (and some Windows players)
    # refuse to recognize even though the stream is fine. Matroska has no
    # such codec tag, so MKV is left alone.
    out_ext = os.path.splitext(request.output_path)[1].lower().lstrip('.')
    if codec_plan.produces_hevc and out_ext in ('mp4', 'mov'):
        cmd += ['-tag:v', 'hvc1']

    cmd += [
        '-r', str(properties['frame_rate']),
        '-pix_fmt', codec_plan.pix_fmt,
        '-strict', '-2',
    ]
    cmd += streams.audio_codec_args      # copy, or transcode when container demands
    cmd += streams.subtitle_codec_args   # copy / mov_text / omitted
    cmd += [
        '-map_metadata', '0',  # Copy all metadata
        '-movflags', '+faststart',  # Optimize for streaming playback
        os.path.normpath(request.output_path),
        '-y'
    ]

    logging.debug(f"Constructed ffmpeg command: {' '.join(cmd)}")
    return cmd
