"""Drawing commands share the bot's filters, send lock and outgoing policies."""

import asyncio
import logging
import time

from backend.core.bot_control import should_respond
from backend.core.image_generation import (
    ImageGenerationError,
    drawing_prompt,
    generate_image,
)
from backend.handlers.filter import should_reply_with_reason


async def _allowed(bot, event, text):
    if bot._is_paused or (bot._stop_event and bot._stop_event.is_set()):
        return False
    if not bot.bot_cfg.get("image_generation_enabled", True):
        return False
    if not should_respond(bot.bot_cfg)[0]:
        return False
    if not should_reply_with_reason(event, bot.config)[0]:
        return False
    policy = await bot.evaluate_outgoing_reply_policy(
        event=event, user_text=text, reply_text="[AI 生成图片] " + text,
    )
    return not policy.get("should_queue", False)


async def handle_drawing_command(bot, wx, event, text, trace_id=None):
    prompt = drawing_prompt(text, bot.bot_cfg.get("self_name", "小黄"))
    if prompt is None:
        return False
    if not await _allowed(bot, event, text):
        bot._log_flow(logging.INFO, "IMAGE.SKIP_POLICY", event=event, trace_id=trace_id)
        return True
    # One generation globally at a time, one attempt per minute. No background
    # retry can accidentally regenerate a paid image or send to a changed target.
    now = time.monotonic()
    if getattr(bot, "_image_generation_busy", False) or now < getattr(bot, "_image_generation_next", 0):
        bot._log_flow(logging.INFO, "IMAGE.SKIP_COOLDOWN", event=event, trace_id=trace_id)
        return True
    bot._image_generation_busy = True
    bot._image_generation_next = now + 60
    try:
        transport = bot._get_active_transport(wx)
        if not getattr(transport, "supports_generated_images", False):
            raise ImageGenerationError("当前微信传输后端不支持发送生成图片。")
        if getattr(transport, "send_enabled", True) is False:
            raise ImageGenerationError("当前未开启自动发送，未调用付费画图接口。")
        bot._log_flow(logging.INFO, "IMAGE.GENERATE_START", event=event, trace_id=trace_id)
        path = await generate_image(prompt)
        async with bot.wx_lock:
            if not await _allowed(bot, event, text):
                bot._log_flow(logging.INFO, "IMAGE.SEND_CANCELLED", event=event, trace_id=trace_id)
                return True
            transport = bot._get_active_transport(wx)
            result = await asyncio.to_thread(transport.send_image, str(path), who=event.chat_name, exact=True)
        if not isinstance(result, dict) or not result.get("success"):
            raise ImageGenerationError("图片已生成，但未确认微信发送成功；已保存在本机 generated-images 目录，未自动重发。")
        bot._log_flow(logging.INFO, "IMAGE.SENT", event=event, trace_id=trace_id)
    except ImageGenerationError as exc:
        bot._log_flow(logging.WARNING, "IMAGE.FAILED", event=event, trace_id=trace_id, error=str(exc))
        if await _allowed(bot, event, text):
            await bot._send_smart_reply(bot._get_active_transport(wx), event, str(exc), trace_id=trace_id)
    except Exception:  # noqa: BLE001 - normalize transport errors without exposing secrets
        # No raw provider, clipboard or filesystem exceptions in chat/logs.
        bot._log_flow(logging.WARNING, "IMAGE.UNEXPECTED_FAILURE", event=event, trace_id=trace_id)
    finally:
        bot._image_generation_busy = False
    return True
