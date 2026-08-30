# Third-Party Notices

HDR to SDR Converter bundles third-party software. Their licenses are reproduced
in the `licenses/` folder inside the installation directory.

## FFmpeg

This application bundles `ffmpeg.exe` and `ffprobe.exe` binaries built from the
official FFmpeg source with two small local patches (see below).

- **Version:** `N-126335-gb32f8d1c23` (FFmpeg git revision `b32f8d1c23`)
- **License:** GNU General Public License, **version 2 or later** (GPLv2+).
  This build is configured with `--enable-gpl --enable-libx264 --enable-libx265`,
  which makes the resulting FFmpeg binaries GPL-licensed. It is **not** configured with
  `--enable-version3` or `--enable-nonfree`.
- **Full license text:** `licenses/COPYING.GPLv2` and `licenses/ffmpeg-LICENSE.md`
- **Project:** https://ffmpeg.org
- **Build source:** compiled from the official FFmpeg git repository
  (https://git.ffmpeg.org/ffmpeg.git) using media-autobuild_suite
  (https://github.com/m-ab-s/media-autobuild_suite), not a prebuilt distribution.
- **Local patches:** two Vulkan usage-flag opportunistic-add sites are disabled
  (`VIDEO_ENCODE_SRC_BIT_KHR`, `HOST_TRANSFER_BIT_EXT` in `hwcontext_vulkan.c`) --
  this app uses neither capability, and on at least one affected GPU/driver
  combination requesting them broke Vulkan-uploaded frame allocation entirely.
  Patch source and full writeup: `tools/ffmpeg-patches/` in this repository.

### Written offer for source code

Under GNU General Public License version 2, section 3(b), we provide a written
offer to supply the complete corresponding source code on request. For a period
of three years from the date of distribution, we will provide a complete
machine-readable copy of the corresponding source code, for no more than the
cost of physically performing the distribution. Contact: hdrtosdr.dev@outlook.com
or https://github.com/TORlN/HDR-to-SDR/issues

For convenience, the FFmpeg source code is also available from the upstream
repository at revision `b32f8d1c23`:
`git clone https://github.com/FFmpeg/FFmpeg && git checkout b32f8d1c23`
Local patches on top of that revision are in `tools/ffmpeg-patches/` in this repository.

## dav1d

Bundled inside the FFmpeg binaries above (`--enable-libdav1d`), providing
software AV1 decoding. BSD 2-Clause License (permissive, GPL-compatible;
does not affect the GPLv2+ status above). Project: https://code.videolan.org/videolan/dav1d

## x264

Bundled inside the FFmpeg binaries above. GNU General Public License version 2
or later. Full text: `licenses/x264-COPYING`. Project: https://www.videolan.org/developers/x264.html

## x265

Bundled inside the FFmpeg binaries above. GNU General Public License version 2
or later. Full text: `licenses/x265-COPYING`. Project: https://www.videolan.org/developers/x265.html

## HDR to SDR Converter

The application's own source code is licensed under the MIT License; see
`LICENSE`. The application name and logo are not covered by that grant; see
`TRADEMARK.md`.
