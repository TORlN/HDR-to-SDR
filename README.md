# HDR to SDR Converter

This is a simple GUI application to convert HDR videos to SDR using FFmpeg. The application allows you to select an input video file, specify an output file name, adjust the gamma value, and monitor the conversion progress.

## Features

- Select input video files with extensions `.mp4`, `.mkv`, and `.mov`.
- Specify the output file name.
- Adjust gamma value using a slider.
- Monitor conversion progress with a progress bar.
- Option to open the output file after conversion.

## Requirements

- Python 3.x
- FFmpeg

## Installation

1. Clone the repository:
    ```sh
    git clone <repository-url>
    cd <repository-directory>
    ```

2. Install the required Python packages:
    ```sh
    pip install -r requirements.txt
    ```

3. Ensure FFmpeg is installed and available in your system's PATH.

## Usage

1. Run the application:
    ```sh
    python hdr_to_sdr_converter.py
    ```

2. Use the GUI to select an input file, specify the output file name, adjust the gamma value, and start the conversion.

## License

This project is licensed under the MIT License.