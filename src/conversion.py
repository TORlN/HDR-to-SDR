from __future__ import annotations

import os
import subprocess
import threading
import re
import logging
from dataclasses import dataclass, replace
from typing import Any
from conversion_view import ConversionView, Notice
import ffmpeg_command
from utils import (get_video_properties, FFMPEG_CONVERT_FILTER,
                   FFMPEG_EXECUTABLE,
                   VULKAN_DEVICE_ARGS, VULKAN_CUDA_DEVICE_ARGS,
                   build_libplacebo_filter, is_gpu_only_tonemapper,
                   vulkan_libplacebo_available, vulkan_cuda_interop_available,
                   get_lut_filter_path,
                   _startupinfo as _utils_startupinfo)
import platform


@dataclass(frozen=True)
class ConversionRequest:
    """Everything about *what* to encode: no Tk, no callbacks, no I/O.

    Frozen so a derived variant can only be made with dataclasses.replace --
    the GPU->CPU retry depends on deriving its request from the original
    rather than reassembling one, which is how settings used to get dropped.
    """
    input_path: str
    output_path: str
    gamma: float
    use_gpu: bool
    open_after_conversion: bool
    tonemapper: str = 'reinhard'
    quality: int = 23
    quality_mode: str = 'cq'
    bit_depth: int = 8
    licensed: bool = False
    lut_enabled: bool = True


