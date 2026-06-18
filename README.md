This is a desktop GUI application to convert HDR videos to SDR using FFmpeg. The application lets you select an input video (or drag and drop one), live-preview the tonemapped result frame by frame, fine-tune the conversion, and convert single files or a whole queue while monitoring progress.

> ## ⚠️ Project Status
>
> **The current release is the free Community Edition, and it does *not* match the source code in this repository.** The published `.exe` on the [releases page](https://github.com/TORlN/HDR-to-SDR/releases) works and is fully usable, but it is an earlier build that lags behind the ongoing development here.
>
> The source code in this repo is being actively reworked (responsiveness, reliability, and quality improvements) toward a **full monetized release**. Until that ships, treat the released Community Edition as the stable free version and this repository as a preview of work in progress.

## Features

- **Select Input Video Files**: Browse for video files (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.m4v`), or use the "All files" filter for anything else FFmpeg can read.
- **Drag and Drop Files**: Drop a single file to preview it, or drop several at once to queue them for batch conversion.
- **Live Frame Preview**: See the original (HDR) frame next to the converted (SDR) result side by side. Five evenly-spaced frame buttons let you scrub through the video, and the previews scale smoothly as you resize the window.
- **Custom Frame Preview**: Jump the preview to any exact timestamp (`HH:MM:SS`, `MM:SS`, or plain seconds) in addition to the five frame buttons.
- **Adjust Gamma Value**: Drag a slider (or type a value) to fine-tune the gamma of the output; the preview updates instantly.
- **Conversion Methods**: Choose between a **Static** or **Dynamic** method. Static applies the same conversion regardless of the file; Dynamic analyzes the original's brightness (MAXFALL) for a more faithful result.
- **Tonemappers**: Pick between Reinhard, Mobius, and Hable.
- **Quality Control**: A single Quality slider (CRF 17–28 on CPU, CQ 15–30 on GPU) trades file size against quality.
- **Output Container**: Explicitly choose the output container (MP4 / MKV / MOV); it defaults to match the input. Audio and subtitles are stream-copied when the container allows, and transcoded or dropped only when it can't hold them (e.g. TrueHD audio or PGS subtitles into MP4).
- **Video Info Strip**: After a file loads, a one-line summary shows resolution, frame rate, codec, HDR/SDR, and audio codec.
- **GPU Acceleration**: When enabled, conversions run the HDR→SDR tonemapping on the GPU via libplacebo (Vulkan) and encode with the detected hardware encoder (`h264_nvenc` / `h264_amf` / `h264_qsv`). Because tonemapping — not encoding — is the real bottleneck, moving it to the GPU can roughly halve conversion time on capable hardware. Falls back automatically to CPU tonemapping when Vulkan/libplacebo isn't available, and to CPU encoding if the GPU encoder fails.
- **Batch Conversion Queue**: Add multiple files (via "Add Files" or by dropping several at once) and convert them sequentially. The queue shows a per-file status (pending / converting / done / failed), lets you click an entry to preview it, remove or clear entries, and reports a summary when it finishes.
- **Monitor & Cancel**: A progress bar tracks the active conversion, and a Cancel button stops it cleanly.
- **Open Output File**: Optionally open the output automatically when the conversion completes.
- **Persistent Settings**: Gamma, conversion method, tonemapper, quality, GPU toggle, preview toggle, and "open after conversion" are saved between sessions.
- **Dark Theme**: A flat, color-based dark UI that stays smooth during window resizing.

## Requirements

- Python 3.10 or newer (tested on 3.10–3.13)
- FFmpeg (`ffmpeg` and `ffprobe` on your PATH, or bundled alongside the app)
- GPU acceleration is optional. GPU tonemapping needs an ffmpeg build with libplacebo (Vulkan); GPU encoding is supported on NVIDIA (`h264_nvenc`), AMD (`h264_amf`), and Intel (`h264_qsv`) hardware. The app degrades gracefully to CPU when either is unavailable.

## Installation

### Normal Installation

1. Download the latest release from the [releases page](https://github.com/TORlN/HDR-to-SDR/releases).
2. Run the `HDR_to_SDR_Converter.exe` file.

### Development Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/TORlN/HDR-to-SDR
    cd HDR-to-SDR
    ```

2. Create and activate a virtual environment:
    - On Windows:
        ```sh
        python -m venv .venv
        .venv\Scripts\activate
        ```
    - On macOS/Linux:
        ```sh
        python -m venv .venv
        source .venv/bin/activate
        ```

3. Install the required Python packages:
    ```sh
    pip install -r requirements.txt
    ```

4. Ensure `ffmpeg` and `ffprobe` are available. Either install FFmpeg so they're on your PATH, or place `ffmpeg.exe`, `ffprobe.exe`, and `ffplay.exe` in the `src` folder (where the app looks first).

### Running from source

```sh
python src/main.pyw
```

## Testing

The project is covered by a unit/integration test suite (currently 331 tests) run on CI against Python 3.10–3.13.

Run the suite:
```sh
python -m unittest discover -s test -p '*_test.py' -t .
```

Run it with coverage (configuration lives in `.coveragerc`; CI enforces a 90% floor):
```sh
pip install coverage
python -m coverage run -m unittest discover -s test -p '*_test.py' -t .
python -m coverage report
```

Some tests (smoke and performance audits) only run when a sample video and a real FFmpeg are present, and skip automatically otherwise.

## Building the executable

With the development environment set up and `ffmpeg.exe`, `ffprobe.exe`, and `ffplay.exe` placed in the `src` folder, build a standalone `.exe` with PyInstaller:

```sh
pyinstaller --onefile --noconsole --name "HDR_to_SDR_Converter" --icon=logo/icon.ico ^
    --paths=.venv\Lib\site-packages ^
    --hidden-import=numpy ^
    --hidden-import=numpy.core ^
    --add-data ".venv\Lib\site-packages\tkinterdnd2;tkinterdnd2" ^
    --add-data ".venv\Lib\site-packages\PIL;PIL" ^
    --add-binary "src\ffmpeg.exe;." ^
    --add-binary "src\ffprobe.exe;." ^
    --add-binary "src\ffplay.exe;." ^
    --collect-submodules numpy ^
    --log-level DEBUG ^
    src\main.pyw
```

The compiled executable will be located in the `dist` directory.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
