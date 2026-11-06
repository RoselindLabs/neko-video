"""Chat-native video attachments: resolve via adapter API into a scoped asset.

The adapter flattens video segments to plain text, so the plugin reads the original
OneBot payload back via the public ``get_msg`` action and resolves a download URL
through ``get_file``. Only bounded metadata is kept; no cookies are reused.
"""

import asyncio
import re
from urllib.parse import urlsplit

VIDEO_TEXT = re.compile(r"\[视频\][^\n]{0,300}")
VIDEO_EXTENSIONS = (".mp4", ".mov")
MAX_NAME = 240


def video_file_name(data):
    """Only a bounded video filename from actual adapter file metadata."""
    value = data.get("name") or data.get("file_name") or data.get("file")
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_NAME
        or any(ord(c) < 32 for c in value)
        or "/" in value
        or "\\" in value
        or not value.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return ""
    return value


def resource_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(c.isspace() for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.port in (None, 80, 443)
            and parsed.username is None
            and parsed.password is None
        ):
            return value
    except ValueError:
        pass
    return ""


def file_identity(data):
    value = data.get("file_id") or data.get("id")
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[\w./:+-]{1,256}", value)
        or any(p in (".", "..") for p in value.split("/"))
        or value.startswith(("http:", "https:"))
    ):
        raise ChatMediaError("视频文件编号无效，未查询或读取路径")
    return value


def attachment_hash(data):
    """Opaque private-file locator metadata; never returned with media handles."""
    value = data.get("file_hash", "")
    if (
        not isinstance(value, str)
        or len(value) > 1024
        or any(ord(character) < 33 for character in value)
    ):
        raise ChatMediaError("视频附件校验元数据无效")
    return value


class ChatMediaError(ValueError):
    """Refusal with a user-facing reason; never retried automatically."""


def chat_video_name(message):
    """Extract the video file name from the adapter's flattened text marker."""
    text = str(message.get("processed_plain_text") or "")
    for match in VIDEO_TEXT.finditer(text):
        inner = match.group(0)
        name = re.search(r"文件:\s*([^\s，,]+\.+[A-Za-z0-9]{1,8})", inner)
        if name:
            candidate = name.group(1).replace("\\", "/").rsplit("/", 1)[-1][:MAX_NAME]
            if candidate.lower().endswith(VIDEO_EXTENSIONS):
                return candidate
    return ""


