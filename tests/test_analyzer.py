import unittest
from unittest.mock import patch

from core.analyzer import analyze_transcript_for_viral_clips, candidate, generate_post_copy


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
                      {"start": 20, "end": 25, "text": "Inside the clip"}]
        settings = {"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "ollama", "llm_model": "qwen2.5:7b"}
        response = [{"title": "Faithful title", "hook": "An opening line", "music_track": "../music.wav"}]
        with patch("core.analyzer.ask_model", return_value=response) as local:
            copy = generate_post_copy(clip, transcript, "Source", settings, ("library.wav",))
        self.assertIn("Inside the clip", local.call_args.args[0])
        self.assertNotIn("Outside of clip", local.call_args.args[0])
        self.assertEqual(copy["copy_source"], "ai")
        self.assertEqual(copy["music_track"], "")


if __name__ == "__main__":
    unittest.main()
