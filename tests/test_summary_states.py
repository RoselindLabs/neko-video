"""Fixed public outcomes, no platform requests or model calls."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from neko_video.official import OfficialVideoService
from neko_video.presentation import render
from neko_video.transport import ProviderError


class SummaryStates(unittest.IsolatedAsyncioTestCase):
    async def test_douyin_disabled_unobtained_failed_empty_and_available(self):
        for enabled, cookie, generation, streamed, expected in (
            (False, "", {}, b"", "disabled"),
            (True, "", {}, b"", "not_obtained"),
            (True, "synthetic", ProviderError("secret diagnostic"), b"", "error"),
            (True, "synthetic", {"status_code": 0, "business_data": []}, b"", "not_provided"),
            (True, "synthetic", {"status_code": 0}, b"", "not_obtained"),
        ):
            with self.subTest(expected=expected):
                service = OfficialVideoService(
                    SimpleNamespace(enable_ai_summary=enabled),
                    SimpleNamespace(douyin_cookie=AsyncMock(return_value=cookie)),
                    None,
                )
                with (
                    patch(
                        "neko_video.official.request_bytes", AsyncMock(return_value=(streamed, {}))
                    ),
                    patch("neko_video.official.basic_douyin", return_value={"title": "synthetic"}),
                    patch("neko_video.official.chapter_douyin", return_value={}),
                    patch(
                        "neko_video.official.request_json",
                        AsyncMock(
                            side_effect=generation if isinstance(generation, Exception) else None,
                            return_value=(generation, {}),
                        ),
                    ),
                ):
                    result = await service._douyin("7123456789012345678")
                self.assertEqual(result["summary_status"], expected)
                self.assertNotIn("暂无", render(result))
                self.assertNotIn("secret diagnostic", render(result))

    async def test_info_p_parameter_reports_first_part_without_changing_source(self):
        service = OfficialVideoService(
            SimpleNamespace(enable_ai_summary=False, cache_ttl_seconds=60), None, None
        )
        with patch("neko_video.official.Video") as video:
            video.return_value.get_info = AsyncMock(
                return_value={"title": "synthetic", "bvid": "BV17x411w7KC", "videos": 3}
            )
            result = await service.summary("https://www.bilibili.com/video/BV17x411w7KC?p=2")
        self.assertIn("默认首P（P1）", render(result))
        self.assertIn("忽略?p", render(result))
        self.assertNotIn("当前分P", render(result))