class ConversionManager:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.cancelled: bool = False
        self._gpu_encoder: str | None = None
        self._request: ConversionRequest | None = None
        self._view: ConversionView | None = None

    def start(self, request: ConversionRequest, view: ConversionView) -> bool:
        """Public entry point. The only way to begin a conversion.

        view.on_complete decides batch vs interactive; the caller sets it when
        constructing the view. The GPU->CPU retry re-enters here with a
        request derived via replace().
        """
        # Guarded (not a bare abspath()): verify_paths' "both paths given" check
        # relies on an empty string staying falsy. os.path.abspath('') resolves
        # to the cwd, which is truthy, so that guard would silently stop firing
        # on a blank path if abspath ran unconditionally here.
        request = replace(
            request,
            input_path=(os.path.abspath(request.input_path)
                        if request.input_path else request.input_path),
            output_path=(os.path.abspath(request.output_path)
                         if request.output_path else request.output_path),
        )

        # Every early-out below must go through here rather than a bare
        # `return`: on_complete drives the batch queue, and a guard that
        # fires without calling it leaves the item stuck at 'Converting'
        # forever. Returning False also lets single-file callers know the
        # conversion never actually started.
        def _abort_before_start() -> bool:
            if view.on_complete is not None:
                view.on_complete(False)
            return False

        if not self.verify_paths(request.input_path, request.output_path, view):
            return _abort_before_start()

        incompatibility = self.validate_bit_depth_output(
            request.output_path, request.bit_depth)
        if incompatibility:
            self._reject(incompatibility, view)
            return _abort_before_start()

        self._request = request
        self._view = view
        self.cancelled = False

        properties = get_video_properties(request.input_path)
        if properties is None:
            self._reject("Failed to retrieve video properties.", view)
            return _abort_before_start()

        # A missing/zero duration would make progress tracking divide by zero in
        # the monitor thread (which would then die silently, leaving the UI stuck).
        if not properties.get('duration'):
            self._reject(
                "Could not determine the video's duration, so it can't be converted.",
                view)
            return _abort_before_start()

        view.set_inputs_enabled(False)
        view.set_cancel_visible(True, on_cancel=self.cancel_conversion)

        try:
            cmd = self.construct_ffmpeg_command(request, properties, view)
        except Exception:
            # The UI was already disabled and the cancel button gridded above,
            # but self.process hasn't been assigned yet -- Cancel would be a
            # no-op and gui.py's generic error handler doesn't re-enable the
            # UI. Undo both here so the app isn't left permanently disabled,
            # then let the original exception propagate unchanged so callers
            # still see/log/report it exactly as before.
            view.set_inputs_enabled(True)
            view.set_cancel_visible(False)
            raise
        self.process = self.start_ffmpeg_process(cmd)

        thread = threading.Thread(
            target=self.monitor_progress,
            args=(request, view, properties['duration']))
        thread.daemon = True
        thread.start()
        return True

    @staticmethod
    def _reject(message: str, view: ConversionView) -> None:
        """Hand a guard rejection to the view.

        A batch run has no human watching for it, so a modal here would stall
        the whole queue until someone clicks it -- BatchConversionView turns
        this into a log line instead, and the item is still marked Failed and
        visible in the batch list.
        """
        view.notify(Notice.warning("Warning", message))

    def verify_paths(self, input_path: str, output_path: str,
                     view: ConversionView) -> bool:
        if not input_path or not output_path:
            self._reject(
                "Please select both an input file and specify an output file.", view)
            return False
        # normcase folds case on Windows (NTFS is case-insensitive) and is a
        # no-op elsewhere -- without it, 'movie.mp4' vs 'Movie.mp4' would
        # pass this guard and ffmpeg (-y) would read and write the same file.
        if os.path.normcase(os.path.abspath(input_path)) == \
                os.path.normcase(os.path.abspath(output_path)):
            self._reject("Input and output file cannot be the same.", view)
            return False
        return True

    def _resolve_gpu_encoder(self) -> 'str | None':
        if self._gpu_encoder is None:
            self._gpu_encoder = self.detect_gpu_encoder()
        return self._gpu_encoder

    def construct_ffmpeg_command(self, request: ConversionRequest,
                                 properties: dict[str, Any],
                                 view: ConversionView) -> list[str]:
        input_path = request.input_path
        output_path = request.output_path
        gamma = request.gamma
        use_gpu = request.use_gpu
        tonemapper = request.tonemapper
        quality = request.quality
        quality_mode = request.quality_mode
        bit_depth = request.bit_depth
        licensed = request.licensed
        lut_enabled = request.lut_enabled

        cmd = [
            FFMPEG_EXECUTABLE,
            '-loglevel', 'info',
        ]

        tone = ffmpeg_command._tonemap_plan(request, properties, vulkan_libplacebo_available)
        use_gpu = tone.use_gpu
        dovi_needs_rpu = tone.dovi_needs_rpu
        use_libplacebo = tone.use_libplacebo
        for notice in tone.notices:
            view.notify(notice)

        gpu = ffmpeg_command._gpu_device_args(
            tone, self._resolve_gpu_encoder, vulkan_cuda_interop_available)
        active_encoder = gpu.active_encoder
        use_cuda_interop = gpu.use_cuda_interop
        cmd += gpu.pre_input_args
        for notice in gpu.notices:
            view.notify(notice)

        # Input file
        cmd += ['-i', os.path.normpath(input_path)]

        # The filter must be applied before mapping streams
        filter_str = ffmpeg_command._filter_args(request, tone, gpu)
        cmd += [
            '-filter_complex', f'[0:v:0]{filter_str}[vout]',
            '-map', '[vout]'  # Map the filtered video output
        ]

        # Map remaining streams. Audio is always mapped; subtitle mapping
        # depends on the output container, and Free-tier Dolby Vision sources
        # get an audio tier split (see ffmpeg_command._stream_map_args).
        streams = ffmpeg_command._stream_map_args(request, properties)
        cmd += streams.map_args

        # Output Color Depth: 10-bit (free) / 12-bit (Pro, CPU-only) avoids the
        # banding that gradient-heavy HDR sources produce once crushed down to
        # 8-bit. Hardware H.264 encoders have no 10-bit mode at all, so they're
        # swapped for their HEVC counterpart, which uses the semi-planar p010le
        # format instead of a 10-bit planar YUV format. 12-bit forces the CPU
        # path (see the use_gpu guard above), so it never reaches this swap.
        codec_plan = ffmpeg_command._codec_and_pix_fmt(request, properties, active_encoder)
        pix_fmt = codec_plan.pix_fmt

        # Encoding settings. In Constant Quality mode, `quality` is CRF for
        # libx264/5 or CQ/global_quality/QP for the GPU encoders (lower =
        # better). In Target Bitrate mode, `quality` is the user's chosen
        # kbps value; T/maxrate/bufsize are its "target average, capped
        # burst" ffmpeg args, standard on every encoder here (GPU or CPU).
        quality = str(quality)
        bitrate_rc_args: list = []  # type: ignore[type-arg]
        if quality_mode == 'bitrate':
            target_bv = int(quality) * 1000
            bitrate_rc_args = ['-b:v', str(target_bv),
                               '-maxrate', str(int(target_bv * 1.5)),
                               '-bufsize', str(target_bv * 2)]

        # MKV containers often report bit_rate=0; fall back to 8 Mbps so nvenc/
        # qsv never receive -b:v 0 / -maxrate 0 / -bufsize 0.
        _bv = properties['bit_rate'] or 8_000_000
        # libx264 tops out at 10-bit -- feeding it 12-bit silently downgrades to
        # yuv420p10le instead of erroring, so 12-bit (and HEVC-source
        # preservation in general) must switch codecs explicitly.
        codec = codec_plan.codec
        # Per encoder: (args shared by both modes, Constant-Quality-only args,
        # Target-Bitrate-only args). The command is always
        # `-c:v <codec>` + shared + (bitrate-only + bitrate_rc_args | cq-only),
        # so a change to one mode can't silently skip the other. The key is the
        # vendor suffix: an unrecognised encoder is already nulled to None by
        # the vendor dispatch above, so this lookup is total.
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
        cmd += ['-c:v', codec] + shared + (
            bitrate_args + bitrate_rc_args if quality_mode == 'bitrate' else cq_args)

        # HEVC in MP4/MOV must be tagged 'hvc1': ffmpeg's default sample entry
        # is 'hev1', which QuickTime/Apple devices (and some Windows players)
        # refuse to recognize even though the stream is fine. Matroska has no
        # such codec tag, so MKV is left alone.
        out_ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if codec_plan.produces_hevc and out_ext in ('mp4', 'mov'):
            cmd += ['-tag:v', 'hvc1']

        # Common settings
        cmd += [
            '-r', str(properties['frame_rate']),
            '-pix_fmt', pix_fmt,
            '-strict', '-2',
        ]
        cmd += streams.audio_codec_args      # copy, or transcode when container demands
        cmd += streams.subtitle_codec_args   # copy / mov_text / omitted
        cmd += [
            '-map_metadata', '0', # Copy all metadata
            '-movflags', '+faststart',  # Optimize for streaming playback
            os.path.normpath(output_path),
            '-y'
        ]

        logging.debug(f"Constructed ffmpeg command: {' '.join(cmd)}")
        return cmd

    # .m4v is Apple's legacy "iPod video" MPEG-4 profile: it predates HEVC/10-bit
    # entirely and only ever allowed 8-bit H.264 Baseline/Main/High. Unlike plain
    # .mp4/.mov it can never carry a higher-bit-depth stream, regardless of encoder.
    _HIGH_BIT_DEPTH_INCOMPATIBLE_EXTS = {'m4v'}

    def validate_bit_depth_output(self, output_path: str,
                                  bit_depth: int) -> str | None:
        """Return a user-facing error string if *bit_depth* can't be honored for
        this output container, else None. Callers must check this before
        constructing/launching ffmpeg so an invalid combination is caught with
        a warning instead of a failing subprocess."""
        if bit_depth <= 8:
            return None
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if ext in self._HIGH_BIT_DEPTH_INCOMPATIBLE_EXTS:
            return (
                f"{bit_depth}-bit output is not supported for the legacy .{ext} container. "
                "Choose MP4, MOV, or MKV instead."
            )
        return None

    def start_ffmpeg_process(self, cmd: list[str]) -> subprocess.Popen[str]:
        """Start the FFmpeg process without showing a console window."""
        startupinfo, creationflags = _utils_startupinfo()

        process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,  # output goes to a file; don't fill an unread pipe
            universal_newlines=True,
            startupinfo=startupinfo,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        logging.debug(f"Started FFmpeg process with command: {' '.join(cmd)}")
        return process

    def monitor_progress(self, request: ConversionRequest, view: ConversionView,
                         duration: float) -> None:
        progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
        error_messages: list[str] = []
        gpu_error_detected = False

        # Capture a stable local reference at thread-entry time.  cancel_conversion
        # on the main thread can set self.process = None concurrently; using `proc`
        # throughout this function prevents AttributeError if that happens between
        # the loop ending and proc.returncode being read.
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            if self.cancelled:
                break
            decoded_line = line.strip()
            logging.debug(decoded_line)
            error_messages.append(decoded_line)
            match = progress_pattern.search(decoded_line)
            if match and duration:
                elapsed_time = self.parse_time(match.group(1))
                progress = (elapsed_time / duration) * 100
                view.set_progress(progress)

            # ffmpeg's own banner lines echo the input/output path verbatim
            # ("Input #0, ..., from '<path>':" / "Output #0, ..., to
            # '<path>':"), so a file merely named e.g. 'cuda_test.mp4' would
            # otherwise match these keywords and misdiagnose an unrelated
            # failure (bad codec, full disk) as a GPU error.
            is_path_banner_line = "from '" in decoded_line or " to '" in decoded_line
            if not is_path_banner_line and any(
                    k in decoded_line.lower() for k in ('cuda', 'nvcuda.dll', 'amf', 'mfx')):
                gpu_error_detected = True

        if proc is not None:
            proc.wait()
            returncode = proc.returncode
            if returncode != 0 and request.use_gpu and gpu_error_detected and not self.cancelled:
                logging.warning("GPU acceleration failed. Retrying with CPU encoding.")
                # The retry touches Tk (gpu checkbox, dialog, UI state) and must run
                # on the main thread, not this worker thread.
                view.schedule(lambda: self._retry_with_cpu(request, view))
            else:
                self.handle_completion(request, view, error_messages, returncode)

    def _retry_with_cpu(self, request: ConversionRequest,
                        view: ConversionView) -> None:
        """Restart the conversion on the CPU after a GPU failure. Main thread.

        The retry derives its request from the original with replace() rather
        than re-reading the GUI: a path edited mid-conversion used to redirect
        the retry to a different file. *request* is the snapshot monitor_progress
        was handed for this run, not self._request -- self._request can already
        belong to a different, later-started conversion by the time this
        after(0) callback fires (e.g. cancel + immediately start another file).
        """
        view.on_gpu_fallback()
        view.notify(Notice.warning(
            "GPU Acceleration Failed",
            "GPU acceleration failed. Switching to CPU encoding."))
        try:
            self.start(replace(request, use_gpu=False), view)
        except Exception as e:
            # E.g. a GPU-only tonemapper (BT.2390/Spline) with no CPU
            # implementation, raised from construct_ffmpeg_command -- start
            # restores UI state before re-raising in that case. But start can
            # also raise from start_ffmpeg_process (e.g. a missing ffmpeg
            # binary), which sits outside that try/except and leaves the UI
            # disabled. Either way, nothing else in this call path calls
            # on_complete -- without this the batch item would be stuck at
            # 'Converting' forever.
            logging.error(f"CPU retry failed to start ({request.tonemapper}): {e}")
            if view.on_complete is not None:
                view.on_complete(False)

    def parse_time(self, time_str: str) -> float:
        hours, minutes, seconds = map(float, time_str.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    def handle_completion(self, request: ConversionRequest, view: ConversionView,
                          error_messages: list[str], returncode: int) -> None:
        def _handle() -> None:
            # returncode is the value monitor_progress already read from its
            # own locally-captured proc, not re-read from self.process here:
            # cancel_conversion (main thread) can set self.process = None
            # between monitor_progress finishing and this after(0)-scheduled
            # callback actually running, which would otherwise misreport a
            # conversion that had already finished successfully.
            on_complete = view.on_complete
            if on_complete is not None:
                # Batch/queue mode: no per-file dialog and the UI stays disabled
                # between files. The callback marks status and advances the queue
                # (the final summary + UI re-enable happen when the queue drains).
                success = returncode == 0
                if not success and not self.cancelled:
                    tail = '\n'.join(error_messages[-50:])
                    logging.error(f"Batch item failed with code "
                                  f"{returncode}: {tail}")
                on_complete(success)
                return

            if returncode == 0:
                logging.info("Conversion completed successfully.")
                view.notify(Notice.info(
                    "Success",
                    f"Conversion complete! Output saved to: {request.output_path}"))
                if request.open_after_conversion:
                    view.open_output(request.output_path)
            elif not self.cancelled:
                tail = error_messages[-50:]  # ffmpeg stderr can be thousands of progress lines; show only the tail where real errors appear
                error_message = '\n'.join(tail)
                logging.error(f"Conversion failed with code {returncode}: {error_message}")
                view.notify(Notice.error(
                    "Error", f"Conversion failed with code {returncode}\n{error_message}"))

            view.set_inputs_enabled(True)
            view.set_cancel_visible(False)
            view.restore_drop_target()

        view.schedule(_handle)

    def cancel_conversion(self) -> None:
        self.cancelled = True
        view = self._view
        if self.process and view is not None:
            self.process.terminate()
            self.process = None
            view.schedule(lambda: view.notify(Notice.info(
                "Cancelled", "Video conversion has been cancelled.")))
            view.set_inputs_enabled(True)
            view.set_cancel_visible(False)
            view.restore_drop_target()

    def _nvidia_present(self) -> bool:
        """Return True if nvidia-smi reports a usable NVIDIA GPU."""
        try:
            si, flags = _utils_startupinfo()
            result = subprocess.run(
                ['nvidia-smi'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=si,
                creationflags=flags,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError):
            return False

    def _list_encoders(self) -> str:
        """Return lowercase stdout of 'ffmpeg -encoders', or '' on failure."""
        try:
            si, flags = _utils_startupinfo()
            process = subprocess.Popen(
                [FFMPEG_EXECUTABLE, '-encoders'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                startupinfo=si,
                creationflags=flags,
            )
            stdout, _ = process.communicate()
            return stdout.lower() if process.returncode == 0 else ''
        except (FileNotFoundError, OSError):
            return ''

    def detect_gpu_encoder(self) -> str | None:
        """Detect best available H.264 GPU encoder; sets and returns self._gpu_encoder.

        Priority: NVENC (requires confirmed NVIDIA GPU) > AMF > QSV > None.
        """
        encoders = self._list_encoders()
        nvidia = self._nvidia_present()

        if nvidia and 'h264_nvenc' in encoders:
            self._gpu_encoder = 'h264_nvenc'
        elif 'h264_amf' in encoders:
            self._gpu_encoder = 'h264_amf'
        elif 'h264_qsv' in encoders:
            self._gpu_encoder = 'h264_qsv'
        else:
            self._gpu_encoder = None

        logging.debug(f"Detected GPU encoder: {self._gpu_encoder}")
        return self._gpu_encoder

    def is_gpu_acceleration_available(self) -> bool:
        """True if any GPU acceleration is usable: a hardware H.264 encoder
        (nvenc/amf/qsv) and/or GPU tonemapping via libplacebo. Either one alone
        makes the GPU toggle worthwhile -- a machine with Vulkan/libplacebo but
        no hardware encoder still gets the (bigger) tonemapping speedup -- so the
        toggle is gated on the union, not on the encoder alone."""
        try:
            if self._gpu_encoder is None:
                self._gpu_encoder = self.detect_gpu_encoder()
            has_encoder = self._gpu_encoder is not None
        except Exception as e:
            logging.error(f"Error checking GPU availability: {e}")
            has_encoder = False
        return has_encoder or vulkan_libplacebo_available()

conversion_manager = ConversionManager()