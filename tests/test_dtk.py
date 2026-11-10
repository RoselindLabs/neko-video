import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from neko_video.config import Config
from neko_video.dtk import ENDPOINT, media_result, resolve_dtk, target
from neko_video.media import MediaError, download_image
from neko_video.media_service import MediaService
from neko_video.storage import write_json

ID = "7123456789012345678"
TASK = "11111111-2222-4333-8444-555555555555"
KEY = "synthetic-test-key"


def content():
    return {
        "platform": "douyin",
        "kind": "video",
        "content_id": ID,
        "is_private": False,
        "is_deleted": False,
        "title": "synthetic",
        "description": "synthetic",
        "author": {"nickname": "test"},
        "media": {"video": {"url": "https://media.example/video.mp4"}, "images": [], "covers": []},
    }


def envelope(data, status=200):
    return httpx.Response(status, json={"success": True, "error": None, "data": data})


class DTKTests(unittest.IsolatedAsyncioTestCase):
    async def run_resolver(self, handler, text=ID, **kwargs):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            return await resolve_dtk(client, text, KEY, **kwargs)

    async def test_fixed_task_polling_headers_and_normalized_result(self):
        requests = []

        def handler(req):
            requests.append(req)
            self.assertEqual(req.headers["authorization"], "Bearer " + KEY)
            self.assertEqual(req.headers["cookie"], "")
            self.assertEqual(str(req.url).split("/api/")[0], ENDPOINT)
            if req.method == "POST":
                self.assertEqual(
                    json.loads(req.content),
                    {
                        "url": f"https://www.douyin.com/video/{ID}",
                        "include_raw": False,
                    },
                )
                self.assertEqual(req.url.params["wait"], "0")
                return envelope({"task_id": TASK, "state": "queued"}, 202)
            self.assertEqual(req.url.path, "/api/v1/tasks/" + TASK)
            return envelope(
                {"task_id": TASK, "state": "running"}
                if len(requests) == 2
                else {
                    "task_id": TASK,
                    "state": "done",
                    "data": content(),
                }
            )

        with patch("neko_video.dtk.POLL_SECONDS", 0):
            result = await self.run_resolver(handler)
        self.assertEqual([r.method for r in requests], ["POST", "GET", "GET"])
        self.assertEqual(result["provider"], "dtk")
        self.assertEqual(result["video_urls"], ["https://media.example/video.mp4"])

    async def test_failure_redirect_mismatch_and_errors_never_resubmit(self):
        for reply in [
            httpx.Response(401, text=KEY),
            httpx.Response(429, text=KEY),
            httpx.Response(302, headers={"location": "https://evil.example/"}),
            httpx.Response(200, content=b"not-json"),
            envelope({"task_id": "../../admin", "state": "queued"}, 202),
            envelope({**content(), "content_id": "7123456789012345679"}),
            envelope({**content(), "platform": "bilibili"}),
            envelope({**content(), "kind": "live"}),
            envelope({**content(), "kind": []}),
            envelope({**content(), "is_private": True}),
        ]:
            calls = []

            def handler(req):
                calls.append(req)
                return reply

            with self.subTest(reply=reply.status_code), self.assertRaises(MediaError) as error:
                await self.run_resolver(handler)
            self.assertNotIn(KEY, str(error.exception))
            self.assertEqual(len(calls), 1)

    async def test_terminal_poll_and_wrong_task_fail_without_new_post(self):
        for result in [
            envelope({"task_id": TASK, "state": "failed", "error": KEY}),
            envelope({"task_id": "other", "state": "done", "data": content()}),
            httpx.Response(404, text=KEY),
        ]:
            calls = []

            def handler(req):
                calls.append(req.method)
                return (
                    envelope({"task_id": TASK, "state": "queued"}, 202)
                    if len(calls) == 1
                    else result
                )

            with patch("neko_video.dtk.POLL_SECONDS", 0), self.assertRaises(MediaError):
                await self.run_resolver(handler)
            self.assertEqual(calls, ["POST", "GET"])

    async def test_poll_limit_deadline_and_cancellation(self):
        calls = []

        def handler(req):
            calls.append(req.method)
            return envelope(
                {"task_id": TASK, "state": "running"}, 202 if req.method == "POST" else 200
            )

        with patch("neko_video.dtk.POLL_SECONDS", 0), self.assertRaises(MediaError):
            await self.run_resolver(handler)
        self.assertEqual(calls, ["POST"] + ["GET"] * 25)
        calls.clear()
        with patch("neko_video.dtk.WAIT_SECONDS", 0.01), self.assertRaises(MediaError):
            await self.run_resolver(handler)
        self.assertEqual(calls, ["POST"])
        calls.clear()
        task = asyncio.create_task(self.run_resolver(handler))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(calls, ["POST"])

    async def test_input_and_endpoint_rejected_before_network(self):
        handler = unittest.mock.Mock(side_effect=AssertionError("network"))
        for text in [
            "https://127.0.0.1/",
            "https://evil.example/",
            "https://www.tiktok.com/a",
        ]:
            with self.assertRaises(MediaError):
                await self.run_resolver(handler, text)
        with self.assertRaises(MediaError):
            await self.run_resolver(handler, endpoint="http://127.0.0.1:8000")
        handler.assert_not_called()

    async def test_response_limit_and_media_public_boundary(self):
        with patch("neko_video.dtk.MAX_API_BYTES", 10), self.assertRaises(MediaError):
            await self.run_resolver(lambda req: envelope(content()))
        data = content()
        data["media"]["video"]["url"] = "http://127.0.0.1:25526/private"
        with self.assertRaises(MediaError):
            media_result(data)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: self.fail("private request"))
        ) as client:
            with self.assertRaises(MediaError):
                await download_image(
                    client,
                    "https://media.example/video",
                    resolver=lambda *args: [(0, 0, 0, "", ("127.0.0.1", 0))],
                )

    def test_album_live_photo_mapping_and_bounds(self):
        data = content()
        data["kind"] = "image_album"
        data["media"]["video"] = None
        data["media"]["images"] = [
            {
                "url": "https://media.example/image",
                "video": {"url": "https://media.example/live.mp4"},
            }
        ] * 70
        result = media_result(data)
        self.assertEqual(result["image_total"], 70)
        self.assertEqual(len(result["images"]), 64)
        self.assertEqual(result["images"][0]["video_urls"], ["https://media.example/live.mp4"])

    async def test_service_selection_private_key_and_scope(self):
        cfg = Config()
        self.assertEqual(cfg.media.provider, "legacy")
        cfg.plugin.enabled = True
        cfg.media.provider = "dtk"
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder) / "dtk-api-secrets.json", {"api_key": KEY})
            service = MediaService(cfg, SimpleNamespace(paths=SimpleNamespace(data_dir=folder)))

            async def preview(result, start, client, **kwargs):
                self.assertNotIn("authorization", client.headers)
                key = kwargs["register"]("video", result["video_urls"], "")
                return {"success": True, "key": key}

            with (
                patch(
                    "neko_video.media_service.resolve_dtk",
                    AsyncMock(return_value=media_result(content())),
                ) as dtk,
                patch("neko_video.media_service.resolve_media", AsyncMock()) as legacy,
                patch("neko_video.media_service.preview_result", preview),
            ):
                result = await service.read(ID, scope="chat-a")
                self.assertTrue(result["success"])
                self.assertEqual(dtk.call_args.args[2], KEY)
                legacy.assert_not_called()
                self.assertEqual(service.assets.peek("chat-a", result["key"]).source, "douyin")
                with self.assertRaises(ValueError):
                    service.assets.peek("chat-b", result["key"])
                cfg.parse.enable_douyin = False
                self.assertFalse((await service.read(ID, scope="chat-a"))["success"])
                self.assertEqual(dtk.call_count, 1)
                cfg.parse.enable_douyin = True
                dtk.side_effect = MediaError("DTK failure")
                self.assertFalse((await service.read(ID, scope="chat-a"))["success"])
                legacy.assert_not_called()
            await service.close()


