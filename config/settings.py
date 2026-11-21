import json
import os
from tempfile import NamedTemporaryFile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "settings.json"

DEFAULT_SETTINGS = {
    # Local LLM Engine (Ollama / Local OpenAI-compatible)
    "llm_base_url": "http://localhost:11434/v1",
    "llm_api_key": "ollama",
    "llm_model": "qwen2.5:7b",
    
    # Video Output Specifications
    "output_width": 1080,
    "output_height": 1920,
    "fps": 30,
    
    # Video formatting
    "default_mode": "smart_blur",
    "enable_hflip": False,           # Horizontal mirror
    "audio_tempo": 1.04,
    "color_contrast": 1.05,          # Subtle color shift
    "color_saturation": 1.08,
    "bg_music_volume": 0.12,         # 12% background music volume
    
    # Clip Target Duration
    "clip_duration_min": 30,
    "clip_duration_max": 60,
    "max_clips_to_generate": 3,
    
    # Transcription Settings
    "whisper_model_size": "base",    # "tiny", "base", "small", "medium"
    "whisper_device": "auto"         # "cuda" or "cpu" or "auto"
}

def load_settings():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
                settings = DEFAULT_SETTINGS.copy()
                settings.update(user_settings)
                return settings
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(new_settings):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_FILE.parent, delete=False) as temp:
        try:
            json.dump(new_settings, temp, indent=4, ensure_ascii=False)
            temp.write("\n")
            temp.close()
            os.replace(temp.name, CONFIG_FILE)
        finally:
            temp.close()
            Path(temp.name).unlink(missing_ok=True)
    return new_settings
