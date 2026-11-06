"""Additional real Host consumption check; runtime loader QA remains separate."""

import argparse
from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def verify(host, temp_dir):
    root = Path(__file__).resolve().parents[1]
    with ExitStack() as cleanup:
        directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=temp_dir))
        cwd = Path.cwd()
        os.chdir(directory)
        cleanup.callback(os.chdir, cwd)
        os.environ.update(
            MAIBOT_INSTANCE_DIR=directory, MAIBOT_LOCALE="zh-CN", MAIBOT_HOST_VERSION="1.3.19"
        )
        sys.path[:0] = [str(root), str(host), str(host / "scripts")]
        import neko_pytest_guard  # noqa: F401
        from src.plugin_runtime.component_query import ComponentQueryService
        from src.maisaka.reasoning_engine import MaisakaReasoningEngine
        from src.plugin_runtime.hook_payloads import (
            serialize_prompt_items,
            deserialize_prompt_items,
        )
        from src.common.data_models.message_component_data_model import (
            MessageSequence,
            TextComponent,
        )
        from src.maisaka.context.history import build_prefixed_message_sequence
        from src.maisaka.context.messages import _build_item_from_sequence
        from src.maisaka.context.planner_messages import build_planner_prefix
        from src.llm_models.payload_content.context_item import RoleType
        from src.common.database.database import engine
        from src.common.logger import close_handlers
        from neko_video.video_analysis import VideoAnalyzer
        from neko_video.context import VideoContext
        from neko_video.config import Config

        cleanup.callback(engine.dispose)
        cleanup.callback(close_handlers)
        value = VideoAnalyzer.output(
            {
                "success": True,
                "_analysis_text": "a" * 6000 + "tail",
                "sample_positions_seconds": [1],
                "model_truncated": True,
            },
            0,
            False,
        )
        entry = SimpleNamespace(name="neko_video_analyze", plugin_id="neko.video")
        result = ComponentQueryService._parse_tool_invoke_result(entry, value)
        # Real result -> real history text builder -> actual append to ToolResultMessage.
        runtime = SimpleNamespace(_chat_history=[])
        fake = SimpleNamespace(
            _runtime=runtime,
            _active_logical_turn_id="synthetic-turn",
            _iter_tool_result_media_items=lambda result: [],
            _append_tool_result_media_messages=lambda *args: None,
            _append_tool_post_history_messages=lambda *args: None,
        )
        fake._build_tool_result_history_content = lambda call, result, **kw: (
            MaisakaReasoningEngine._build_tool_result_history_content(fake, call, result, **kw)
        )
        call = SimpleNamespace(func_name="neko_video_analyze", call_id="synthetic")
        MaisakaReasoningEngine._append_tool_execution_result(fake, call, result)
        visible = runtime._chat_history[0].content
        assert (
            "offset=6000" in visible and "仅续读成功缓存" in visible and "模型输出已截断" in visible
        )
        # Existing Host structured-content fallback is conditional, not a guarantee:
        # a nonempty content wins. Validate both paths instead of claiming no fallback.
        old = ComponentQueryService._parse_tool_invoke_result(
            entry, {"success": True, "content": "old text", "next_analysis_offset": 6000}
        )
        assert old.get_history_content() == "old text"
        fallback = ComponentQueryService._parse_tool_invoke_result(
            entry, {"success": True, "next_analysis_offset": 6000}
        )
        assert "next_analysis_offset" in fallback.get_history_content()
        refs = VideoContext()
        refs.record({"session_id": "s", "message_id": "42"}, "external", "bilibili")
        prefix = build_planner_prefix(
            timestamp=datetime(2026, 11, 6), user_name="synthetic", message_id="42"
        )
        for components in ([TextComponent("body")], [TextComponent("body"), TextComponent("tail")]):
            sequence = build_prefixed_message_sequence(MessageSequence(components), prefix)
            source = _build_item_from_sequence(
                RoleType.User, sequence, "", enable_visual_message=False
            )
            wire = serialize_prompt_items([source])
            output = refs.inject(wire, Config().parse, session_id="s", item_schema_version=1)
            assert output and output[0] == wire[0] and len(deserialize_prompt_items(output)) == 2
        print(
            "REAL_HOST_VIDEO_OK: actual tool conversion/history append, conditional fallback, actual prefix/serialize/reference/deserialize; no external calls"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-source", type=Path, required=True)
    parser.add_argument("--temp-dir", type=Path, required=True)
    args = parser.parse_args()
    verify(args.host_source.resolve(), args.temp_dir.resolve())
