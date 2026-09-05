# FFmpeg source patches

Patches applied to the bundled ffmpeg's source before it's built via
[media-autobuild_suite](https://github.com/m-ab-s/media-autobuild_suite)
(MABS), via `ffmpeg_extra.sh`'s `_pre_configure()` hook -- MABS's
documented customization point, sourced automatically right after ffmpeg's
git checkout/update and before `./configure`, so it survives every
`ffmpegUpdate=y` rebuild.

`ffmpeg_extra.sh` in this folder is the source of truth. MABS actually runs
a copy at `C:\MABS\build\ffmpeg_extra.sh`. If MABS is ever reinstalled from
scratch, or you're not sure the local copy matches, just copy this folder's
`ffmpeg_extra.sh` over it.

**Do not also drop a copy inside the `ffmpeg-git` checkout itself** (e.g.
`C:\MABS\build\ffmpeg-git\ffmpeg_extra.sh`). An earlier version of this doc
recommended exactly that as a "hedge," reasoning it was ambiguous which cwd
MABS's `[[ -f ffmpeg_extra.sh ]]` check ran from. It turned out to be the
opposite of a hedge: `media-suite_compile.sh` sources
`$LOCALBUILDDIR/ffmpeg_extra.sh` correctly (via `check_custom_patches`,
called from `do_vcs`) *and then separately* does a second, cwd-relative
`source ffmpeg_extra.sh` right after (`media-suite_compile.sh` around the
ffmpeg block). Since `source` redefines functions, a stale copy sitting in
the checkout silently wins over the correct one -- which is exactly what
happened: `compile.log` from the 2026-08-29 rebuild shows the *first*
ffmpeg build pass in the run using the old, broken `patch -p1` version (the
stale in-checkout copy, sourced last) while the *second* pass -- the one
that actually produces `bin-video/ffmpeg.exe` -- used the correct sed
version, only because the stale copy happened to be gone by then. Keep
exactly one copy of this file, at `C:\MABS\build\ffmpeg_extra.sh`, and
nowhere inside `ffmpeg-git/`.

We use `sed` against a distinctive line's exact content, not a positional
unified diff (`git diff` / `patch -p1`) -- a diff broke within days here
because ffmpeg-git master keeps reformatting the *conditions* that guard
these usage-flag assignments, even though the assignment lines themselves
stay put. `grep`-check before editing and `sed -i` make each patch
idempotent (a no-op if already applied), so re-running `_pre_configure`
across rebuilds is always safe.

## vulkan-video-encode-src

Neutralizes `hwcontext_vulkan.c`'s opportunistic
`VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR` usage-add (upstream commits
`e41e21a1ec`/`eea648ef1d`). On at least this dev's NVIDIA driver (610.88),
the format capability query reports `VIDEO_ENCODE_SRC` support for `rgba`
that the actual memory-type query then can't back, breaking
`vulkan_pool_alloc` (`"No memory type found for flags 0x1"`) for every
Vulkan-uploaded rgba frame -- hit via the GPU-only tonemappers
(BT.2390/Spline), which end their libplacebo filter chain in
`format=rgba,hwdownload` before `lut3d`. This app never uses ffmpeg's
native Vulkan video encoders, so the capability is pure downside here.
See [[vulkan-interop-broken-after-mabs-rebuild]] in project memory.

**Update:** disproven as the actual cause of the crash below (kept anyway,
still correct to disable) -- see `vulkan-host-transfer`.

First attempt (2026-08-29, a positional `patch -p1` against a captured
`git diff`) failed on the very next rebuild: ffmpeg-git master had already
reworded the surrounding `if` conditions (moved ~288 lines, changed the
guard logic, even reverted to an older variant of it), so the diff's
context no longer matched anywhere. Replaced with the `sed`-based approach
in `ffmpeg_extra.sh` above, which only cares about the one line whose
*effect* matters.

Second attempt (same day): the sed version's own success message
(`"neutralized VIDEO_ENCODE_SRC_BIT_KHR usage-add"`) appeared in
`compile.log` immediately before the `configure`/`make`/`install` sequence
that produced the final `bin-video/ffmpeg.exe` -- yet the resulting binary
still failed the Vulkan rgba repro identically, and a naive post-build grep
for `VIDEO_ENCODE_SRC_BIT_KHR` looked unpatched. Root-caused the *dispatch*
half of that to the clobbering mirror documented above (fixed, mirror
removed). Re-checked the source more carefully afterward and found the
patch had, in fact, compiled in correctly (mtime of the recompiled
`hwcontext_vulkan.o` was after the sed edit; the naive grep had just also
matched two unrelated, un-patched lines nearby) -- so the mechanism was
fine. The patch itself was just wrong: `supported_usage` on the crashing
format/config never has the `VIDEO_ENCODE_SRC_BIT_KHR` bit set in the first
place, so neutralizing its opportunistic add was a no-op for this crash
regardless of whether it landed.

Found the actual cause via one-shot debug instrumentation (temporarily
added to `_pre_configure`, since removed): `alloc_mem()`'s
`VkMemoryRequirements.memoryTypeBits` for the failing image was `0xc`
(only memory-type indices 2/3 legal on this dev's NVIDIA RTX 4090, driver
610.88) -- this device's host-visible/staging pool, neither index carrying
`VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT`, while the caller asks for
device-local (`req_flags=0x1`, hence "No memory type found for flags 0x1").
The image's `usage` (`0x40000f`) included `VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT`
(bit 22) -- see `vulkan-host-transfer` below.

## vulkan-host-transfer

Neutralizes `hwcontext_vulkan.c`'s opportunistic
`VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT` usage-add -- the actual cause of the
`"No memory type found for flags 0x1"` crash `vulkan-video-encode-src`
above originally (and wrongly) targeted. On this dev's NVIDIA RTX 4090
(driver 610.88), an image created with this usage bit gets a
`VkMemoryRequirements.memoryTypeBits` that only admits this driver's
host-visible/staging memory-type indices (2/3), none of which carry
`VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT` -- so `alloc_mem()`'s device-local
allocation always fails, breaking `vulkan_pool_alloc` for every
Vulkan-uploaded rgba frame, same downstream symptom as before (GPU-only
tonemappers ending their filter chain in `format=rgba,hwdownload` before
`lut3d`).

Upstream already guards this usage bit with a probe-and-drop function,
`vulkan_host_transfer_usable()` (a comment right above it: *"Drivers may
expose the format feature, yet reject the final image, or provide no
memory type from which such an image can be allocated"* -- describing this
exact failure mode) -- but that probe reports this config as usable when
it demonstrably isn't, a gap in upstream's own workaround that isn't ours
to fix from a downstream patch. This app never issues host-side image
copies, so -- same reasoning as `vulkan-video-encode-src` -- the capability
is pure downside here regardless of whether the probe gets it right.

Confirmed via one-shot debug instrumentation added directly around
`alloc_mem()`'s memory-type search and the `hwctx->usage` assembly in
`vulkan_frames_init()` (see git history of `ffmpeg_extra.sh` for the exact
`av_log` lines, since removed): with only `vulkan-video-encode-src` applied,
`usage=0x40000f supported_usage=0x40001f memoryTypeBits=0xc` -- proving
`VIDEO_ENCODE_SRC_BIT_KHR` was never even in `supported_usage` for this
format/config (confirming that patch's irrelevance here) while
`HOST_TRANSFER_BIT_EXT` (`0x400000`, part of `0x40000f`) was.

## Release input pinning

The exact `src/ffmpeg.exe` and `src/ffprobe.exe` release inputs are tracked
through Git LFS. `tools/ffmpeg-manifest.json` records their sizes, SHA-256
digests, upstream revision, configuration, MABS provenance, and the patch
revision that produced them. `build_installer.bat` verifies the manifest before
PyInstaller runs, so an unreviewed binary cannot be packaged and signed.

After rebuilding FFmpeg:

1. Confirm `C:\MABS\build\ffmpeg_extra.sh` exactly matches the tracked
   `ffmpeg_extra.sh` in this directory.
2. Copy the new `ffmpeg.exe` and `ffprobe.exe` from MABS `bin-video` into
   `src\`.
3. Update every affected revision, configuration, size, and SHA-256 value in
   `tools/ffmpeg-manifest.json`. Record a full MABS commit when available. If
   MABS came from an archive without Git metadata, record SHA-256 values for
   the exact suite scripts instead.
4. Run `.venv\Scripts\python.exe tools\verify_ffmpeg_manifest.py .`.
5. Review the manifest and Git LFS pointer changes together before release.