class ChatMediaService:
    """Resolves chat-native video messages to assets via public adapter actions."""

    def __init__(self, context, settings, assets):
        self.context = context
        self.settings = settings
        self.assets = assets
        self.slots = asyncio.Semaphore(2)

    async def _action_data(self, action, params):
        async with asyncio.timeout(30):
            response = await self.context.api.call(
                "adapter.napcat.action.call_data", action_name=action, params=params
            )
        if (
            not isinstance(response, dict)
            or response.get("success") is False
            or response.get("status") == "failed"
        ):
            raise ChatMediaError("QQ接口未返回消息详情")
        return response

    async def _source_segment(self, message, message_id):
        """Read original video/file segments, never a URL copied from chat text."""
        detail = await self._action_data("get_msg", {"message_id": str(message_id)})
        info = message.get("message_info") or {}
        group = str((info.get("group_info") or {}).get("group_id") or "")
        user = self._peer(message)
        if str(detail.get("message_id") or "") != message_id:
            raise ChatMediaError("QQ视频消息编号与当前消息不符")
        if group:
            if str(detail.get("group_id") or "") != group:
                raise ChatMediaError("QQ视频来自其他群聊")
        else:
            sender = str(detail.get("user_id") or (detail.get("sender") or {}).get("user_id") or "")
            if sender == str(message.get("account_id") or ""):
                sender = str(detail.get("target_id") or "")
            if detail.get("group_id") or not user or sender != user:
                raise ChatMediaError("QQ视频来自其他私聊或缺少真实对端")
        segments = detail.get("message")
        if not isinstance(segments, list) or len(segments) > 64:
            raise ChatMediaError("QQ消息详情缺少消息段")
        found = []
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                continue
            data = segment["data"]
            if segment.get("type") == "video" and chat_video_name(message):
                found.append(("video", data, chat_video_name(message)))
            elif segment.get("type") == "file" and (name := video_file_name(data)):
                found.append(("file", data, name))
        if len(found) != 1:
            raise ChatMediaError("需要一条只含一个可识别视频的消息；不按普通文本链接猜附件")
        if found[0][0] == "file":
            info = message.get("message_info") or {}
            group = str((info.get("group_info") or {}).get("group_id") or "")
            user = self._peer(message)
            if str(detail.get("message_id") or "") != message_id:
                raise ChatMediaError("QQ文件消息编号与当前消息不符")
            if group:
                if str(detail.get("group_id") or "") != group:
                    raise ChatMediaError("QQ文件来自其他群聊")
            if not group:
                actual_hash = attachment_hash(found[0][1])
                # Host file components currently omit hashes. If a structured
                # host source does carry one, a conflicting raw message cannot
                # silently replace that already-bound attachment identity.
                host_segments = message.get("raw_message") or []
                if not isinstance(host_segments, list) or len(host_segments) > 128:
                    raise ChatMediaError("宿主文件消息段无效或超限")
                actual_id = found[0][1].get("file_id") or found[0][1].get("id")
                for segment in host_segments:
                    if not isinstance(segment, dict) or segment.get("type") != "file":
                        continue
                    expected = segment.get("data")
                    if not isinstance(expected, dict):
                        continue
                    if actual_id and (expected.get("file_id") or expected.get("id")) == actual_id:
                        expected_hash = attachment_hash(expected)
                        if expected_hash and expected_hash != actual_hash:
                            raise ChatMediaError("原私聊视频附件校验来源发生变化")
        return found[0]

    @staticmethod
    def _peer(message):
        info = message.get("message_info") or {}
        user = str((info.get("user_info") or {}).get("user_id") or "")
        if user == str(message.get("account_id") or ""):
            user = str(
                (info.get("additional_config") or {}).get("platform_io_target_user_id") or ""
            )
        return user

    async def _account(self, message):
        info = message.get("message_info") or {}
        account = str(message.get("account_id") or "")
        group = str((info.get("group_info") or {}).get("group_id") or "")
        user = self._peer(message)
        if (
            not account.isdecimal()
            or (group and not group.isdecimal())
            or (not group and not user.isdecimal())
        ):
            raise ChatMediaError("视频文件缺少当前QQ账号或聊天归属")
        login = await self._action_data("get_login_info", {})
        if str(login.get("user_id") or "") != account:
            raise ChatMediaError("QQ文件接口登录账号与消息账号不符")
        return group, user

    async def _file_url(self, data, message):
        group, user = await self._account(message)
        file_hash = attachment_hash(data) if not group else ""
        for key in ("url", "file_url", "file"):
            if url := resource_url(data.get(key)):
                return url
        ident = file_identity(data)
        if group:
            busid = data.get("busid", 0)
            if type(busid) is not int or not 0 <= busid <= 2**31 - 1:
                raise ChatMediaError("群视频文件busid无效")
            result = await self._action_data(
                "get_group_file_url",
                {"group_id": group, "file_id": ident, **({"busid": busid} if busid else {})},
            )
        else:
            result = await self._action_data(
                "get_private_file_url",
                {
                    "user_id": user,
                    "file_id": ident,
                    **({"file_hash": file_hash} if file_hash else {}),
                },
            )
        if url := resource_url(result.get("url")):
            return url
        raise ChatMediaError("未取得视频附件的公开下载地址，不读取服务器本地路径")

    async def resolve_url(self, data, group_id=""):
        """Return a downloadable http(s) URL for one video segment payload."""
        for key in ("url", "file_url"):
            value = resource_url(data.get(key))
            if value:
                return value
        file_id = str(data.get("file_id") or "")
        if not re.fullmatch(r"[\w./:+-]{1,256}", file_id or ""):
            raise ChatMediaError("视频编号无效，请重新发送")
        params = {"file_id": file_id}
        if group_id:
            params["group_id"] = group_id
        try:
            resource = await self._action_data("get_file", params)
        except ChatMediaError:
            if not group_id:
                raise
            resource = await self._action_data("get_group_file_url", params)
        for key in ("url", "file", "file_url"):
            value = resource_url(resource.get(key))
            if value:
                return value
        raise ChatMediaError("未能取得该视频的下载地址，请重新发送")

    async def register(self, message, scope, message_id=""):
        """Register one chat-native video message as a scoped video asset."""
        if not scope or not isinstance(message, dict) or message.get("session_id") != scope:
            raise ChatMediaError("缺少当前聊天")
        message_id = str(message_id or message.get("message_id") or "").strip()
        if not message_id or len(message_id) > 128:
            raise ChatMediaError("消息编号无效")
        if str(message.get("message_id") or "") != message_id:
            raise ChatMediaError("消息编号不匹配")
        info = message.get("message_info") or {}
        extra = info.get("additional_config") or {}
        notice = extra.get("napcat_notice_payload") or {}
        group_id = str((info.get("group_info") or {}).get("group_id") or "")
        is_upload = extra.get("napcat_notice_type") == "group_upload"
        if (
            not is_upload
            and not chat_video_name(message)
            and "[文件]" not in str(message.get("processed_plain_text") or "")
        ):
            raise ChatMediaError("该消息没有聊天视频或视频附件")
        async with self.slots:
            if is_upload:
                if (
                    not isinstance(notice, dict)
                    or not group_id
                    or str(notice.get("group_id") or "") != group_id
                ):
                    raise ChatMediaError("群上传记录与当前群聊不符")
                data = notice.get("file")
                if not isinstance(data, dict) or not (name := video_file_name(data)):
                    raise ChatMediaError("群上传记录不是可识别的视频附件")
                if notice.get("self_id") and str(notice["self_id"]) != str(
                    message.get("account_id") or ""
                ):
                    raise ChatMediaError("群上传记录属于其他QQ账号")
                kind = "file"
            else:
                kind, data, name = await self._source_segment(message, message_id)
            if kind == "video":
                await self._account(message)
            url = (
                await self._file_url(data, message)
                if kind == "file"
                else await self.resolve_url(data, group_id)
            )
        # Video analysis hashes downloaded content itself; registering a
        # non-content digest would make that check always fail.
        media_id = self.assets.add(scope, "video", (url,), "", source="chat")
        return {
            "media_id": media_id,
            "name": name,
            "source_kind": "chat_video" if kind == "video" else "chat_file",
        }
