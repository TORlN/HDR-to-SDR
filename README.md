This is a desktop GUI application to convert HDR videos to SDR using FFmpeg. The application lets you select an input video (or drag and drop one), live-preview the tonemapped result frame by frame, fine-tune the conversion, and convert single files or a whole queue while monitoring progress.

The [latest release](https://github.com/TORlN/HDR-to-SDR/releases) is the **free Community Edition** — download and run it with no account required. Entering a **Pro license key** inside the app unlocks the additional features listed below.

> **Note:** Pro license key sales are a work in progress and not yet publicly available. Watch this repo or the website for updates.

## Features

### Free (Community Edition)

- **Select Input Video Files**: Browse for video files (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.m4v`), or use the "All files" filter for anything else FFmpeg can read.
- **Drag and Drop**: Drop a single file to load and preview it.
- **Live Frame Preview**: See the original (HDR) frame next to the converted (SDR) result side by side. Five evenly-spaced frame buttons let you scrub through the video, and the previews scale smoothly as you resize the window.
- **Adjust Gamma Value**: Drag a slider (or type a value) to fine-tune the gamma of the output; the preview updates instantly.
- **Conversion Methods**: Choose between a **Static** or **Dynamic** method. Static applies the same conversion regardless of the file; Dynamic analyzes the original's brightness (MAXFALL) for a more faithful result.
- **Tonemappers**: Pick between Reinhard, Mobius, and Hable.
- **Video Info Strip**: After a file loads, a one-line summary shows resolution, frame rate, codec, HDR/SDR, and audio codec.
- **Monitor & Cancel**: A progress bar tracks the active conversion, and a Cancel button stops it cleanly.
- **Open Output File**: Optionally open the output automatically when the conversion completes.
- **Dark Theme**: A flat, color-based dark UI that stays smooth during window resizing.

### Pro (Licensed)

All free features, plus:

- **GPU Acceleration**: Runs HDR→SDR tonemapping on the GPU via libplacebo (Vulkan) and encodes with the detected hardware encoder (`h264_nvenc` / `h264_amf` / `h264_qsv`). Because tonemapping — not encoding — is the real bottleneck, moving it to the GPU can roughly halve conversion time on capable hardware. Falls back automatically to CPU tonemapping when Vulkan/libplacebo isn't available, and to CPU encoding if the GPU encoder fails.
- **Quality Control**: A Quality slider (CRF 17–28 on CPU, CQ 15–30 on GPU) trades file size against quality.
- **Output Container**: Explicitly choose the output container (MP4 / MKV / MOV); it defaults to match the input. Audio and subtitles are stream-copied when the container allows, and transcoded or dropped only when it can't hold them (e.g. TrueHD audio or PGS subtitles into MP4).
- **Custom Frame Seek**: Jump the preview to any exact timestamp (`HH:MM:SS`, `MM:SS`, or plain seconds) in addition to the five frame buttons.
- **Batch Conversion Queue**: Add multiple files (via "Add Files" or by dropping several at once) and convert them sequentially. The queue shows a per-file status (pending / converting / done / failed), lets you click an entry to preview it, remove or clear entries, and reports a summary when it finishes.
- **Persistent Settings**: Gamma, conversion method, tonemapper, quality, GPU toggle, preview toggle, and "open after conversion" are saved between sessions.

## Licensing

The Pro version uses a **node-locked license** issued via [Keygen.sh](https://keygen.sh). Activation requires an internet connection the first time a key is entered on a new device. After activation, the app works offline indefinitely — it re-validates against the server at most once every 30 days. If the server is unreachable at that point, the local token is trusted so paid users are never blocked by network failures.

The license key is stored in `%APPDATA%\HDR-to-SDR\license.dat` as an HMAC-signed, hardware-bound token. Copying the file to another machine will not work — the HMAC is keyed to the machine's hardware fingerprint.

## Requirements

- Python 3.10 or newer (tested on 3.10–3.13)
- FFmpeg (`ffmpeg` and `ffprobe` on your PATH, or bundled alongside the app)
- GPU acceleration is optional. GPU tonemapping needs an ffmpeg build with libplacebo (Vulkan); GPU encoding is supported on NVIDIA (`h264_nvenc`), AMD (`h264_amf`), and Intel (`h264_qsv`) hardware. The app degrades gracefully to CPU when either is unavailable.

## Installation

1. Download the latest release from the [releases page](https://github.com/TORlN/HDR-to-SDR/releases).
2. Run the `HDR_to_SDR_Converter.exe` file.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
