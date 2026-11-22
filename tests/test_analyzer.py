import unittest
from unittest.mock import patch

from core.analyzer import analyze_transcript_for_viral_clips, candidate, generate_post_copy, posting_tags


class HighlightTests(unittest.TestCase):
    def test_checks_transcript_after_first_window(self):
        transcript = [
            {"start": seconds, "end": seconds + 10, "text": "Discussion " * 35}
            for seconds in range(0, 2400, 10)
        ]
        transcript[-1]["text"] = "A revealing final answer"

        def model(prompt, *_):
            return ([{"start_time": 2160, "end_time": 2400, "title": "The final answer", "score": 87}]
                    if "A revealing final answer" in prompt else [])

        with patch("core.analyzer.ask_model", side_effect=model) as local:
            clips = analyze_transcript_for_viral_clips(transcript, 2400, min_dur=180, max_dur=600, max_clips=1)
        self.assertGreater(local.call_count, 1)
        self.assertEqual(clips[0]["start_time"], 2160)
        self.assertEqual(clips[0]["selection"], "ai")

    def test_timeline_fallback_stays_within_short_or_silent_source(self):
        for length in (12, 180, 730):
            with self.subTest(length=length):
                clips = sorted(analyze_transcript_for_viral_clips([], length), key=lambda clip: clip["start_time"])
                self.assertTrue(clips)
                previous_end = 0
                for clip in clips:
                    start, end = clip["start_time"], clip["end_time"]
                    self.assertGreaterEqual(start, previous_end)
                    self.assertLessEqual(end, length)
                    self.assertGreaterEqual(end - start, min(30, length))
                    self.assertLessEqual(end - start, 60)
                    previous_end = end

    def test_rejects_unsafe_or_out_of_bounds_model_values(self):
        for start, end in ((-1, 35), (0, 101), (float("nan"), 35), (5, float("inf"))):
            with self.subTest(start=start, end=end):
                self.assertIsNone(candidate({"start_time": start, "end_time": end, "score": 87}, 20, 60, 0, 100))

    def test_post_copy_uses_only_selected_range_and_allowlisted_music(self):
        clip = {"start_time": 15, "end_time": 45}
        transcript = [{"start": 0, "end": 10, "text": "Outside of clip"},
                      {"start": 20, "end": 25, "text": "Inside the clip, a rider tests the wrench on a loose bolt."}]
        settings = {"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "ollama", "llm_model": "qwen2.5:7b"}
        response = [{"title": "Faithful title", "hook": "An opening line", "music_track": "../music.wav"}]
        with patch("core.analyzer.ask_model", return_value=response) as local:
            copy = generate_post_copy(clip, transcript, "Source", settings, ("library.wav",))
        self.assertIn("Inside the clip", local.call_args.args[0])
        self.assertNotIn("Outside of clip", local.call_args.args[0])
        self.assertEqual(copy["copy_source"], "ai")
        self.assertEqual(copy["music_track"], "")

    def test_posting_kit_only_uses_model_output(self):
        clip = {"start_time": 0, "end_time": 15}
        transcript = [{"start": 1, "end": 10, "text": "The loose crank bolt was making the bicycle click while pedaling."}]
        settings = {"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "ollama", "llm_model": "qwen2.5:7b"}
        with patch("core.analyzer.ask_model", return_value=[]):
            copy = generate_post_copy(clip, transcript, "Source", settings)
        self.assertEqual(copy["copy_source"], "model_unavailable")
        self.assertEqual(copy["hook"], "")
        self.assertEqual(copy["description"], "")

        with patch("core.analyzer.ask_model", return_value=[{"title": "The demo", "editing_tip": "Open on the first example."}]):
            copy = generate_post_copy(clip, transcript, "Source", settings)
        self.assertEqual(copy["copy_source"], "ai")
        self.assertEqual(copy["copy_model"], "qwen2.5:7b")
        self.assertEqual(copy["editing_tip"], "Open on the first example.")
        self.assertEqual(copy["hook"], "")
        self.assertEqual(copy["description"], "")


    def test_sparse_context_requires_notes_and_draft_validates_tags_and_evidence(self):
        settings = {"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "ollama", "llm_model": "qwen2.5:7b"}
        notes = "The rider tightens a loose crank bolt and the clicking stops."
        response = [{
            "title": "Where did the click go?",
            "title_variants": ["Where did the click go!", "Diagnose a noisy crank", "One wrench fixed the wobble"],
            "hook": "Listen to that click",
            "caption_lead": "The bike clicks on every pedal stroke.",
            "caption_body": "The rider tightens a loose crank bolt and spins the pedals again. Listen for the difference on the second pass.",
            "caption_cta": "Which pass sounds better? #Cycling",
            "pinned_comment": "How do you diagnose a noisy crank? #BikeRepair",
            "tags": ["#viral", "#BikeRepair", "#bikerepair", "Cycling", "CrankBolt"],
            "audience": "Cyclists maintaining their own bikes",
            "posting_tip": "Open on the clicking sound",
            "evidence": "tightens a loose crank bolt",
        }]
        with patch("core.analyzer.ask_model", return_value=response) as local:
            missing = generate_post_copy({}, [], "Bike repair", settings, excerpt="Hear that?")
            self.assertEqual(missing["copy_source"], "no_transcript")
            local.assert_not_called()
            copy = generate_post_copy({}, [], "Bike repair", settings, excerpt="Hear that?", notes=notes)
            local.assert_called_once()
            self.assertIn("schema", local.call_args.kwargs)
        self.assertTrue(copy["copy_ready"])
        self.assertEqual(copy["tags"], ["BikeRepair", "Cycling", "CrankBolt"])
        self.assertEqual(copy["caption_cta"], "Which pass sounds better?")
        self.assertEqual(copy["pinned_comment"], "How do you diagnose a noisy crank?")
        self.assertEqual(copy["title_variants"], ["Diagnose a noisy crank", "One wrench fixed the wobble"])
        self.assertEqual(len(copy["description"].split("\n\n")), 3)
        self.assertEqual(copy["evidence"], "tightens a loose crank bolt")

    def test_posting_kit_uses_one_verified_edition_and_limits_tags(self):
        settings = {"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "ollama", "llm_model": "qwen2.5:7b"}
        raw = {
            "title": "GTA 6: From Pickup to Sports Car #gaming",
            "title_variants": ["The Pickup Gets Left Behind in GTA VI", "One Switch Changes the Drive"],
            "hook": "Watch the car swap",
            "caption_lead": "The player switches cars in GTA V.",
            "caption_body": "The player swaps a black pickup for a red sports car.",
            "pinned_comment": "Which car would you pick?",
            "tags": ["#robery", "#Gaming", "#GTAVI", "#GTAV", "#CarSwap", "#SportsCar", "#GTAGameplay"],
            "audience": "Players who like free roam", "posting_tip": "Open on the car swap",
            "evidence": "swaps a black pickup for a red sports car",
        }
        notes = "In GTA V, the player swaps a black pickup for a red sports car."
        with patch("core.analyzer.ask_model", return_value=[raw]) as model:
            copy = generate_post_copy({}, [], "GTA 6 PS5 Gameplay", settings, excerpt="", notes=notes,
                                      researched_hashtags=("GTA6",))
        model.assert_called_once()
        self.assertIn("You cannot see footage", model.call_args.args[0])
        self.assertTrue(copy["copy_ready"])
        self.assertEqual(copy["title"], "GTA: From Pickup to Sports Car")
        self.assertIn("GTA V", copy["description"])
        self.assertEqual(copy["tags"], ["GTAV", "CarSwap", "SportsCar"])
        self.assertEqual(posting_tags(["GTA_6", "GTAVGameplay", "GTAViceCity", "CarSwap", "PS5"]), ["GTAViceCity", "CarSwap"])
        self.assertEqual(posting_tags(["GTAV", "GTA6", "CarSwap"], "GTA V and GTA VI are both mentioned."), ["CarSwap"])


if __name__ == "__main__":
    unittest.main()
