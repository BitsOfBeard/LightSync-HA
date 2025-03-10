# LightSync  
Sync ambient lighting with your screen color using Home Assistant.

## Features  
- Real-time screen color sampling (single monitor).
- Adaptive brightness scaling with configurable thresholds.
- Seamless integration with Home Assistant via WebSocket/REST.
- Graceful state restoration on script exit.

## Getting Started  

### Prerequisites  
- Python 3.10+  
- Home Assistant instance (2023.9 or newer)  
- Light entity with RGB/brightness control  

### Installation  
1. Clone the repository:  
   ```bash  
   git clone https://github.com/BitsOfBeard/LightSync-HA.git
   cd LightSync-HA
   ```  
2. Install dependencies:  
   ```bash  
   pip install -r requirements.txt  
   ```  
3. Configure:  
   - Rename `config_example.yaml` to `config.yaml`.  
   - Update `config.yaml` with your Home Assistant details.  

### Obtaining a Long-Lived Access Token  
1. In Home Assistant, go to your profile (bottom-left).  
2. Scroll to **Long-Lived Access Tokens** → **Create Token**.  
3. Name it (e.g., `LightSync`) and copy the token.  
4. Set it as an environment variable:  
   ```bash  
   export HA_TOKEN=&quot;your_token_here&quot;  # Linux/macOS  
   setx HA_TOKEN &quot;your_token_here&quot;    # Windows  
   ```  

## How It Works  
### Key Components  
1. **Screen Capture**:  
   - Uses `mss` to capture screen pixels at 10 FPS.  
   - Downsamples to 100x100px for performance.  
2. **Color Processing**:  
   - Converts RGB to luminance via `0.2126*R + 0.7152*G + 0.0722*B`.  
   - Maps screen brightness to light brightness using a power curve (`BRIGHTNESS_POWER_FACTOR`).  
3. **Home Assistant Integration**:  
   - WebSocket connection for real-time light control.  
   - REST API for initial state backup.  

### Update Logic  
| Condition                      | Action                           |  
|--------------------------------|----------------------------------|  
| ΔColor > 20 or ΔBrightness > 15 | Immediate light update           |  
| No changes for 5 seconds       | Force update to avoid HA timeouts|  

## Limitations  
1. **Single-Monitor Only**: Targets primary monitor (`sct.monitors[1]`).  
2. **Color Accuracy**:  
   - No gamma correction (uses linear RGB).  
   - Limited to 8-bit color depth.  
3. **Performance**:  
   - ~5% CPU usage on 4K displays (due to full-screen capture).  
4. **Network Dependency**: Requires stable connection to Home Assistant.  

## Configuration Reference  
```yaml  
# config.yaml  
home_assistant:  
  url: &quot;your_ha_url.com&quot;          # No http:// prefix  
  light_entity_id: &quot;light.example&quot;  

brightness:  
  min_threshold: 5                # Screen level to turn off light  
  power_factor: 0.8               # 1.0 = linear, 2.0 = aggressive dimming  

update:  
  color_threshold: 20             # ΔRGB to trigger update (Euclidean norm)  
  force_interval: 5               # Max seconds between updates  
