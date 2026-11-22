from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import downloader, studio_pipeline


class StudioPipelineTests(unittest.TestCase):
    def test_highlights_use_audio_and_fetch_only_the_selected_video_range(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = str(root / "example_audio.webm")
            request = SimpleNamespace(
                source_type="url", url="https://www.youtube.com/watch?v=abcdefghijk", selection="highlights",
                clip_length=next(iter(studio_pipeline.CLIP_LENGTHS)), output_resolution=720,
                mode="standard", mirror=False, playback_speed=1,
                audio_speed=False, color_grade=False, color_look="original", edge_fades=False,
                custom_opening_text="", auto_delete_source=True,
            )
            settings = dict(
                whisper_model_size="base", whisper_device="cpu", llm_base_url="local", llm_api_key="", llm_model="qwen",
                max_clips_to_generate=1, music_mode="original", opening_hook=False, captions=True, caption_style="bold",
                caption_color="white", brand_name="",
            )
            info = {"title": "Example", "id": "abcdefghijk", "duration": 120}
            transcript = [{"start": 5, "end": 10, "text": "Check the requested range."}]
            cleanup = Mock()
            with patch.multiple(
                studio_pipeline, OUTPUT_DIR=root, fetch_subtitles_only=Mock(return_value={"info": info, "subtitle_path": None}),
                download_audio_only=Mock(return_value={"audio_path": audio}), download_video=Mock(),
                download_video_section=Mock(return_value=True), get_transcript=Mock(return_value=transcript),
                analyze_transcript_for_viral_clips=Mock(return_value=[{"start_time": 5, "end_time": 25}]),
                generate_post_copy=Mock(return_value={"copy_source": "ai", "hook": ""}), save_task_info=Mock(),
                generate_ass_subtitles=Mock(), render_viral_clip=Mock(), generate_clip_metadata=Mock(),
            ):
                self.assertEqual(studio_pipeline.run_job("test", request, settings, Mock(), cleanup), 1)
                self.assertEqual(studio_pipeline.download_audio_only.call_args.kwargs["info"], info)
                self.assertEqual(studio_pipeline.get_transcript.call_args.kwargs["audio_path"], audio)
                self.assertEqual(studio_pipeline.download_video_section.call_args.args[:3], (request.url, 5, 25))
                self.assertIsNone(studio_pipeline.render_viral_clip.call_args.kwargs["source_audio_path"])
                studio_pipeline.download_video.assert_not_called()
            cleanup.assert_called_once_with([audio])

    def test_audio_only_webm_cannot_be_reused_as_a_full_video(self):
        with TemporaryDirectory() as tmp, patch.object(downloader, "DOWNLOADS_DIR", Path(tmp)):
            path = Path(tmp) / "Example_abcdefghijk_audio.webm"
            path.write_bytes(b"audio" * 1024)
            with patch.object(downloader, "run_download") as download:
                result = downloader.download_audio_only("https://example.test", info={"title": "Example", "id": "abcdefghijk"})
            self.assertIn("Example_abcdefghijk_audio.%(ext)s", " ".join(download.call_args.args[0]))
            self.assertEqual(result["audio_path"], str(path))
            self.assertIsNone(downloader.check_existing_download("Example", "abcdefghijk")["video_path"])

    def test_full_download_keeps_subtitle_default_for_cli_callers(self):
        with TemporaryDirectory() as tmp, patch.multiple(
            downloader, DOWNLOADS_DIR=Path(tmp), extract_video_info=Mock(return_value={"title": "Example"}), run_download=Mock(),
        ):
            downloader.download_video("https://example.test")
            downloader.download_video("https://example.test", include_subtitles=False)
            commands = [call.args[0] for call in downloader.run_download.call_args_list]
            self.assertIn("--write-subs", commands[0])
            self.assertNotIn("--write-subs", commands[1])
