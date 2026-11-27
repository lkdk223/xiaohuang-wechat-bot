"""Explicit, bounded image requests; never reuse chat OAuth credentials."""

import base64
import binascii
import io
import os
import re
import uuid
from pathlib import Path

import httpx
from PIL import Image

from backend.utils.runtime_artifacts import runtime_path


class ImageGenerationError(Exception):
    """A safe, user-facing error (no provider response or credentials)."""


def drawing_prompt(text, self_name="小黄"):
    # Deliberately do not infer billable actions from ordinary discussion.
    prefix = rf"(?:/画图|/image2|{re.escape(self_name)}[，,：:\s]*画图)"
    match = re.fullmatch(prefix + r"[：:\s]+([\s\S]+)", str(text or "").strip())
    return match.group(1).strip() if match else None


def validate_generated_image(filepath):
    if Path(filepath).is_symlink():
        raise ImageGenerationError("不允许发送图片符号链接。")
    path = Path(filepath).resolve()
    root = Path(runtime_path("generated-images", "placeholder")).parent.resolve()
    if path.parent != root or path.suffix != ".png":
        raise ImageGenerationError("仅允许发送本机生成目录中的 PNG 图片。")
    if not 0 < path.stat().st_size <= 20 * 1024 * 1024:
        raise ImageGenerationError("生成图片大小无效。")
    with Image.open(path) as picture:
        if picture.format != "PNG" or max(picture.size) > 4096:
            raise ImageGenerationError("生成图片格式无效。")
        picture.verify()
    return path


async def generate_image(prompt):
    ark_key = os.environ.get("ARK_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    key = ark_key or openai_key
    if not key:
        raise ImageGenerationError("画图尚未配置 ARK_API_KEY 或 OPENAI_API_KEY；网页登录不能代替图片 API Key。")
    if not 1 <= len(prompt) <= 4000:
        raise ImageGenerationError("画图描述需要在 1–4000 字以内。")
    if ark_key:
        url = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        payload = {
            "model": os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-pro-260628").strip(),
            "prompt": prompt,
            "size": "2K",
            "response_format": "b64_json",
            "watermark": False,
        }
    else:
        url = "https://api.openai.com/v1/images/generations"
        payload = {"model": "gpt-image-2", "prompt": prompt, "n": 1,
                   "size": "1024x1024", "quality": "low", "output_format": "png"}
    try:
        # No automatic retries: an ambiguous timeout may already have incurred a charge.
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        if response.status_code != 200:
            raise ImageGenerationError(
                f"图片生成接口返回 HTTP {response.status_code}，请检查 API 权限、余额或网络；未自动重试。"
            )
        encoded = response.json()["data"][0]["b64_json"]
        if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
            raise ValueError("oversized image")
        decoded = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(decoded)) as picture:
            if max(picture.size) > 4096:
                raise ValueError("invalid image")
            picture.load()
            output = io.BytesIO()
            picture.save(output, "PNG")
            raw = output.getvalue()
    except ImageGenerationError:
        raise
    except httpx.TimeoutException:
        raise ImageGenerationError("画图请求超时，结果不确定，未自动重试以避免重复扣费。") from None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OSError, binascii.Error):
        raise ImageGenerationError("图片生成未成功：网络异常或返回的图片无效；未自动重试。") from None
    path = Path(runtime_path("generated-images", f"{uuid.uuid4().hex}.png"))
    path.write_bytes(raw)
    return path
