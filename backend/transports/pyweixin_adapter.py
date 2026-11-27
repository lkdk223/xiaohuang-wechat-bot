"""Visible WeChat 4.x transport powered by the official ``pyweixin`` RPA API.

This backend deliberately monitors exact private-chat or group-chat allowlist
targets.  It does not read the chat database or inject into WeChat.  The default
sidebar mode keeps one WeChat main window and switches exact allowlisted rows in
its conversation list; the former independent-window mode remains available as
a rollback option.  Image and sticker bubbles are captured from the allowlisted
chat for multimodal understanding; payment and other attachment messages remain
blocked.  Existing visible messages are snapshotted on startup and ignored.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import logging
import os
import re
import threading
import time
from typing import Any

from packaging.version import InvalidVersion, Version

from .base import BaseTransport

logger = logging.getLogger(__name__)

MINIMUM_WEIXIN_VERSION = "4.1.6"
_BLOCKED_CONTENT = re.compile(
    r"(?:红包|转账|收款|付款|支付|微信支付|退款|银行卡|￥|¥|\[文件\]|\[视频\]|\[语音\])",
    re.IGNORECASE,
)
_IMAGE_TEXT_MARKERS = ("[图片]", "图片", "圖片", "[image]", "image", "[photo]", "photo")
_STICKER_TEXT_MARKERS = (
    "[表情]",
    "[表情包]",
    "[动画表情]",
    "动画表情",
    "[动态表情]",
    "动态表情",
    "動態貼圖",
    "[sticker]",
    "sticker",
    "[emoji]",
    "emoji",
    "[gif]",
    "gif",
    "animated stickers",
)


class PyWeixinVisualMessage:
    """Media bubble wrapper compatible with the shared ``save_file`` contract."""

    def __init__(
        self,
        bubble: Any,
        *,
        dialog: Any,
        lock: threading.RLock,
        sender: str,
        media_type: str,
        is_group: bool,
        minimize_after: bool = True,
        capture_resolver: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self._bubble = bubble
        self._dialog = dialog
        self._lock = lock
        self.sender = sender
        self.type = media_type
        self.content = "[表情包]" if media_type == "emoji" else "[图片]"
        self.attr = None
        self.is_at_me = False
        self.is_group = is_group
        self._minimize_after = minimize_after
        self._capture_resolver = capture_resolver

    @staticmethod
    def _capture_area(control: Any) -> int:
        try:
            rectangle = control.rectangle()
            return max(0, int(rectangle.width())) * max(0, int(rectangle.height()))
        except Exception:
            return 0

    def _capture_target(self) -> Any:
        try:
            images = list(self._bubble.descendants(control_type="Image") or [])
        except Exception:
            images = []
        candidates = [candidate for candidate in images if callable(getattr(candidate, "capture_as_image", None))]
        if candidates:
            largest = max(candidates, key=self._capture_area)
            # A lone small Image control is usually the sender avatar while
            # custom stickers are painted directly by Qt.  Capturing the whole
            # reference bubble retains that painted sticker.  With multiple
            # images, or one sufficiently large image, the largest control is
            # the cleaner crop for the vision model.
            if len(candidates) > 1 or self._capture_area(largest) >= 64 * 64:
                return largest
        return self._bubble

    def save_file(self, path: str) -> str:
        """Capture the visible current frame without reading private chat storage."""
        target_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with self._lock:
            try:
                if self._capture_resolver is not None:
                    self._dialog, self._bubble = self._capture_resolver()
                self._dialog.restore()
                capture_target = self._capture_target()
                capture = getattr(capture_target, "capture_as_image", None)
                if not callable(capture):
                    raise RuntimeError("微信表情气泡不支持画面截取")
                image = capture()
                if image is None:
                    raise RuntimeError("微信表情气泡截取结果为空")
                if getattr(image, "mode", "RGB") not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(target_path, format="JPEG", quality=92)
                return target_path
            finally:
                if self._minimize_after:
                    try:
                        self._dialog.minimize()
                    except Exception:
                        logger.debug("重新最小化表情包来源窗口失败", exc_info=True)


class TransportUnavailableError(RuntimeError):
    """Raised when the visible WeChat 4.x transport cannot start safely."""


class PyWeixinTransport(BaseTransport):
    """Allowlisted multi-contact WeChat 4.x UI automation transport."""

    backend_name = "pyweixin"

    def __init__(self, bot_cfg: dict[str, Any], ai_client: Any | None = None) -> None:
        self.bot_cfg = dict(bot_cfg or {})
        self.ai_client = ai_client
        self._lock = threading.RLock()
        self._dialog_window = None
        self._chat_list = None
        self._input_edit = None
        self._seen_runtime_ids: set[tuple[Any, ...]] = set()
        self._last_item_signature: tuple[Any, ...] | None = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._main_window = None
        self._session_list = None
        self._active_sidebar_target = ""
        self._sidebar_row_signatures: dict[str, str] = {}
        self._closed = False
        self.send_enabled = bool(self.bot_cfg.get("pyweixin_send_enabled", False))
        self.window_mode = str(
            self.bot_cfg.get("pyweixin_window_mode", "sidebar") or "sidebar"
        ).strip().lower()
        if self.window_mode not in {"sidebar", "independent"}:
            raise TransportUnavailableError(
                "pyweixin_window_mode 仅支持 sidebar 或 independent"
            )
        self.targets = self._resolve_targets()
        self.target = self.targets[0]

        if os.name != "nt":
            raise TransportUnavailableError("pyweixin 仅支持 Windows")

        try:
            from pyweixin import GlobalConfig, Navigator, Tools
            from pyweixin.Uielements import Edits, Lists, Main_window, Texts
        except ImportError as exc:
            raise TransportUnavailableError(
                "缺少微信 4.x 自动化依赖，请安装 pywechat127==1.9.8"
            ) from exc

        self._Navigator = Navigator
        self._Tools = Tools
        self._Lists = Lists
        self._Edits = Edits
        self._MainWindow = Main_window
        self._Texts = Texts
        GlobalConfig.language = "简体中文"
        GlobalConfig.close_weixin = False
        GlobalConfig.is_maximize = False
        GlobalConfig.search_pages = 0

        if not Tools.is_weixin_running():
            raise TransportUnavailableError("未检测到正在运行的微信 4.x，请先登录微信")

        self.wechat_version = str(GlobalConfig.Version or "").strip()
        self._validate_version()
        try:
            if self.window_mode == "sidebar":
                self._open_sidebar_sessions()
            else:
                for target in self.targets:
                    self._open_target_window(target)
        except Exception:
            self.close()
            raise

    def _resolve_targets(self) -> list[str]:
        explicit = str(self.bot_cfg.get("pyweixin_test_contact") or "").strip()
        if not self.bot_cfg.get("whitelist_enabled", False):
            raise TransportUnavailableError("pyweixin 安全模式要求启用联系人白名单")
        whitelist: list[str] = []
        for item in list(self.bot_cfg.get("whitelist") or []):
            name = str(item or "").strip()
            if name and name not in whitelist:
                whitelist.append(name)
        if not whitelist:
            raise TransportUnavailableError("pyweixin 安全模式要求至少配置一个白名单联系人")
        max_contacts = max(1, int(self.bot_cfg.get("pyweixin_max_contacts", 8) or 8))
        if len(whitelist) > max_contacts:
            raise TransportUnavailableError(
                f"pyweixin 白名单最多允许 {max_contacts} 个联系人，当前为 {len(whitelist)} 个"
            )
        if explicit and explicit in whitelist:
            whitelist.remove(explicit)
            whitelist.insert(0, explicit)
        return whitelist

    def _resolve_target(self) -> str:
        """Compatibility helper retained for integrations that expect one primary target."""
        return self._resolve_targets()[0]

    def _validate_version(self) -> None:
        if not self.wechat_version:
            raise TransportUnavailableError("无法读取当前微信版本")
        try:
            current = Version(self.wechat_version)
        except InvalidVersion as exc:
            raise TransportUnavailableError(
                f"无法识别当前微信版本：{self.wechat_version}"
            ) from exc
        if current < Version(MINIMUM_WEIXIN_VERSION):
            raise TransportUnavailableError(
                f"pyweixin 要求微信 {MINIMUM_WEIXIN_VERSION}+，当前为 {self.wechat_version}"
            )

    @staticmethod
    def _runtime_id(item: Any) -> tuple[Any, ...]:
        value = getattr(getattr(item, "element_info", None), "runtime_id", None)
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return (str(value),)

    @classmethod
    def _item_signature(cls, item: Any) -> tuple[Any, ...]:
        """Identify the latest bubble even when WeChat recycles its UIA id."""
        return (
            cls._runtime_id(item),
            str(item.class_name() or ""),
            str(item.window_text() or "").strip(),
        )

    @staticmethod
    def _content_signature(item: Any) -> tuple[str, str]:
        """Return a stable bubble identity that survives a UIA window rebuild."""
        return (
            str(item.class_name() or ""),
            str(item.window_text() or "").strip(),
        )

    @staticmethod
    def _visual_message_type(item: Any) -> str | None:
        """Classify visible image/sticker bubbles across WeChat 4.x UIA variants."""
        raw = str(item.window_text() or "").strip().lower()
        class_name = str(item.class_name() or "").lower()
        # pyweixin exposes both user images and collected/custom stickers as
        # ChatBubbleReferItemView.  Their accessible labels are unbracketed:
        # `图片` and `动画表情` in Simplified Chinese.  Keep these broad label
        # checks scoped to the reference-bubble class so ordinary text such as
        # "给我发张图片" is not mistaken for media.
        if "chatbubblereferitemview" in class_name:
            if any(marker.lower() in raw for marker in _STICKER_TEXT_MARKERS):
                return "emoji"
            if any(marker.lower() in raw for marker in _IMAGE_TEXT_MARKERS):
                return "image"

        # Bracketed placeholders are produced by some adapters and older
        # WeChat accessibility builds, sometimes on a generic bubble class.
        if any(marker.lower() in raw for marker in _STICKER_TEXT_MARKERS if marker.startswith("[")):
            return "emoji"
        if any(marker.lower() in raw for marker in _IMAGE_TEXT_MARKERS if marker.startswith("[")):
            return "image"

        if any(marker in class_name for marker in ("sticker", "emoji", "emoticon", "gif")):
            return "emoji"
        if any(marker in class_name for marker in ("image", "photo", "picture")):
            return "image"

        try:
            images = list(item.descendants(control_type="Image") or [])
        except Exception:
            images = []
        # A normal bubble may contain one avatar.  A media bubble normally has
        # another Image control; an otherwise textless bubble is also visual.
        if len(images) >= 2 or (images and not raw):
            return "emoji"
        return None

    @classmethod
    def _reconnected_seen_ids(
        cls,
        previous_signatures: list[tuple[str, str]],
        items: list[Any],
    ) -> set[tuple[Any, ...]]:
        """Acknowledge only the old prefix after a window handle is rebuilt.

        UIA runtime ids change across a rebuild.  Match the longest suffix of
        the old visible sequence inside the new sequence and leave its tail as
        unread.  The earliest equal match is intentional: if a member sends
        the same text twice, the later duplicate must remain a new message.
        """
        current = [cls._content_signature(item) for item in items]
        previous = list(previous_signatures or [])
        for overlap in range(min(len(previous), len(current)), 0, -1):
            suffix = previous[-overlap:]
            for start in range(0, len(current) - overlap + 1):
                if current[start : start + overlap] == suffix:
                    end = start + overlap
                    return {cls._runtime_id(item) for item in items[:end]}
        if previous and current and previous[-1] != current[-1]:
            # The old anchor scrolled out while the triggering message became
            # visible.  Retain just the newest candidate instead of replaying
            # the entire visible history.
            return {cls._runtime_id(item) for item in items[:-1]}
        return {cls._runtime_id(item) for item in items}

    def _visible_items(self, session: dict[str, Any] | None = None) -> list[Any]:
        chat_list = session.get("chat_list") if session is not None else self._chat_list
        if chat_list is None:
            return []
        # Selection mode normally exposes bubbles as CheckBox controls, but
        # WeChat can append later arrivals as ListItem controls while retaining
        # the older CheckBox children.  Treating ListItem as a fallback loses
        # every message after the first successful reply.  Merge both views and
        # deduplicate by UI Automation runtime id instead.
        items = [
            *chat_list.children(control_type="CheckBox"),
            *chat_list.children(control_type="ListItem"),
        ]
        unique: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for item in items:
            runtime_id = self._runtime_id(item)
            if runtime_id in seen:
                continue
            seen.add(runtime_id)
            unique.append(item)
        return unique

    @staticmethod
    def _find_existing_target_window(target: str) -> Any | None:
        """Return an already-open dedicated chat window for ``target``.

        WeChat 4.x occasionally exposes an independent window's UIA title as
        the generic ``Weixin`` even though its Win32 caption still contains the
        contact name.  ``pyweixin`` only performs an exact top-level UIA-title
        lookup, which makes reconnecting fail in that state.  Inspecting the
        visible contact-name text inside each dedicated chat window is stable
        across both title variants.
        """
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            windows = desktop.windows(
                class_name="mmui::ChatSingleWindow",
                top_level_only=True,
            )
        except Exception:
            logger.debug("枚举已有微信独立窗口失败", exc_info=True)
            return None

        for dialog in windows:
            try:
                if str(dialog.window_text() or "").strip() == target:
                    return desktop.window(handle=dialog.handle)
                for control in dialog.descendants(control_type="Text"):
                    if str(control.window_text() or "").strip() != target:
                        continue
                    element_info = getattr(control, "element_info", None)
                    automation_id = str(
                        getattr(element_info, "automation_id", "") or ""
                    ).lower()
                    class_name = str(control.class_name() or "").lower()
                    if (
                        "current_chat_name" in automation_id
                        or "text" in class_name
                        or "label" in class_name
                        or "hbox" in class_name
                    ):
                        return desktop.window(handle=dialog.handle)
            except Exception:
                logger.debug("检查微信独立窗口失败", exc_info=True)
        return None

    def _wait_for_target_window(
        self,
        target: str,
        *,
        attempts: int = 5,
        interval: float = 0.25,
    ) -> Any | None:
        """Wait briefly for WeChat to publish a newly-created UIA window."""
        for attempt in range(max(1, attempts)):
            dialog = self._find_existing_target_window(target)
            if dialog is not None:
                return dialog
            if attempt + 1 < attempts:
                time.sleep(interval)
        return None

    def _current_sidebar_chat_name(self) -> str:
        main_window = self._main_window
        if main_window is None:
            return ""
        try:
            label = main_window.child_window(**self._Texts.CurrentChatNameText)
            if not label.exists(timeout=0.5):
                return ""
            return str(label.window_text() or "").strip()
        except Exception:
            logger.debug("读取当前主窗口会话名失败", exc_info=True)
            return ""

    def _wait_for_sidebar_target(self, target: str) -> bool:
        for attempt in range(8):
            if self._current_sidebar_chat_name() == target:
                return True
            if attempt < 7:
                time.sleep(0.1)
        return False

    @staticmethod
    def _sidebar_item_name(item: Any) -> str:
        try:
            automation_id = str(item.automation_id() or "")
        except Exception:
            automation_id = str(
                getattr(getattr(item, "element_info", None), "automation_id", "")
                or ""
            )
        prefix = "session_item_"
        return automation_id[len(prefix) :] if automation_id.startswith(prefix) else ""

    def _sidebar_rows(self) -> dict[str, Any]:
        """Return exact allowlisted rows while boundedly traversing the sidebar."""
        session_list = self._session_list
        if session_list is None:
            return {}
        found: dict[str, Any] = {}
        seen_pages: set[tuple[str, ...]] = set()
        try:
            session_list.type_keys("{HOME}")
            for _ in range(20):
                items = list(session_list.children(control_type="ListItem") or [])
                page_names = tuple(self._sidebar_item_name(item) for item in items)
                if page_names in seen_pages:
                    break
                seen_pages.add(page_names)
                for item in items:
                    name = self._sidebar_item_name(item)
                    if name in self.targets:
                        found[name] = item
                if len(found) == len(self.targets):
                    break
                session_list.type_keys("{PGDN}")
                time.sleep(0.05)
        finally:
            try:
                session_list.type_keys("{HOME}")
            except Exception:
                logger.debug("恢复会话列表位置失败", exc_info=True)
        return found

    def _click_sidebar_target(self, target: str) -> bool:
        """Find and click an exact target without invalidating an off-screen row."""
        session_list = self._session_list
        if session_list is None:
            return False
        seen_pages: set[tuple[str, ...]] = set()
        session_list.type_keys("{HOME}")
        for _ in range(20):
            items = list(session_list.children(control_type="ListItem") or [])
            page_names = tuple(self._sidebar_item_name(item) for item in items)
            if page_names in seen_pages:
                break
            seen_pages.add(page_names)
            for item in items:
                if self._sidebar_item_name(item) == target:
                    item.click_input()
                    return True
            session_list.type_keys("{PGDN}")
            time.sleep(0.05)
        session_list.type_keys("{HOME}")
        return False

    def _sidebar_changed_targets(self) -> list[str]:
        """Detect unread or preview-changed allowlisted conversations.

        Preview changes cover muted conversations whose sidebar row has no
        numeric unread badge.  The first scan only establishes a baseline.
        """
        # New activity moves conversations into the visible recent list.
        # Reading it must not focus or scroll the sidebar on every idle poll.
        rows = {
            self._sidebar_item_name(item): item
            for item in self._session_list.children(control_type="ListItem")
            if self._sidebar_item_name(item) in self.targets
        }
        raw_signatures = getattr(self, "_sidebar_raw_signatures", {})
        self._sidebar_raw_signatures = raw_signatures
        changed: list[str] = []
        unread_pattern = re.compile(
            r"(?:\n\[(?:\d+)(?:条|則)?\]|\d+\s*(?:条)?(?:新消息|未读)|\d+\s*new messages?)",
            re.IGNORECASE,
        )
        for target in self.targets:
            item = rows.get(target)
            if item is None:
                continue
            raw_signature = str(item.window_text() or "").strip()
            signature = unread_pattern.sub("", raw_signature).strip()
            previous = self._sidebar_row_signatures.get(target)
            self._sidebar_row_signatures[target] = signature
            previous_raw = raw_signatures.get(target)
            raw_signatures[target] = raw_signature
            if (unread_pattern.search(raw_signature) and raw_signature != previous_raw) or (
                previous is not None and signature != previous
            ):
                changed.append(target)
        return changed

    def _bind_sidebar_target(
        self,
        target: str,
        *,
        snapshot_existing: bool,
    ) -> dict[str, Any]:
        main_window = self._main_window
        if main_window is None:
            raise TransportUnavailableError("微信主窗口尚未就绪")
        chat_list = main_window.child_window(**self._Lists.FriendChatList)
        input_edit = main_window.child_window(**self._Edits.CurrentChatEdit)
        if not chat_list.exists(timeout=2) or not input_edit.exists(timeout=1):
            raise TransportUnavailableError(f"未找到“{target}”右侧的消息区或输入框")
        self._Tools.activate_chatList(chat_list)
        items = self._visible_items({"chat_list": chat_list})
        previous = self._sessions.get(target)
        if snapshot_existing or previous is None:
            seen_runtime_ids = {self._runtime_id(item) for item in items}
        else:
            seen_runtime_ids = self._reconnected_seen_ids(
                list(previous.get("visible_item_signatures") or []),
                items,
            )
        session = {
            "dialog": main_window,
            "chat_list": chat_list,
            "input_edit": input_edit,
            "is_group": bool(self._Tools.is_group_chat(main_window)),
            "seen_runtime_ids": seen_runtime_ids,
            "last_item_signature": self._item_signature(items[-1]) if items else None,
            "visible_item_signatures": [self._content_signature(item) for item in items],
        }
        if previous and previous.get("generated_image_echo_until"):
            session["generated_image_echo_until"] = previous[
                "generated_image_echo_until"
            ]
        self._sessions[target] = session
        self._active_sidebar_target = target
        if target == self.target:
            self._dialog_window = main_window
            self._chat_list = chat_list
            self._input_edit = input_edit
            self._seen_runtime_ids = seen_runtime_ids
            self._last_item_signature = session["last_item_signature"]
        return session

    def _activate_sidebar_target(
        self,
        target: str,
        *,
        snapshot_existing: bool = False,
        allow_search: bool = False,
    ) -> dict[str, Any]:
        """Select and verify one exact sidebar conversation before UI access."""
        main_window = self._main_window
        if main_window is None:
            raise TransportUnavailableError("微信主窗口尚未就绪")
        current_name = self._current_sidebar_chat_name()
        existing = getattr(self, "_sessions", {}).get(target)
        if (
            current_name == target
            and getattr(self, "_active_sidebar_target", "") == target
            and existing is not None
            and not snapshot_existing
        ):
            # Preserve unread runtime IDs and avoid moving the mouse to the
            # scrollbar merely to check an already selected conversation.
            return existing
        main_window.restore()
        if current_name != target:
            if self._click_sidebar_target(target):
                pass
            elif allow_search:
                main_window = self._Navigator.open_dialog_window(
                    friend=target,
                    select=False,
                    is_maximize=False,
                    search_pages=0,
                )
                self._main_window = main_window
                self._session_list = main_window.child_window(
                    **self._MainWindow.SessionList
                )
            else:
                raise TransportUnavailableError(
                    f"侧边栏未找到白名单会话“{target}”"
                )
        if not self._wait_for_sidebar_target(target):
            raise TransportUnavailableError(
                f"会话切换校验失败：右侧当前不是“{target}”，已禁止读写"
            )
        return self._bind_sidebar_target(
            target,
            snapshot_existing=snapshot_existing,
        )

    def _resolve_sidebar_visual_capture(
        self,
        target: str,
        runtime_id: tuple[Any, ...],
        content_signature: tuple[str, str],
    ) -> tuple[Any, Any]:
        """Reactivate the source chat before capturing a deferred media frame."""
        session = self._activate_sidebar_target(target)
        items = self._visible_items(session)
        for item in reversed(items):
            if self._runtime_id(item) == runtime_id:
                return session["dialog"], item
        for item in reversed(items):
            if self._content_signature(item) == content_signature:
                return session["dialog"], item
        raise RuntimeError("原图片或表情包已不在当前可见区域")

    def _open_sidebar_sessions(self) -> None:
        try:
            self._main_window = self._Navigator.open_weixin(is_maximize=False)
            self._session_list = self._main_window.child_window(
                **self._MainWindow.SessionList
            )
            if not self._session_list.exists(timeout=2):
                raise TransportUnavailableError("未找到微信主窗口左侧会话列表")
            for target in self.targets:
                self._activate_sidebar_target(
                    target,
                    snapshot_existing=True,
                    allow_search=True,
                )
            # Establish muted-chat preview baselines after startup snapshots.
            self._sidebar_changed_targets()
        except TransportUnavailableError:
            raise
        except Exception as exc:
            raise TransportUnavailableError(
                f"无法初始化微信主窗口侧边栏监听：{exc}"
            ) from exc

    def _open_target_window(
        self,
        target: str | None = None,
        *,
        snapshot_existing: bool = True,
    ) -> None:
        target = str(target or self.target).strip()
        try:
            dialog = self._find_existing_target_window(target)
            if dialog is None:
                navigator_error: Exception | None = None
                try:
                    dialog = self._Navigator.open_seperate_dialog_window(
                        friend=target,
                        # Keep the window visible until its controls have been
                        # bound.  WeChat 4.x can delay publishing the final UIA
                        # title, while pyweixin immediately performs an exact
                        # lookup when window_minimize=True.
                        select=False,
                        is_maximize=False,
                        window_minimize=False,
                        close_weixin=False,
                    )
                except Exception as exc:  # noqa: BLE001 - pywinauto exception family
                    navigator_error = exc
                    logger.warning(
                        "pyweixin 打开联系人“%s”后未立即返回窗口，正在重新发现",
                        target,
                    )
                if dialog is None:
                    dialog = self._wait_for_target_window(target)
                if dialog is None and navigator_error is not None:
                    raise navigator_error
                if dialog is None:
                    raise TransportUnavailableError(
                        f"未找到联系人“{target}”的独立聊天窗口"
                    )
            is_group = bool(self._Tools.is_group_chat(dialog))
            session = {
                "dialog": dialog,
                "chat_list": dialog.child_window(**self._Lists.FriendChatList),
                "input_edit": dialog.child_window(**self._Edits.InputEdit),
                "is_group": is_group,
                "seen_runtime_ids": set(),
                "last_item_signature": None,
            }
            if not session["chat_list"].exists(timeout=2):
                raise TransportUnavailableError(f"未找到联系人“{target}”的聊天消息区域")
            # WeChat virtualizes bubbles outside the visible viewport.  A
            # detached chat window that was never activated can keep polling
            # successfully while remaining parked on an old viewport, so new
            # images/stickers never appear in ``children()``.  pyweixin's own
            # listener anchors the list to the bottom before taking its initial
            # runtime-id snapshot; mirror that required setup here.
            self._Tools.activate_chatList(session["chat_list"])
            visible = self._visible_items(session)
            session["seen_runtime_ids"] = (
                {self._runtime_id(item) for item in visible}
                if snapshot_existing
                else set()
            )
            session["last_item_signature"] = (
                self._item_signature(visible[-1]) if visible else None
            )
            session["visible_item_signatures"] = [
                self._content_signature(item) for item in visible
            ]
            try:
                dialog.minimize()
            except Exception:
                logger.debug("最小化已有监听窗口失败", exc_info=True)
            self._sessions[target] = session
            if target == self.target:
                self._dialog_window = session["dialog"]
                self._chat_list = session["chat_list"]
                self._input_edit = session["input_edit"]
                self._seen_runtime_ids = session["seen_runtime_ids"]
                self._last_item_signature = session["last_item_signature"]
        except TransportUnavailableError:
            raise
        except Exception as exc:
            raise TransportUnavailableError(
                f"无法打开白名单联系人“{target}”的独立聊天窗口：{exc}"
            ) from exc

    def _refresh_session_controls(
        self,
        target: str,
        session: dict[str, Any],
    ) -> list[Any] | None:
        """Rebind controls after WeChat rebuilds the message-list UIA node."""
        dialog = session.get("dialog")
        if dialog is None:
            return None
        try:
            chat_list = dialog.child_window(**self._Lists.FriendChatList)
            if not chat_list.exists(timeout=1):
                return None
            session["chat_list"] = chat_list
            session["input_edit"] = dialog.child_window(**self._Edits.InputEdit)
            self._Tools.activate_chatList(chat_list)
            items = self._visible_items(session)
            logger.info("已重新绑定联系人“%s”的消息控件", target)
            return items
        except Exception:
            logger.debug("重新绑定联系人消息控件失败", exc_info=True)
            return None

    def close(self) -> None:
        self._closed = True
        sessions = dict(getattr(self, "_sessions", {}) or {})
        dialog = self._dialog_window
        self._dialog_window = None
        self._chat_list = None
        self._input_edit = None
        dialogs = [item.get("dialog") for item in sessions.values()]
        if not dialogs and dialog is not None:
            dialogs = [dialog]
        if getattr(self, "window_mode", "independent") == "independent":
            seen_handles: set[Any] = set()
            for dialog in dialogs:
                if dialog is None:
                    continue
                handle = getattr(dialog, "handle", id(dialog))
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
                try:
                    dialog.close()
                except Exception:
                    logger.debug("关闭 pyweixin 白名单窗口失败", exc_info=True)
        self._sessions = {}
        self._main_window = None
        self._session_list = None

    def get_transport_status(self) -> dict[str, Any]:
        mode = "自动发送已启用" if self.send_enabled else "安全测试：仅生成草稿"
        sessions = dict(getattr(self, "_sessions", {}) or {})
        group_count = sum(bool(item.get("is_group")) for item in sessions.values())
        private_count = max(0, len(self.targets) - group_count)
        return {
            "transport_backend": self.backend_name,
            "silent_mode": False,
            "wechat_version": self.wechat_version,
            "required_wechat_version": f"{MINIMUM_WEIXIN_VERSION}+",
            "supports_native_quote": False,
            "supports_voice_transcription": False,
            "transport_status": "connected" if not self._closed else "disconnected",
            "transport_warning": (
                f"界面自动化（{'主窗口侧边栏' if self.window_mode == 'sidebar' else '独立窗口'}）；"
                f"监听 {private_count} 个白名单私聊、"
                f"{group_count} 个白名单群聊；{mode}"
            ),
        }

    def _legacy_session(self) -> dict[str, Any]:
        return {
            "dialog": getattr(self, "_dialog_window", None),
            "chat_list": getattr(self, "_chat_list", None),
            "input_edit": getattr(self, "_input_edit", None),
            "is_group": False,
            "seen_runtime_ids": getattr(self, "_seen_runtime_ids", set()),
            "last_item_signature": getattr(self, "_last_item_signature", None),
        }

    @staticmethod
    def _extract_group_text(item: Any) -> tuple[str, str] | None:
        """Extract a group sender and body without guessing ambiguous bubbles.

        Weixin 4.x normally exposes the sender and body as separate Text UIA
        descendants.  Some builds flatten them into ``sender:\nbody`` or
        ``sender\nbody``.  A flattened single-line value has no reliable
        boundary when nicknames contain spaces, so it is intentionally skipped.
        """
        texts: list[str] = []
        try:
            controls = item.descendants(control_type="Text")
        except Exception:
            controls = []
        for control in controls:
            value = str(control.window_text() or "").strip()
            if value and (not texts or value != texts[-1]):
                texts.append(value)
        if len(texts) >= 2:
            sender = texts[0]
            content = "\n".join(texts[1:]).strip()
            if sender and content:
                return sender, content

        raw = str(item.window_text() or "").strip()
        for separator in (":\n", "：\n", "\n"):
            if separator not in raw:
                continue
            sender, content = raw.split(separator, 1)
            sender = sender.strip()
            content = content.strip()
            if sender and content:
                return sender, content
        return None

    def _poll_target(self, target: str, session: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            items = self._visible_items(session)
        except Exception:  # noqa: BLE001 - pywinauto exposes multiple backend exceptions
            previous_signatures = list(session.get("visible_item_signatures") or [])
            logger.warning("pyweixin 消息控件失效，正在重新绑定联系人“%s”", target)
            items = self._refresh_session_controls(target, session)
            if items is None:
                logger.warning("pyweixin 窗口失效，正在重新打开联系人“%s”", target)
                # Do not close the previous specification here.  A transient
                # UIA failure does not mean the actual window is gone.
                if getattr(self, "window_mode", "independent") == "sidebar":
                    self._activate_sidebar_target(target)
                else:
                    self._open_target_window(target, snapshot_existing=False)
                session = self._sessions[target]
                items = self._visible_items(session)
            session["seen_runtime_ids"] = self._reconnected_seen_ids(
                previous_signatures,
                items,
            )

        events: list[dict[str, Any]] = []
        current_ids = {self._runtime_id(item) for item in items}
        if (
            getattr(self, "window_mode", "independent") == "sidebar"
            and items
            and [self._content_signature(item) for item in items]
            == session.get("visible_item_signatures")
            and not current_ids.intersection(session.get("seen_runtime_ids", set()))
        ):
            # Minimize/restore can rebuild every UIA ID without adding a
            # message. Rebase the snapshot instead of replying to history.
            session["seen_runtime_ids"] = current_ids
            session["last_item_signature"] = self._item_signature(items[-1])
            return []
        seen_runtime_ids = session.setdefault("seen_runtime_ids", set())
        new_items = [
            item for item in items if self._runtime_id(item) not in seen_runtime_ids
        ]
        current_signatures = [self._content_signature(item) for item in items]
        latest_signature = self._item_signature(items[-1]) if items else None
        previous_signature = session.get("last_item_signature")
        if (
            items
            and latest_signature != previous_signature
            and all(candidate is not items[-1] for candidate in new_items)
        ):
            # Qt may recycle the latest row's UIA runtime id.  Reconsider it
            # only when its own signature changes; merely scrolling or sending
            # a reply must not replay the preceding sticker.
            new_items.append(items[-1])
        session["last_item_signature"] = latest_signature

        is_group = bool(session.get("is_group", False))
        prefix = re.compile(rf"^{re.escape(target)}\s+")
        for item in new_items:
            runtime_id = self._runtime_id(item)
            visual_type = self._visual_message_type(item)
            if visual_type is None and str(item.class_name() or "") not in {
                "mmui::ChatBubbleItemView",
                "mmui::ChatTextItemView",
                "mmui::ChatItemView",
            }:
                seen_runtime_ids.add(runtime_id)
                continue
            if visual_type is not None and is_group:
                # UIA frequently rebuilds an old sticker row after our text
                # reply, giving it a fresh runtime id.  If an outgoing bubble
                # already follows this visual row, it predates a response and
                # must not be treated as a newly received sticker.
                dialog = session.get("dialog")
                input_edit = session.get("input_edit")
                item_index = next(
                    (index for index, candidate in enumerate(items) if candidate is item),
                    -1,
                )
                if dialog is not None and input_edit is not None and item_index >= 0:
                    try:
                        dialog.restore()
                        if any(
                            self._Tools.is_my_bubble(dialog, later, input_edit)
                            for later in items[item_index + 1 :]
                        ):
                            seen_runtime_ids.add(runtime_id)
                            continue
                    except Exception:
                        logger.debug("检查表情后续回复失败", exc_info=True)
                    finally:
                        if getattr(self, "window_mode", "independent") == "independent":
                            try:
                                dialog.minimize()
                            except Exception:
                                logger.debug("重新最小化监听窗口失败", exc_info=True)
            raw = str(item.window_text() or "").strip()
            if is_group or not prefix.search(raw):
                dialog = session.get("dialog")
                input_edit = session.get("input_edit")
                if dialog is None or input_edit is None:
                    # The session may be repaired on the next poll.  Do not
                    # acknowledge this bubble until its direction is known.
                    continue
                try:
                    dialog.restore()
                    if self._Tools.is_my_bubble(dialog, item, input_edit):
                        if time.monotonic() < session.get("generated_image_echo_until", 0):
                            seen_runtime_ids.add(runtime_id)
                            continue
                        allow_own_visual = bool(
                            visual_type is not None
                            and is_group
                            and getattr(self, "bot_cfg", {}).get(
                                "pyweixin_reply_to_own_group_visual",
                                False,
                            )
                        )
                        if not allow_own_visual:
                            seen_runtime_ids.add(runtime_id)
                            continue
                except Exception:
                    logger.warning("无法判断新气泡方向，将在下一轮重试", exc_info=True)
                    continue
                finally:
                    if getattr(self, "window_mode", "independent") == "independent":
                        try:
                            dialog.minimize()
                        except Exception:
                            logger.debug("重新最小化监听窗口失败", exc_info=True)

            sender = target
            if is_group:
                parsed = self._extract_group_text(item)
                if parsed is not None:
                    sender, content = parsed
                else:
                    # WeChat 4.1.13 often exposes only the body in normal chat
                    # mode.  Switching to multi-select reveals the nickname but
                    # also destroys the independent window's UIA handle, which
                    # stops all subsequent polling.  Exact sender identity is
                    # not required for the user's "reply to every group text"
                    # policy, so keep the stable normal view and use a neutral
                    # label when the nickname is unavailable.
                    sender = "群成员"
                    content = raw
            elif prefix.search(raw):
                content = prefix.sub("", raw, count=1).strip()
            else:
                content = raw
            if visual_type is not None:
                content = "[表情包]" if visual_type == "emoji" else "[图片]"
            if (not content and visual_type is None) or _BLOCKED_CONTENT.search(content):
                seen_runtime_ids.add(runtime_id)
                continue
            message: Any
            if visual_type is not None:
                message = PyWeixinVisualMessage(
                    item,
                    dialog=session.get("dialog"),
                    lock=self._lock,
                    sender=sender,
                    media_type=visual_type,
                    is_group=is_group,
                    minimize_after=(
                        getattr(self, "window_mode", "independent") == "independent"
                    ),
                    capture_resolver=(
                        partial(
                            self._resolve_sidebar_visual_capture,
                            target,
                            runtime_id,
                            self._content_signature(item),
                        )
                        if getattr(self, "window_mode", "independent") == "sidebar"
                        else None
                    ),
                )
            else:
                message = {
                    "content": content,
                    "sender": sender,
                    "type": "text",
                    "is_group": is_group,
                    "is_self": False,
                }
            events.append(
                {
                    "chat_name": target,
                    "chat_type": "group" if is_group else "friend",
                    "msg": [message],
                }
            )
            seen_runtime_ids.add(runtime_id)
        if len(seen_runtime_ids) > 5000:
            session["seen_runtime_ids"] = {
                runtime_id
                for runtime_id in current_ids
                if runtime_id in seen_runtime_ids
            }
        session["visible_item_signatures"] = current_signatures
        return events

    def poll_new_messages(self, filter_mute: bool = False) -> Any:
        del filter_mute
        with self._lock:
            if self._closed:
                return []
            if getattr(self, "window_mode", "independent") == "sidebar":
                events = []
                try:
                    targets = self._sidebar_changed_targets()
                except Exception:
                    logger.warning("扫描微信主窗口侧边栏失败", exc_info=True)
                    # An inaccessible/minimized UI is not evidence of new
                    # messages. Never restore a window as a polling fallback.
                    return []
                for target in targets:
                    if target not in self.targets:
                        continue
                    try:
                        session = self._activate_sidebar_target(target)
                        events.extend(self._poll_target(target, session))
                    except Exception:
                        logger.warning(
                            "跳过无法安全激活的白名单会话“%s”",
                            target,
                            exc_info=True,
                        )
                return events
            sessions = dict(getattr(self, "_sessions", {}) or {})
            legacy_mode = not sessions
            if legacy_mode:
                sessions = {self.target: self._legacy_session()}
            events = []
            for target, session in sessions.items():
                events.extend(self._poll_target(target, session))
            if legacy_mode:
                primary = sessions[self.target]
                self._seen_runtime_ids = primary["seen_runtime_ids"]
                self._last_item_signature = primary["last_item_signature"]
            return events

    def send_text(
        self,
        msg: str,
        who: str | None = None,
        clear: bool = True,
        at: Any | None = None,
        exact: bool = True,
    ) -> dict[str, Any]:
        del at, exact
        target = str(who or "").strip()
        allowed_targets = set(getattr(self, "targets", []) or [self.target])
        if target not in allowed_targets:
            return {"success": False, "message": "目标不在 pyweixin 白名单中"}
        if not self.send_enabled:
            return {"success": False, "message": "当前为仅生成草稿模式，尚未启用自动发送"}
        text = str(msg or "").strip()
        from backend.utils.message import refine_reply_text

        text = refine_reply_text(text)
        if not text:
            return {"success": False, "message": "不能发送空消息"}
        try:
            import pyautogui

            with self._lock:
                if getattr(self, "window_mode", "independent") == "sidebar":
                    session = self._activate_sidebar_target(target)
                else:
                    session = (getattr(self, "_sessions", {}) or {}).get(target)
                dialog = session.get("dialog") if session else self._dialog_window
                input_edit = session.get("input_edit") if session else self._input_edit
                if dialog is None or input_edit is None:
                    return {"success": False, "message": "目标会话尚未就绪"}
                dialog.restore()
                if not input_edit.exists(timeout=1):
                    return {"success": False, "message": "目标会话的输入框不可用"}
                if clear:
                    input_edit.set_text("")
                input_edit.click_input()
                input_edit.set_text(text)
                time.sleep(0.2)
                actual = str(input_edit.window_text() or "").replace("\r\n", "\n").strip()
                if actual != text.replace("\r\n", "\n").strip():
                    return {
                        "success": False,
                        "message": "输入框内容与待发送回复不一致，未发送，请检查草稿",
                    }
                pyautogui.hotkey("alt", "s", _pause=False)
                time.sleep(0.25)
                if str(input_edit.window_text() or "").strip():
                    return {"success": False, "message": "发送后输入框未清空，已判定为发送失败"}
                # Sending changes the Qt chat viewport and can rebuild UIA row
                # ids.  Snapshot that resulting viewport so the message which
                # triggered this reply is not emitted again on the next poll.
                if session is not None:
                    try:
                        items = self._visible_items(session)
                        session["seen_runtime_ids"].update(
                            self._runtime_id(item) for item in items
                        )
                        session["last_item_signature"] = (
                            self._item_signature(items[-1]) if items else None
                        )
                        session["visible_item_signatures"] = [
                            self._content_signature(item) for item in items
                        ]
                    except Exception:
                        logger.debug("发送后刷新消息快照失败", exc_info=True)
            return {"success": True, "message": "已通过微信 4.x 界面发送"}
        except Exception as exc:  # noqa: BLE001 - normalize third-party RPA failures
            return {"success": False, "message": f"pyweixin 发送失败：{exc}"}
        finally:
            if (
                getattr(self, "window_mode", "independent") == "independent"
                and "dialog" in locals()
                and dialog is not None
            ):
                try:
                    dialog.minimize()
                except Exception:
                    logger.debug("重新最小化监听窗口失败", exc_info=True)

    supports_generated_images = True

    def send_image(self, filepath: str, who: str | None = None, exact: bool = True) -> dict[str, Any]:
        """Paste a validated generated bitmap into its dedicated allowlisted window."""
        from backend.core.image_generation import validate_generated_image
        from backend.transports.image_clipboard import pasted_bitmap

        target = str(who or "").strip()
        if target not in set(getattr(self, "targets", [])) or not self.send_enabled:
            return {"success": False, "message": "目标不在白名单或自动发送未开启"}
        try:
            path = validate_generated_image(filepath)
            import pyautogui

            with self._lock:
                if getattr(self, "window_mode", "independent") == "sidebar":
                    session = self._activate_sidebar_target(target)
                else:
                    session = getattr(self, "_sessions", {}).get(target)
                if not session or session.get("dialog") is None or session.get("input_edit") is None:
                    return {"success": False, "message": "目标会话尚未就绪"}
                dialog, edit = session["dialog"], session["input_edit"]
                dialog.restore()
                if not edit.exists(timeout=1) or str(edit.window_text() or "").strip():
                    return {"success": False, "message": "输入框不可用或有未发送草稿，已停止"}
                before = {self._runtime_id(item) for item in self._visible_items(session)}
                edit.click_input()
                if not edit.has_keyboard_focus():
                    return {"success": False, "message": "未获得目标输入框焦点"}
                # Even an ambiguous send must not trigger recognition of our own image.
                session["generated_image_echo_until"] = time.monotonic() + 30
                with pasted_bitmap(path):
                    pyautogui.hotkey("ctrl", "v", _pause=False)
                    time.sleep(0.8)
                    if not edit.has_keyboard_focus():
                        return {"success": False, "message": "粘贴后焦点改变，未执行发送"}
                    pyautogui.hotkey("alt", "s", _pause=False)
                    for _ in range(12):
                        time.sleep(0.25)
                        items = self._visible_items(session)
                        sent = [item for item in items if self._runtime_id(item) not in before
                                and self._visual_message_type(item) is not None
                                and self._Tools.is_my_bubble(dialog, item, edit)]
                        if sent:
                            session["seen_runtime_ids"].update(self._runtime_id(item) for item in sent)
                            return {"success": True, "message": "已观察到新发出的图片气泡"}
                return {"success": False, "message": "未确认新图片气泡，未自动重发"}
        except Exception:  # Do not expose clipboard contents or local paths.
            logger.warning("生成图片发送失败，未自动重发")
            return {"success": False, "message": "图片发送失败，未自动重发"}
        finally:
            if (
                getattr(self, "window_mode", "independent") == "independent"
                and "dialog" in locals()
                and dialog is not None
            ):
                try:
                    dialog.minimize()
                except Exception:
                    pass

    def send_files(
        self,
        filepath: str,
        who: str | None = None,
        exact: bool = True,
    ) -> dict[str, Any]:
        del filepath, who, exact
        return {"success": False, "message": "安全测试模式禁止自动发送文件"}
