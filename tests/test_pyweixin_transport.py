from __future__ import annotations

import threading
import sys
from types import SimpleNamespace

from PIL import Image

import backend.transports.pyweixin_adapter as pyweixin_adapter
from backend.config_schemas import BotConfig
from backend.handlers.converters import normalize_new_messages
from backend.handlers.filter import should_reply_with_reason
from backend.transports.pyweixin_adapter import PyWeixinTransport
from backend.types import MessageEvent


class _ElementInfo:
    def __init__(self, runtime_id):
        self.runtime_id = runtime_id


class _Item:
    def __init__(
        self,
        runtime_id,
        text,
        class_name="mmui::ChatBubbleItemView",
        descendant_texts=None,
        descendant_images=None,
        capture_color=None,
        capture_size=(240, 180),
    ):
        self.element_info = _ElementInfo(runtime_id)
        self._text = text
        self._class_name = class_name
        self._descendant_texts = descendant_texts or []
        self._descendant_images = descendant_images or []
        self._capture_color = capture_color
        self._capture_size = capture_size

    def window_text(self):
        return self._text

    def class_name(self):
        return self._class_name

    def descendants(self, **kwargs):
        if kwargs == {"control_type": "Text"}:
            return [SimpleNamespace(window_text=lambda value=value: value) for value in self._descendant_texts]
        if kwargs == {"control_type": "Image"}:
            return self._descendant_images
        raise AssertionError(kwargs)

    def capture_as_image(self):
        if self._capture_color is None:
            raise RuntimeError("capture not configured")
        return Image.new("RGB", self._capture_size, self._capture_color)


class _VisualControl:
    def __init__(self, width, height, color):
        self.width = width
        self.height = height
        self.color = color

    def rectangle(self):
        return SimpleNamespace(width=lambda: self.width, height=lambda: self.height)

    def capture_as_image(self):
        return Image.new("RGB", (self.width, self.height), self.color)


class _ChatList:
    def __init__(self, items, *, checkbox_items=True, list_items=None):
        self.items = items
        self.checkbox_items = checkbox_items
        self.list_items = items if list_items is None else list_items

    def children(self, **kwargs):
        control_type = kwargs.get("control_type")
        assert control_type in {"ListItem", "CheckBox"}
        if control_type == "CheckBox":
            return self.items if self.checkbox_items else []
        return self.list_items

    def exists(self, timeout=0):
        del timeout
        return True


class _Dialog:
    def __init__(self):
        self.restored = 0
        self.minimized = 0

    def restore(self):
        self.restored += 1

    def minimize(self):
        self.minimized += 1

    def close(self):
        return None


class _TextControl:
    def __init__(self, text, class_name="mmui::XTextView", automation_id=""):
        self._text = text
        self._class_name = class_name
        self.element_info = SimpleNamespace(automation_id=automation_id)

    def window_text(self):
        return self._text

    def class_name(self):
        return self._class_name


class _ExistingDialog(_Dialog):
    def __init__(self, title, texts, handle):
        super().__init__()
        self._title = title
        self._texts = texts
        self.handle = handle

    def window_text(self):
        return self._title

    def descendants(self, **kwargs):
        assert kwargs == {"control_type": "Text"}
        return self._texts


class _Tools:
    def __init__(self, *, outgoing):
        self.outgoing = outgoing

    def is_my_bubble(self, dialog, item, input_edit):
        del dialog, input_edit
        return item is self.outgoing

    def activate_chatList(self, chat_list):
        chat_list.activated = True


class _SendEdit:
    def __init__(self):
        self.value = ""

    def exists(self, timeout=0):
        del timeout
        return True

    def set_text(self, value):
        self.value = value

    def click_input(self):
        return None

    def window_text(self):
        return self.value


class _SidebarItem:
    def __init__(self, name, text, on_click=None):
        self.name = name
        self.text = text
        self.on_click = on_click

    def automation_id(self):
        return f"session_item_{self.name}"

    def window_text(self):
        return self.text

    def click_input(self):
        if self.on_click:
            self.on_click(self.name)


