import unittest
from unittest.mock import Mock, patch

import app


class StudioLaunchTests(unittest.TestCase):
    @patch("app.uvicorn.Server")
    @patch("app.webbrowser.open")
    @patch("app.requests.get")
    def test_existing_studio_reopens_without_starting_another_server(self, get, open_browser, server):
        get.return_value = Mock(ok=True)
        get.return_value.json.return_value = {
            "paths": {"/api/edit/{folder_name}/{filename}/audio-tracks": {"post": {}}},
        }

        self.assertEqual(app.launch_studio(open_browser=True), 0)
        open_browser.assert_called_once_with("http://127.0.0.1:8000")
        server.assert_not_called()

    @patch("app.uvicorn.Server")
    @patch("app.webbrowser.open")
    @patch("app.requests.get")
    def test_older_studio_is_reported_without_starting_another_worker(self, get, open_browser, server):
        get.return_value = Mock(ok=True)
        get.return_value.json.return_value = {
            "paths": {"/api/edit/{folder_name}/{filename}/separate-audio": {"post": {}}},
        }

        with patch("builtins.print") as print_status:
            self.assertEqual(app.launch_studio(open_browser=True), 1)
        self.assertIn("older studio", print_status.call_args.args[0])
        open_browser.assert_not_called()
        server.assert_not_called()
