import unittest

from core.hashtag_research import relevant_hashtags


class HashtagResearchTests(unittest.TestCase):
    def test_uses_explicit_topic_matches_from_distinct_short_videos(self):
        videos = [
            {"id": "abcdefghij1", "title": "#GTA5 #gta5 #police", "duration": 42, "view_count": 4500,
             "description": "#gta6 #fyp #ScorpioStatus", "tags": ["GTA5HiddenTag"]},
            {"id": "abcdefghij2", "description": "#GTA5 #GTAV #police", "duration": 50, "view_count": 2500},
            {"id": "abcdefghij3", "title": "#GTAV", "duration": 48, "view_count": 900},
            {"id": "abcdefghij1", "description": "#gta5", "duration": 42, "view_count": 4500},
            {"id": "abcdefghij4", "title": "#PoliceCar", "duration": 800, "view_count": 9500000},
            {"id": "abcdefghij5", "title": "#GTAVMods", "duration": 40, "view_count": 9000},
            {"id": "../../local", "title": "#GTAV", "duration": 40, "view_count": 99999},
        ]
        sampled, tags = relevant_hashtags("GTA V police car", videos, exclude_id="abcdefghij5")
        self.assertEqual(sampled, 3)
        self.assertEqual({tag["tag"].casefold(): tag["matches"] for tag in tags},
                         {"gta5": 2, "gtav": 2, "police": 2})
        self.assertEqual(tags[0]["video_id"], "abcdefghij1")
