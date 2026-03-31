import asyncio
import json
import logging
import os
import signal
import time
import cProfile
import pstats
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import mss
import numpy as np
import requests
import websockets
import yaml
from PIL import Image

CONFIG_PATH = Path("config.yaml")
PROFILE_OUTPUT_PATH = Path("profile_results.txt")

logger = logging.getLogger("lightsync")


@dataclass(frozen=True)
class HomeAssistantConfig:
    url: str
    token: str
    light_entity_id: str


@dataclass(frozen=True)
class BrightnessConfig:
    min_threshold: float = 5
    max_screen: float = 255
    power_factor: float = 0.8
    min_light: int = 1
    max_light: int = 100


@dataclass(frozen=True)
class UpdateConfig:
    color_threshold: float = 20
    brightness_threshold: float = 15
    force_interval: float = 5
    sleep_interval: float = 0.10


@dataclass(frozen=True)
class CaptureConfig:
    monitor_index: int = 1
    downsample_size: int = 100


@dataclass(frozen=True)
class AppConfig:
    home_assistant: HomeAssistantConfig
    brightness: BrightnessConfig
    update: UpdateConfig
    capture: CaptureConfig


def setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config file: {path}. Copy config_example.yaml to config.yaml first."
        )

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a YAML mapping at the top level.")

    return data


def require_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid string config value: {key}")
    return value.strip()


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://{url}"


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    raw = load_yaml_config(path)

    ha_raw = raw.get("home_assistant", {})
    brightness_raw = raw.get("brightness", {})
    update_raw = raw.get("update", {})
    capture_raw = raw.get("capture", {})

    if not isinstance(ha_raw, dict):
        raise ValueError("home_assistant must be a mapping.")
    if not isinstance(brightness_raw, dict):
        raise ValueError("brightness must be a mapping.")
    if not isinstance(update_raw, dict):
        raise ValueError("update must be a mapping.")
    if not isinstance(capture_raw, dict):
        raise ValueError("capture must be a mapping.")

    token_from_file = str(ha_raw.get("token", "")).strip()
    token = os.getenv("HA_TOKEN", token_from_file).strip()

    if not token or "{{" in token or "YOUR_HA_TOKEN" in token:
        raise ValueError(
            "Home Assistant token is missing. Set HA_TOKEN or update config.yaml."
        )

    return AppConfig(
        home_assistant=HomeAssistantConfig(
            url=normalize_base_url(require_str(ha_raw, "url")),
            token=token,
            light_entity_id=require_str(ha_raw, "light_entity_id"),
        ),
        brightness=BrightnessConfig(
            min_threshold=float(brightness_raw.get("min_threshold", 5)),
            max_screen=float(brightness_raw.get("max_screen", 255)),
            power_factor=float(brightness_raw.get("power_factor", 0.8)),
            min_light=int(brightness_raw.get("min_light", 1)),
            max_light=int(brightness_raw.get("max_light", 100)),
        ),
        update=UpdateConfig(
            color_threshold=float(update_raw.get("color_threshold", 20)),
            brightness_threshold=float(update_raw.get("brightness_threshold", 15)),
            force_interval=float(update_raw.get("force_interval", 5)),
            sleep_interval=float(update_raw.get("sleep_interval", 0.10)),
        ),
        capture=CaptureConfig(
            monitor_index=int(capture_raw.get("monitor_index", 1)),
            downsample_size=int(capture_raw.get("downsample_size", 100)),
        ),
    )


