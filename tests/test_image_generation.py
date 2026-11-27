import asyncio
import base64
import io
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from backend import bot_image_flow as flow
from backend.core import image_generation as images


@pytest.mark.parametrize("text,prompt", [
    ("/画图 一只小狗", "一只小狗"), ("/image2 a cat", "a cat"),
    ("小黄，画图：雪山", "雪山"), ("不要画图", None),
    ("他说 /画图 小狗", None), ("怎么使用image2", None), ("/画图", None),
])
def test_explicit_command(text, prompt):
    assert images.drawing_prompt(text) == prompt


def mock_client(monkeypatch, response=None, error=None):
    post = AsyncMock(return_value=response, side_effect=error)
    client = SimpleNamespace(post=post)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(images.httpx, "AsyncClient", MagicMock(return_value=context))
    return post


@pytest.mark.asyncio
async def test_missing_key_never_calls_api(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    post = mock_client(monkeypatch)
    with pytest.raises(images.ImageGenerationError, match="ARK_API_KEY"):
        await images.generate_image("cat")
    post.assert_not_called()


@pytest.mark.asyncio
async def test_generate_png(monkeypatch, tmp_path):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr(images, "runtime_path", lambda *parts: str(tmp_path / parts[-1]))
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, "PNG")
    post = mock_client(monkeypatch, httpx.Response(200, json={"data": [
        {"b64_json": base64.b64encode(buffer.getvalue()).decode()}
    ]}))
    path = await images.generate_image("cat")
    assert images.validate_generated_image(path) == path
    assert post.call_args.args[0] == "https://api.openai.com/v1/images/generations"
    assert post.call_args.kwargs["json"]["model"] == "gpt-image-2"
    assert post.call_args.kwargs["json"]["n"] == 1
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_ark_key_uses_seedream(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "test-only-ark-key")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")
    monkeypatch.setattr(images, "runtime_path", lambda *parts: str(tmp_path / parts[-1]))
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, "JPEG")
    post = mock_client(monkeypatch, httpx.Response(200, json={"data": [
        {"b64_json": base64.b64encode(buffer.getvalue()).decode()}
    ]}))

    path = await images.generate_image("cat")

    assert images.validate_generated_image(path) == path
    assert post.call_args.args[0] == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    assert post.call_args.kwargs["json"]["model"] == "doubao-seedream-5-0-pro-260628"
    assert post.call_args.kwargs["json"]["response_format"] == "b64_json"
    assert post.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response,error", [
    (httpx.Response(401, text="secret-provider-detail"), None),
    (httpx.Response(200, json={"data": [{"b64_json": "not an image"}]}), None),
    (None, httpx.ReadTimeout("secret-provider-detail")),
])
async def test_errors_redacted_no_retry(monkeypatch, response, error):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    post = mock_client(monkeypatch, response, error)
    with pytest.raises(images.ImageGenerationError) as caught:
        await images.generate_image("cat")
    assert "secret" not in str(caught.value)
    assert "test-only-key" not in str(caught.value)
    assert post.await_count == 1


def bot_fixture(monkeypatch):
    transport = SimpleNamespace(supports_generated_images=True, send_enabled=True,
                                send_image=MagicMock(return_value={"success": True}))
    bot = SimpleNamespace(
        bot_cfg={}, config={}, _is_paused=False, _stop_event=None, wx_lock=asyncio.Lock(),
        evaluate_outgoing_reply_policy=AsyncMock(return_value={"should_queue": False}),
        _get_active_transport=lambda wx: transport, _log_flow=MagicMock(),
        _send_smart_reply=AsyncMock(),
    )
    monkeypatch.setattr(flow, "should_reply_with_reason", lambda *args: (True, "ok"))
    monkeypatch.setattr(flow, "should_respond", lambda *args: (True, ""))
    generate = AsyncMock(return_value="generated.png")
    monkeypatch.setattr(flow, "generate_image", generate)
    return bot, transport, generate, SimpleNamespace(chat_name="允许的群")


