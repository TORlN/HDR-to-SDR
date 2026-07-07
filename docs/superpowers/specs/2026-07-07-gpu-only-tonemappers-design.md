# GPU-Only Tonemappers (BT.2390 + Spline) — Design

> Supersedes `2026-07-07-bt2390-tonemapper-design.md`. That spec assumed
> zscale's `tonemap` filter supports a `bt2390` value. Verified against the
> real bundled ffmpeg (`ffmpeg -h filter=tonemap`) that it does not — zscale
> only ever supported `none/linear/gamma/clip/reinhard/hable/mobius`.
> `libplacebo`'s `tonemapping=` option does support `bt.2390` and `spline`
> (confirmed via `ffmpeg -h filter=libplacebo`). All uncommitted code written
> against the old spec was discarded. This spec replaces it and folds in
> Spline, previously deferred, since both tonemappers need identical
> treatment.

## Problem

The app offers three tonemappers today (Reinhard, Mobius, Hable), which work
identically on both conversion paths:

- CPU path: `zscale`'s `tonemap=<name>` option — used for preview always, and
  for main conversion whenever GPU tonemapping isn't active (GPU toggle off,
  12-bit output, or the libplacebo/Vulkan capability probe failing).
- GPU path: `libplacebo`'s `tonemapping=<name>` option — main conversion only.

BT.2390 (ITU-R EETF, better highlight rolloff) and Spline (libplacebo's
scene-adaptive default) are both genuinely better tonemappers than the
existing three, but **neither has a zscale/CPU implementation** — confirmed
against the real ffmpeg build, not assumed. They only exist via `libplacebo`.

## Goal

Add both as selectable tonemappers, available only when GPU tonemapping is
actually active, with preview that renders the true algorithm (not an
approximation) whenever they're selectable at all, and a clear failure
instead of a crash for the one situation where a selectable choice still
can't run on GPU (per-item settings forcing CPU after the fact).

## Design

### 1. `utils.py`

- `TONEMAP = ["Reinhard", "Mobius", "Hable", "BT.2390", "Spline"]`
- New constant:

  ```python
  GPU_ONLY_TONEMAPPERS = {'bt.2390', 'spline'}
  ```

  Lowercase, matching libplacebo's own spelling exactly — no CPU alias is
  needed or possible for these two.

- New GPU-preview extraction functions, one single-frame and one
  multi-position (mirroring `extract_frame_with_conversion` /
  `extract_frames_with_conversion_batch`, but building a libplacebo filter
  chain instead of a zscale one). Both reuse the existing
  `build_libplacebo_filter(gamma, tonemapper, width=, height=, cuda_input=False)`
  — it's already parameterized for exactly this — and prepend
  `VULKAN_DEVICE_ARGS` before `-i`, the same way `construct_ffmpeg_command`
  already does for the main conversion's plain-Vulkan path. Deliberately use
  the plain-Vulkan (CPU-decode) path, not the NVIDIA CUDA-interop fast path:
  interop optimizes full-length encodes, not single preview frames, and
  adding it here is unjustified complexity for this use case.
- The multi-position preview variant does NOT attempt the CPU batch
  function's "N frames in one ffmpeg process" trick — it loops the
  single-frame GPU extraction N times. Building a shared-Vulkan-device
  multi-input filter graph is materially more complex and not worth it for
  this narrower, heavier-weight use case (YAGNI).

### 2. `preview.py`

- Preview dispatch branches on `tonemapper.lower() in GPU_ONLY_TONEMAPPERS`:
  - True → the new GPU extraction path.
  - False (Reinhard/Mobius/Hable, unchanged) → the existing CPU/zscale path,
    untouched, preserving the documented preview-performance
    characteristics for those three.
- This is the only way preview can be an exact match for these two
  algorithms: there is no CPU approximation involved anywhere in this
  design.

### 3. `gui.py`

- New `_apply_tonemap_choices()` method (same shape as the existing
  `_apply_quality_range()`): sets the tonemapper combobox's `values` to
  the full `TONEMAP` list when `gpu_accel_var.get() and
  vulkan_libplacebo_available()`, otherwise to `TONEMAP` minus the two
  GPU-only entries. Called at startup and whenever the GPU checkbox is
  toggled (`check_gpu_acceleration`). If BT.2390 or Spline was selected and
  becomes unavailable, resets the selection to Mobius.
- Tooltip text gets `(GPU Only)` appended to both new lines.

### 4. `conversion.py` — the safety net

Bit depth is chosen per queued batch item, independently of the single
global tonemapper setting, and 12-bit output always forces the CPU path
regardless of the GPU toggle (existing, unrelated behavior). This means a
GPU-only tonemapper can be validly selected (GPU on, probe passed) and still
end up routed to the CPU branch for one specific item.

In `construct_ffmpeg_command`, immediately before building the CPU/zscale
filter string, check:

```python
if tonemapper in GPU_ONLY_TONEMAPPERS:
    raise ValueError(
        f"{tonemapper} requires GPU tonemapping; this item's settings "
        "force CPU processing — change the tonemapper or output bit depth."
    )
```

This raises instead of silently building an invalid `tonemap=bt.2390` /
`tonemap=spline` zscale filter string (which is what would happen without
this check — confirmed experimentally: real ffmpeg errors with "Unable to
parse tonemap option value"). The exception surfaces through existing,
unmodified error handling: a single-file conversion already catches and
message-boxes exceptions from this method; a batch item already marks
itself `Failed` with the exception's message on an unhandled error. No new
UI plumbing is needed for the error path itself.

## Testing (new logic only — no bloat)

1. GPU preview extraction (single-frame): command contains `libplacebo`,
   `tonemapping=bt.2390` (or `spline`), and the Vulkan device args.
2. GPU preview extraction (multi-position): same assertions, confirms N
   separate invocations rather than one batched process.
3. `construct_ffmpeg_command`: raises `ValueError` with the expected message
   when the CPU branch would run with a `GPU_ONLY_TONEMAPPERS` entry
   selected (simulate via 12-bit bit depth forcing `use_gpu=False`).
4. `construct_ffmpeg_command`: unchanged GPU-path behavior still produces
   `tonemapping=bt.2390` / `tonemapping=spline` when GPU is actually active
   (regression guard, same spirit as the original spec's Task 3).
5. GUI gating: combobox values include/exclude BT.2390 and Spline based on
   `gpu_accel_var` + `vulkan_libplacebo_available()`; selection resets to
   Mobius when a selected GPU-only entry becomes unavailable.
6. Batch: an item with 12-bit forced and a GPU-only tonemapper selected ends
   up `Failed` with the expected message, and the batch continues to the
   next item rather than halting.

No test for the `TONEMAP`/`GPU_ONLY_TONEMAPPERS` constants themselves
(trivial, covered transitively by the GUI gating test and the existing
`gui_integration_test.py` combobox assertion) or the tooltip text (no
logic).

## Out of scope

- CUDA-interop fast path for GPU preview (plain Vulkan is enough for single
  frames).
- Batched multi-input Vulkan preview extraction (loop single-frame instead).
- Any CPU approximation/substitution for BT.2390 or Spline anywhere —
  superseded by making both strictly GPU-only with a real GPU preview path.
