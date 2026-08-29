# FFmpeg source patches

Patches applied to the bundled ffmpeg's source before it's built via
[media-autobuild_suite](https://github.com/m-ab-s/media-autobuild_suite)
(MABS), via `ffmpeg_extra.sh`'s `_pre_configure()` hook -- MABS's
documented customization point, sourced automatically right after ffmpeg's
git checkout/update and before `./configure`, so it survives every
`ffmpegUpdate=y` rebuild.

`ffmpeg_extra.sh` in this folder is the source of truth. MABS actually runs
a copy at `C:\MABS\build\ffmpeg_extra.sh` (mirrored to
`C:\MABS\build\ffmpeg-git\ffmpeg_extra.sh` too, as a hedge -- static
reading of MABS's `do_vcs` left it ambiguous which cwd the
`[[ -f ffmpeg_extra.sh ]]` check runs from, and both locations are cheap
to keep in sync). If MABS is ever reinstalled from scratch, or you're not
sure the local copies match, just copy this folder's `ffmpeg_extra.sh`
over both.

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

First attempt (2026-08-29, a positional `patch -p1` against a captured
`git diff`) failed on the very next rebuild: ffmpeg-git master had already
reworded the surrounding `if` conditions (moved ~288 lines, changed the
guard logic, even reverted to an older variant of it), so the diff's
context no longer matched anywhere. Replaced with the `sed`-based approach
in `ffmpeg_extra.sh` above, which only cares about the one line whose
*effect* matters.