@pytest.mark.asyncio
async def test_send_and_cooldown(monkeypatch):
    bot, transport, generate, event = bot_fixture(monkeypatch)
    assert await flow.handle_drawing_command(bot, transport, event, "/画图 猫")
    transport.send_image.assert_called_once_with("generated.png", who="允许的群", exact=True)
    assert await flow.handle_drawing_command(bot, transport, event, "/画图 猫")
    generate.assert_awaited_once()
    bot._send_smart_reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("block", ["manual", "paused", "disabled", "whitelist", "send_disabled"])
async def test_policy_blocks_paid_call(monkeypatch, block):
    bot, transport, generate, event = bot_fixture(monkeypatch)
    if block == "manual":
        bot.evaluate_outgoing_reply_policy.return_value = {"should_queue": True}
    elif block == "paused":
        bot._is_paused = True
    elif block == "disabled":
        bot.bot_cfg["image_generation_enabled"] = False
    elif block == "whitelist":
        monkeypatch.setattr(flow, "should_reply_with_reason", lambda *args: (False, "blocked"))
    else:
        transport.send_enabled = False
    await flow.handle_drawing_command(bot, transport, event, "/画图 猫")
    generate.assert_not_awaited()
    transport.send_image.assert_not_called()


@pytest.mark.asyncio
async def test_policy_rechecked_after_generation(monkeypatch):
    bot, transport, generate, event = bot_fixture(monkeypatch)
    async def pause(prompt):
        bot._is_paused = True
        return "generated.png"
    generate.side_effect = pause
    await flow.handle_drawing_command(bot, transport, event, "/画图 猫")
    transport.send_image.assert_not_called()


@pytest.mark.asyncio
async def test_failed_send_never_claims_success(monkeypatch):
    bot, transport, _generate, event = bot_fixture(monkeypatch)
    transport.send_image.return_value = {"success": False}
    await flow.handle_drawing_command(bot, transport, event, "/画图 猫")
    assert "未确认微信发送成功" in bot._send_smart_reply.call_args.args[2]
    assert transport.send_image.call_count == 1


def test_transport_rejects_non_generated_path(tmp_path):
    path = tmp_path / "private.png"
    Image.new("RGB", (16, 16)).save(path)
    with pytest.raises(images.ImageGenerationError):
        images.validate_generated_image(path)


@pytest.mark.parametrize("case,success", [
    ("success", True), ("wrong_target", False), ("draft", False),
    ("no_focus", False), ("no_bubble", False),
])
def test_pyweixin_generated_image_delivery(monkeypatch, case, success):
    from backend.transports import image_clipboard
    from backend.transports import pyweixin_adapter as adapter

    monkeypatch.setattr(images, "validate_generated_image", lambda path: path)
    monkeypatch.setattr(image_clipboard, "pasted_bitmap", lambda path: nullcontext())
    monkeypatch.setattr(adapter.time, "sleep", lambda seconds: None)
    hotkey = MagicMock()
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(hotkey=hotkey))
    transport = adapter.PyWeixinTransport.__new__(adapter.PyWeixinTransport)
    transport.targets = ["群"]
    transport.send_enabled = True
    transport._lock = threading.RLock()
    edit = MagicMock()
    edit.window_text.return_value = "草稿" if case == "draft" else ""
    edit.has_keyboard_focus.return_value = case != "no_focus"
    dialog = MagicMock()
    session = {"dialog": dialog, "input_edit": edit, "seen_runtime_ids": set()}
    transport._sessions = {"群": session}
    transport._visible_items = MagicMock(side_effect=[[], *([[]] * 12)] if case == "no_bubble" else [[], ["image"]])
    transport._runtime_id = lambda item: item
    transport._visual_message_type = lambda item: "image"
    transport._Tools = SimpleNamespace(is_my_bubble=lambda *args: True)
    result = transport.send_image("generated.png", who="错误群" if case == "wrong_target" else "群")
    assert result["success"] is success
    if case in {"wrong_target", "draft", "no_focus"}:
        hotkey.assert_not_called()
    else:
        assert hotkey.call_count == 2  # paste + send, never retry
    if success:
        assert "image" in session["seen_runtime_ids"]


def test_image_toggle_survives_shared_config_validation():
    from backend.shared_config import validate_shared_config

    config = {"api": {}, "logging": {}, "bot": {"image_generation_enabled": False}}
    assert validate_shared_config(config)["bot"]["image_generation_enabled"] is False
