from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from backend.bot_event_flow import maybe_save_event_image
from backend.bot import WeChatBot
from backend.handlers.converters import normalize_new_messages
from backend.utils.message import (
    build_visual_user_prompt,
    extract_visual_chat_reply,
    is_visual_message,
)


class _StickerMessage:
    content = ""
    sender = "小王"
    type = "emoji"
    attr = None
    is_at_me = False

    def save_file(self, path: str) -> str:
        Image.new("RGB", (48, 48), "orange").save(path, format="JPEG")
        return path


def test_sticker_message_is_preserved_as_visual_event():
    events = normalize_new_messages(
        {"chat_name": "测试群", "chat_type": "group", "msg": [_StickerMessage()]},
        self_name="小黄",
    )

    assert len(events) == 1
    assert events[0].msg_type == "emoji"
    assert events[0].content == "[表情包]"
    assert events[0].raw_item is not None
    assert is_visual_message(events[0].msg_type) is True


@pytest.mark.asyncio
async def test_sticker_frame_is_saved_for_existing_multimodal_pipeline(tmp_path):
    event = normalize_new_messages(
        {"chat_name": "小王", "chat_type": "friend", "msg": [_StickerMessage()]},
        self_name="小黄",
    )[0]

    image_path = await maybe_save_event_image(event, save_root=str(tmp_path / "frame.jpg"))

    assert image_path == str(tmp_path / "frame.jpg")
    assert Path(image_path).exists()
    with Image.open(image_path) as frame:
        assert frame.size == (48, 48)


def test_sticker_prompt_requests_intent_aware_chat_response():
    prompt = build_visual_user_prompt("[表情包]")

    assert "文字" in prompt
    assert "情绪" in prompt
    assert "交流意图" in prompt
    assert "自然回应" in prompt
    assert "最终只输出" in prompt
    assert "不要输出图片描述" in prompt


def test_visual_reply_extractor_keeps_only_direct_reply_after_analysis():
    response = (
        "这张图是一个抱着小猫的女孩，表达撒娇和求抱抱。\n\n"
        "你可以回：哎哟这也太萌了吧，抱走了哈😊"
    )

    assert extract_visual_chat_reply(response) == "哎哟这也太萌了吧，抱走了哈😊"


def test_visual_reply_extractor_uses_last_candidate_when_model_repeats():
    response = "画面分析\n你可以回：第一条\n画面分析\n建议回复：最终这条"

    assert extract_visual_chat_reply(response) == "最终这条"


def test_identical_visual_capture_is_suppressed_within_dedup_window(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    Image.new("RGB", (16, 16), "purple").save(first)
    second.write_bytes(first.read_bytes())
    bot = WeChatBot("config.yaml")
    bot.bot_cfg = {"visual_duplicate_window_sec": 90.0}
    event = SimpleNamespace(chat_name="测试群", msg_type="emoji")

    assert bot._is_duplicate_visual_capture(event, str(first)) is False
    assert bot._is_duplicate_visual_capture(event, str(second)) is True


def test_different_visual_capture_is_not_suppressed(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    Image.new("RGB", (16, 16), "purple").save(first)
    Image.new("RGB", (16, 16), "yellow").save(second)
    bot = WeChatBot("config.yaml")
    bot.bot_cfg = {"visual_duplicate_window_sec": 90.0}
    event = SimpleNamespace(chat_name="测试群", msg_type="emoji")

    assert bot._is_duplicate_visual_capture(event, str(first)) is False
    assert bot._is_duplicate_visual_capture(event, str(second)) is False


@pytest.mark.asyncio
async def test_sticker_capture_is_deleted_when_processing_exits_early(tmp_path):
    capture = tmp_path / "ignored-sticker.jpg"
    Image.new("RGB", (16, 16), "green").save(capture)
    bot = WeChatBot("config.yaml")
    bot.sem = asyncio.Semaphore(1)
    bot.ipc = MagicMock()
    bot.bot_manager = SimpleNamespace(broadcast_event=AsyncMock())
    bot.bot_cfg = {}
    bot.config = {"bot": {}}
    bot._maybe_save_event_image = AsyncMock(return_value=str(capture))
    event = SimpleNamespace(
        chat_name="测试群",
        sender="小王",
        content="[表情包]",
        msg_type="emoji",
        is_group=True,
        is_at_me=False,
        is_self=False,
        chat_type="group",
        raw_item=_StickerMessage(),
        timestamp=None,
    )

    with patch("backend.bot.should_respond", return_value=(False, "")):
        await bot.handle_event(MagicMock(), event)
        await asyncio.sleep(0)

    assert capture.exists() is False


@pytest.mark.asyncio
async def test_visual_event_without_capture_never_calls_model():
    bot = WeChatBot("config.yaml")
    bot.sem = asyncio.Semaphore(1)
    bot.ipc = MagicMock()
    bot.bot_manager = SimpleNamespace(broadcast_event=AsyncMock())
    bot.bot_cfg = {}
    bot.config = {"bot": {}}
    bot._maybe_save_event_image = AsyncMock(return_value=None)
    bot._process_and_reply = AsyncMock()
    event = SimpleNamespace(
        chat_name="测试群",
        sender="群成员",
        content="[表情包]",
        msg_type="emoji",
        is_group=True,
        is_at_me=False,
        is_self=False,
        chat_type="group",
        raw_item=_StickerMessage(),
        timestamp=None,
    )

    await bot.handle_event(MagicMock(), event)

    bot._process_and_reply.assert_not_awaited()
