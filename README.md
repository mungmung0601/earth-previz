# Earth Previz

**Automatically generate aerial cinematic previz videos from a single coordinate or address.**

Google Photorealistic 3D Tiles + CesiumJS based. Specialized tool for drone/helicopter shooting pre-visualization.

## Key Features

- Address or coordinate input → Automatically generate 5~10 diverse aerial shots
- 10 camera presets: orbit, flyby, flythrough, descent/ascent, and more
- Real-time adjustment of camera altitude, radius, azimuth, tilt, and speed via sliders
- 270p preview → 4K final rendering
- Automatically bake altitude/speed/recommended platform (drone vs helicopter) metadata overlay on MP4
- Automatic output of KML (Google Earth Pro tour) + JSX (After Effects camera data)

## Requirements

- **Python 3.9+**
- **FFmpeg** (must be installed on your system)
- **Google Maps Platform API Key** (Map Tiles API enabled + billing account required)

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/mungmung0601/earth-previz.git
cd earth-previz

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browser
python -m playwright install chromium

# 4. (Optional) Create .env file
cp .env.example .env
# Open .env and enter your GOOGLE_MAPS_API_KEY
```

### FFmpeg Installation

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows (Chocolatey)
choco install ffmpeg
```

## Running

### Web App (Recommended)

```bash
python app.py
```

Open `http://127.0.0.1:5100` in your browser.

### Or 1-click Run

```bash
# macOS / Linux
./run.sh

# Windows
run.bat
```

### CLI Mode

```bash
# Generate by address
python bot.py --place "1472 Broadway, New York" --shots 5 --duration-sec 7 --resolution 480p

# Generate by coordinates
python bot.py --lat 40.756 --lng -73.986 --shots 3 --duration-sec 10 --resolution 720p

# dry-run (metadata + KML/JSX only, skip rendering)
python bot.py --place "Seoul City Hall" --shots 10 --dry-run
```

## Usage Flow (Web App)

1. **Enter API key** → Automatically verify billing connection status
2. **Enter address or coordinates** + shot count, video length, resolution, texture selection
3. **Generate preview** → Real-time progress bar display
4. **Select video** → Edit with camera parameter sliders
5. **Generate** → Final render at desired resolution (up to 4K) + download

## Output Files

```
output/run_YYYYMMDD_HHMMSS/
├── videos/          # MP4 files (with metadata overlay)
├── kml/             # Google Earth Pro tour files
├── jsx/             # After Effects camera scripts
└── metadata/        # JSON/CSV analysis data
```

## Project Structure

```
earth-previz/
├── app.py              # Flask web app (main entry point)
├── bot.py              # CLI mode
├── camera_path.py      # 10 camera path presets
├── renderer.py         # Playwright + CesiumJS headless rendering
├── encoder.py          # FFmpeg MP4 encoding
├── recommender.py      # Drone vs helicopter recommendation analysis
├── geocoder.py         # Address → coordinate conversion
├── models.py           # Data models (CameraKeyframe, ShotPlan)
├── kml_exporter.py     # KML tour output
├── jsx_exporter.py     # After Effects JSX output
├── esp_parser.py       # Earth Studio ESP file parser
├── esp_exporter.py     # ESP-compatible output
├── web/
│   └── viewer.html     # CesiumJS 3D viewer
├── templates/
│   └── index.html      # Web app UI
├── requirements.txt
├── run.sh              # macOS/Linux run script
├── run.bat             # Windows run script
└── .env.example
```

## Google Maps API Key Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create or select a project
3. Link a [Billing](https://console.cloud.google.com/billing) account
4. Enable [Map Tiles API](https://console.cloud.google.com/apis/library/tile.googleapis.com)
5. Create an API key in [Credentials](https://console.cloud.google.com/apis/credentials)
6. Enter the key in the app or save it in the `.env` file

## License

MIT