def bili_content(parts=2):
    data = content()
    data.update(platform="bilibili", content_id="BV17x411w7KC")
    data["parts"] = [{"part": n, "cid": str(n), "title": f"P{n}"} for n in range(1, parts + 1)]
    data["media"]["video"]["format"] = "mp4"
    return data


class BilibiliDTKTests(unittest.IsolatedAsyncioTestCase):
    run_resolver = DTKTests.run_resolver

    async def test_bili_part_submission_and_fixed_poll(self):
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                self.assertEqual(req.method, "GET")
                self.assertEqual(req.url.path, "/api/v1/bilibili/video")
                self.assertEqual(req.url.params["part"], "2")
                self.assertEqual(req.url.params["aweme_id"], "BV17x411w7KC")
                self.assertNotIn("url", req.url.params)
                return envelope({"task_id": TASK, "state": "queued"}, 202)
            self.assertEqual(req.url.path, "/api/v1/tasks/" + TASK)
            return envelope({"task_id": TASK, "state": "done", "data": bili_content()})

        with patch("neko_video.dtk.POLL_SECONDS", 0):
            result = await self.run_resolver(
                handler, "https://www.bilibili.com/video/BV17x411w7KC?p=2"
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["source"], "bilibili")
        self.assertIn("P2", result["source_note"])

    def test_bili_selection_and_strict_formats(self):
        self.assertEqual(target("av170001")[3], 1)
        self.assertEqual(target("BV17x411w7KC")[3], 1)
        media_result(bili_content(), "av170001", "bilibili")
        for link in [
            "https://www.bilibili.com/video/BV17x411w7KC?p=0",
            "https://www.bilibili.com/video/BV17x411w7KC?p=1&p=2",
            "https://www.bilibili.com/video/BV17x411w7KC?p=abc",
            "https://www.bilibili.com/bangumi/play/ep1",
            "https://b23.tv/abc?p=2",
            "https://user:password@www.bilibili.com/video/BV17x411w7KC",
        ]:
            with self.subTest(link=link), self.assertRaises(MediaError):
                target(link)
        for changes in [
            {"parts": []},
            {"parts": [{"part": 2}]},
            {"content_id": "BV1xx411c7mD"},
            {
                "media": {
                    "video": None,
                    "images": [],
                    "covers": [],
                    "streams": [{"url": "https://media.example/dash"}],
                }
            },
            {
                "media": {
                    "video": {"url": "https://media.example/dash", "format": "dash"},
                    "images": [],
                    "covers": [],
                }
            },
        ]:
            with self.assertRaises(MediaError):
                media_result({**bili_content(), **changes}, "av170001", "bilibili")
        with self.assertRaisesRegex(MediaError, "不存在"):
            media_result(bili_content(), "", "bilibili", 3)
        with self.assertRaisesRegex(MediaError, "多P短链"):
            media_result(bili_content(), "", "bilibili", 1, True)
        self.assertEqual(
            media_result(bili_content(1), "", "bilibili", 1, True)["source"], "bilibili"
        )

    async def test_bili_service_is_independent_of_douyin_and_marks_scope(self):
        cfg = Config()
        cfg.plugin.enabled = True
        cfg.media.provider = "dtk"
        cfg.parse.enable_douyin = False
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder) / "dtk-api-secrets.json", {"api_key": KEY})
            service = MediaService(cfg, SimpleNamespace(paths=SimpleNamespace(data_dir=folder)))

            async def preview(result, start, client, **kwargs):
                self.assertEqual(client.headers["referer"], "https://www.bilibili.com/")
                self.assertNotIn("authorization", client.headers)
                return {
                    "success": True,
                    "content": "preview",
                    "key": kwargs["register"]("video", result["video_urls"], ""),
                }

            with (
                patch(
                    "neko_video.media_service.resolve_dtk",
                    AsyncMock(return_value=media_result(bili_content(), "", "bilibili")),
                ),
                patch("neko_video.media_service.preview_result", preview),
            ):
                result = await service.read("BV17x411w7KC", scope="bili-chat")
                self.assertTrue(result["success"])
                self.assertIn("P1", result["content"])
                self.assertEqual(service.assets.peek("bili-chat", result["key"]).source, "bilibili")
            await service.close()
