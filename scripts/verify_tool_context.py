"""Called by the isolated Host consumption check; synthetic RPC/media only."""

import copy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def verify_tool_context(directory):
    from maibot_sdk.components import collect_components
    from maibot_sdk.context import PluginContext
    from src.core.tooling import ToolInvocation
    from src.maisaka.reasoning_engine import MaisakaReasoningEngine
    from src.maisaka.focus.tools import execution_context
    from src.plugin_runtime.component_query import ComponentQueryService
    from src.plugin_runtime.host.component_registry import ToolEntry
    from neko_video.config import Config
    from neko_video.plugin import NekoVideoPlugin
    from neko_video.media_service import MediaService
    from neko_video.chat_media import ChatMediaService
    from neko_video.video_analysis import VideoAnalyzer

    count = 0
    for mode in ("normal", "focus"):
        for platform in ("qq", "telegram"):
            for group in (False, True):
                session = f"{mode}-{platform}-{group}"
                group_id = "123" if group else ""
                user_id = "" if group else "10001"
                runtime = SimpleNamespace(
                    session_id=session,
                    chat_stream=SimpleNamespace(
                        platform=platform,
                        group_id=group_id,
                        user_id=user_id,
                        is_group_session=group,
                    ),
                )
                context = (
                    MaisakaReasoningEngine(runtime)._build_tool_execution_context("offline")
                    if mode == "normal"
                    else execution_context(runtime, "offline")
                )
                calls = []

                async def rpc(method, plugin_id, payload):
                    assert (method, plugin_id) == ("cap.call", "neko.video")
                    calls.append(payload)
                    cap = payload["capability"]
                    if cap == "chat.get_all_streams":
                        return {
                            "success": True,
                            "streams": [
                                {
                                    "session_id": session,
                                    "platform": "qq",
                                    "account_id": "789",
                                    "group_id": group_id,
                                    "user_id": user_id,
                                    "chat_type": "group" if group else "private",
                                    "is_group_session": group,
                                }
                            ],
                        }
                    if cap == "api.call":
                        assert payload["args"]["args"]["action_name"] == "get_login_info"
                        return {"success": True, "result": {"user_id": "789"}}
                    if cap == "send.image":
                        assert payload["args"]["stream_id"] == session
                        return {"success": False}
                    raise AssertionError("Unexpected RPC: " + cap)

                class Plugin(NekoVideoPlugin):
                    config = Config()
                    ctx = PluginContext("neko.video", rpc_call=rpc)

                bot = Plugin()
                bot.config.plugin.enabled = True
                bot.config.parse.enable_douyin = True
                bot.config.analysis.enabled = True
                bot.config.asr.enabled = False
                bot.ready = True
                # The domain service only needs an isolated path and the actual SDK send.
                transport = SimpleNamespace(
                    paths=SimpleNamespace(data_dir=directory / session),
                    send=bot.ctx.send,
                    call_capability=bot.ctx.call_capability,
                )
                bot.media = MediaService(bot.config, transport)
                bot.chat_media = ChatMediaService(bot.ctx, bot.config, bot.media.assets)
                bot.analyzer = VideoAnalyzer(
                    bot.config,
                    bot.media.assets,
                    directory / session,
                    AsyncMock(side_effect=AssertionError("model")),
                )
                entries = {
                    item["name"]: ToolEntry(item["name"], "TOOL", "neko.video", item["metadata"])
                    for item in collect_components(bot)
                    if item["type"] == "TOOL"
                }

                def payload(name, **business):
                    args = dict(
                        business,
                        session_id="foreign",
                        stream_id="foreign",
                        chat_id="foreign",
                        platform="qq",
                        account_id="999",
                        _host_context={"session_id": "foreign"},
                    )
                    before = copy.deepcopy(args)
                    invocation = ToolInvocation(
                        tool_name=name, arguments=args, session_id=session, stream_id=session
                    )
                    result = ComponentQueryService._build_tool_invocation_payload(
                        invocation, context, entry=entries[name]
                    )
                    assert args == before and result["stream_id"] == "foreign"
                    assert result["_host_context"]["account_id"] == ""
                    return result

                try:
                    fixture = dict(
                        title="synthetic",
                        author="",
                        images=[],
                        image_total=0,
                        covers=[],
                        video_urls=["https://media.invalid/video"],
                    )
                    with patch(
                        "neko_video.media_service.resolve_media", AsyncMock(return_value=fixture)
                    ):
                        opened = await bot.open(
                            **payload("neko_video_open", link="7123456789012345678")
                        )
                    assert opened["success"], opened
                    key = next(iter(bot.media.assets.items))
                    assert bot.media.assets.peek(session, key).scope == session
                    assert ("neko_video_send" in opened["content"]) == (platform == "qq")
                    # A persisted unknown under the unchanged trusted session/content key blocks
                    # even continuation before any download/model, despite forged ordinary IDs.
                    digest = hashlib.sha256(b"synthetic-video").hexdigest()
                    signature = bot.analyzer.signature(False)
                    attempt = hashlib.sha256(
                        ("video-attempt-v1\0" + session + "\0" + digest).encode()
                    ).hexdigest()
                    analysis_key = hashlib.sha256(
                        (
                            "video-analysis-v1\0" + session + "\0" + digest + "\0" + signature
                        ).encode()
                    ).hexdigest()
                    assert await bot.analyzer.db("admit", analysis_key, attempt)
                    bot.analyzer.handles[(session, key, signature)] = (analysis_key, attempt)
                    analyzed = await bot.analyze(
                        **payload("neko_video_analyze", media_id=key, offset=6000)
                    )
                    assert not analyzed["success"] and "未决分析" in analyzed["content"], analyzed
                    for name, handler, business in (
                        ("neko_video_open", bot.open, {"link": "7123456789012345678"}),
                        ("neko_video_analyze", bot.analyze, {"media_id": key}),
                        ("neko_video_send", bot.send, {"media_id": key}),
                    ):
                        for invalid in (
                            None,
                            {},
                            {**payload(name, **business)["_host_context"], "stream_id": "foreign"},
                        ):
                            result = await handler(
                                **{**payload(name, **business), "_host_context": invalid}
                            )
                            assert not result["success"] and "可信身份" in result["content"]
                        invocation = ToolInvocation(
                            tool_name=name, arguments=payload(name, **business)
                        )
                        missing = ComponentQueryService._build_tool_invocation_payload(
                            invocation, None, entry=entries[name]
                        )
                        assert (
                            missing["_host_context"] == {}
                            and not (await handler(**missing))["success"]
                        )
                    if platform != "qq":
                        result = await bot.open(
                            **payload("neko_video_open", source_message_id="42")
                        )
                        assert not result["success"] and "仅支持QQ" in result["content"]
                        assert not (await bot.send(**payload("neko_video_send", media_id=key)))[
                            "success"
                        ]
                        assert not calls
                    else:
                        raw = b"synthetic-original-image"
                        digest = hashlib.sha256(raw).hexdigest()
                        with (
                            patch(
                                "neko_video.media_service.download_image",
                                AsyncMock(return_value=raw),
                            ),
                            patch("neko_video.media_service.validate_original"),
                        ):
                            for _ in range(2):
                                image = bot.media.assets.add(
                                    session, "image", ["https://media.invalid/image"], digest
                                )
                                sent = await bot.send(**payload("neko_video_send", media_id=image))
                                assert not sent["success"], sent
                        assert sum(c["capability"] == "send.image" for c in calls) == 1
                        identity = hashlib.sha256(
                            ("send-v1\0" + session + "\0image\0" + digest).encode()
                        ).hexdigest()
                        assert bot.media.delivery_store.get(identity, identity) == {
                            "state": "unknown"
                        }
                    bot.analyzer.generate.assert_not_awaited()
                    count += 1
                finally:
                    await bot.on_unload()
    print(
        f"REAL_HOST_CONTEXT_OK: {count} normal/Focus platform/group cases; real SDK scope/assets/unknown, no external IO"
    )
