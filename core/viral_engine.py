import json
import os
import re
import sys
from pathlib import Path

# Locate skills directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"

# Load hookscore and title linter if available
YT_SCRIPT_DIR = SKILLS_DIR / "yt-script"
YT_PACKAGE_DIR = SKILLS_DIR / "yt-package"

if str(YT_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(YT_SCRIPT_DIR))
if str(YT_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(YT_PACKAGE_DIR))

try:
    import hookscore
except ImportError:
    hookscore = None

try:
    import title as title_linter
except ImportError:
    title_linter = None

# Load the 21 hook formulas from yt-script/hooks.json
HOOKS_JSON_PATH = YT_SCRIPT_DIR / "hooks.json"
HOOK_FORMULAS = []
if HOOKS_JSON_PATH.is_file():
    try:
        with open(HOOKS_JSON_PATH, "r", encoding="utf-8") as f:
            HOOK_FORMULAS = json.load(f).get("hooks", [])
    except Exception as e:
        print(f"Warning: Could not load hooks.json: {e}")


def get_viral_hook_guidance() -> str:
    """Returns condensed hook formula guidelines for the local LLM prompt."""
    return (
        "VIRAL YOUTUBE SHORTS RULES (from youtube-agent-skill):\n"
        "1. TITLE: Must be 35-55 characters (so it never truncates on mobile). "
        "Use 1 or 2 ALL-CAPS words for emphasis (e.g. TRAPPED, WORST, BUSTED, IMPOSSIBLE). "
        "Always include a concrete figure, number ($10K, 30 mins), or specific name. "
        "Always append #Shorts at the very end.\n"
        "2. OPENING ON-SCREEN HOOK: 2 to 5 words max. Must NOT repeat the title words! "
        "(Title and thumbnail/hook must complement each other, not say the same thing twice). "
        "Must create a curiosity gap that stops the thumb in 1.5 seconds.\n"
        "3. PINNED COMMENT: Must ask a direct, polarizing 'What would you do?' or 'A vs B' question "
        "that forces viewers into the comments (e.g. 'Be honest: are you taking the easy $10K or risking it all? 👇'). "
        "Comment velocity is the #1 signal for the algorithm.\n"
        "4. CAPTION: Engaging 1-2 sentence story. Include a subscribe call-to-action: "
        "'Subscribe to SnapOdd for daily gaming highlights! 🔔' and 3-4 clean tags (#GTA6 #Gaming #Shorts #MrBeast).\n"
        "5. PROVEN VIRAL HOOK STYLES TO USE:\n"
        "   - The Dilemma / Stakes First: 'Safe $10K or Risk Dying For $100K?'\n"
        "   - The Unexpected Twist / In Media Res: 'He Thought He Escaped Until... 💀'\n"
        "   - The Mistake / Warning: 'The Worst Place To Hide In GTA 6 😭'\n"
        "   - The Disbelief / Scale: 'Bro Somehow LOST Money Robbing A Bank?! 💀'\n"
    )


def score_hook(hook_text: str) -> dict:
    """Scores an on-screen hook using hookscore.py heuristic."""
    if not hook_text or not hookscore:
        return {"score": 50, "band": "WORKABLE", "formula": "Unclassified", "weakest": None}
    try:
        parts, verdict, name, hits = hookscore.score(hook_text)
        return {
            "score": int(verdict),
            "band": hookscore.band(verdict),
            "formula": name,
            "hits": hits,
            "parts": parts,
            "weakest": min(parts, key=parts.get) if parts else None,
            "tip": hookscore.FIX.get(min(parts, key=parts.get)) if parts else None,
        }
    except Exception as e:
        return {"score": 50, "band": "WORKABLE", "formula": "Unclassified", "error": str(e)}


def lint_title_and_hook(title: str, hook: str = None) -> dict:
    """Lints title and hook pairing using title.py."""
    if not title or not title_linter:
        return {"score": 75, "issues": [], "good": []}
    try:
        # Strip #Shorts before linting length if desired, but title.check handles it
        clean_title = re.sub(r"#shorts\b", "", title, flags=re.I).strip()
        result = title_linter.check(clean_title, hook)
        return result
    except Exception as e:
        return {"score": 70, "issues": [("error", str(e))], "good": []}


def evaluate_clip_packaging(title: str, hook: str, pinned_comment: str = "") -> dict:
    """Calculates overall viral packaging score and recommendations."""
    t_lint = lint_title_and_hook(title, hook)
    h_eval = score_hook(hook)

    # Composite packaging score
    t_score = t_lint.get("score", 70)
    h_score = h_eval.get("score", 50)
    has_comment = bool(pinned_comment and "?" in pinned_comment)
    
    # Weight: 55% title linter, 35% hook score, 10% pinned comment engagement prompt
    packaging_score = int(round(0.55 * t_score + 0.35 * h_score + (10 if has_comment else 0)))
    packaging_score = max(10, min(100, packaging_score))

    feedback = []
    if t_lint.get("good"):
        feedback.extend([f"✓ {item}" for item in t_lint["good"][:2]])
    if t_lint.get("issues"):
        feedback.extend([f"⚠ {msg}" for _, msg in t_lint["issues"][:2]])
    if h_eval.get("formula") and h_eval["formula"] != "Unclassified":
        feedback.append(f"★ Hook: {h_eval['formula']}")
    elif h_eval.get("tip"):
        feedback.append(f"Tip: {h_eval['tip']}")

    verdict = "VIRAL READY" if packaging_score >= 85 else "STRONG" if packaging_score >= 70 else "NEEDS POLISH"

    return {
        "packaging_score": packaging_score,
        "packaging_verdict": verdict,
        "hook_formula": h_eval.get("formula", "Unclassified"),
        "hook_score": h_score,
        "title_score": t_score,
        "packaging_feedback": " | ".join(feedback),
    }


def score_candidate_opening(speech_text: str) -> int:
    """Scores candidate clip speech to prioritize high-stakes, curious openings."""
    if not speech_text or not hookscore:
        return 50
    # Take first 15 words
    words = speech_text.split()[:15]
    first_sentence = " ".join(words)
    try:
        _, verdict, _, _ = hookscore.score(first_sentence)
        return int(verdict)
    except Exception:
        return 50
