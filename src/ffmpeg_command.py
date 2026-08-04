"""Decide the ffmpeg command for one conversion (audit item 6).

Split out of ConversionManager.construct_ffmpeg_command, which was a
253-line, complexity-45 function doing six separable jobs. Pure functions
only -- no tkinter, no gui, no conversion.py -- so each is testable with no
ConversionView or test double. build() (added in Task 8) is the one impure
piece: it owns the ConversionView and decides notice-emission order.

Three sharp edges, each documented at the code that resolves them:
  - HEVC-swap ordering: active_encoder can be reassigned by the H.264->HEVC
    preservation swap. CodecPlan.produces_hevc (Task 6) is computed at the
    point of the swap so no caller has to reconstruct it from a
    possibly-stale value.
  - GPU-probe laziness: a 12-bit request forces use_gpu off before any GPU
    vendor probe would run, so resolve_gpu_encoder (Task 3) must be a lazy
    callable, not a pre-resolved value -- calling it unconditionally would
    add a probe subprocess call on a path that has none today.
  - This module never imports vulkan_libplacebo_available or
    vulkan_cuda_interop_available from utils, even though it is the only
    code that calls them. Both are always parameters (see Probes, added in
    Task 8). Two reasons, not one: it preserves their laziness exactly like
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

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from conversion_view import Notice


class RequestLike(Protocol):
    """The subset of ConversionRequest this module needs, described
    structurally so this module never imports conversion.py -- conversion.py
    imports this module (Task 8), and the layer table forbids a cycle.
    ConversionRequest, being a plain dataclass with matching attribute
    names, satisfies this automatically.

    Declared as read-only @property members, not plain annotations: plain
    Protocol attributes are structurally writable, but ConversionRequest is
    a frozen dataclass (no setter) -- pyright flags that mismatch as a
    reportArgumentType error at the one real call site (conversion.py's
    `ffmpeg_command._tonemap_plan(request, ...)`) otherwise."""
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
    function that reaches a real ffmpeg subprocess -- see this task's
    file-level note for why that matters for the existing test suite's
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
