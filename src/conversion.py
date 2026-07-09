import os
import subprocess
import threading
import webbrowser
import re
import logging
from tkinter import messagebox
from utils import (get_video_properties, FFMPEG_CONVERT_FILTER,
                   FFMPEG_EXECUTABLE, FFPROBE_EXECUTABLE,
                   VULKAN_DEVICE_ARGS, VULKAN_CUDA_DEVICE_ARGS,
                   build_libplacebo_filter, GPU_ONLY_TONEMAPPERS,
                   vulkan_libplacebo_available, vulkan_cuda_interop_available,
                   _startupinfo as _utils_startupinfo)
from tkinterdnd2 import DND_FILES
import sys
import platform  # Add this import at the top

class ConversionManager:
    def __init__(self):
        self.process = None
        self.cancelled = False
        self._gpu_encoder = None

    def start_conversion(self, input_path, output_path, gamma, use_gpu,
                         progress_var, interactable_elements, gui_instance,
                         open_after_conversion, cancel_button, tonemapper='reinhard',
                         quality=23, quality_mode='cq', on_complete=None, bit_depth=8,
                         licensed=False):
        if not self.verify_paths(input_path, output_path):
            return

        incompatibility = self.validate_bit_depth_output(output_path, bit_depth)
        if incompatibility:
            messagebox.showwarning("Warning", incompatibility)
            return

        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)
        self.cancelled = False
        self.use_gpu = use_gpu  # Store the use_gpu state
        self._quality = quality  # remembered so a GPU->CPU retry keeps the same quality
        self._quality_mode = quality_mode  # remembered so a GPU->CPU retry keeps the same mode
        self._bit_depth = bit_depth  # remembered so a GPU->CPU retry keeps the same color depth
        # Remembered so a GPU->CPU retry keeps the license tier: the Dolby
        # Vision audio split (Pro passthrough vs Free stereo downmix) must not
        # silently demote a Pro user on retry.
        self._licensed = licensed
        # When set (batch/queue runs), the per-file success/error dialog is
        # suppressed and this callback drives queue progression instead.
        self._on_complete = on_complete

        properties = get_video_properties(input_path)
        if properties is None:
            messagebox.showwarning("Warning", "Failed to retrieve video properties.")
            return

        # A missing/zero duration would make progress tracking divide by zero in
        # the monitor thread (which would then die silently, leaving the UI stuck).
        if not properties.get('duration'):
            messagebox.showwarning(
                "Warning", "Could not determine the video's duration, so it can't be converted.")
            return

        self.disable_ui(interactable_elements)
        cancel_button.config(command=lambda: self.cancel_conversion(
            gui_instance, interactable_elements, cancel_button))
        cancel_button.grid()

        try:
            cmd = self.construct_ffmpeg_command(
                input_path, output_path, gamma, properties, use_gpu,
                tonemapper=tonemapper, quality=quality, quality_mode=quality_mode,
                bit_depth=bit_depth, licensed=licensed
            )
        except Exception:
            # The UI was already disabled and the cancel button gridded above,
            # but self.process hasn't been assigned yet -- Cancel would be a
            # no-op and gui.py's generic error handler doesn't re-enable the
            # UI. Undo both here so the app isn't left permanently disabled,
            # then let the original exception propagate unchanged so callers
            # still see/log/report it exactly as before.
            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()
            raise
        self.process = self.start_ffmpeg_process(cmd)

        thread = threading.Thread(target=self.monitor_progress, args=(
            progress_var, properties['duration'], gui_instance, interactable_elements,
            cancel_button, output_path, open_after_conversion, gamma, tonemapper))
        thread.daemon = True
        thread.start()

    def verify_paths(self, input_path, output_path):
        if not input_path or not output_path:
            messagebox.showwarning(
                "Warning", "Please select both an input file and specify an output file.")
            return False
        if os.path.abspath(input_path) == os.path.abspath(output_path):
            messagebox.showwarning(
                "Warning", "Input and output file cannot be the same.")
            return False
        return True

    def disable_ui(self, elements):
        for element in elements:
            element.config(state="disabled")

    def enable_ui(self, elements):
        for element in elements:
            element.config(state="normal")

    # Hardware H.264 encoders can't do 10-bit at all; their HEVC counterparts
    # can. Also used to preserve an already-HEVC source's codec on GPU (see
    # the "want_hevc" dispatch below), not just the mandatory 10-bit case.
    _HW_HEVC_ENCODER_MAP = {
        'h264_nvenc': 'hevc_nvenc',
        'h264_amf':   'hevc_amf',
        'h264_qsv':   'hevc_qsv',
    }

    def construct_ffmpeg_command(self, input_path, output_path, gamma, properties, use_gpu,
                               tonemapper='reinhard', quality=23, quality_mode='cq',
                               bit_depth=8, licensed=False):
        # No hardware encoder (any vendor/generation) has a 12-bit HEVC profile
        # in its API -- it's a fixed silicon limitation, not a driver gap.
        # 12-bit always forces the full CPU pipeline (tonemap + encode).
        if bit_depth >= 12:
            use_gpu = False

        cmd = [
            FFMPEG_EXECUTABLE,
            '-loglevel', 'info',
        ]
        current_platform = platform.system().lower()

        # GPU tonemapping (libplacebo/Vulkan) is the big win: it offloads the
        # single-threaded CPU tonemap that otherwise bottlenecks the whole
        # pipeline. Gated on the same "GPU acceleration" toggle plus a one-time
        # capability probe; falls back to the CPU tonemap chain when unavailable.
        #
        # Dolby Vision profile 5 (IPTPQc2) is the exception to the toggle: it
        # has no HDR10-compatible base layer, so the zscale chain decodes to
        # wrong colors (green/purple cast). libplacebo applies the DoVi RPU
        # during tonemapping (apply_dovi defaults on), so when it's available
        # it is used for profile 5 even in CPU mode — only the tonemap runs on
        # the GPU; encoding still follows the encoder dispatch below. Profiles
        # 7/8 carry an HDR10-compatible base layer and are already handled
        # correctly by the standard chain.
        dovi_needs_rpu = (properties.get('is_dolby_vision')
                          and properties.get('dovi_profile') == 5)
        use_libplacebo = (use_gpu or dovi_needs_rpu) and vulkan_libplacebo_available()

        # GPU acceleration setup — dispatch on whichever encoder was detected
        active_encoder = None
        if use_gpu:
            if self._gpu_encoder is None:
                self._gpu_encoder = self.detect_gpu_encoder()
            active_encoder = self._gpu_encoder

            if active_encoder == 'h264_nvenc':
                if current_platform in ["windows", "linux"]:
                    if not use_libplacebo:
                        cmd += ['-hwaccel', 'cuda', '-hwaccel_device', '0']
                else:
                    messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                    active_encoder = None
            elif active_encoder == 'h264_qsv':
                if current_platform in ["windows", "linux"]:
                    if not use_libplacebo:
                        cmd += ['-hwaccel', 'qsv']
                else:
                    messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                    active_encoder = None
            elif active_encoder == 'h264_amf':
                if current_platform not in ["windows", "linux"]:
                    messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                    active_encoder = None
                # AMF needs no separate hwaccel flag
            elif active_encoder is not None:
                messagebox.showwarning("Warning", "GPU acceleration is not supported on this platform.")
                active_encoder = None

        # NVIDIA fast path: use CUDA→Vulkan interop so NVDEC handles decode on
        # the GPU and feeds frames directly into libplacebo without a CPU detour.
        # Other vendors (AMF, QSV) don't have CUDA; they fall back to CPU decode.
        use_cuda_interop = (
            use_libplacebo
            and active_encoder == 'h264_nvenc'
            and vulkan_cuda_interop_available()
        )

        # Device args go before -i. Interop path sets up linked cuda+vulkan
        # devices and enables -hwaccel cuda; plain Vulkan path sets up vulkan only.
        if use_cuda_interop:
            cmd += VULKAN_CUDA_DEVICE_ARGS
        elif use_libplacebo:
            cmd += VULKAN_DEVICE_ARGS

        # Input file
        cmd += ['-i', os.path.normpath(input_path)]

        # The filter must be applied before mapping streams
        tonemapper = tonemapper.lower()
        if use_libplacebo:
            filter_str = build_libplacebo_filter(
                gamma, tonemapper, cuda_input=use_cuda_interop)
        else:
            if tonemapper in GPU_ONLY_TONEMAPPERS:
                raise ValueError(
                    f"{tonemapper} requires GPU tonemapping; this item's "
                    "settings force CPU processing — change the tonemapper "
                    "or output bit depth."
                )
            filter_str = FFMPEG_CONVERT_FILTER.format(gamma=gamma, tonemapper=tonemapper)
        cmd += [
            '-filter_complex', f'[0:v:0]{filter_str}[vout]',
            '-map', '[vout]'  # Map the filtered video output
        ]

        # Map remaining streams. Audio is always mapped; subtitle mapping depends
        # on the output container (see _container_stream_args).
        subtitle_map_args, audio_codec_args, subtitle_codec_args = \
            self._container_stream_args(output_path, properties)

        # Dolby Vision audio tier split: Pro keeps the container-aware
        # passthrough decided above (lossless copy wherever the container
        # allows, full multi-channel layout always preserved). Free is
        # explicitly restricted to the first audio stream, downmixed to
        # 2-channel stereo AAC.
        if properties.get('is_dolby_vision') and not licensed:
            audio_map_args = ['-map', '0:a:0?']
            audio_codec_args = list(self._FREE_DOVI_AUDIO_ARGS)
        else:
            audio_map_args = ['-map', '0:a?']   # Map all audio streams if they exist
        cmd += audio_map_args
        cmd += subtitle_map_args

        # Output Color Depth: 10-bit (free) / 12-bit (Pro, CPU-only) avoids the
        # banding that gradient-heavy HDR sources produce once crushed down to
        # 8-bit. Hardware H.264 encoders have no 10-bit mode at all, so they're
        # swapped for their HEVC counterpart, which uses the semi-planar p010le
        # format instead of a 10-bit planar YUV format. 12-bit forces the CPU
        # path (see the use_gpu guard above), so it never reaches this swap.
        codec_name = (properties.get('codec_name') or '').lower()
        source_is_hevc = codec_name == 'hevc'

        if bit_depth >= 12:
            pix_fmt = 'yuv420p12le'
        elif bit_depth == 10:
            pix_fmt = 'yuv420p10le'
        else:
            pix_fmt = 'yuv420p'

        # GPU: H.264 hardware encoders can't do 10-bit at all (mandatory
        # swap); an already-HEVC source also swaps at 8-bit purely to
        # preserve the source codec, since libx264/h264_* could otherwise
        # handle 8-bit just fine.
        if (bit_depth >= 10 or source_is_hevc) and active_encoder in self._HW_HEVC_ENCODER_MAP:
            active_encoder = self._HW_HEVC_ENCODER_MAP[active_encoder]
            if bit_depth == 10:
                pix_fmt = 'p010le'

        # CPU: libx264 tops out at 10-bit, so 12-bit must switch to libx265;
        # an already-HEVC source also switches (preservation) even at 8/10-bit,
        # where libx264 could otherwise still handle the bit depth.
        want_libx265 = bit_depth >= 12 or source_is_hevc

        # Encoding settings. In Constant Quality mode, `quality` is CRF for
        # libx264/5 or CQ/global_quality/QP for the GPU encoders (lower =
        # better). In Target Bitrate mode, `quality` is the user's chosen
        # kbps value; T/maxrate/bufsize are its "target average, capped
        # burst" ffmpeg args, standard on every encoder here (GPU or CPU).
        quality = str(quality)
        target_bv = maxrate = bufsize = None
        bitrate_rc_args: list = []  # type: ignore[type-arg]
        if quality_mode == 'bitrate':
            target_bv = int(quality) * 1000
            maxrate = int(target_bv * 1.5)
            bufsize = target_bv * 2
            bitrate_rc_args = ['-b:v', str(target_bv), '-maxrate', str(maxrate),
                                '-bufsize', str(bufsize)]

        if active_encoder in ('h264_nvenc', 'hevc_nvenc'):
            if quality_mode == 'bitrate':
                cmd += [
                    '-c:v', active_encoder,
                    '-preset', 'p4',
                    '-tune', 'hq',
                    '-rc', 'vbr',
                ] + bitrate_rc_args
            else:
                # MKV containers often report bit_rate=0; fall back to 8 Mbps
                # so nvenc doesn't receive -b:v 0 / -maxrate 0 / -bufsize 0.
                _bv = properties['bit_rate'] or 8_000_000
                cmd += [
                    '-c:v', active_encoder,
                    '-preset', 'p4',
                    '-tune', 'hq',
                    '-rc', 'vbr',
                    '-cq', quality,
                    '-b:v', str(_bv),
                    '-maxrate', str(_bv),
                    '-bufsize', str(_bv * 2)
                ]
        elif active_encoder in ('h264_amf', 'hevc_amf'):
            if quality_mode == 'bitrate':
                cmd += [
                    '-c:v', active_encoder,
                    '-quality', 'balanced',
                    '-rc', 'vbr_peak',
                ] + bitrate_rc_args
            else:
                cmd += [
                    '-c:v', active_encoder,
                    '-quality', 'balanced',
                    '-rc', 'cqp',
                    '-qp_i', quality, '-qp_p', quality, '-qp_b', quality,
                ]
        elif active_encoder in ('h264_qsv', 'hevc_qsv'):
            if quality_mode == 'bitrate':
                cmd += [
                    '-c:v', active_encoder,
                ] + bitrate_rc_args
            else:
                _bv = properties['bit_rate'] or 8_000_000
                cmd += [
                    '-c:v', active_encoder,
                    '-global_quality', quality,
                    '-b:v', str(_bv),
                ]
        elif want_libx265:
            # libx264 tops out at 10-bit -- feeding it 12-bit silently
            # downgrades to yuv420p10le instead of erroring, so 12-bit (and
            # HEVC-source preservation in general) must switch codecs
            # explicitly. libx265 has no 'film' tune (x264's -tune film
            # fails encoder init on x265), so it's omitted here.
            if quality_mode == 'bitrate':
                cmd += [
                    '-c:v', 'libx265',
                    '-preset', 'veryfast',
                ] + bitrate_rc_args
            else:
                cmd += [
                    '-c:v', 'libx265',
                    '-preset', 'veryfast',
                    '-crf', quality,
                ]
        else:
            if quality_mode == 'bitrate':
                cmd += [
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-tune', 'film',
                ] + bitrate_rc_args
            else:
                # No -b:v here: libx264 in CRF (constant-quality) mode ignores
                # a target bitrate, so it was dead weight.
                cmd += [
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-tune', 'film',
                    '-crf', quality,
                ]

        # HEVC in MP4/MOV must be tagged 'hvc1': ffmpeg's default sample entry
        # is 'hev1', which QuickTime/Apple devices (and some Windows players)
        # refuse to recognize even though the stream is fine. Matroska has no
        # such codec tag, so MKV is left alone.
        produces_hevc = (
            active_encoder in self._HW_HEVC_ENCODER_MAP.values()
            or (active_encoder is None and want_libx265)
        )
        out_ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if produces_hevc and out_ext in ('mp4', 'mov'):
            cmd += ['-tag:v', 'hvc1']

        # Common settings
        cmd += [
            '-r', str(properties['frame_rate']),
            '-pix_fmt', pix_fmt,
            '-strict', '-2',
        ]
        cmd += audio_codec_args      # copy, or transcode when container demands
        cmd += subtitle_codec_args   # copy / mov_text / omitted
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

    def validate_bit_depth_output(self, output_path, bit_depth):
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

    # Free-tier audio for Dolby Vision sources: first stream only, forced
    # 2-channel stereo. AAC is safe in every output container we offer
    # (MP4/MOV/MKV), so the downmix never needs container-specific handling.
    _FREE_DOVI_AUDIO_ARGS = ['-c:a', 'aac', '-ac', '2', '-b:a', '192k']

    # Audio/subtitle codecs that the MP4-family containers (.mp4/.m4v/.mov) accept
    # via stream copy. Anything else must be transcoded or dropped.
    _MP4_AUDIO_OK = {'aac', 'ac3', 'eac3', 'mp3', 'alac'}
    _TEXT_SUBTITLES = {'subrip', 'srt', 'ass', 'ssa', 'text', 'mov_text', 'webvtt'}
    _MP4_FAMILY = {'mp4', 'm4v', 'mov'}

    def _container_stream_args(self, output_path, properties):
        """Decide subtitle mapping and audio/subtitle codecs for the output container.

        Prefer lossless stream copy. For MP4-family containers, which can't copy
        TrueHD/DTS audio or ASS/PGS subtitles, fall back to transcoding audio to
        AAC and text subtitles to mov_text, and drop image subtitles (e.g. PGS)
        that no MP4 codec can represent. Non-MP4 containers (notably MKV) keep the
        original copy-everything behavior.

        Returns ``(subtitle_map_args, audio_codec_args, subtitle_codec_args)``.
        """
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if ext not in self._MP4_FAMILY:
            # MKV and friends accept the source streams as-is.
            return (['-map', '0:s?'], ['-c:a', 'copy'], ['-c:s', 'copy'])

        audio_codec = (properties.get('audio_codec') or '').lower()
        if audio_codec and audio_codec not in self._MP4_AUDIO_OK:
            bit_rate = properties.get('audio_bit_rate') or 0
            target_rate = str(min(int(bit_rate), 384000)) if bit_rate else '192k'
            audio_codec_args = ['-c:a', 'aac', '-b:a', target_rate]
        else:
            audio_codec_args = ['-c:a', 'copy']

        # Map only subtitle streams MP4 can hold (text); drop image subs entirely.
        subtitle_map_args = []
        for stream in properties.get('subtitle_streams', []):
            if (stream.get('codec_name') or '').lower() in self._TEXT_SUBTITLES:
                subtitle_map_args += ['-map', f"0:{stream['index']}"]
        subtitle_codec_args = ['-c:s', 'mov_text'] if subtitle_map_args else []

        return (subtitle_map_args, audio_codec_args, subtitle_codec_args)

    def start_ffmpeg_process(self, cmd):
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

    def monitor_progress(self, progress_var, duration, gui_instance, interactable_elements,
                         cancel_button, output_path, open_after_conversion, gamma,
                         tonemapper='reinhard'):
        progress_pattern = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
        error_messages = []
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
                gui_instance.root.after(0, lambda p=progress: progress_var.set(p))
                gui_instance.root.after(0, gui_instance.root.update_idletasks)

            if any(k in decoded_line.lower() for k in ('cuda', 'nvcuda.dll', 'amf', 'mfx')):
                gpu_error_detected = True

        if proc is not None:
            proc.wait()
            returncode = proc.returncode
            if returncode != 0 and self.use_gpu and gpu_error_detected and not self.cancelled:
                logging.warning("GPU acceleration failed. Retrying with CPU encoding.")
                # The retry touches Tk (gpu checkbox, dialog, UI state) and must run
                # on the main thread, not this worker thread.
                gui_instance.root.after(0, lambda: self._retry_with_cpu(
                    gui_instance, interactable_elements, cancel_button, progress_var,
                    open_after_conversion, gamma, tonemapper))
            else:
                self.handle_completion(gui_instance, interactable_elements, cancel_button,
                                    output_path, open_after_conversion, error_messages)

    def _retry_with_cpu(self, gui_instance, interactable_elements, cancel_button,
                        progress_var, open_after_conversion, gamma, tonemapper):
        """Restart the conversion on the CPU after a GPU failure. Runs on the main thread."""
        gui_instance.gpu_accel_var.set(False)
        # A raw Variable.set() doesn't fire the checkbox's command= callback
        # (check_gpu_acceleration), which is normally what persists a GPU
        # toggle onto the current batch item's stored settings -- without
        # this, reselecting the item later would restore the stale,
        # pre-failure gpu_accel=True.
        gui_instance._write_back_current_settings()
        messagebox.showwarning("GPU Acceleration Failed",
                               "GPU acceleration failed. Switching to CPU encoding.")
        self.start_conversion(
            input_path=gui_instance.input_path_var.get(),
            output_path=gui_instance.output_path_var.get(),
            gamma=gamma,
            use_gpu=False,  # Force CPU encoding
            progress_var=progress_var,
            interactable_elements=interactable_elements,
            gui_instance=gui_instance,
            open_after_conversion=open_after_conversion,
            cancel_button=cancel_button,
            tonemapper=tonemapper,
            quality=getattr(self, '_quality', 23),
            quality_mode=getattr(self, '_quality_mode', 'cq'),
            on_complete=getattr(self, '_on_complete', None),
            bit_depth=getattr(self, '_bit_depth', 8),
            licensed=getattr(self, '_licensed', False),
        )

    def parse_time(self, time_str):
        hours, minutes, seconds = map(float, time_str.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    def handle_completion(self, gui_instance, interactable_elements, cancel_button,
                          output_path, open_after_conversion, error_messages):
        def _handle():
            # Snapshot the exit code once (the process may be None on a bare/mock
            # instance); None is treated as "not success" everywhere below.
            returncode = self.process.returncode if self.process is not None else None
            on_complete = getattr(self, '_on_complete', None)
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
                messagebox.showinfo(
                    "Success", f"Conversion complete! Output saved to: {output_path}")
                if open_after_conversion:
                    webbrowser.open(output_path)
            elif not self.cancelled:
                tail = error_messages[-50:]  # ffmpeg stderr can be thousands of progress lines; show only the tail where real errors appear
                error_message = '\n'.join(tail)
                logging.error(f"Conversion failed with code {returncode}: {error_message}")
                messagebox.showerror(
                    "Error", f"Conversion failed with code {returncode}\n{error_message}")

            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

        gui_instance.root.after(0, _handle)

    def cancel_conversion(self, gui_instance, interactable_elements, cancel_button):
        self.cancelled = True
        if self.process:
            self.process.terminate()
            self.process = None
            gui_instance.root.after(0, lambda: messagebox.showinfo(
                "Cancelled", "Video conversion has been cancelled."))
            self.enable_ui(interactable_elements)
            cancel_button.grid_remove()

            if hasattr(gui_instance, 'register_drop_target'):
                gui_instance.register_drop_target()

    def _nvidia_present(self):
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

    def _list_encoders(self):
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

    def detect_gpu_encoder(self):
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

    def is_gpu_available(self):
        try:
            return self.detect_gpu_encoder() is not None
        except Exception as e:
            logging.error(f"Error checking GPU availability: {e}")
            return False

    def is_gpu_acceleration_available(self):
        """True if any GPU acceleration is usable: a hardware H.264 encoder
        (nvenc/amf/qsv) and/or GPU tonemapping via libplacebo. Either one alone
        makes the GPU toggle worthwhile -- a machine with Vulkan/libplacebo but
        no hardware encoder still gets the (bigger) tonemapping speedup -- so the
        toggle is gated on the union, not on the encoder alone."""
        return self.is_gpu_available() or vulkan_libplacebo_available()

conversion_manager = ConversionManager()