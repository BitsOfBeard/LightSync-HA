# LightSync

Sync a Home Assistant RGB light with the average color of your screen.

## Why I made this

I originally built LightSync to add more atmosphere while playing horror games, especially dark and moody ones. The main use case was games like *Silent Hill 2*, but it also works well for games in general and can be useful for video playback when normal screen capture works.

## What it does

LightSync samples the average color of a selected display, estimates perceived screen brightness, and sends updates to a Home Assistant light entity. When the screen is very dark, the light turns off. When the script exits, it restores the original light state.

## Features

- Real-time screen color sampling
- Adaptive brightness mapping
- Home Assistant integration over WebSocket and REST
- Configurable thresholds and timing
- Original light state restoration on exit

## Intended use

This project is for ambient lighting, not precise color reproduction or zone-based bias lighting. It works best when you want your room lighting to roughly follow the mood of what is on screen.

Typical use cases include:

- Horror games
- Story-driven games
- General gaming
- Local video playback
- Non-DRM browser video playback

## Limitations

LightSync currently samples one monitor at a time.

It uses the average color of the whole screen. That is simple and fast, but it is not the same as multi-zone Ambilight-style systems.

If protected video playback renders as a black frame to screen capture on your system, LightSync will only see black and the light will dim or turn off.

## Requirements

- Python 3.10+
- Home Assistant
- An RGB-capable light entity in Home Assistant

## Installation

Clone the repository and install the dependencies:

```bash
git clone https://github.com/BitsOfBeard/LightSync-HA.git
cd LightSync-HA
pip install -r requirements.txt
```

## Configuration

Copy `config_example.yaml` to `config.yaml` and update the values.

You can set the Home Assistant token in either of two ways.

Use an environment variable:

```bash
export HA_TOKEN="your_token_here"
```

Or place it in `config.yaml`.

Using an environment variable is recommended.

## Example config

```yaml
home_assistant:
  url: "https://assistant.local:8123"
  token: "{{ YOUR_HA_TOKEN }}"
  light_entity_id: "light.behind_screen"

brightness:
  min_threshold: 5
  max_screen: 255
  power_factor: 0.8
  min_light: 1
  max_light: 100

update:
  color_threshold: 20
  brightness_threshold: 15
  force_interval: 5
  sleep_interval: 0.10

capture:
  monitor_index: 1
  downsample_size: 100
```

## Running

```bash
python lightsync.py
```

Optional profiling:

```bash
ENABLE_PROFILING=1 python lightsync.py
```

Optional verbose logging:

```bash
LOG_LEVEL=DEBUG python lightsync.py
```

## How it works

LightSync captures the selected monitor, downsamples the frame, computes the average RGB color, and converts that into an approximate luminance value using:

`0.2126 * R + 0.7152 * G + 0.0722 * B`

That brightness value is then mapped onto Home Assistant light brightness using a configurable power curve. Large changes in color or brightness trigger immediate updates, and a periodic forced update keeps the light in sync even during slow scene changes.

## Notes on movies and streaming services

Local files and normal browser playback should generally work.

Streaming services that use DRM may not work for ambient sync, because some systems expose protected video to screen capture as a black frame. If that happens, LightSync is behaving correctly from its point of view, but it only sees black.

## Safety note

This project sends frequent light updates. If your light or integration is rate-limited, increase `sleep_interval` and the update thresholds.

## Development notes

LightSync uses:

- `mss` for screen capture
- `numpy` for color math
- `Pillow` for resizing
- `websockets` for Home Assistant control
- `requests` for initial state retrieval
- `PyYAML` for configuration loading

## License
MIT
