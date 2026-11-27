from types import SimpleNamespace

from backend.prompt_templates import DEFAULT_WECHAT_PERSONA_PROMPT
from backend.utils.config import (
    compose_system_prompt_template,
    extract_editable_system_prompt,
    resolve_system_prompt,
)


def test_resolve_system_prompt_replaces_placeholders():
    bot_cfg = {
        "system_prompt": (
            "# 历史对话\n{history_context}\n\n"
            "# 用户画像\n{user_profile}\n\n"
            "# 当前情境\n{emotion_hint}{time_hint}{style_hint}\n"
        ),
        "system_prompt_overrides": {},
        "profile_inject_in_prompt": True,
        "emotion_inject_in_prompt": True,
    }
    event = SimpleNamespace(chat_name="Alice")
    user_profile = {"nickname": "Bob", "raw_item": "should_not_leak"}
    emotion = SimpleNamespace(
        emotion="happy", confidence=0.9, intensity=4, suggested_tone="light"
    )
    context = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "system", "content": "rag context"},
    ]

    rendered = resolve_system_prompt(event, bot_cfg, user_profile, emotion, context)
    assert "{history_context}" not in rendered
    assert "{user_profile}" not in rendered
    assert "{emotion_hint}" not in rendered
    assert "{time_hint}" not in rendered
    assert "{style_hint}" not in rendered

    assert "User: hello" in rendered
    assert "Assistant: hi" in rendered
    assert "- nickname: Bob" in rendered
    assert "raw_item" not in rendered
    assert "happy" in rendered
    assert "light" in rendered


def test_resolve_system_prompt_appends_required_injections_when_no_placeholders():
    bot_cfg = {
        "system_prompt": "base prompt",
        "system_prompt_overrides": {},
        "profile_inject_in_prompt": True,
        "emotion_inject_in_prompt": True,
    }
    event = SimpleNamespace(chat_name="Alice")
    user_profile = {"nickname": "Bob"}
    emotion = SimpleNamespace(emotion="neutral")

    rendered = resolve_system_prompt(event, bot_cfg, user_profile, emotion, [])
    assert "base prompt" in rendered
    assert "# 用户画像" in rendered
    assert "- nickname: Bob" in rendered
    assert "# 当前情境" in rendered
    assert "【当前情绪】neutral" in rendered


def test_resolve_system_prompt_prefers_contact_prompt_but_still_keeps_required_injections():
    bot_cfg = {
        "system_prompt": "base prompt",
        "system_prompt_overrides": {"Alice": "override prompt"},
        "profile_inject_in_prompt": True,
        "emotion_inject_in_prompt": False,
    }
    event = SimpleNamespace(chat_name="Alice")
    user_profile = {
        "nickname": "Bob",
        "profile_summary": "关系：老朋友",
        "contact_prompt": "contact prompt",
    }
    context = [{"role": "user", "content": "hello again"}]

    rendered = resolve_system_prompt(event, bot_cfg, user_profile, None, context)

    assert rendered.startswith("contact prompt")
    assert "override prompt" not in rendered
    assert "base prompt" not in rendered
    assert "User: hello again" in rendered


def test_system_prompt_helpers_strip_and_rebuild_fixed_injection_block():
    editable = "像本人一样回复，不要太官腔。"
    composed = compose_system_prompt_template(editable)

    assert editable in composed
    assert "{history_context}" in composed
    assert "{user_profile}" in composed
    assert "{emotion_hint}{time_hint}{style_hint}" in composed
    assert extract_editable_system_prompt(composed) == editable


def test_resolve_system_prompt_adds_time_and_style_injections_when_template_lacks_them(
    monkeypatch,
):
    bot_cfg = {
        "system_prompt": "只保留自定义规则",
        "system_prompt_overrides": {},
        "profile_inject_in_prompt": False,
        "emotion_inject_in_prompt": False,
    }
    event = SimpleNamespace(chat_name="Alice")
    context = [
        {"role": "user", "content": "你今天忙吗"},
        {"role": "assistant", "content": "还行，怎么了"},
    ]

    monkeypatch.setattr(
        "backend.core.emotion.get_time_aware_prompt_addition",
        lambda: "【时间感知】现在是晚上，回复可以更放松。",
    )
    monkeypatch.setattr(
        "backend.core.emotion.analyze_conversation_style",
        lambda items: {"style": "casual", "count": len(items)},
    )
    monkeypatch.setattr(
        "backend.core.emotion.get_style_adaptation_hint",
        lambda style_info: "延续对方的口语节奏，保持自然。",
    )

    rendered = resolve_system_prompt(event, bot_cfg, None, None, context)

    assert rendered.startswith("只保留自定义规则")
    assert "【时间感知】现在是晚上，回复可以更放松。" in rendered
    assert "【对话风格】延续对方的口语节奏，保持自然。" in rendered


def test_resolve_system_prompt_enforces_current_self_name_over_old_prompts():
    bot_cfg = {
        "self_name": "小黄",
        "system_prompt": "沿用过去保存的联系人提示词。",
        "system_prompt_overrides": {},
        "profile_inject_in_prompt": False,
        "emotion_inject_in_prompt": False,
    }

    rendered = resolve_system_prompt(
        SimpleNamespace(chat_name="项目群"),
        bot_cfg,
        {"contact_prompt": "这是历史生成的旧身份提示词。"},
        None,
        [],
    )

    assert rendered.endswith(
        "# 当前身份名称\n你的名字只能是「小黄」。任何旧名称都已废弃，不要再使用其他名字自称。"
    )


def test_resolve_system_prompt_adds_natural_wechat_discipline_by_default():
    rendered = resolve_system_prompt(
        SimpleNamespace(chat_name="朋友群", is_group=True),
        {"self_name": "小黄", "system_prompt": DEFAULT_WECHAT_PERSONA_PROMPT},
        None,
        None,
        [{"role": "user", "content": "累死了"}],
    )

    assert "# 微信自然聊天纪律（高优先级）" in rendered
    assert "不为维持对话强行反问" in rendered
    assert "群聊只接当前这句话和最近语境" in rendered
    assert "立即完成上一轮已经答应或提出的内容" in rendered
    assert "不要再回复“马上、我来、可以、我按……说”" in rendered
    assert "当前消息主体是英文且不含中文字符" in rendered
    assert "整条回复只用自然英文" in rendered
    assert "先完成对方的要求，再尽量简短" in rendered
    assert "当条回复直接给出明确选项" in rendered
    assert "不能只回“选这个”" in rendered
    assert "闲聊、玩笑和调侃默认只回一句" in rendered
    assert "像熟人一样含蓄地接一句" in rendered
    assert "不讲风险、边界或“是否合适”" in rendered
    assert "匹配对方这一条的语气和信息量" in rendered
    assert "先像群友一样回应这句话本身" in rendered
    assert "被直接询问时承认是 AI 聊天伙伴" in rendered
    assert "对方不知道这是代答" not in rendered
    assert "主人本人" not in rendered


def test_resolve_system_prompt_can_disable_natural_wechat_discipline():
    rendered = resolve_system_prompt(
        SimpleNamespace(chat_name="Alice"),
        {"system_prompt": "只按事实回答", "natural_chat_mode": False},
        None,
        None,
        [],
    )

    assert "只按事实回答" in rendered
    assert "微信自然聊天纪律" not in rendered