class _SessionList:
    def __init__(self, items):
        self.items = items
        self.keys = []

    def children(self, **kwargs):
        assert kwargs == {"control_type": "ListItem"}
        return self.items

    def type_keys(self, keys):
        self.keys.append(keys)

    def exists(self, timeout=0):
        del timeout
        return True


class _CurrentChatLabel:
    def __init__(self, state):
        self.state = state

    def exists(self, timeout=0):
        del timeout
        return True

    def window_text(self):
        return self.state["current"]


class _MainWindow(_Dialog):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def child_window(self, **kwargs):
        assert kwargs == {"kind": "current-label"}
        return _CurrentChatLabel(self.state)


def _event(chat_name: str) -> MessageEvent:
    return MessageEvent(
        chat_name=chat_name,
        sender=chat_name,
        content="你好",
        is_group=False,
        is_at_me=False,
        msg_type="text",
        is_self=False,
        chat_type="friend",
    )


def test_bot_config_preserves_pyweixin_transport_fields():
    config = BotConfig(
        transport_backend="pyweixin",
        pyweixin_test_contact="测试联系人",
        pyweixin_send_enabled=False,
        pyweixin_window_mode="sidebar",
    )
    assert config.transport_backend == "pyweixin"
    assert config.pyweixin_test_contact == "测试联系人"
    assert config.pyweixin_send_enabled is False
    assert config.pyweixin_window_mode == "sidebar"


def test_private_chat_must_match_whitelist():
    config = {"bot": {"whitelist_enabled": True, "whitelist": ["测试联系人"]}}
    assert should_reply_with_reason(_event("测试联系人"), config) == (True, "ok")
    assert should_reply_with_reason(_event("其他联系人"), config) == (
        False,
        "chat_not_in_whitelist",
    )


def test_pyweixin_resolves_multiple_deduplicated_whitelist_targets():
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.bot_cfg = {
        "whitelist_enabled": True,
        "whitelist": ["小王", "测试联系人", "小王"],
        "pyweixin_test_contact": "测试联系人",
        "pyweixin_max_contacts": 8,
    }

    assert transport._resolve_targets() == ["测试联系人", "小王"]


def test_sidebar_scan_detects_unread_and_muted_preview_changes():
    unread = _SidebarItem("项目群", "项目群\n[2条]\n新消息")
    muted = _SidebarItem("闲聊群", "闲聊群\n消息免打扰\n旧内容")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.targets = ["项目群", "闲聊群"]
    transport._session_list = _SessionList([unread, muted])
    transport._sidebar_row_signatures = {
        "项目群": "项目群\n旧内容",
        "闲聊群": "闲聊群\n消息免打扰\n上一条",
    }

    assert transport._sidebar_changed_targets() == ["项目群", "闲聊群"]
    assert transport._session_list.keys == []


def test_sidebar_idle_activation_preserves_session_without_mouse_actions():
    state = {"current": "项目群"}
    main_window = _MainWindow(state)
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport._main_window = main_window
    transport._Texts = SimpleNamespace(CurrentChatNameText={"kind": "current-label"})
    transport._active_sidebar_target = "项目群"
    session = {"seen_runtime_ids": {(1,)}, "last_item_signature": None}
    transport._sessions = {"项目群": session}

    for _ in range(3):
        assert transport._activate_sidebar_target("项目群") is session
    assert main_window.restored == 0
    assert main_window.minimized == 0
    assert session["seen_runtime_ids"] == {(1,)}


def test_sidebar_activation_clicks_exact_row_and_verifies_right_pane(monkeypatch):
    state = {"current": "其他群"}
    row = _SidebarItem("项目群", "项目群\n[1条]\n你好", lambda name: state.update(current=name))
    main_window = _MainWindow(state)
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.targets = ["项目群"]
    transport._main_window = main_window
    transport._session_list = _SessionList([row])
    transport._Texts = SimpleNamespace(CurrentChatNameText={"kind": "current-label"})
    expected = {"target": "项目群"}
    monkeypatch.setattr(
        transport,
        "_bind_sidebar_target",
        lambda target, snapshot_existing: expected,
    )

    result = transport._activate_sidebar_target("项目群")

    assert result is expected
    assert state["current"] == "项目群"
    assert main_window.restored == 1


