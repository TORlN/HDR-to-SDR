"""Pure output-resolution catalog, sizing, validation, and naming helpers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolutionPreset:
    """A supported standard output target, expressed by its short edge."""

    label: str
    filename_label: str
    short_edge: int


@dataclass(frozen=True)
class ResolutionTarget:
    """A requested short-edge target, optionally supplied as a custom value."""

    short_edge: int
    custom: bool = False


PRESETS = (
    ResolutionPreset('8K', '8k', 4320),
    ResolutionPreset('4K', '4k', 2160),
    ResolutionPreset('1440p', '1440p', 1440),
    ResolutionPreset('1080p', '1080p', 1080),
    ResolutionPreset('720p', '720p', 720),
    ResolutionPreset('480p', '480p', 480),
)


def _even_floor(value: int) -> int:
    """Return the nearest usable even pixel count at or below ``value``."""
    return max(2, value - value % 2)


def _validate_source_dimensions(source_width: int, source_height: int) -> None:
    if source_width <= 0 or source_height <= 0:
        raise ValueError('Source dimensions must be positive integers.')


def preset_for_short_edge(short_edge: int) -> ResolutionPreset | None:
    """Return the standard preset with ``short_edge``, if one exists."""
    return next(
        (preset for preset in PRESETS if preset.short_edge == short_edge),
        None,
    )


def output_dimensions(
    source_width: int,
    source_height: int,
    target: ResolutionTarget | None,
) -> tuple[int, int] | None:
    """Return normalized output dimensions, or ``None`` for source size."""
    _validate_source_dimensions(source_width, source_height)
    if target is None:
        return None
    if target.short_edge <= 0:
        raise ValueError('Custom short edge must be a positive integer.')

    source_short = min(source_width, source_height)
    target_short = _even_floor(target.short_edge)
    if target_short == source_short:
        return None

    scale = target_short / source_short
    return (
        _even_floor(int(source_width * scale)),
        _even_floor(int(source_height * scale)),
    )


def validate_target(
    source_width: int,
    source_height: int,
    target: ResolutionTarget | None,
    licensed: bool,
) -> None:
    """Raise ``ValueError`` when a target is structurally or tier-invalid."""
    _validate_source_dimensions(source_width, source_height)
    if target is None:
        return
    if target.short_edge <= 0:
        raise ValueError('Custom short edge must be a positive integer.')
    if target.custom:
        if not licensed:
            raise ValueError('Custom resolution and upscaling require Pro.')
        return

    if preset_for_short_edge(target.short_edge) is None:
        raise ValueError('Unknown resolution preset.')
    if not licensed and target.short_edge >= min(source_width, source_height):
        raise ValueError('Custom resolution and upscaling require Pro.')


def output_suffix(
    source_width: int,
    source_height: int,
    target: ResolutionTarget | None,
) -> str | None:
    """Return the automatic filename suffix for a normalized target."""
    dimensions = output_dimensions(source_width, source_height, target)
    if dimensions is None or target is None:
        return None
    if target.custom:
        return f'{dimensions[0]}x{dimensions[1]}'

    preset = preset_for_short_edge(target.short_edge)
    if preset is None:
        raise ValueError('Unknown resolution preset.')
    return preset.filename_label
