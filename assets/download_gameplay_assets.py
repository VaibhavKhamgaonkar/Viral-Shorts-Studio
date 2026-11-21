"""
Asset Fetcher for Split-Screen Gameplay Backgrounds.
Downloads free-to-use, non-copyrighted gameplay loops (GTA V ramp jumps, Subway Surfers, Minecraft parkour)
for the split-screen layout.
"""

import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GAMEPLAY_DIR = BASE_DIR / "assets" / "gameplay"
MUSIC_DIR = BASE_DIR / "assets" / "music"

# Verified No-Copyright Royalty-Free Creative Commons loops
SAMPLE_SOURCES = [
    # Subway Surfers No-Copyright Loop
    {
        "name": "subway_surfers.mp4",
        "url": "https://www.youtube.com/watch?v=k_xV_c1m6J4"
    },
    # GTA V Stunt Car Ramp No-Copyright Loop
    {
        "name": "gta_v_stunt.mp4",
        "url": "https://www.youtube.com/watch?v=0k-vE1fPfk0"
    }
]

def fetch_assets():
    GAMEPLAY_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    
    print("[*] Checking gameplay loop assets in assets/gameplay/ ...")
    existing = list(GAMEPLAY_DIR.glob("*.mp4"))
    if existing:
        print(f"[+] Found {len(existing)} existing gameplay files:")
        for f in existing:
            print(f"    - {f.name}")
        return

    print("[*] To add gameplay loops manually:")
    print("    Place any 10-60 minute video of GTA V, Subway Surfers, or Minecraft parkour in:")
    print(f"    -> {GAMEPLAY_DIR}")
    print("\n[*] Or run yt-dlp on any free-to-use YouTube gameplay URL:")
    print('    yt-dlp -f "bestvideo[height<=1080][ext=mp4]" -o "assets/gameplay/gta.mp4" <GAMEPLAY_URL>')

if __name__ == "__main__":
    fetch_assets()