def test_sidebar_activation_blocks_when_right_pane_does_not_match(monkeypatch):
    state = {"current": "其他群"}
    row = _SidebarItem("项目群", "项目群\n[1条]\n你好")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.targets = ["项目群"]
    transport._main_window = _MainWindow(state)
    transport._session_list = _SessionList([row])
    transport._Texts = SimpleNamespace(CurrentChatNameText={"kind": "current-label"})

    try:
        transport._activate_sidebar_target("项目群")
    except pyweixin_adapter.TransportUnavailableError as exc:
        assert "已禁止读写" in str(exc)
    else:
        raise AssertionError("会话名不匹配时必须禁止读写")


def test_sidebar_poll_does_not_activate_current_chat_without_new_activity(monkeypatch):
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.window_mode = "sidebar"
    transport.target = "项目群"
    transport.targets = ["项目群"]
    transport._active_sidebar_target = "项目群"
    transport._closed = False
    transport._lock = threading.RLock()
    session = {"target": "项目群"}
    monkeypatch.setattr(transport, "_sidebar_changed_targets", lambda: [])
    monkeypatch.setattr(transport, "_activate_sidebar_target", lambda target: session)
    monkeypatch.setattr(
        transport,
        "_poll_target",
        lambda target, active_session: [target]
        if active_session is session
        else [],
    )

    assert transport.poll_new_messages() == []


def test_find_existing_target_window_accepts_generic_uia_title(monkeypatch):
    generic = _ExistingDialog(
        "Weixin",
        [
            _TextControl(
                "测试联系人",
                "mmui::XStaticLabel",
                automation_id="current_chat_name_label",
            ),
            _TextControl("发送", "mmui::XOutlineButton"),
        ],
        101,
    )
    other = _ExistingDialog("小王", [_TextControl("小王")], 202)
    specifications = {101: object(), 202: object()}

    class _Desktop:
        def windows(self, **kwargs):
            assert kwargs == {
                "class_name": "mmui::ChatSingleWindow",
                "top_level_only": True,
            }
            return [generic, other]

        def window(self, *, handle):
            return specifications[handle]

    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(Desktop=lambda **kwargs: _Desktop()),
    )

    assert PyWeixinTransport._find_existing_target_window("测试联系人") is specifications[101]
    assert PyWeixinTransport._find_existing_target_window("小王") is specifications[202]


def test_sidebar_rebuilt_identical_history_does_not_reply():
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.window_mode = "sidebar"
    old = _Item([1], "旧消息")
    rebuilt = _Item([2], "旧消息")
    session = {
        "chat_list": _ChatList([rebuilt]),
        "seen_runtime_ids": {(1,)},
        "visible_item_signatures": [transport._content_signature(old)],
    }
    assert transport._poll_target("项目群", session) == []
    assert session["seen_runtime_ids"] == {(2,)}


def test_sidebar_scan_failure_does_not_activate_window(monkeypatch):
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.window_mode = "sidebar"
    transport._closed = False
    transport._lock = threading.RLock()

    def unavailable():
        raise RuntimeError("UI unavailable")

    def unexpected_activation(target):
        raise AssertionError("must not activate without activity")

    monkeypatch.setattr(transport, "_sidebar_changed_targets", unavailable)
    monkeypatch.setattr(transport, "_activate_sidebar_target", unexpected_activation)
    assert transport.poll_new_messages() == []


