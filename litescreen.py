import mss
import numpy as np
from PIL import Image
import asyncio
import json
import time
import cProfile
import pstats
from io import StringIO
import os
import websockets
import requests
import yaml

def load_config():  
    with open("config.yaml", "r") as f:  
        config = yaml.safe_load(f)  
    return config  

CONFIG = load_config()  
HA_URL = CONFIG["home_assistant"]["url"]  
HA_TOKEN = os.getenv("HA_TOKEN", CONFIG["home_assistant"]["token"])  # Prioritizes env vars  
LIGHT_ENTITY_ID = CONFIG["home_assistant"]["light_entity_id"]

# Brightness thresholds and mapping configuration
MIN_BRIGHTNESS_THRESHOLD = 5  # Screen brightness level below which the light is turned off
MAX_SCREEN_BRIGHTNESS = 255  # Maximum possible screen brightness
BRIGHTNESS_POWER_FACTOR = 0.8  # Aggressiveness of scaling for brightness
MIN_LIGHT_BRIGHTNESS = 1  # Minimum brightness percentage for the light
MAX_LIGHT_BRIGHTNESS = 100  # Maximum brightness percentage for the light

# Thresholds for triggering updates
DRASTIC_COLOR_THRESHOLD = 20  # Color difference threshold for triggering light update
DRASTIC_BRIGHTNESS_THRESHOLD = 15  # Brightness difference threshold for triggering light update
FORCE_UPDATE_INTERVAL = 5  # Force update every 5 seconds if no significant change
SLEEP_INTERVAL = 0.10  # Interval between screen samples (in seconds)

# Global state to keep track of original light state
original_light_state = None
message_id = 1  # Global message ID to ensure uniqueness

async def get_average_color():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        sct_img = sct.grab(monitor)
        img = Image.frombytes('RGB', sct_img.size, sct_img.rgb)
        img = img.resize((100, 100), resample=Image.BILINEAR)
        img_array = np.array(img)
        avg_color = img_array.mean(axis=(0, 1))
        return avg_color

async def set_light_color_ws(websocket, r, g, b, brightness):
    global message_id
    message_id += 1  # Increment this ID for each message to be unique

    # Validate brightness to ensure it's within a range acceptable by Home Assistant
    brightness = max(MIN_LIGHT_BRIGHTNESS, min(MAX_LIGHT_BRIGHTNESS, brightness)) if brightness > 0 else MIN_LIGHT_BRIGHTNESS

    payload = {
        "id": message_id,
        "type": "call_service",
        "domain": "light",
        "service": "turn_on",
        "service_data": {
            "entity_id": LIGHT_ENTITY_ID,
            "rgb_color": [int(r), int(g), int(b)],
            "brightness_pct": int(brightness)
        }
    }
    await websocket.send(json.dumps(payload))
    response = await websocket.recv()
    response_data = json.loads(response)
    
    # Check response for both result and success keys
    if "result" not in response_data or not response_data.get("success", False):
        print(f"Failed to set light color via WebSocket: {response_data}")


