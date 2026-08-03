# HDR to SDR Converter

![Tests](https://github.com/TORlN/HDR-to-SDR/actions/workflows/python-tests.yml/badge.svg)
![Coverage floor](https://img.shields.io/badge/coverage-90%25%20floor-brightgreen)
![Release](https://img.shields.io/github/v/release/TORlN/HDR-to-SDR)
![License](https://img.shields.io/github/license/TORlN/HDR-to-SDR)

This is a desktop GUI application to convert HDR videos to SDR using FFmpeg. The application lets you select an input video (or drag and drop one), live-preview the tonemapped result frame by frame, fine-tune the conversion, and convert single files or a whole queue while monitoring progress.

The [latest release](https://github.com/TORlN/HDR-to-SDR/releases) is free to download with no account required. **Pro is now available**, purchase a license key at [hdrtosdr.com/#pricing](https://hdrtosdr.com/#pricing) to unlock the additional features listed below. More Pro features are actively in development and will be rolled out in upcoming releases.

## Under the Hood

- **GPU/CPU dual pipeline**: tonemapping runs on the GPU via libplacebo (Vulkan) when available, falling back to a pure-CPU ffmpeg filter chain — GPU tonemapping roughly halves conversion time on capable hardware.
- **Real color science**: gamut conversion runs through a generated BT.2020→BT.709 3D LUT (tetrahedral interpolation) instead of approximate gamma math, on both the CPU and GPU paths.
- **Dolby Vision (profile 5) RPU handling**, automatic hardware encoder detection (NVENC / AMF / QSV) with CPU fallback, and a licensing system built on HMAC-signed, hardware-locked offline license tokens.
- **Tested and typed**: 21 test modules run against Python 3.10–3.13 (headless, via Xvfb, to exercise the real Tkinter GUI) on every push, gated by a 90% coverage floor and a zero-error `pyright` pass on `src/`.
- **Signed, installable releases**: PyInstaller build + Inno Setup installer, code-signed via Azure Trusted Signing, with an in-app auto-updater.

## Features

### Free (Community Edition)

- **Select Input Video Files**: Browse for video files (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.m4v`), or use the "All files" filter for anything else FFmpeg can read.
- **Drag and Drop**: Drop a single file to load and preview it.
- **Live Frame Preview**: See the original (HDR) frame next to the converted (SDR) result side by side. Five evenly-spaced frame buttons let you scrub through the video, and the previews scale smoothly as you resize the window.
- **Adjust Gamma Value**: Drag a slider (or type a value) to fine-tune the gamma of the output; the preview updates instantly.
- **Tonemappers**: Pick between Reinhard, Mobius, Hable, BT.2390, and Spline. BT.2390 and Spline are GPU-only (libplacebo) and shown greyed out until GPU tonemapping is active.
- **Video Info Strip**: After a file loads, a one-line summary shows resolution, frame rate, codec, HDR/SDR, audio codec, and the probed source bitrate (estimated from the container total when a source, e.g. MKV, doesn't expose a per-stream bitrate). Dolby Vision sources are detected automatically and flagged in this strip.
- **Monitor & Cancel**: A progress bar tracks the active conversion, and a Cancel button stops it cleanly.
- **Open Output File**: Optionally open the output automatically when the conversion completes.
- **Dark Theme**: A flat, color-based dark UI that stays smooth during window resizing.
- **GPU Acceleration**: Runs HDR→SDR tonemapping on the GPU via libplacebo (Vulkan) and encodes with the detected hardware encoder (`h264_nvenc` / `h264_amf` / `h264_qsv`). Because tonemapping (not encoding) is the real bottleneck, moving it to the GPU can roughly halve conversion time on capable hardware. Falls back automatically to CPU tonemapping when Vulkan/libplacebo isn't available, and to CPU encoding if the GPU encoder fails.
- **H.265/HEVC Preservation**: An HEVC source is re-encoded back to HEVC instead of being converted to H.264.
- **10-Bit Output**: Encode the SDR output at 10-bit color depth to avoid banding on gradients.
- **Dolby Vision Support (Stereo Audio)**: Dolby Vision (profile 5) RPU metadata is routed through the libplacebo tonemapper for an accurate conversion. The source audio track is downmixed to 2-channel stereo AAC in the output.
- **Persistent Settings**: Gamma, tonemapper, quality, container, GPU toggle, preview toggle, and "open after conversion" are saved between sessions.

### Pro (Licensed)

All free features, plus:

- **Quality Control**: A Quality Mode dropdown switches between **Constant Quality** (a CRF 17–28 on CPU / CQ 15–30 on GPU slider that lets the encoder auto-vary bitrate per scene) and **Target Bitrate** (you set the average output bitrate directly, up to the source's own bitrate).
- **Output Container**: Explicitly choose the output container (MP4 / MKV / MOV); it defaults to match the input. Audio and subtitles are stream-copied when the container allows, and transcoded or dropped only when it can't hold them (e.g. TrueHD audio or PGS subtitles into MP4).
- **Custom Frame Seek**: Jump the preview to any exact timestamp (`HH:MM:SS`, `MM:SS`, or plain seconds) in addition to the five frame buttons.
- **Batch Conversion Queue**: Add multiple files (via "Add Files" or by dropping several at once) and convert them sequentially. The queue shows a per-file status (pending / converting / done / failed), lets you click an entry to preview it, remove or clear entries, and reports a summary when it finishes. Each item remembers its own settings (quality mode, quality/bitrate value, bit depth) and restores them when you reselect it, and the list marks any item whose settings you've customized away from the shared defaults.
- **Apply to All**: Copy the currently displayed settings onto every item in the batch queue in one click.
- **12-Bit Output**: Encode the SDR output at 12-bit color depth (CPU only) for the widest gradient headroom.
- **Dolby Vision Support (Full Audio)**: The source audio track is preserved (stream-copied or transcoded to fit the container) instead of being downmixed to stereo.

More Pro features are on the way. Follow this repo or check [hdrtosdr.com](https://hdrtosdr.com) for updates.

## Network activity & privacy

This application makes outbound requests only for updates and (in Pro builds) license
validation. Both calls are made directly from your machine to the respective APIs:

| Endpoint | Purpose | When | Source |
|---|---|---|---|
| `api.github.com` | Checks whether a newer release exists, and downloads the installer if you accept. The actual installer download is a redirect to GitHub's asset CDN (`objects.githubusercontent.com`), not `api.github.com` itself | On startup, and when you click Update | `src/updater.py` |
| `api.lemonsqueezy.com` | Validates a Pro license key; the implementation is in a private module | On Pro activation, and at most once every 30 days afterwards | Private (not in public repo) |

**There is no analytics, no telemetry, no crash reporting, and no usage tracking** of
any kind. No analytics module has ever existed in this codebase. Video conversion
is entirely local — `ffmpeg` runs on your machine and never uploads anything.

**On Pro license activation and validation only:** A derived hardware fingerprint
(SHA-256 hash of MAC address, hostname, CPU architecture, and OS family) is sent
to Lemon Squeezy to node-lock the license to your machine. This fingerprint is
used to verify that a given license key is activated on this machine only. The
Community Edition makes no calls to Lemon Squeezy.

The Community Edition makes only the `api.github.com` update check. Video files
are never uploaded, and your files' contents never leave your machine.

## Licensing

Pro licenses are sold through [Lemon Squeezy](https://hdrtosdr.com/#pricing) and are **node-locked** to the machine they are activated on. Activation requires an internet connection the first time a key is entered on a new device. After activation, the app works offline indefinitely. It re-validates against the server at most once every 30 days. If the server is unreachable at that point, the local token is trusted so paid users are never blocked by network failures.

The license token is stored in `%APPDATA%\HDR-to-SDR\license.dat` as an HMAC-signed, hardware-bound file. Copying the file to another machine will not work: the signing key is derived from a build-time secret combined with the machine's hardware fingerprint, so a token can be verified only by a genuine build running on the machine it was issued to.

**Upgrading from 3.1.x or earlier:** tokens issued before 3.2.0 were signed without that secret, so they cannot be trusted offline and are re-validated online once, then rewritten in the new format. In practice this means the first launch after updating needs an internet connection; if you launch offline on that one run, the app will ask you to activate. Reconnect and relaunch and it will pick the license back up, without consuming an extra activation. After that, offline use is indefinite as described above.

### Forking

This code is MIT-licensed and you are welcome to fork it. The application name
and logo are not part of that grant — please rename your fork, use your own
icon, and generate a fresh installer `AppId`. See `TRADEMARK.md`.

## Requirements

- Python 3.10 or newer (tested on 3.10–3.13)
- FFmpeg (`ffmpeg` and `ffprobe` on your PATH, or bundled alongside the app)
- GPU acceleration is optional. GPU tonemapping needs an ffmpeg build with libplacebo (Vulkan); GPU encoding is supported on NVIDIA (`h264_nvenc`), AMD (`h264_amf`), and Intel (`h264_qsv`) hardware. The app degrades gracefully to CPU when either is unavailable.

## Installation

1. Download the latest release from the [releases page](https://github.com/TORlN/HDR-to-SDR/releases).
2. Run the `HDR_to_SDR_Setup.exe` installer.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

Released builds bundle FFmpeg, which is distributed separately under the GNU GPL v2 or later, along with x264 and x265. Their license texts, and a written offer for the corresponding source, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the [`licenses/`](licenses/) folder. The MIT grant above covers this project's own code, not the bundled third-party binaries.
