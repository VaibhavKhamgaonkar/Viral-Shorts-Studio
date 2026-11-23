---
name: faceless-short-skill
description: >-
  Create, script, render, and automate faceless short-form explainer videos for YouTube Shorts, TikTok, and Instagram Reels. Use when the user asks to create faceless videos, short video explainers, script-to-video shorts, YouTube Shorts automation, or publish and schedule vertical video content.
---

# Faceless Short Explainer Skill

An end-to-end framework and runbook for creating high-retention, studio-quality **faceless short video explainers** (9:16 vertical, 1080x1920) for YouTube Shorts, TikTok, and Instagram Reels.

This skill equips the agent to manage the complete lifecycle:
1. **Topic Research & Viral Angle Ideation**
2. **High-Retention Scriptwriting & Word-Level Timing**
3. **Scene Storyboarding & Visual Asset Direction**
4. **Voiceover (TTS), Dynamic Captions & Sound Design**
5. **Programmatic / Cloud Video Rendering**
6. **YouTube Shorts SEO, Safe-Zone Compliance & Publishing**

---

## 1. Golden Rules of Short Video Explainers

1. **No Filler, No Greetings**: Never start with "Hey guys", "Welcome back", or channel branding. The first 1.5 seconds determine whether the viewer stays or swipes.
2. **Hook with a Curiosity Gap**: Open with a contrarian truth, startling statistic, or unfinished question.
3. **Visual Change Every 2-3 Seconds**: Attention resets every 2.5 seconds. Alter camera angle, switch visual assets, animate kinetic typography, or zoom in/out.
4. **Keep Text in Safe Zones**: Mobile apps overlay UI (channel name, like button, sound icon) that hides content outside the safe zone. See [Safe Zones Guide](references/safe-zones.md).
5. **Seamless Loop**: The final sentence should flow naturally into the first sentence so the short plays continuously on loop.

---

## 2. Retention Script Architecture (The 4-Beat Short)

Target duration: **30 to 55 seconds** (65 to 120 spoken words at 140 WPM).

```
[0:00 - 0:03] BEAT 1: THE HOOK
Pattern interrupt or provocative statement that forces the brain to pause.
Example: "You are breathing plastic right now, but not the way you think."

[0:03 - 0:12] BEAT 2: THE PROBLEM / PREMISE
Ground the topic in a concrete dilemma or relatable mystery.
Example: "Scientists just sampled cloud vapor over Mount Fuji and found synthetic micro-particles."

[0:12 - 0:42] BEAT 3: THE CORE EXPLANATION (3 MICRO-BEATS)
Deliver 2 to 3 rapid, sequential insights with kinetic diagrams or visual shifts:
- Micro-beat A: How it gets there (evaporation, ocean spray).
- Micro-beat B: What it actually does (seeds acid rain, enters food cycles).
- Micro-beat C: The surprising twist or implication.

[0:42 - 0:55] BEAT 4: THE REVELATION & INFINITE LOOP
Deliver the punchline or takeaway, ending on a phrase that leads directly back into Beat 1.
Example: "...which means every time it rains outdoors..." -> (loops back to "You are breathing plastic...")
```

See [Script Templates](references/script-templates.md) for pre-built hooks and scripts across Tech, Science, History, and Business niches.

---

## 3. Scene Storyboard & Visual Composition

Every scene must define:
- **Duration**: 2.0s – 3.5s per scene.
- **Visual Focus**: Exactly 1 primary visual element (kinetic typography, diagram, icon animation, or B-roll video).
- **Background Layer**: Dark aesthetic (#0a0a0f), subtle abstract gradient mesh, grid plane, or ambient loop.
- **Accent Highlight**: Neon green (`#00FF66`), golden amber (`#FFB800`), or electric cyan (`#00E5FF`).

### Scene Specification Schema (`storyboard.json`):
```json
{
  "title": "Why Concorde Really Stopped Flying",
  "aspectRatio": "9:16",
  "resolution": [1080, 1920],
  "fps": 30,
  "scenes": [
    {
      "sceneId": 1,
      "timeRange": [0.0, 3.2],
      "narration": "Concorde wasn't grounded because of one crash.",
      "visualType": "kinetic_text_with_split_render",
      "visualElements": {
        "text": "THE CONCORDE MYTH",
        "graphic": "jet_silhouette_crosshair",
        "motion": "zoom_in_3pct"
      },
      "sfx": "whoosh_bass_drop"
    }
  ]
}
```

---

## 4. Voiceover & Kinetic Captioning

### Text-to-Speech (TTS) Specifications
- **Voice Style**: Deep, authoritative, confident documentary narrator (e.g. ElevenLabs *Adam*, *George*, or OpenAI *Onyx*).
- **Speaking Rate**: 1.05x to 1.1x speed for snappy, high-energy retention.
- **Audio Levelling**:
  - Voice track: Peak at -1.0 dBFS (loud and crisp).
  - Background Music (BGM): Ducked to -20 dBFS to -24 dBFS during speech.
  - SFX (whooshes, pops, risers): -10 dBFS to -14 dBFS.

### Kinetic Captions (Word-by-Word Animation)
- **Word Chunking**: Display 1 to 3 words simultaneously on screen.
- **Highlighting**: Highlight current spoken word with active color (Yellow `#FFDD00` or Lime `#22C55E`).
- **Font**: Ultra-bold sans-serif: `Montserrat Black`, `Impact`, `The Bold Font`, or `Inter ExtraBold`.
- **Placement**: Centered at Y: 1100px – 1300px (directly in viewer's natural eye-line).

---

## 5. Rendering Engine Options

Choose the rendering engine that fits your project setup:

### Option A: Cloud Automated Engine via Faceless CLI (`faceless.so`)
Fastest all-in-one method for script-to-video, voice synthesis, automated captioning, and direct publishing:
```bash
# Verify or run CLI
npx -y faceless-cli --help

# Create a video from a script
npx -y faceless-cli videos create --script "Your narration script here..." --voice "adam" --style "realistic"

# Check render status & download
npx -y faceless-cli videos status <video-id>

# Auto-publish or schedule to YouTube Shorts
npx -y faceless-cli posts publish --video-id <video-id> --platform youtube --title "Why Planes Can't Fly in Space #Shorts"
```
See [CLI Reference](references/cli-reference.md) for full parameter details.

### Option B: Local Programmatic Remotion / FFmpeg Engine
For 100% free, offline, code-driven rendering without external subscription fees:
1. Initialize Remotion project with 9:16 template (`1080x1920`).
2. Generate voiceover and word timestamps via local TTS (Kokoro/Edge-TTS/Whisper).
3. Render frame-accurate TSX components with dynamic captions.

### Option C: InVideo AI Plugin
Connect via the official InVideo plugin (`antigravity-plugins-partners/plugin/invideo`) for AI-assisted timeline editing.

---

## 6. YouTube Shorts Optimization Checklist

Before publishing, verify all items:
- [ ] **Aspect Ratio**: Exact 9:16 vertical (1080 x 1920 px).
- [ ] **Length**: Between 30s and 58s (never exceed 60 seconds).
- [ ] **Safe-Zone Check**: No captions or crucial graphics in top 220px or bottom 380px.
- [ ] **Title**: Under 55 characters, curiosity-driven + `#Shorts` tag.
- [ ] **Thumbnail Frame**: Pick the most intriguing visual frame in YouTube Studio.
- [ ] **Description**: 2-3 sentence teaser, 3-5 relevant hashtags (`#Shorts #Tech #Explained`).
- [ ] **Audio Bed**: Royalty-free or trending audio from YouTube library.
