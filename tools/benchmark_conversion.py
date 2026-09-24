"""Local CPU and GPU conversion throughput benchmark."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
for _directory in (REPO_ROOT, REPO_ROOT / 'src'):
    _directory_text = str(_directory)
    if _directory_text not in sys.path:
        sys.path.insert(0, _directory_text)

from conversion_view import ConversionView, Notice  # noqa: E402
from src.conversion import ConversionManager, ConversionRequest  # noqa: E402
from utils import get_video_properties  # noqa: E402


MODE_SETTINGS: dict[str, tuple[bool, bool]] = {
    'CPU accurate': (False, True),
    'GPU accurate': (True, True),
    'GPU fast': (True, False),
}
FIXTURE_PATH = str(REPO_ROOT / 'test' / 'smoke_test_videos' / 'hdr10_10bit.mp4')
PROGRESS_POLL_INTERVAL = 0.1
GRACEFUL_STOP_TIMEOUT = 5.0
TERMINATE_TIMEOUT = 5.0


@dataclass(frozen=True)
class RunResult:
    status: str
    frames: int
    duration: float
    diagnostic: str | None = None

    @property
    def fps(self) -> float:
        return self.frames / self.duration if self.duration > 0 else 0.0


class _HeadlessView:
    """Record command-builder notices without creating any Tk widgets."""

    on_complete: Callable[[bool, str | None], None] | None = None

    def __init__(self) -> None:
        self.notices: list[Notice] = []

    def notify(self, notice: Notice) -> None:
        self.notices.append(notice)

    def schedule(self, fn: Callable[[], None]) -> None:
        pass

    def set_progress(self, pct: float) -> None:
        pass

    def set_inputs_enabled(self, enabled: bool) -> None:
        pass

    def set_cancel_visible(self, visible: bool,
                           on_cancel: Callable[[], None] | None = None) -> None:
        pass

    def restore_drop_target(self) -> None:
        pass

    def open_output(self, path: str) -> None:
        pass


def build_request(mode: str, input_path: str,
                  output_path: str) -> ConversionRequest:
    try:
        use_gpu, lut_enabled = MODE_SETTINGS[mode]
    except KeyError as error:
        raise ValueError(f'Unknown benchmark mode: {mode}') from error

    return ConversionRequest(
        input_path=input_path,
        output_path=output_path,
        gamma=1.0,
        use_gpu=use_gpu,
        open_after_conversion=False,
        tonemapper='reinhard',
        quality=23,
        quality_mode='cq',
        bit_depth=10,
        licensed=False,
        lut_enabled=lut_enabled,
        resolution=None,
    )


def _ffmpeg_file_url(path: str) -> str:
    """Build the file URL format accepted by the bundled Windows FFmpeg."""
    return f'file:{Path(path).resolve().as_posix()}'


def build_command(mode: str, input_path: str, output_path: str,
                  progress_path: str, manager: ConversionManager,
                  view: ConversionView) -> list[str]:
    properties = get_video_properties(input_path)
    if properties is None:
        raise ValueError(f'Could not read video properties for {input_path}')

    request = build_request(mode, input_path, output_path)
    command = manager.construct_ffmpeg_command(request, properties, view)

    input_index = command.index('-i')
    command[input_index:input_index] = ['-stream_loop', '-1']
    command[1:1] = [
        '-stats_period', '0.1',
        '-progress', _ffmpeg_file_url(progress_path),
    ]

    try:
        movflags_index = command.index('-movflags')
    except ValueError:
        pass
    else:
        if command[movflags_index + 1] == '+faststart':
            del command[movflags_index:movflags_index + 2]

    output_index = command.index(os.path.normpath(output_path), command.index('-i') + 2)
    command[output_index:output_index + 1] = ['-f', 'null', '-']
    return command


def validate_mode_command(mode: str, command: list[str]) -> str | None:
    settings = MODE_SETTINGS.get(mode)
    if settings is None:
        return f'Unknown benchmark mode: {mode}'

    try:
        filter_graph = command[command.index('-filter_complex') + 1]
        encoder = command[command.index('-c:v') + 1]
        null_muxer = command[command.index('-f') + 1:command.index('-f') + 3]
    except (ValueError, IndexError):
        return 'FFmpeg command is missing a required conversion argument'

    if null_muxer != ['null', '-']:
        return 'FFmpeg command does not use the null output muxer'

    use_gpu, lut_enabled = settings
    uses_libplacebo = 'libplacebo=' in filter_graph
    uses_lut = 'lut3d=file=' in filter_graph
    if not use_gpu:
        if uses_libplacebo or 'zscale=t=linear' not in filter_graph or not uses_lut:
            return 'CPU accurate mode did not build the CPU tonemap and LUT path'
        if encoder not in ('libx264', 'libx265'):
            return f'CPU accurate mode selected a non-CPU encoder: {encoder}'
    else:
        if not uses_libplacebo:
            return 'GPU tonemapping unavailable: command resolved to the CPU filter path'
        if uses_lut != lut_enabled:
            expected = 'with' if lut_enabled else 'without'
            return f'{mode} did not build the GPU color path {expected} the LUT'
    return None


def read_progress(path: Path, offset: int, last_frame: int) -> tuple[int, int]:
    """Read complete appended FFmpeg progress records without consuming a partial line."""
    with path.open('rb') as progress_file:
        progress_file.seek(offset)
        data = progress_file.read()

    complete_end = data.rfind(b'\n')
    if complete_end < 0:
        return offset, last_frame

    complete_records = data[:complete_end + 1]
    frame_count = last_frame
    for line in complete_records.splitlines():
        key, separator, value = line.partition(b'=')
        if key != b'frame' or not separator:
            continue
        try:
            parsed = int(value)
        except ValueError:
            continue
        if parsed >= frame_count:
            frame_count = parsed

    return offset + len(complete_records), frame_count


def _new_run_file(temp_dir: Path, prefix: str) -> Path:
    with tempfile.NamedTemporaryFile(prefix=prefix, dir=temp_dir, delete=False) as stream:
        return Path(stream.name)


def _stop_process(process: subprocess.Popen[bytes]) -> tuple[int | None, str | None]:
    forced_stop = None
    try:
        if process.stdin is not None:
            process.stdin.write(b'q\n')
            process.stdin.flush()
            process.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass

    try:
        return process.wait(timeout=GRACEFUL_STOP_TIMEOUT), forced_stop
    except subprocess.TimeoutExpired:
        forced_stop = 'terminate'
        process.terminate()

    try:
        return process.wait(timeout=TERMINATE_TIMEOUT), forced_stop
    except subprocess.TimeoutExpired:
        forced_stop = 'kill'
        process.kill()
        return process.wait(), forced_stop


def run_timed(command: list[str], duration: float,
              temp_dir: Path) -> RunResult:
    """Measure one FFmpeg run, excluding shutdown and reaping time."""
    if duration <= 0:
        return RunResult('FAILED', 0, 0.0, 'Run duration must be positive')
    try:
        progress_index = command.index('-progress') + 1
    except ValueError:
        return RunResult('FAILED', 0, 0.0, 'FFmpeg command has no progress output')
    if progress_index >= len(command):
        return RunResult('FAILED', 0, 0.0, 'FFmpeg progress output path is missing')

    progress_path = None
    diagnostic_path = None
    diagnostic_file = None
    process = None
    measured_frames = 0
    measured_duration = 0.0
    status = 'FAILED'
    diagnostic = None
    reaped = False

    try:
        progress_path = _new_run_file(temp_dir, 'benchmark-progress-')
        diagnostic_path = _new_run_file(temp_dir, 'benchmark-stderr-')
        diagnostic_file = diagnostic_path.open('wb')
        run_command = list(command)
        run_command[progress_index] = _ffmpeg_file_url(str(progress_path))
        try:
            process = subprocess.Popen(
                run_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=diagnostic_file,
            )
        except OSError as error:
            diagnostic = f'Could not start FFmpeg: {error}'
            return RunResult(status, 0, 0.0, diagnostic)

        started_at = time.perf_counter()
        offset = 0
        while True:
            now = time.perf_counter()
            elapsed = now - started_at
            if elapsed >= duration:
                measured_duration = elapsed
                if process.poll() is not None:
                    process.wait()
                    reaped = True
                    diagnostic = 'FFmpeg exited before the measurement cutoff'
                else:
                    return_code, forced_stop = _stop_process(process)
                    reaped = True
                    if measured_frames == 0:
                        diagnostic = 'FFmpeg completed zero frames'
                    elif forced_stop is not None:
                        status = 'SUCCESSFUL'
                        diagnostic = f'FFmpeg required {forced_stop} after the cutoff'
                    elif return_code == 0:
                        status = 'SUCCESSFUL'
                    else:
                        diagnostic = f'FFmpeg exited with status {return_code} after the cutoff'
                break

            offset, measured_frames = read_progress(
                progress_path, offset, measured_frames)
            if process.poll() is not None:
                process.wait()
                reaped = True
                measured_duration = elapsed
                diagnostic = 'FFmpeg exited before the measurement cutoff'
                break

            time.sleep(min(PROGRESS_POLL_INTERVAL, duration - elapsed))

    except (OSError, subprocess.SubprocessError) as error:
        diagnostic = f'FFmpeg run failed: {error}'
    finally:
        if process is not None and not reaped:
            try:
                if process.poll() is None:
                    _, forced_stop = _stop_process(process)
                    if forced_stop is not None and diagnostic is None:
                        diagnostic = f'FFmpeg required {forced_stop} during cleanup'
                else:
                    process.wait()
                reaped = True
            except (OSError, subprocess.SubprocessError) as error:
                diagnostic = f'{diagnostic or "FFmpeg cleanup failed"}: {error}'
        if diagnostic_file is not None:
            diagnostic_file.close()
        if diagnostic_path is not None:
            try:
                stderr_text = diagnostic_path.read_text(
                    encoding='utf-8', errors='replace').strip()
                if stderr_text:
                    stderr_text = stderr_text[-2000:]
                    diagnostic = '\n'.join(
                        part for part in (diagnostic, stderr_text) if part)
            except OSError:
                pass
        if process is None or reaped:
            for path in (progress_path, diagnostic_path):
                if path is not None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass

    if measured_frames == 0 and status == 'SUCCESSFUL':
        status = 'FAILED'
        diagnostic = diagnostic or 'FFmpeg completed zero frames'
    return RunResult(status, measured_frames, measured_duration, diagnostic)
