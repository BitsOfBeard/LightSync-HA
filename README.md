# LightSync

Sync a Home Assistant RGB light with the average color of your screen.

## Why I made this

I originally built LightSync to add a bit more atmosphere while playing horror games, especially dark and moody ones. The main use case was games like *Silent Hill 2*, but it also works nicely for games in general and can be useful for video playback when normal screen capture is available.

## What it does

LightSync samples the average color of your primary display, estimates perceived screen brightness, and sends updates to a Home Assistant light entity over WebSocket. When the screen is very dark, the light turns off. When the script exits, it restores the original light state.

## Features

- Real-time screen color sampling
- Adaptive brightness scaling
- Home Assistant integration over WebSocket and REST
- Configurable thresholds and timing
- Original light state restoration on exit

## Intended use

This project is meant for ambient lighting, not precise bias lighting or color-accurate content matching. It works best when you want your room lighting to roughly follow the mood of whatever is on screen.

Typical use cases include:

- Horror games
- Story-driven games
- General gaming
- Local video playback
- Non-DRM browser video playback

## Limitations

LightSync currently samples one monitor at a time.

It uses the average color of the whole screen. That is simple and fast, but it is not the same thing as zone-based Ambilight.

If protected video playback renders as a black frame to screen capture on your system, LightSync will only see black and the light will dim or turn off. This can happen with DRM-protected streaming services depending on the browser, operating system, GPU path, and player.

## Requirements

- Python 3.10+
- Home Assistant
- An RGB-capable light entity in Home Assistant

## Installation

```bash
git clone https://github.com/BitsOfBeard/LightSync-HA.git
cd LightSync-HA
pip install -r requirements.txt
