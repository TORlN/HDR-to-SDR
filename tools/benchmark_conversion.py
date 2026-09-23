"""Local CPU and GPU conversion throughput benchmark."""
from __future__ import annotations

import os
import sys
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
