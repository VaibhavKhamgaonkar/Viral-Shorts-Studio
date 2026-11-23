# Mobile Viewport & Safe-Zone Guide

When designing vertical short-form explainer videos (1080 x 1920, 9:16 aspect ratio), social platforms overlay heavy native UI controls (search bar, profile icons, action buttons, title description). Placing critical text or visual subjects in these overlay zones causes them to be covered.

---

## 1. Pixel Coordinate Safe-Zones (1080 x 1920 px)

```
+--------------------------------------------------------+ (0, 0)
|                                                        |
|   TOP BUFFER: 0 to 220 px (UNSAFE)                     |
|   [Search bar, sound selection pill, camera icons]     |
|                                                        |
+--------------------------------------------------------+ Y: 220 px
|                                                        |
|                                                        |
|   PRIMARY VISUAL ZONE: Y: 220 px to 850 px             |
|   - Diagram animations                                 |
|   - B-roll video footage & 3D models                   |
|   - Data visualizations & illustrations                |
|                                                        |
|                                                        |
+--------------------------------------------------------+ Y: 850 px
|                                                        |
|   GOLDEN CAPTION ZONE: Y: 900 px to 1350 px            |
|   - Dynamic word-by-word kinetic captions              |
|   - Natural viewer eye-rest position                   |
|                                                        |
+--------------------------------------------------------+ Y: 1350 px
|                                                        |
|   SECONDARY GRAPHIC ZONE: Y: 1350 px to 1540 px        |
|                                                        |
+--------------------------------------------------------+ Y: 1540 px
|                                      | RIGHT BUFFER    |
|   BOTTOM BUFFER: 1540 to 1920 px     | X: 960-1080 px  |
|   (UNSAFE)                           | (UNSAFE)        |
|   [Video title, channel handle,      | [Like, Dislike, |
|    Subscribe pill, music track title]|  Comments,      |
|                                      |  Share, Remix]  |
+--------------------------------------------------------+ (1080, 1920)
```

---

## 2. Platform Safe-Zone Rules

### YouTube Shorts
- **Top Offset**: Keep clear above `Y = 220 px`.
- **Bottom Offset**: Keep clear below `Y = 1540 px`. The bottom 380 px contains channel icon, username, subscribe button, and title line.
- **Right Margin**: Keep at least `120 px` from the right edge between `Y = 800 px` and `1600 px` for action buttons.
- **Left Margin**: Keep at least `60 px` padding.

### TikTok & Instagram Reels Compatibility
- Safe-zones align closely with YouTube Shorts. Staying within the **1080 x 1320 px** center rectangle guarantees 100% visibility across YouTube Shorts, TikTok, and Instagram Reels simultaneously.

---

## 3. Kinetic Caption Styling Best Practices

| Property | Recommended Value | Reason |
| :--- | :--- | :--- |
| **Font Family** | Montserrat Black, Impact, Anton, Inter ExtraBold | Maximum legibility on mobile screens |
| **Font Size** | 68 px to 84 px | Easily readable without covering the background |
| **Text Transform** | UPPERCASE | High impact, faster subconscious recognition |
| **Max Words on Screen** | 1 to 3 words | Keeps viewer eyes locked on the rhythmic flow |
| **Active Word Color** | `#FFDD00` (Yellow) or `#00FF66` (Neon Green) | Contrast against dark backgrounds |
| **Inactive Word Color**| `#FFFFFF` (White) | Clean baseline contrast |
| **Text Shadow / Stroke** | 4px Black Stroke or `0 8px 24px rgba(0,0,0,0.85)` | Ensures readability regardless of scene brightness |
