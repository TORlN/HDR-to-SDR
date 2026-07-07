# BT.2390 Tonemapper — Design

> **SUPERSEDED** by `2026-07-07-gpu-only-tonemappers-design.md`. This spec
> incorrectly assumed zscale's `tonemap` filter supports a `bt2390` value;
> verified against the real ffmpeg build that it does not. Kept for history
> only — do not implement from this file.

## Problem

The app currently offers three tonemappers (Reinhard, Mobius, Hable), driven by a
single `tonemapper` string that's passed, lowercased, straight into both filter
chains:

- CPU path: `zscale`'s `tonemap=<name>` option (used for preview always, and for
  main conversion whenever GPU tonemapping isn't active).
- GPU path: `libplacebo`'s `tonemapping=<name>` option (main conversion only,
  never used for preview).

This works today only because all three existing names are spelled identically
in both filters. BT.2390 (the ITU-R EETF, a broadcast-standard curve with
better highlight rolloff than the existing three) breaks that assumption:
`zscale` spells it `bt2390`, `libplacebo` spells it `bt.2390`.

A second candidate discussed (libplacebo's `spline` tonemapper) has no `zscale`
equivalent at all and is deferred to a future pass — it needs GPU-availability
gating and a decision about preview fallback that's out of scope here.

## Goal

Add BT.2390 as a fourth selectable tonemapper. It must work identically well on
the CPU and GPU conversion paths, and preview must render the true BT.2390
curve (no approximation/substitution) since preview accuracy is a priority.

## Design

### 1. `utils.py`

- Add `"BT.2390"` to the `TONEMAP` list (drives the GUI combobox).
- Add an alias map for the one place CPU and GPU spellings diverge:

  ```python
  _ZSCALE_TONEMAP_ALIASES = {'bt.2390': 'bt2390'}
  ```

- Apply the alias (after lowercasing) at both CPU/`zscale` filter-building call
  sites: `extract_frame_with_conversion` and
  `extract_frames_with_conversion_batch`. These are the preview code paths —
  preview always uses `zscale`, never `libplacebo`, so this is also what makes
  preview render the true curve.
- `build_libplacebo_filter` needs no change: it already lowercases the
  tonemapper string, and `"bt.2390"` (with the dot) is libplacebo's real
  spelling.

### 2. `conversion.py`

- In `construct_ffmpeg_command`, apply the same alias when building the CPU
  path's filter string (the `else` branch that formats
  `FFMPEG_CONVERT_FILTER`). The GPU/`libplacebo` branch is untouched.

### 3. `gui.py`

- Add one line to the tonemapper tooltip describing BT.2390.
- No availability gating: BT.2390 works unconditionally on both CPU and GPU
  paths, unlike the deferred Spline case. The existing
  `gui_integration_test.py` assertion that the combobox's values equal
  `tuple(TONEMAP)` will pick up the new entry automatically — no test change
  needed there.

### Preview accuracy note

Because BT.2390 has a real, non-approximated `zscale` implementation, preview
and CPU-path conversion both run the true curve — no substitution logic is
introduced. If a given conversion happens to run on the GPU path instead,
preview (always CPU) and the final GPU output are two different
implementations of the same standard curve and could differ slightly at the
pixel level — this is pre-existing behavior common to all four tonemappers
today, not a gap introduced by this change, and is out of scope to fix here.

## Testing (TDD, minimal — no bloat)

Three new tests, written before the corresponding code change:

1. `conversion_test.py`: CPU-path filter string contains `tonemap=bt2390` when
   `tonemapper='BT.2390'` (extends the existing pattern used for the Hable GPU
   passthrough test).
2. `conversion_test.py`: GPU-path filter string contains `tonemapping=bt.2390`
   when `tonemapper='BT.2390'` (mirrors the existing Hable GPU test).
3. `utils_test.py`: preview extraction (`extract_frame_with_conversion` or the
   batch variant) applies the same alias, producing `tonemap=bt2390` in the
   filter — this is the test that guards preview accuracy specifically.

No new test for the `TONEMAP` list addition (covered transitively by the
existing GUI combobox assertion) or the tooltip text change (not logic worth
asserting on).

## Out of scope

- Spline tonemapper (deferred to a future pass: needs GPU-availability gating
  in the GUI and a decision on preview behavior since it has no CPU
  equivalent).
- Reconciling CPU-preview vs GPU-conversion pixel differences for any
  tonemapper (pre-existing, not introduced here).
