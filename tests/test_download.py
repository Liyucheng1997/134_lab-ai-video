import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import config, download


class YtdlpAccessArgsTests(unittest.TestCase):
    def test_prefers_an_existing_cookie_file_over_the_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with (
                patch.object(config, "YTDLP_COOKIE_FILE", str(cookie_file)),
                patch.object(config, "YTDLP_COOKIES_FROM_BROWSER", "chrome"),
            ):
                self.assertEqual(
                    config.ytdlp_cookie_args(),
                    ["--cookies", str(cookie_file)],
                )

    def test_falls_back_to_the_browser_when_the_cookie_file_is_missing(self):
        with (
            patch.object(config, "YTDLP_COOKIE_FILE", "F:/nope/missing.txt"),
            patch.object(config, "YTDLP_COOKIES_FROM_BROWSER", "edge"),
        ):
            self.assertEqual(
                config.ytdlp_cookie_args(),
                ["--cookies-from-browser", "edge"],
            )

    def test_returns_no_cookie_args_when_nothing_is_configured(self):
        with (
            patch.object(config, "YTDLP_COOKIE_FILE", ""),
            patch.object(config, "YTDLP_COOKIES_FROM_BROWSER", ""),
        ):
            self.assertEqual(config.ytdlp_cookie_args(), [])

    def test_challenge_args_carry_the_js_runtime_and_remote_components(self):
        with (
            patch.object(config, "YTDLP_JS_RUNTIME", "node:D:/Nodejs/node.exe"),
            patch.object(config, "YTDLP_REMOTE_COMPONENTS", "ejs:github"),
        ):
            self.assertEqual(
                config.ytdlp_challenge_args(),
                [
                    "--js-runtimes", "node:D:/Nodejs/node.exe",
                    "--remote-components", "ejs:github",
                ],
            )

    def test_remote_components_can_be_disabled_by_emptying_the_setting(self):
        with (
            patch.object(config, "YTDLP_JS_RUNTIME", "node:node.exe"),
            patch.object(config, "YTDLP_REMOTE_COMPONENTS", ""),
        ):
            self.assertEqual(
                config.ytdlp_challenge_args(),
                ["--js-runtimes", "node:node.exe"],
            )

    def test_access_args_combine_cookies_and_challenge_flags(self):
        with (
            patch.object(config, "YTDLP_COOKIE_FILE", ""),
            patch.object(config, "YTDLP_COOKIES_FROM_BROWSER", "firefox"),
            patch.object(config, "YTDLP_JS_RUNTIME", "deno:deno.exe"),
            patch.object(config, "YTDLP_REMOTE_COMPONENTS", "ejs:github"),
        ):
            self.assertEqual(
                config.ytdlp_access_args(),
                [
                    "--cookies-from-browser", "firefox",
                    "--js-runtimes", "deno:deno.exe",
                    "--remote-components", "ejs:github",
                ],
            )


class DownloadFailureMessageTests(unittest.TestCase):
    def test_an_unrecognised_failure_keeps_the_plain_message(self):
        self.assertEqual(download._download_failure_message(), "yt-dlp 下载失败")

    def test_a_bot_check_without_cookies_asks_for_cookie_configuration(self):
        with patch.object(config, "ytdlp_cookie_args", return_value=[]):
            message = download._download_failure_message(saw_bot_check=True)
        self.assertIn("YTDLP_COOKIE_FILE", message)

    def test_a_bot_check_with_cookies_points_at_expiry_and_rate_limits(self):
        with patch.object(
            config, "ytdlp_cookie_args", return_value=["--cookies", "c.txt"]
        ):
            message = download._download_failure_message(saw_bot_check=True)
        self.assertIn("已失效", message)
        self.assertIn("429", message)

    def test_a_missing_format_without_a_js_runtime_asks_for_one(self):
        with patch.object(config, "ytdlp_challenge_args", return_value=[]):
            message = download._download_failure_message(saw_no_format=True)
        self.assertIn("JS 运行时", message)

    def test_a_missing_format_with_a_js_runtime_blames_ejs_or_restrictions(self):
        with patch.object(
            config, "ytdlp_challenge_args", return_value=["--js-runtimes", "node"]
        ):
            message = download._download_failure_message(saw_no_format=True)
        self.assertIn("EJS", message)

    def test_a_bot_check_wins_when_both_symptoms_appear(self):
        with (
            patch.object(config, "ytdlp_cookie_args", return_value=[]),
            patch.object(config, "ytdlp_challenge_args", return_value=[]),
        ):
            message = download._download_failure_message(
                saw_bot_check=True, saw_no_format=True
            )
        self.assertIn("YTDLP_COOKIE_FILE", message)


class YtdlpStreamDetectionTests(unittest.TestCase):
    """yt-dlp 的失败原因只出现在输出流里，必须逐行识别。"""

    def _run_with_output(self, lines: list[str]) -> str:
        class FakeProc:
            def __init__(self):
                self.stdout = iter(lines)

            def wait(self):
                return 1

        with (
            patch.object(download.subprocess, "Popen", return_value=FakeProc()),
            patch.object(config, "ytdlp_cookie_args", return_value=[]),
            patch.object(config, "ytdlp_challenge_args", return_value=[]),
            self.assertRaises(RuntimeError) as caught,
        ):
            download._ytdlp_stream(["yt-dlp"])
        return str(caught.exception)

    def test_detects_the_youtube_bot_check(self):
        message = self._run_with_output([
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot.",
        ])
        self.assertIn("YTDLP_COOKIE_FILE", message)

    def test_detects_rate_limiting(self):
        message = self._run_with_output([
            "WARNING: Unable to download webpage: HTTP Error 429: Too Many Requests",
        ])
        self.assertIn("YTDLP_COOKIE_FILE", message)

    def test_detects_a_missing_video_format(self):
        message = self._run_with_output([
            "WARNING: Only images are available for download.",
            "ERROR: Requested format is not available.",
        ])
        self.assertIn("JS 运行时", message)


if __name__ == "__main__":
    unittest.main()