def test_open_target_window_recovers_when_navigator_exact_lookup_fails(monkeypatch):
    chat_list = _ChatList([_Item([1], "已有消息")])
    input_edit = _SendEdit()

    class _RecoveredDialog(_Dialog):
        def child_window(self, **kwargs):
            return chat_list if kwargs == {"kind": "list"} else input_edit

    class _Navigator:
        @staticmethod
        def open_seperate_dialog_window(**kwargs):
            assert kwargs["friend"] == "测试群聊"
            assert kwargs["window_minimize"] is False
            raise RuntimeError("exact UIA title was not published yet")

    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试群聊"
    transport._Navigator = _Navigator
    activated = []
    transport._Tools = SimpleNamespace(
        is_group_chat=lambda dialog: True,
        activate_chatList=lambda chat_list: activated.append(chat_list),
    )
    transport._Lists = SimpleNamespace(FriendChatList={"kind": "list"})
    transport._Edits = SimpleNamespace(InputEdit={"kind": "edit"})
    transport._sessions = {}
    transport._dialog_window = None
    transport._chat_list = None
    transport._input_edit = None
    recovered = _RecoveredDialog()
    discoveries = iter([None, None, recovered])
    monkeypatch.setattr(
        transport,
        "_find_existing_target_window",
        lambda target: next(discoveries),
    )
    monkeypatch.setattr(pyweixin_adapter.time, "sleep", lambda _: None)

    transport._open_target_window("测试群聊")

    assert transport._sessions["测试群聊"]["dialog"] is recovered
    assert transport._sessions["测试群聊"]["is_group"] is True
    assert activated == [chat_list]
    assert recovered.minimized == 1


def test_reconnect_keeps_new_tail_unread_when_runtime_ids_change():
    previous = [
        ("mmui::ChatBubbleItemView", "第一条"),
        ("mmui::ChatBubbleItemView", "第二条"),
    ]
    rebuilt = [
        _Item([101], "第一条"),
        _Item([102], "第二条"),
        _Item([103], "触发重连的新消息"),
    ]

    seen = PyWeixinTransport._reconnected_seen_ids(previous, rebuilt)

    assert seen == {(101,), (102,)}


def test_reconnect_does_not_swallow_duplicate_new_tail():
    previous = [
        ("mmui::ChatBubbleItemView", "上一条"),
        ("mmui::ChatBubbleItemView", "重复内容"),
    ]
    rebuilt = [
        _Item([111], "上一条"),
        _Item([112], "重复内容"),
        _Item([113], "重复内容"),
    ]

    seen = PyWeixinTransport._reconnected_seen_ids(previous, rebuilt)

    assert seen == {(111,), (112,)}


