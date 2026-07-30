# Third-Party Notices

HDR to SDR Converter bundles third-party software. Their licenses are reproduced
in the `licenses/` folder inside the installation directory.

## FFmpeg

This application bundles unmodified `ffmpeg.exe` and `ffprobe.exe` binaries.

- **Version:** `N-125146-gc6bb22dea0` (FFmpeg git revision `c6bb22dea0`)
- **License:** GNU General Public License, **version 2 or later** (GPLv2+).
  This build is configured with `--enable-gpl --enable-libx264 --enable-libx265`,
  which makes the combined work GPL-licensed. It is **not** configured with
  `--enable-version3` or `--enable-nonfree`.
- **Full license text:** `licenses/COPYING.GPLv2` and `licenses/ffmpeg-LICENSE.md`
- **Project:** https://ffmpeg.org
- **Build source:** https://www.gyan.dev/ffmpeg/builds/

### Written offer for source code

Under GNU General Public License version 2, section 3(b), we provide a written
offer to supply the complete corresponding source code on request. For a period
of three years from the date of distribution, we will provide a complete
machine-readable copy of the corresponding source code, for no more than the
cost of physically performing the distribution. Contact: hdrtosdr.dev@outlook.com
or https://github.com/TORlN/HDR-to-SDR/issues

For convenience, the FFmpeg source code is also available from the upstream
repository at revision `c6bb22dea0`:
`git clone https://github.com/FFmpeg/FFmpeg && git checkout c6bb22dea0`

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
