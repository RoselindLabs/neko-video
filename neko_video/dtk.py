"""Opt-in DTK 5.1.11 media resolver; never handles platform cookies.

Wire contract reviewed at 49f4a9730b478c5f56d843a1c033e0960814aaaf.
Only the administrator-selected loopback API bypasses public media validation.
"""

import asyncio
import json
import re
from uuid import UUID
from urllib.parse import parse_qs, urlsplit

import httpx

from .media import MAX_API_BYTES, MediaError, media_url, share_target, urls

ENDPOINT = "http://127.0.0.1:25526"
POLL_SECONDS = 1
WAIT_SECONDS = 25
BILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
BILI_SHORT = {"b23.tv", "www.b23.tv", "bili2233.cn", "www.bili2233.cn"}
BILI_ID = r"(?:BV1[0-9A-Za-z]{9}|av[1-9][0-9]{0,15})"


def matches_bili_id(actual, expected):
    if not expected:
        return True
    if not expected.startswith("av"):
        return actual == expected
    # Public BV/av encoding, 2024 51-bit scheme; no account or network lookup.
    alphabet = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
    chars = list(actual)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    try:
        number = 0
        for char in chars[3:]:
            number = number * 58 + alphabet.index(char)
        return ((number & 2251799813685247) ^ 23442827791579) == int(expected[2:])
    except ValueError:
        return False


def target(text):
    """Local allowlist only; never resolve share links with account clients."""
    if not isinstance(text, str) or len(text) > 10000:
        raise MediaError("请提供B站或抖音视频链接")
    if re.fullmatch(BILI_ID, text.strip()):
        return "bilibili", text.strip(), "", 1, False
    for raw in re.findall(r'https?://[^\s<>"“”]+', text):
        raw = raw.rstrip("，。！!）)；;、")
        if not media_url(raw):
            continue
        parsed = urlsplit(raw)
        if parsed.hostname not in BILI_HOSTS | BILI_SHORT:
            continue
        short = parsed.hostname in BILI_SHORT
        match = re.fullmatch(r"/video/(" + BILI_ID + r")/?", parsed.path)
        if not short and not match:
            raise MediaError("B站只接受普通视频链接，不接受直播、番剧或个人空间")
        values = parse_qs(parsed.query, keep_blank_values=True).get("p", ["1"])
        if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]{0,3}", values[0]):
            raise MediaError("B站p参数须为唯一的1到9999整数")
        if short and (not re.fullmatch(r"/[0-9A-Za-z]+/?", parsed.path) or parsed.query):
            raise MediaError("B站短链请使用无参数短链或完整video链接明确选择P")
        return "bilibili", match[1] if match else "", raw, int(values[0]), short
    vid, share = share_target(text)
    return "douyin", vid, share, 1, False


def object_urls(value):
    if not isinstance(value, dict):
        return []
    alternatives = value.get("urls")
    alternatives = alternatives[:15] if isinstance(alternatives, list) else []
    return urls([value.get("url"), *alternatives])


def media_result(data, expected_id="", platform="douyin", part=1, short=False):
    if (
        not isinstance(data, dict)
        or data.get("platform") != platform
        or data.get("kind") not in ("video", "image_album")
        or not isinstance(data.get("content_id"), str)
        or not re.fullmatch(
            r"\d{19}" if platform == "douyin" else r"BV1[0-9A-Za-z]{9}", data["content_id"]
        )
        or (platform == "douyin" and expected_id and data["content_id"] != expected_id)
        or (platform == "bilibili" and not matches_bili_id(data["content_id"], expected_id))
        or data.get("is_deleted") is not False
        or data.get("is_private") is not False
    ):
        raise MediaError("DTK返回的来源或内容类型不匹配")
    media = data.get("media")
    if not isinstance(media, dict):
        raise MediaError("DTK缺少媒体信息")
    pictures = media.get("images")
    covers = media.get("covers")
    if not isinstance(pictures, list) or not isinstance(covers, list):
        raise MediaError("DTK媒体格式错误")
    part_note = ""
    if platform == "bilibili":
        parts = data.get("parts")
        if not isinstance(parts, list) or not parts or len(parts) > 9999:
            raise MediaError("B站分P信息缺失或超限，未登记视频")
        numbers = [p.get("part") if isinstance(p, dict) else None for p in parts]
        if any(type(n) is not int for n in numbers) or numbers != list(range(1, len(parts) + 1)):
            raise MediaError("B站分P信息不完整，未登记视频")
        if short and len(parts) != 1:
            raise MediaError("B站多P短链无法确认目标P；请用完整video链接并指定?p=N")
        if part not in numbers:
            raise MediaError("B站请求的P不存在，未替换为第一P")
        video = media.get("video")
        if (
            data.get("kind") != "video"
            or pictures
            or not isinstance(video, dict)
            or video.get("format") != "mp4"
        ):
            raise MediaError("B站未返回单段渐进MP4；不支持DASH分离音视频、分段流或缺失播放流")
        part_note = f"B站本次仅登记P{part}（共{len(parts)}P）；无p参数默认P1，不代表完整合集。"
    images = [
        {"urls": object_urls(item), "video_urls": object_urls(item.get("video"))}
        if isinstance(item, dict)
        else {"urls": [], "video_urls": []}
        for item in pictures[:64]
    ]
    author = data.get("author")
    title = data.get("description") or data.get("title")
    name = author.get("nickname") if isinstance(author, dict) else None
    result = {
        "title": title[:2000] if isinstance(title, str) else "",
        "author": name[:120] if isinstance(name, str) else "",
        "images": images,
        "image_total": len(pictures),
        "video_urls": object_urls(media.get("video")),
        "covers": [url for item in covers[:8] for url in object_urls(item)][:16],
        "provider": "dtk",
        "source": platform,
        "source_note": part_note,
    }
    if not result["video_urls"] and not any(item["urls"] for item in images):
        raise MediaError("DTK未返回可用图片或视频")
    return result


