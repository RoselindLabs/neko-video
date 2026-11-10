"""Thin MaiBot adapter: components, chat context and lifecycle only."""

import asyncio
import base64
import re
from pathlib import Path

import httpx
from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder, ToolParameterInfo, ToolParamType

from .config import Config
from .chat_media import ChatMediaError, ChatMediaService
from .credentials import CredentialService
from .media_service import MediaService
from .official import OfficialVideoService
from .presentation import render
from .transport import UA
from .video_analysis import VideoAnalyzer
from .context import VideoContext


def scope_of(kwargs):
    return str(
        kwargs.get("session_id") or kwargs.get("stream_id") or kwargs.get("chat_id") or ""
    ).strip()


def sender_of(explicit, kwargs):
    value = (
        explicit
        or kwargs.get("user_id")
        or ((kwargs.get("message") or {}).get("message_info") or {})
        .get("user_info", {})
        .get("user_id")
    )
    value = str(value or "").strip()
    return value[3:].strip() if value.lower().startswith("qq:") else value


class NekoVideoPlugin(MaiBotPlugin):
    config_model = Config

    def __init__(self):
        super().__init__()
        self.client = None
        self.credentials = None
        self.official = None
        self.media = None
        self.chat_media = None
        self.poll_task = None
        self.tasks = set()
        self.lifecycle = asyncio.Lock()
        self.login_start = asyncio.Lock()
        self.ready = False
        self.analyzer = None
        self.references = VideoContext()

    async def on_load(self):
        async with self.lifecycle:
            await self._open()
        self.ctx.logger.info("Neko Video 已加载")

    async def _open(self):
        directory = Path(self.ctx.paths.data_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.client = httpx.AsyncClient(
            timeout=12, follow_redirects=False, trust_env=False, headers={"User-Agent": UA}
        )
        self.credentials = CredentialService(directory, self.config.credential, self.client)
        self.official = OfficialVideoService(self.config.parse, self.credentials, self.client)
        self.media = MediaService(self.config, self.ctx)
        await self.media.start()
        self.chat_media = ChatMediaService(self.ctx, self.config, self.media.assets)
        self.analyzer = VideoAnalyzer(
            self.config, self.media.assets, directory, self._visual_generate, self._transcribe_audio
        )
        self.ready = True

    async def _close(self):
        self.ready = False
        self.references.clear()
        if self.analyzer is not None:
            await self.analyzer.close()
        if self.media is not None:
            await self.media.close()
        jobs = [
            task
            for task in [self.poll_task, *self.tasks]
            if task is not None and task is not asyncio.current_task()
        ]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.tasks.clear()
        self.poll_task = None
        if self.client is not None:
            await self.client.aclose()
        self.client = None

    async def on_unload(self):
        async with self.lifecycle:
            await self._close()

    async def on_config_update(self, scope, config_data, version):
        del scope, config_data, version
        async with self.lifecycle:
            await self._close()
            await self._open()

    def enabled(self):
        return self.ready and self.config.plugin.enabled

    async def _visual_generate(self, prompt):
        return await self.ctx.llm.generate(
            prompt, task_name="vlm", max_tokens=2500, temperature=0.2
        )

    async def _transcribe_audio(self, audio_base64):
        return await self.ctx.llm.transcribe_audio(
            audio_base64=audio_base64,
            task_name="voice",
            rpc_timeout_ms=self.config.asr.timeout_seconds * 1000,
        )

    async def _summary(self, link, expected=None, timeout=40):
        if not self.enabled():
            return None
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(timeout):
                return await self.official.summary(link, expected)
        finally:
            self.tasks.discard(task)

    @HookHandler(
        "chat.receive.after_process",
        name="neko_video_context",
        description="为分享链接附加平台文字资料",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=10000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject(self, **kwargs):
        message = kwargs.get("message")
        if not self.enabled() or not isinstance(message, dict) or message.get("is_notify"):
            return {"action": "continue"}
        group = bool((message.get("message_info") or {}).get("group_info"))
        if not (
            self.config.parse.enable_in_group if group else self.config.parse.enable_in_private
        ):
            return {"action": "continue"}
        text = str(message.get("processed_plain_text") or "")
        if not text:
            text = " ".join(
                x["data"]
                for x in message.get("raw_message") or []
                if isinstance(x, dict)
                and x.get("type") == "text"
                and isinstance(x.get("data"), str)
            )
        if not text or "[Neko Video]" in text:
            return {"action": "continue"}
        try:
            info = await self._summary(text, timeout=9)
        except Exception:
            return {"action": "continue"}
        if not info:
            return {"action": "continue"}
        if self.enabled():
            self.references.record(message, render(info, injected=True), info["platform"])
        return {"action": "continue"}

    def _reference_context(self, **kwargs):
        if not self.enabled():
            self.references.clear()
        items = self.references.inject(
            kwargs.get("items"),
            self.config.parse,
            session_id=kwargs.get("session_id", ""),
            tool_definitions=kwargs.get("tool_definitions"),
            item_schema_version=kwargs.get("item_schema_version"),
            reply_message_id=kwargs.get("reply_message_id", ""),
        )
        return (
            {"action": "continue", "modified_kwargs": {**kwargs, "items": items}}
            if items is not None
            else {"action": "continue"}
        )

    @HookHandler(
        "maisaka.planner.before_request",
        name="neko_video_reference_planner",
        description="附加来源绑定的视频临时参考",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=2000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def planner_reference(self, **kwargs):
        return self._reference_context(**kwargs)

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="neko_video_reference_replyer",
        description="附加来源绑定的视频临时参考",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=2000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def replyer_reference(self, **kwargs):
        return self._reference_context(**kwargs)

    async def _summary_tool(self, link, expected):
        if not self.enabled():
            return {"success": False, "content": "插件未启用"}
        try:
            info = await self._summary(link, expected)
            if info:
                return {"success": True, "content": render(info)}
        except Exception:
            pass
        return {"success": False, "content": "未取得有效视频资料，未猜测画面。"}

    @Tool(
        "neko_video_info",
        description="读取B站或抖音的平台摘要、字幕等文字资料，不分析画面。",
        timeout_ms=45000,
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="B站/抖音分享链接、BV/av号或抖音19位ID",
                required=True,
            )
        ],
    )
    async def info(self, link="", **kwargs):
        del kwargs
        expected = (
            "douyin" if isinstance(link, str) and re.fullmatch(r"\d{19}", link.strip()) else None
        )
        return await self._summary_tool(link, expected)

    async def bilibili(self, link="", **kwargs):
        del kwargs
        return await self._summary_tool(link, "bilibili")

    async def douyin(self, link="", **kwargs):
        del kwargs
        return await self._summary_tool(link, "douyin")

    @Tool(
        "neko_video_open",
        description="打开B站/抖音或按真实消息ID登记视频；理解内容须再analyze。",
        timeout_ms=130000,
        parameters=[
            ToolParameterInfo(
                name="link",
                param_type=ToolParamType.STRING,
                description="抖音链接/19位ID；DTK模式另接B站BV/av/video链接，?p=N选P，默认P1；多P短链须换完整链接。与source_message_id二选一，不接任意网址",
                required=False,
            ),
            ToolParameterInfo(
                name="source_message_id",
                param_type=ToolParamType.STRING,
                description="当前聊天视频消息或视频文件附件的真实消息ID，不是上下文短编号；与link二选一",
                required=False,
            ),
            ToolParameterInfo(
                name="image_start",
                param_type=ToolParamType.INTEGER,
                description="图片起点，省略为0，每次最多4张；续页仍传原link及返回的next_image_start，不传媒体编号",
                required=False,
                default=0,
            ),
        ],
    )
    async def open(self, link="", source_message_id="", image_start=0, **kwargs):
        if (
            not isinstance(link, str)
            or not isinstance(source_message_id, str)
            or bool(link.strip()) == bool(source_message_id.strip())
        ):
            return {
                "success": False,
                "content": "平台视频链接和当前聊天真实消息ID二选一，不能同时填写或留空",
            }
        if any(
            key in kwargs
            for key in ("media_id", "analyze_video", "include_audio", "analysis_offset")
        ):
            return {
                "success": False,
                "content": "open只预览或登记媒体；需要分析时调用neko_video_analyze",
            }
        if source_message_id:
            if type(image_start) is not int or image_start != 0:
                return {"success": False, "content": "聊天视频取源不接受图片起点"}
            return await self.chat_video(source_message_id.strip(), **kwargs)
        return await self.read(link=link, image_start=image_start, **kwargs)

    @Tool(
        "neko_video_analyze",
        description="对本聊天视频编号抽帧理解，按设置转写音轨；可续读成功缓存。",
        timeout_ms=130000,
        parameters=[
            ToolParameterInfo(
                name="media_id",
                param_type=ToolParamType.STRING,
                description="open返回的本聊天视频media_id或实况motion_id，不是封面图片编号；不接受URL/路径。已有成功结果可复用，未知请求禁止换编号重做",
                required=True,
            ),
            ToolParameterInfo(
                name="offset",
                param_type=ToolParamType.INTEGER,
                description="首次0；续读填正文提示的offset或next_analysis_offset，仅取成功缓存，不重新调用模型",
                required=False,
                default=0,
            ),
        ],
    )
    async def analyze(self, media_id="", offset=0, **kwargs):
        if not self.enabled():
            return {"success": False, "content": "插件未启用"}
        if any(
            key in kwargs
            for key in (
                "link",
                "source_message_id",
                "include_audio",
                "analyze_video",
                "image_start",
            )
        ):
            return {
                "success": False,
                "content": "分析只填media_id和可选offset；音轨由插件设置决定，不填其他参数",
            }
        return await self.analyzer.read(media_id, scope_of(kwargs), offset, self.config.asr.enabled)

    async def read(
        self,
        link="",
        image_start=0,
        analyze_video=False,
        media_id="",
        analysis_offset=0,
        include_audio=False,
        **kwargs,
    ):
        if not self.enabled():
            return {"success": False, "content": "插件未启用", "content_items": []}
        if type(analyze_video) is not bool or type(include_audio) is not bool:
            return {
                "success": False,
                "content": "analyze_video/include_audio必须是布尔值",
                "content_items": [],
            }
        if analyze_video:
            if link or type(image_start) is not int or image_start != 0:
                return {
                    "success": False,
                    "content": "分析只填analyze_video=true与当前聊天视频media_id，不混填link或图片起点",
                    "content_items": [],
                }
            return await self.analyzer.read(
                media_id, scope_of(kwargs), analysis_offset, include_audio
            )
        if media_id or analysis_offset or include_audio:
            return {
                "success": False,
                "content": "分析媒体编号时需显式analyze_video=true，未自动调用模型",
                "content_items": [],
            }
        return await self.media.read(link, image_start, scope_of(kwargs))

    async def _chat_message(self, stream_id, message_id):
        """Read back one message dict of this chat; None if not found.

        Capability denial (missing manifest declaration) is surfaced as an
        error string so it cannot masquerade as a missing message.
        """
        message_id = str(message_id or "").strip()
        if not message_id or len(message_id) > 128:
            return None
        try:
            result = await self.ctx.call_capability(
                "message.get_by_id",
                chat_id=stream_id,
                message_id=message_id,
                include_binary_data=False,
            )
        except Exception as exc:
            raise RuntimeError(f"消息读取能力被拒绝：{type(exc).__name__}") from exc
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError("宿主消息查询返回错误，不能据此判断消息不存在")
        if result is None:
            return None
        # PluginContext unwraps the Host's {success, message} RPC envelope.
        # Successful calls normally return the message dict itself, not an
        # object containing another 'message' field.
        if isinstance(result, dict) and "message_id" in result:
            messages = [result]
        elif isinstance(result, list):
            messages = result
        elif isinstance(result, dict):
            messages = result.get("messages") or result.get("message") or []
            if isinstance(messages, dict):
                messages = [messages]
        else:
            messages = []
        for message in messages if isinstance(messages, list) else []:
            if (
                isinstance(message, dict)
                and str(message.get("session_id") or "") == stream_id
                and str(message.get("message_id") or "") == message_id
            ):
                return message
        return None

    async def chat_video(self, source_message_id="", **kwargs):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            return await self._chat_video(source_message_id, **kwargs)
        finally:
            self.tasks.discard(task)

    async def _chat_video(self, source_message_id="", **kwargs):
        if not self.enabled() or not self.config.media.enabled:
            return {"success": False, "content": "插件或媒体功能未启用"}
        if not isinstance(source_message_id, str):
            return {"success": False, "content": "缺少消息编号"}
        # In shared-Focus turns the model passes the target session explicitly;
        # host-injected stream_id is the triggering chat and would mismatch.
        explicit = str(kwargs.get("session_id") or "").strip()
        scope = explicit or scope_of(kwargs)
        if not scope:
            return {"success": False, "content": "缺少当前聊天或消息编号"}
        try:
            message = await self._chat_message(scope, source_message_id)
        except RuntimeError as exc:
            return {"success": False, "content": f"{exc}；未取得消息内容，不重试"}
        if message is None:
            return {
                "success": False,
                "content": "当前聊天未查到这条消息，请核对视频消息ID和目标聊天；不要反复重发视频。",
            }
        try:
            record = await self.chat_media.register(message, scope, message["message_id"])
        except ChatMediaError as exc:
            return {"success": False, "content": str(exc)}
        return {
            "success": True,
            "content": (
                f"已登记聊天视频「{record['name']}」，media_id={record['media_id']}。"
                "尚未分析或发送。"
                f'需要理解内容时调用neko_video_analyze(media_id="{record["media_id"]}")，'
                "才会抽帧并按管理员设置转写第一音轨；不用传include_audio。"
                "需要向QQ发送原视频时调用neko_video_send(media_id=同一编号)，无需先分析。"
                "编号仅本聊天10分钟有效；未知分析或投递不得通过重新open、换编号或等待重做。"
            ),
            **record,
        }

    @Tool(
        "neko_video_send",
        description="向QQ发送本聊天编号对应的原图、视频或实况；仅需发送时调用。",
        platforms=["qq"],
        timeout_ms=190000,
        parameters=[
            ToolParameterInfo(
                name="media_id",
                param_type=ToolParamType.STRING,
                description="open返回的本聊天media_id或实况motion_id，非图片序号/消息ID；直接发送无需先分析。投递未知时禁止重新open、换编号或等待后重发",
                required=True,
            )
        ],
    )
    async def send(self, media_id="", **kwargs):
        if not self.enabled():
            return {"success": False, "content": "插件未启用"}
        return await self.media.send(media_id, scope_of(kwargs))

    def admin(self, user_id, kwargs):
        sender = sender_of(user_id, kwargs)
        allowed = {
            str(x).removeprefix("qq:").strip()
            for x in self.config.permission.admin_users
            if str(x).strip()
        }
        return bool(sender and allowed and sender in allowed)

    @Command("neko_video_login", description="管理员扫码登录B站", pattern=r"^/neko-video\s+login$")
    async def login(self, stream_id="", user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs) or not stream_id:
            return False, "登录未就绪或无管理员权限", True
        async with self.login_start:
            if not self.enabled():
                return False, "插件未就绪", True
            if self.poll_task is not None and not self.poll_task.done():
                return False, "已有登录流程进行中", True
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                return await self._start_login(stream_id)
            finally:
                self.tasks.discard(task)

    async def _start_login(self, stream_id):
        try:
            png = await self.credentials.qrcode()
            sent = await self.ctx.send.image(base64.b64encode(png).decode(), stream_id)
            if not (sent is True or isinstance(sent, dict) and sent.get("success") is True):
                return False, "二维码投递未确认", True
            self.poll_task = asyncio.create_task(self._poll(stream_id))
            return True, "请在3分钟内用B站客户端扫码", True
        except Exception:
            return False, "登录二维码生成失败", True

    async def _poll(self, stream):
        try:
            result = await self.credentials.poll()
            await self.ctx.send.text(result, stream)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.ctx.send.text("扫码状态未确认，请稍后重试", stream)

    @Command(
        "neko_video_status", description="管理员查看视频插件状态", pattern=r"^/neko-video\s+status$"
    )
    async def status(self, user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs):
            return False, "无管理员权限或插件未启用", True
        try:
            return True, await self.credentials.status(), True
        except Exception:
            return False, "凭据状态未确认，已保留原文件", True

    @Command(
        "neko_video_logout", description="管理员清除B站扫码登录", pattern=r"^/neko-video\s+logout$"
    )
    async def logout(self, user_id="", **kwargs):
        if not self.enabled() or not self.admin(user_id, kwargs):
            return False, "无管理员权限或插件未启用", True
        if self.poll_task is not None:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
            self.poll_task = None
        try:
            return True, await self.credentials.logout(), True
        except Exception:
            return False, "登出未完成，请检查私有凭据文件", True