def test_poll_rebinds_rebuilt_chat_list_and_emits_new_tail():
    previous = _Item([121], "之前的消息")
    incoming = _Item([123], "重建时的新消息")

    class _BrokenChatList:
        def children(self, **kwargs):
            del kwargs
            raise RuntimeError("stale UIA element")

    rebuilt_list = _ChatList([_Item([122], "之前的消息"), incoming])

    class _RebuiltDialog(_Dialog):
        def child_window(self, **kwargs):
            return rebuilt_list if kwargs == {"kind": "list"} else object()

    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "项目群"
    transport.targets = ["项目群"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._Lists = SimpleNamespace(FriendChatList={"kind": "list"})
    transport._Edits = SimpleNamespace(InputEdit={"kind": "edit"})
    session = {
        "dialog": _RebuiltDialog(),
        "chat_list": _BrokenChatList(),
        "input_edit": object(),
        "is_group": True,
        "seen_runtime_ids": {(121,)},
        "last_item_signature": transport._item_signature(previous),
        "visible_item_signatures": [transport._content_signature(previous)],
    }
    transport._sessions = {"项目群": session}

    result = transport.poll_new_messages()

    assert result[0]["msg"][0]["content"] == "重建时的新消息"
    assert session["chat_list"] is rebuilt_list
    assert session["seen_runtime_ids"] == {(122,), (123,)}


def test_poll_returns_events_from_multiple_whitelist_contacts():
    first = _Item([31], "测试联系人 你好")
    second = _Item([41], "小王 在吗")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.targets = ["测试联系人", "小王"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._sessions = {
        "测试联系人": {
            "dialog": _Dialog(),
            "chat_list": _ChatList([first]),
            "input_edit": object(),
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        },
        "小王": {
            "dialog": _Dialog(),
            "chat_list": _ChatList([second]),
            "input_edit": object(),
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        },
    }

    result = transport.poll_new_messages()

    assert [event["chat_name"] for event in result] == ["测试联系人", "小王"]
    assert [event["msg"][0]["content"] for event in result] == ["你好", "在吗"]


def test_poll_returns_new_incoming_text_and_visual_messages_for_target():
    old = _Item([1], "测试联系人 旧消息")
    incoming = _Item([2], "测试联系人 新消息")
    outgoing = _Item([3], "知有 我发送的消息")
    transfer = _Item([4], "测试联系人 微信转账 10元")
    image = _Item([5], "测试联系人 [图片]", class_name="mmui::ChatBubbleReferItemView")

    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport._lock = threading.RLock()
    transport._closed = False
    transport._seen_runtime_ids = {(1,)}
    transport._chat_list = _ChatList([old, incoming, outgoing, transfer, image])
    transport._open_target_window = lambda: None

    result = transport.poll_new_messages()

    assert len(result) == 2
    assert result[0]["msg"][0] == {
        "content": "新消息",
        "sender": "测试联系人",
        "type": "text",
        "is_group": False,
        "is_self": False,
    }
    events = normalize_new_messages(result, self_name="小黄")
    assert [event.content for event in events] == ["新消息", "[图片]"]
    assert [event.msg_type for event in events] == ["text", "image"]


def test_poll_emits_capturable_sticker_for_multimodal_recognition(tmp_path):
    avatar = _VisualControl(32, 32, "blue")
    sticker = _VisualControl(160, 120, "yellow")
    incoming = _Item(
        [51],
        "测试联系人 [动画表情]",
        class_name="mmui::ChatBubbleStickerItemView",
        descendant_images=[avatar, sticker],
    )
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.targets = ["测试联系人"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._sessions = {
        "测试联系人": {
            "dialog": dialog,
            "chat_list": _ChatList([incoming]),
            "input_edit": object(),
            "is_group": False,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    raw = transport.poll_new_messages()
    events = normalize_new_messages(raw, self_name="小黄")

    assert len(events) == 1
    assert events[0].msg_type == "emoji"
    assert events[0].content == "[表情包]"
    output = tmp_path / "sticker.jpg"
    assert events[0].raw_item.save_file(str(output)) == str(output.resolve())
    assert output.exists()
    with Image.open(output) as captured:
        assert captured.size == (160, 120)
    assert dialog.restored == 1
    assert dialog.minimized == 1


def test_poll_recognizes_collected_sticker_from_real_pyweixin_accessibility_label(tmp_path):
    collected_sticker = _VisualControl(180, 140, "purple")
    incoming = _Item(
        [52],
        "测试联系人 动画表情",
        class_name="mmui::ChatBubbleReferItemView",
        descendant_images=[collected_sticker],
    )
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.targets = ["测试联系人"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._sessions = {
        "测试联系人": {
            "dialog": dialog,
            "chat_list": _ChatList([incoming]),
            "input_edit": object(),
            "is_group": False,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    events = normalize_new_messages(transport.poll_new_messages(), self_name="小黄")

    assert len(events) == 1
    assert events[0].msg_type == "emoji"
    assert events[0].content == "[表情包]"
    output = tmp_path / "collected-sticker.jpg"
    events[0].raw_item.save_file(str(output))
    with Image.open(output) as captured:
        assert captured.size == (180, 140)


def test_sidebar_visual_capture_reactivates_its_source_chat(tmp_path, monkeypatch):
    old = _Item(
        [91],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
        capture_color="red",
        capture_size=(80, 80),
    )
    rebound = _Item(
        [92],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
        capture_color="green",
        capture_size=(190, 130),
    )
    dialog = _Dialog()
    session = {
        "dialog": dialog,
        "chat_list": _ChatList([rebound]),
        "input_edit": object(),
    }
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport._lock = threading.RLock()
    monkeypatch.setattr(
        transport,
        "_activate_sidebar_target",
        lambda target: session if target == "项目群" else None,
    )
    message = pyweixin_adapter.PyWeixinVisualMessage(
        old,
        dialog=dialog,
        lock=transport._lock,
        sender="小王",
        media_type="emoji",
        is_group=True,
        minimize_after=False,
        capture_resolver=lambda: transport._resolve_sidebar_visual_capture(
            "项目群",
            (91,),
            ("mmui::ChatBubbleReferItemView", "动画表情"),
        ),
    )

    output = tmp_path / "sidebar-sticker.jpg"
    message.save_file(str(output))

    with Image.open(output) as captured:
        assert captured.size == (190, 130)
    assert dialog.minimized == 0


def test_poll_can_reply_to_own_collected_sticker_in_allowlisted_group():
    own_sticker = _Item(
        [54],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
    )
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.bot_cfg = {"pyweixin_reply_to_own_group_visual": True}
    transport.target = "测试群聊"
    transport.targets = ["测试群聊"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=own_sticker)
    transport._sessions = {
        "测试群聊": {
            "dialog": _Dialog(),
            "chat_list": _ChatList([own_sticker]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    events = normalize_new_messages(transport.poll_new_messages(), self_name="小黄")

    assert len(events) == 1
    assert events[0].msg_type == "emoji"
    assert events[0].is_self is False


def test_poll_ignores_own_group_visual_by_default():
    own_sticker = _Item(
        [55],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
    )
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.bot_cfg = {}
    transport.target = "测试群聊"
    transport.targets = ["测试群聊"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=own_sticker)
    transport._sessions = {
        "测试群聊": {
            "dialog": _Dialog(),
            "chat_list": _ChatList([own_sticker]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    events = normalize_new_messages(transport.poll_new_messages(), self_name="小黄")

    assert events == []


def test_poll_recognizes_user_image_from_real_pyweixin_accessibility_label(tmp_path):
    user_image = _VisualControl(320, 200, "green")
    incoming = _Item(
        [53],
        "测试联系人 图片",
        class_name="mmui::ChatBubbleReferItemView",
        descendant_images=[user_image],
    )
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.targets = ["测试联系人"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._sessions = {
        "测试联系人": {
            "dialog": dialog,
            "chat_list": _ChatList([incoming]),
            "input_edit": object(),
            "is_group": False,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    events = normalize_new_messages(transport.poll_new_messages(), self_name="小黄")

    assert len(events) == 1
    assert events[0].msg_type == "image"
    assert events[0].content == "[图片]"
    output = tmp_path / "user-image.jpg"
    events[0].raw_item.save_file(str(output))
    with Image.open(output) as captured:
        assert captured.size == (320, 200)


def test_plain_text_mentioning_image_is_not_misclassified_as_media():
    item = _Item([54], "测试联系人 给我发张图片", class_name="mmui::ChatTextItemView")

    assert PyWeixinTransport._visual_message_type(item) is None


def test_collected_sticker_falls_back_to_whole_bubble_when_only_image_is_avatar(tmp_path):
    avatar = _VisualControl(32, 32, "blue")
    bubble = _Item(
        [55],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
        descendant_images=[avatar],
        capture_color="red",
        capture_size=(210, 150),
    )
    dialog = _Dialog()
    message = pyweixin_adapter.PyWeixinVisualMessage(
        bubble,
        dialog=dialog,
        lock=threading.RLock(),
        sender="小王",
        media_type="emoji",
        is_group=True,
    )

    output = tmp_path / "painted-sticker.jpg"
    message.save_file(str(output))

    with Image.open(output) as captured:
        assert captured.size == (210, 150)
def test_send_is_blocked_in_draft_only_mode():
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.send_enabled = False
    result = transport.send_text("测试", "测试联系人")
    assert result["success"] is False
    assert "草稿" in result["message"]


def test_send_blocks_duplicated_input_before_pressing_send(monkeypatch):
    class DuplicatingEdit(_SendEdit):
        def set_text(self, value):
            self.value = value + value

    pressed = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(
        hotkey=lambda *args, **kwargs: pressed.append(args),
    ))
    monkeypatch.setattr(pyweixin_adapter.time, "sleep", lambda seconds: None)
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "项目群"
    transport.send_enabled = True
    transport._lock = threading.RLock()
    transport._dialog_window = _Dialog()
    transport._input_edit = DuplicatingEdit()

    result = transport.send_text("今晚吃牛肉面吧", "项目群")
    assert result["success"] is False
    assert "不一致" in result["message"]
    assert pressed == []


def test_send_uses_dedicated_dialog_and_verifies_input_cleared(monkeypatch):
    dialog = _Dialog()
    edit = _SendEdit()
    monkeypatch.setattr(pyweixin_adapter.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(hotkey=lambda *_args, **_kwargs: setattr(edit, "value", "")),
    )
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.send_enabled = True
    transport._lock = threading.RLock()
    transport._dialog_window = dialog
    transport._input_edit = edit

    result = transport.send_text("测试", "测试联系人")

    assert result["success"] is True
    assert dialog.restored == 1
    assert dialog.minimized == 1


def test_send_routes_to_the_exact_whitelist_contact_session(monkeypatch):
    first_dialog = _Dialog()
    second_dialog = _Dialog()
    first_edit = _SendEdit()
    second_edit = _SendEdit()
    monkeypatch.setattr(pyweixin_adapter.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(hotkey=lambda *_args, **_kwargs: setattr(second_edit, "value", "")),
    )
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport.targets = ["测试联系人", "小王"]
    transport.send_enabled = True
    transport._lock = threading.RLock()
    transport._sessions = {
        "测试联系人": {"dialog": first_dialog, "input_edit": first_edit},
        "小王": {"dialog": second_dialog, "input_edit": second_edit},
    }

    result = transport.send_text("测试", "小王")

    assert result["success"] is True
    assert first_dialog.restored == 0
    assert second_dialog.restored == 1


def test_visible_items_uses_listitem_when_checkbox_is_unavailable():
    item = _Item([9], "测试联系人 兼容消息")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport._chat_list = _ChatList([item], checkbox_items=False)

    assert transport._visible_items() == [item]


def test_visible_items_merges_new_listitems_with_existing_checkboxes():
    old = _Item([9], "测试联系人 旧消息")
    incoming = _Item([10], "测试联系人 新消息")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport._chat_list = _ChatList([old], list_items=[old, incoming])

    assert transport._visible_items() == [old, incoming]


def test_poll_uses_bubble_direction_for_unprefixed_listitems():
    incoming = _Item([11], "后续消息")
    outgoing = _Item([12], "我的回复")
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport._lock = threading.RLock()
    transport._closed = False
    transport._seen_runtime_ids = set()
    transport._chat_list = _ChatList([], list_items=[incoming, outgoing])
    transport._dialog_window = dialog
    transport._input_edit = object()
    transport._Tools = _Tools(outgoing=outgoing)
    transport._open_target_window = lambda: None

    result = transport.poll_new_messages()

    assert len(result) == 1
    assert result[0]["msg"][0]["content"] == "后续消息"
    assert dialog.restored == 2
    assert dialog.minimized == 2


def test_poll_detects_latest_message_when_wechat_reuses_runtime_id():
    incoming = _Item([21], "测试联系人 第三条")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试联系人"
    transport._lock = threading.RLock()
    transport._closed = False
    transport._seen_runtime_ids = {(21,)}
    transport._last_item_signature = ((21,), "mmui::ChatBubbleItemView", "测试联系人 第二条")
    transport._chat_list = _ChatList([incoming])
    transport._open_target_window = lambda: None

    result = transport.poll_new_messages()

    assert len(result) == 1
    assert result[0]["msg"][0]["content"] == "第三条"


def test_poll_does_not_replay_visual_when_only_viewport_changes():
    incoming = _Item(
        [31],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
    )
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试群聊"
    transport.targets = ["测试群聊"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._sessions = {
        "测试群聊": {
            "dialog": dialog,
            "chat_list": _ChatList([incoming]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": {(31,)},
            "last_item_signature": (
                (31,),
                "mmui::ChatBubbleReferItemView",
                "动画表情",
            ),
            "visible_item_signatures": [
                ("mmui::ChatTextItemView", "之前的文本"),
                ("mmui::ChatBubbleReferItemView", "动画表情"),
            ],
        }
    }

    result = transport.poll_new_messages()

    assert result == []


def test_poll_does_not_replay_visual_with_later_outgoing_reply():
    sticker = _Item(
        [61],
        "动画表情",
        class_name="mmui::ChatBubbleReferItemView",
    )
    outgoing = _Item([62], "小黄 好萌啊")
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "测试群聊"
    transport.targets = ["测试群聊"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=outgoing)
    transport._sessions = {
        "测试群聊": {
            "dialog": dialog,
            "chat_list": _ChatList([sticker, outgoing]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    result = transport.poll_new_messages()

    assert result == []


def test_poll_returns_group_sender_and_group_metadata_from_uia_texts():
    incoming = _Item(
        [51],
        "小王 @知有\u2005 帮我看一下",
        descendant_texts=["小王", "@知有\u2005 帮我看一下"],
    )
    outgoing = _Item([52], "知有 收到", descendant_texts=["知有", "收到"])
    dialog = _Dialog()
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "项目群"
    transport.targets = ["项目群"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=outgoing)
    transport._sessions = {
        "项目群": {
            "dialog": dialog,
            "chat_list": _ChatList([incoming, outgoing]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    result = transport.poll_new_messages()

    assert result == [
        {
            "chat_name": "项目群",
            "chat_type": "group",
            "msg": [
                {
                    "content": "@知有\u2005 帮我看一下",
                    "sender": "小王",
                    "type": "text",
                    "is_group": True,
                    "is_self": False,
                }
            ],
        }
    ]
    events = normalize_new_messages(result, "知有")
    assert len(events) == 1
    assert events[0].chat_name == "项目群"
    assert events[0].sender == "小王"
    assert events[0].is_group is True
    assert events[0].is_at_me is True
    assert events[0].content == "帮我看一下"


def test_poll_uses_neutral_group_sender_without_switching_to_multiselect():
    ambiguous = _Item([61], "昵称可能有空格 消息也有空格")
    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "项目群"
    transport.targets = ["项目群"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _Tools(outgoing=None)
    transport._sessions = {
        "项目群": {
            "dialog": _Dialog(),
            "chat_list": _ChatList([ambiguous]),
            "input_edit": object(),
            "is_group": True,
            "seen_runtime_ids": set(),
            "last_item_signature": None,
        }
    }

    assert transport.poll_new_messages() == [
        {
            "chat_name": "项目群",
            "chat_type": "group",
            "msg": [
                {
                    "content": "昵称可能有空格 消息也有空格",
                    "sender": "群成员",
                    "type": "text",
                    "is_group": True,
                    "is_self": False,
                }
            ],
        }
    ]


def test_poll_retries_group_bubble_after_transient_direction_failure():
    incoming = _Item([71], "稍后重试")

    class _FlakyTools:
        calls = 0

        @classmethod
        def is_my_bubble(cls, dialog, item, input_edit):
            del dialog, item, input_edit
            cls.calls += 1
            if cls.calls == 1:
                raise RuntimeError("transient UIA failure")
            return False

    transport = PyWeixinTransport.__new__(PyWeixinTransport)
    transport.target = "项目群"
    transport.targets = ["项目群"]
    transport._lock = threading.RLock()
    transport._closed = False
    transport._Tools = _FlakyTools
    session = {
        "dialog": _Dialog(),
        "chat_list": _ChatList([incoming]),
        "input_edit": object(),
        "is_group": True,
        "seen_runtime_ids": set(),
        "last_item_signature": None,
    }
    transport._sessions = {"项目群": session}

    assert transport.poll_new_messages() == []
    assert (71,) not in session["seen_runtime_ids"]

    result = transport.poll_new_messages()

    assert result[0]["msg"][0]["content"] == "稍后重试"
    assert (71,) in session["seen_runtime_ids"]