async def request(client, method, path, key, **kwargs):
    """No redirects, cookie forwarding, raw diagnostics or unbounded bodies."""
    try:
        async with client.stream(
            method,
            ENDPOINT + path,
            headers={"Authorization": "Bearer " + key, "Cookie": ""},
            follow_redirects=False,
            **kwargs,
        ) as response:
            if response.status_code not in {200, 202}:
                raise MediaError("DTK接口拒绝或暂不可用")
            raw = bytearray()
            async for block in response.aiter_bytes():
                if len(raw) + len(block) > MAX_API_BYTES:
                    raise MediaError("DTK响应超过大小限制")
                raw.extend(block)
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or payload.get("success") is not True
                or payload.get("error") is not None
                or not isinstance(payload.get("data"), dict)
            ):
                raise MediaError("DTK响应合同不匹配")
            return response.status_code, payload["data"]
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise MediaError("DTK请求失败，未重复提交") from None


async def resolve_dtk(client, text, key, endpoint=ENDPOINT):
    # This is not an arbitrary-URL API client; even private config is restricted.
    if endpoint != ENDPOINT:
        raise MediaError("DTK只允许固定受信回环地址")
    if (
        not isinstance(key, str)
        or not key
        or len(key) > 512
        or not key.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in key)
    ):
        raise MediaError("DTK私有API凭据未配置或格式错误")
    platform, vid, share, part, short = target(text)
    link = f"https://www.douyin.com/video/{vid}" if vid else share
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            if platform == "bilibili":
                params = {"wait": 0, "include_raw": "false", "part": part}
                params["url" if short else "aweme_id"] = share if short else vid
                status, data = await request(
                    client, "GET", "/api/v1/bilibili/video", key, params=params
                )
            else:
                status, data = await request(
                    client,
                    "POST",
                    "/api/v1/parse",
                    key,
                    params={"wait": 0},
                    json={"url": link, "include_raw": False},
                )
            if status == 200:
                return media_result(data, vid, platform, part, short)
            task_id = data.get("task_id")
            if not isinstance(task_id, str) or str(UUID(task_id)) != task_id:
                raise MediaError("DTK任务编号无效")
            if data.get("state") not in ("queued", "running", "done"):
                raise MediaError("DTK任务未成功")
            # Always poll this exact UUID; never resubmit on timeout/failed/404.
            for _ in range(25):
                await asyncio.sleep(POLL_SECONDS)
                status, task = await request(client, "GET", "/api/v1/tasks/" + task_id, key)
                if status != 200 or task.get("task_id") != task_id:
                    raise MediaError("DTK任务响应不匹配")
                if task.get("state") == "done":
                    return media_result(task.get("data"), vid, platform, part, short)
                if task.get("state") not in ("queued", "running"):
                    raise MediaError("DTK任务未成功，未重复提交")
            raise MediaError("DTK任务等待到期，未重复提交")
    except MediaError:
        raise
    except (TimeoutError, ValueError):
        raise MediaError("DTK解析失败或等待到期，未重复提交") from None
    # Cancellation propagates. Do not DELETE: DTK may coalesce other callers
    # onto this task; local cancellation does not imply upstream cancellation.
