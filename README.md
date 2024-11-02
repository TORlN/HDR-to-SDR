This is a simple GUI application to convert HDR videos to SDR using FFmpeg. The application allows you to select an input video file, specify an output file name, adjust the gamma value, and monitor the conversion progress.

## Features

- **Select Input Video Files**: Choose from video files with extensions `.mp4`, `.mkv`, and `.mov`.
- **Specify Output File Name**: Define the name and location of the converted SDR video file.
- **Adjust Gamma Value**: Use a slider to adjust the gamma value for the conversion process, allowing for fine-tuning of the output video.
- **Monitor Conversion Progress**: A progress bar displays the current status of the conversion process.
- **Open Output File**: Option to automatically open the output file after the conversion is complete.

## Requirements

- Python 3.x
- FFmpeg

## Installation

### Normal Installation

1. Download the latest release from the [releases page](https://github.com/<your-username>/<your-repo>/releases).
2. Extract the downloaded zip file.
3. Run the `hdr_to_sdr_converter.exe` file.

### Development Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/<your-username>/<your-repo>.git
    cd <repository-directory>
    ```

2. Install the required Python packages:
    ```sh
    pip install -r requirements.txt
    ```

3. Ensure FFmpeg is installed and available in your system's PATH.

### Development Usage

1. Create a virtual environment:
    ```sh
    python -m venv .venv
    ```

2. Activate the virtual environment:
    - On Windows:
        ```sh
        .venv\Scripts\activate
        ```
    - On macOS/Linux:
        ```sh
        source .venv/bin/activate
        ```

3. Install the required packages:
    ```sh
    pip install -r requirements.txt
    ```

4. Compile the executable:
    ```sh
    pyinstaller --onefile --noconsole --name "HDR_to_SDR_Converter" --icon=icon.ico --add-data ".venv/Lib/site-packages/sv_ttk;sv_ttk" main.py
    ```
5. The compiled executable will be located in the `dist` directory.
...

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.