class ScreenSampler:
    def __init__(self, monitor_index: int, downsample_size: int) -> None:
        self._monitor_index = monitor_index
        self._downsample_size = downsample_size
        self._sct = mss.mss()
        self._monitor = self._resolve_monitor()

    def _resolve_monitor(self) -> dict[str, int]:
        monitors = self._sct.monitors
        if self._monitor_index < 1 or self._monitor_index >= len(monitors):
            raise ValueError(
                f"Invalid monitor_index={self._monitor_index}. "
                f"Available monitor indices: 1..{len(monitors) - 1}"
            )
        return monitors[self._monitor_index]

    def get_average_color(self) -> np.ndarray:
        screenshot = self._sct.grab(self._monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
        img = img.resize(
            (self._downsample_size, self._downsample_size),
            resample=Image.BILINEAR,
        )
        img_array = np.asarray(img, dtype=np.float32)
        return img_array.mean(axis=(0, 1))

    def close(self) -> None:
        self._sct.close()


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig) -> None:
        self.base_url = config.url
        self.token = config.token
        self.light_entity_id = config.light_entity_id
        self.message_id = 1
        self.websocket: websockets.WebSocketClientProtocol | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
        )

    @property
    def rest_state_url(self) -> str:
        return f"{self.base_url}/api/states/{self.light_entity_id}"

    @property
    def websocket_url(self) -> str:
        if self.base_url.startswith("https://"):
            return self.base_url.replace("https://", "wss://", 1) + "/api/websocket"
        return self.base_url.replace("http://", "ws://", 1) + "/api/websocket"

    async def connect(self) -> None:
        self.websocket = await websockets.connect(
            self.websocket_url,
            ping_interval=20,
            ping_timeout=20,
        )
        await self.authenticate()

    async def authenticate(self) -> None:
        if self.websocket is None:
            raise RuntimeError("WebSocket is not connected.")

        first_message = json.loads(await self.websocket.recv())
        if first_message.get("type") != "auth_required":
            raise RuntimeError(
                f"Unexpected initial WebSocket message: {first_message}"
            )

        await self.websocket.send(
            json.dumps(
                {
                    "type": "auth",
                    "access_token": self.token,
                }
            )
        )

        auth_response = json.loads(await self.websocket.recv())
        if auth_response.get("type") != "auth_ok":
            raise RuntimeError(f"WebSocket authentication failed: {auth_response}")

    def get_light_state(self) -> dict[str, Any]:
        response = self.session.get(self.rest_state_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected light state response.")
        return data

    async def _call_service(self, service: str, service_data: dict[str, Any]) -> dict[str, Any]:
        if self.websocket is None:
            raise RuntimeError("WebSocket is not connected.")

        self.message_id += 1
        payload = {
            "id": self.message_id,
            "type": "call_service",
            "domain": "light",
            "service": service,
            "service_data": service_data,
        }

        await self.websocket.send(json.dumps(payload))
        response = json.loads(await self.websocket.recv())

        if response.get("id") != self.message_id:
            logger.debug("Received out-of-order response: %s", response)

        if not response.get("success", False):
            raise RuntimeError(f"Service call failed: {response}")

        return response

    async def turn_on(self, r: float, g: float, b: float, brightness_pct: float) -> None:
        brightness_pct = max(1, min(100, int(round(brightness_pct))))
        await self._call_service(
            "turn_on",
            {
                "entity_id": self.light_entity_id,
                "rgb_color": [int(round(r)), int(round(g)), int(round(b))],
                "brightness_pct": brightness_pct,
            },
        )

    async def turn_off(self) -> None:
        await self._call_service(
            "turn_off",
            {
                "entity_id": self.light_entity_id,
            },
        )

    async def set_light_state(
        self,
        r: float,
        g: float,
        b: float,
        brightness_pct: float,
    ) -> None:
        if brightness_pct <= 0:
            await self.turn_off()
        else:
            await self.turn_on(r, g, b, brightness_pct)

    async def restore_light_state(self, original_state: dict[str, Any]) -> None:
        state = original_state.get("state")
        attributes = original_state.get("attributes", {})

        if state != "on":
            await self.turn_off()
            logger.info("Restored light to off state.")
            return

        service_data: dict[str, Any] = {"entity_id": self.light_entity_id}

        brightness = attributes.get("brightness")
        if brightness is not None:
            service_data["brightness"] = int(brightness)

        rgb_color = attributes.get("rgb_color")
        if isinstance(rgb_color, list) and len(rgb_color) == 3:
            service_data["rgb_color"] = [int(rgb_color[0]), int(rgb_color[1]), int(rgb_color[2])]

        color_temp_kelvin = attributes.get("color_temp_kelvin")
        if color_temp_kelvin is not None:
            service_data["color_temp_kelvin"] = int(color_temp_kelvin)

        effect = attributes.get("effect")
        if effect:
            service_data["effect"] = effect

        await self._call_service("turn_on", service_data)
        logger.info("Restored original light state.")

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
        self.session.close()


def calculate_screen_brightness(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def calculate_light_brightness(
    screen_brightness: float,
    config: BrightnessConfig,
) -> float:
    if screen_brightness < config.min_threshold:
        return 0.0

    normalized = (screen_brightness - config.min_threshold) / (
        config.max_screen - config.min_threshold
    )
    scaled = (normalized ** config.power_factor) * 100.0
    return max(config.min_light, min(config.max_light, scaled))


async def run(stop_event: asyncio.Event) -> None:
    config = load_config()

    sampler = ScreenSampler(
        monitor_index=config.capture.monitor_index,
        downsample_size=config.capture.downsample_size,
    )
    ha = HomeAssistantClient(config.home_assistant)

    original_light_state: dict[str, Any] | None = None
    last_sent_color: np.ndarray | None = None
    last_sent_screen_brightness: float | None = None
    last_sent_light_brightness: float | None = None
    last_update_time = time.monotonic()

    try:
        await ha.connect()
        logger.info("Connected to Home Assistant WebSocket API.")

        original_light_state = ha.get_light_state()
        logger.info("Captured original light state.")

        while not stop_event.is_set():
            avg_color = sampler.get_average_color()
            r, g, b = map(float, avg_color)

            screen_brightness = calculate_screen_brightness(r, g, b)
            light_brightness = calculate_light_brightness(
                screen_brightness,
                config.brightness,
            )

            should_send = False

            if last_sent_color is None:
                should_send = True
            elif light_brightness <= 0 < (last_sent_light_brightness or 0):
                should_send = True
            elif light_brightness > 0 >= (last_sent_light_brightness or 0):
                should_send = True
            elif light_brightness > 0 and (last_sent_light_brightness or 0) > 0:
                color_difference = float(np.linalg.norm(avg_color - last_sent_color))
                brightness_difference = abs(
                    screen_brightness - (last_sent_screen_brightness or 0)
                )

                if (
                    color_difference > config.update.color_threshold
                    or brightness_difference > config.update.brightness_threshold
                ):
                    should_send = True
                elif (time.monotonic() - last_update_time) > config.update.force_interval:
                    should_send = True
            elif (time.monotonic() - last_update_time) > config.update.force_interval:
                should_send = True

            if should_send:
                await ha.set_light_state(r, g, b, light_brightness)
                last_sent_color = avg_color
                last_sent_screen_brightness = screen_brightness
                last_sent_light_brightness = light_brightness
                last_update_time = time.monotonic()

            await asyncio.sleep(config.update.sleep_interval)

    except asyncio.CancelledError:
        logger.info("Cancellation requested.")
        raise
    finally:
        try:
            if original_light_state is not None:
                await ha.restore_light_state(original_light_state)
        except Exception as exc:
            logger.exception("Failed to restore original light state: %s", exc)
        finally:
            await ha.close()
            sampler.close()


def maybe_profile(func) -> None:
    enable_profiling = os.getenv("ENABLE_PROFILING", "0").strip() in {"1", "true", "True"}

    if not enable_profiling:
        func()
        return

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        func()
    finally:
        profiler.disable()
        output = StringIO()
        stats = pstats.Stats(profiler, stream=output).sort_stats("cumulative")
        stats.print_stats()
        PROFILE_OUTPUT_PATH.write_text(output.getvalue(), encoding="utf-8")
        logger.info("Profiling results saved to %s", PROFILE_OUTPUT_PATH)


def main() -> None:
    setup_logging()

    stop_event = asyncio.Event()

    def request_shutdown() -> None:
        logger.info("Shutdown requested.")
        stop_event.set()

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_shutdown)
            except NotImplementedError:
                pass

        try:
            loop.run_until_complete(run(stop_event))
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    maybe_profile(runner)


if __name__ == "__main__":
    main()