def get_light_state_rest():
    url = f"https://{HA_URL}/api/states/{LIGHT_ENTITY_ID}"
    headers = {
        'Authorization': f'Bearer {HA_TOKEN}',
        'Content-Type': 'application/json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to retrieve light state via REST API. Response: {response.text}")
        return None

async def restore_light_state_ws(websocket, original_state):
    global message_id
    if original_state['state'] == 'on':
        brightness = original_state['attributes'].get('brightness', 255)
        rgb_color = original_state['attributes'].get('rgb_color', [255, 255, 255])
        brightness_pct = (brightness / 255) * 100
        await set_light_color_ws(websocket, rgb_color[0], rgb_color[1], rgb_color[2], brightness_pct)
    else:
        message_id += 1
        payload = {
            "id": message_id,
            "type": "call_service",
            "domain": "light",
            "service": "turn_off",
            "service_data": {
                "entity_id": LIGHT_ENTITY_ID
            }
        }
        await websocket.send(json.dumps(payload))
        response = await websocket.recv()
        response_data = json.loads(response)
        if "result" not in response_data or not response_data.get("success", False):
            print(f"Failed to turn off the light via WebSocket: {response_data}")
    print("Restored the original light state.")

async def authenticate_ws(websocket):
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        await websocket.send(json.dumps({
            "type": "auth",
            "access_token": HA_TOKEN
        }))
        auth_response = await websocket.recv()
        auth_response_data = json.loads(auth_response)
        print(f"WebSocket authentication response: {auth_response_data}")  # Log the auth response for debugging
        if "type" in auth_response_data and auth_response_data["type"] == "auth_ok":
            # Add a small delay to ensure readiness after auth
            await asyncio.sleep(0.1)
            return
        elif "type" in auth_response_data and auth_response_data["type"] == "auth_invalid":
            print(f"WebSocket authentication failed: {auth_response_data}")
            if attempt < max_retries:
                print(f"Retrying WebSocket authentication (attempt {attempt + 1}/{max_retries})...")
                await asyncio.sleep(2)
            else:
                raise Exception("WebSocket authentication failed after maximum retries.")


async def main():
    global original_light_state
    last_sent_color = None
    last_sent_brightness = None
    last_update_time = time.time()

    websocket_uri = f"wss://{HA_URL}/api/websocket"

    # Start a websocket connection
    async with websockets.connect(websocket_uri) as websocket:
        try:
            # Authenticate
            await authenticate_ws(websocket)
            # Get the original light state via REST API
            original_light_state = get_light_state_rest()
            if original_light_state is None:
                print("Could not get the original light state. Exiting.")
                return

            while True:
                avg_color = await get_average_color()
                r, g, b = avg_color
                screen_brightness = 0.2126 * r + 0.7152 * g + 0.0722 * b
                light_brightness = ((screen_brightness - MIN_BRIGHTNESS_THRESHOLD) / (MAX_SCREEN_BRIGHTNESS - MIN_BRIGHTNESS_THRESHOLD)) ** BRIGHTNESS_POWER_FACTOR * 100
                light_brightness = max(MIN_LIGHT_BRIGHTNESS, min(MAX_LIGHT_BRIGHTNESS, light_brightness)) if screen_brightness >= MIN_BRIGHTNESS_THRESHOLD else 0

                # If this is the first run, set the color and initialize last sent values
                if last_sent_color is None:
                    await set_light_color_ws(websocket, r, g, b, light_brightness)
                    last_sent_color = avg_color
                    last_sent_brightness = screen_brightness
                    last_update_time = time.time()
                    await asyncio.sleep(SLEEP_INTERVAL)
                    continue

                # Calculate the difference between current and last sent RGB color
                color_difference = np.linalg.norm(avg_color - last_sent_color)
                brightness_difference = abs(screen_brightness - last_sent_brightness)

                # Update light if there is a significant change
                if (color_difference > DRASTIC_COLOR_THRESHOLD or brightness_difference > DRASTIC_BRIGHTNESS_THRESHOLD):
                    await set_light_color_ws(websocket, r, g, b, light_brightness)
                    last_sent_color = avg_color
                    last_sent_brightness = screen_brightness
                    last_update_time = time.time()

                # Force update if necessary
                elif (time.time() - last_update_time) > FORCE_UPDATE_INTERVAL:
                    await set_light_color_ws(websocket, r, g, b, light_brightness)
                    last_sent_color = avg_color
                    last_sent_brightness = screen_brightness
                    last_update_time = time.time()

                await asyncio.sleep(SLEEP_INTERVAL)

        except Exception as e:
            print(f"An error occurred: {e}")

        finally:
            if original_light_state:
                await restore_light_state_ws(websocket, original_light_state)

if __name__ == "__main__":
    # Start profiling
    pr = cProfile.Profile()
    pr.enable()

    try:
        asyncio.run(main())
    finally:
        # Stop profiling
        pr.disable()
        s = StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
        ps.print_stats()
        with open('profile_results.txt', 'w') as f:
            f.write(s.getvalue())
        print("Profiling results saved to profile_results.txt")