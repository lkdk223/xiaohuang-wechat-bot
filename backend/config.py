"""
微信 AI 机器人配置文件。

本文件包含机器人运行所需的全部配置项，可根据需要进行修改。
配置修改后支持热重载，无需重启程序。

配置步骤：
    1. 复制此文件或直接修改
    2. 填写 API 密钥（建议使用 api_keys.py 分离管理）
    3. 根据需要调整 bot 配置
    4. 运行 python main.py

配置分区：
    - api: 模型接口相关配置（预设、密钥、参数）
    - bot: 机器人行为配置（回复策略、记忆、过滤规则）
    - logging: 日志输出配置

注意事项：
    - API 密钥建议放在 api_keys.py 中，避免提交到版本控制
    - 部分配置项支持按会话覆盖（如 system_prompt_overrides）
    - 群聊功能需正确配置 self_name 和 whitelist
"""
from copy import deepcopy

from backend.core.oauth_support import get_preset_auth_summary
from backend.prompt_templates import DEFAULT_WECHAT_PERSONA_PROMPT

# ═══════════════════════════════════════════════════════════════════════════════
#                               全局配置字典
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG = {'api': {'base_url': 'https://api.openai.com/v1',
         'api_key': 'YOUR_API_KEY',
         'model': 'gpt-4o-mini',
         'embedding_model': 'text-embedding-3-small',
         'alias': '小欧',
         'timeout_sec': 8,
         'max_retries': 1,
         'temperature': 0.6,
         'max_tokens': 512,
         'max_completion_tokens': None,
         'reasoning_effort': None,
         'allow_empty_key': False,
         'active_preset': 'Ollama',
         'presets': [{'name': 'OpenAI',
                      'provider_id': 'openai',
                      'alias': '小欧',
                      'base_url': 'https://api.openai.com/v1',
                      'api_key': 'YOUR_OPENAI_KEY',
                      'model': 'gpt-4o-mini',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Doubao',
                      'provider_id': 'doubao',
                      'alias': '小豆',
                      'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
                      'api_key': 'YOUR_DOUBAO_KEY',
                      'model': 'doubao-seed-1-8-251228',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': 512,
                      'reasoning_effort': None,
                      'embedding_model': 'YOUR_DOUBAO_EMBEDDING_ENDPOINT',
                      'allow_empty_key': False},
                     {'name': 'DeepSeek',
                      'provider_id': 'deepseek',
                      'alias': '小深',
                      'base_url': 'https://api.deepseek.com/v1',
                      'api_key': 'YOUR_DEEPSEEK_KEY',
                      'model': 'deepseek-chat',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Groq',
                      'provider_id': 'groq',
                      'alias': '小咕',
                      'base_url': 'https://api.groq.com/openai/v1',
                      'api_key': 'YOUR_GROQ_KEY',
                      'model': 'llama3-70b-8192',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'SiliconFlow',
                      'provider_id': 'siliconflow',
                      'alias': '小硅',
                      'base_url': 'https://api.siliconflow.cn/v1',
                      'api_key': 'YOUR_SILICONFLOW_KEY',
                      'model': 'deepseek-ai/DeepSeek-V3',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'OpenRouter',
                      'provider_id': 'openrouter',
                      'alias': '小路',
                      'base_url': 'https://openrouter.ai/api/v1',
                      'api_key': 'YOUR_OPENROUTER_KEY',
                      'model': 'openai/gpt-4o-mini',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Together',
                      'provider_id': 'together',
                      'alias': '小合',
                      'base_url': 'https://api.together.xyz/v1',
                      'api_key': 'YOUR_TOGETHER_KEY',
                      'model': 'meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Fireworks',
                      'provider_id': 'fireworks',
                      'alias': '小焰',
                      'base_url': 'https://api.fireworks.ai/inference/v1',
                      'api_key': 'YOUR_FIREWORKS_KEY',
                      'model': 'accounts/fireworks/models/llama-v3p1-70b-instruct',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Mistral',
                      'provider_id': 'mistral',
                      'alias': '小风',
                      'base_url': 'https://api.mistral.ai/v1',
                      'api_key': 'YOUR_MISTRAL_KEY',
                      'model': 'mistral-large-latest',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Moonshot',
                      'provider_id': 'moonshot',
                      'alias': '小月',
                      'base_url': 'https://api.moonshot.cn/v1',
                      'api_key': 'YOUR_MOONSHOT_KEY',
                      'model': 'moonshot-v1-8k',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Perplexity',
                      'provider_id': 'perplexity',
                      'alias': '小悟',
                      'base_url': 'https://api.perplexity.ai',
                      'api_key': 'YOUR_PERPLEXITY_KEY',
                      'model': 'llama-3.1-sonar-large-128k-online',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': False},
                     {'name': 'Zhipu',
                      'provider_id': 'zhipu',
                      'alias': '小G',
                      'base_url': 'https://open.bigmodel.cn/api/paas/v4',
                      'api_key': 'YOUR_ZHIPU_KEY',
                      'model': 'GLM-4.7-Flash',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': 'embedding-2',
                      'allow_empty_key': False},
                     {'name': 'Qwen',
                      'provider_id': 'qwen',
                      'alias': '小千',
                      'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                      'api_key': 'YOUR_QWEN_KEY',
                      'model': 'qwen3.5-flash',
                      'timeout_sec': 10,
                      'max_retries': 2,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': '',
                      'allow_empty_key': False},
                     {'name': 'Ollama',
                      'provider_id': 'ollama',
                      'alias': '本地',
                      'base_url': 'http://127.0.0.1:11434/v1',
                      'api_key': '',
                      'model': 'deepseek-v3.2:cloud',
                      'timeout_sec': 20,
                      'max_retries': 1,
                      'temperature': 0.6,
                      'max_tokens': 512,
                      'max_completion_tokens': None,
                      'reasoning_effort': None,
                      'embedding_model': None,
                      'allow_empty_key': True}]},
 'bot': {'self_name': '小黄',
         'self_name_aliases': ['知有', 'ikun'],
         'system_prompt': DEFAULT_WECHAT_PERSONA_PROMPT,
         'system_prompt_overrides': {},
         'reply_suffix': ' （🤖💬 AI代言中～）',
         'emoji_policy': 'mixed',
         'emoji_replacements': {},
         'transport_backend': 'wcferry',
         'silent_mode_required': True,
         'required_wechat_version': '3.9.12.51',
         'pyweixin_test_contact': '',
         'pyweixin_send_enabled': False,
         'pyweixin_window_mode': 'sidebar',
         'pyweixin_max_contacts': 8,
         'pyweixin_whitelist_auto_reply': True,
         'pyweixin_reply_to_own_group_visual': False,
         'voice_to_text': True,
         'voice_to_text_fail_reply': '',
         'voice_transcription_model': 'gpt-4o-mini-transcribe',
         'voice_transcription_timeout_sec': 30.0,
         'memory_db_path': 'data/chat_memory.db',
         'memory_context_limit': 12,
         'memory_ttl_sec': None,
         'memory_cleanup_interval_sec': 0.0,
         'context_rounds': 4,
         'context_max_tokens': 1200,
         'history_max_chats': 120,
         'history_ttl_sec': None,
         'poll_interval_min_sec': 0.05,
         'poll_interval_max_sec': 1.0,
         'poll_interval_backoff_factor': 1.2,
         'poll_timeout_sec': 10.0,
         'visual_duplicate_window_sec': 90.0,
         'min_reply_interval_sec': 0.1,
         'random_delay_range_sec': [0.1, 0.3],
         'merge_user_messages_sec': 0.45,
         'merge_user_messages_max_wait_sec': 1.2,
         'reply_chunk_size': 500,
         'reply_chunk_delay_sec': 0.0,
         'natural_split_enabled': True,
         'natural_split_min_chars': 30,
         'natural_split_max_chars': 120,
         'natural_split_max_segments': 3,
         'natural_split_delay_sec': [0.3, 0.8],
         'reply_deadline_sec': 30.0,
         'max_concurrency': 5,
         'keepalive_idle_sec': 180.0,
         'filter_mute': True,
         'ignore_official': True,
         'ignore_service': True,
         'allow_filehelper_self_message': True,
         'ignore_names': ['微信团队'],
         'ignore_keywords': ['订阅号'],
         'whitelist_enabled': True,
         'whitelist': ['点菜炫饭群(', '🐶 🐶 🐶 🐶 🐶 🐶'],
         'reload_ai_client_on_change': True,
         'config_reload_sec': 2.0,
         'config_reload_mode': 'auto',
         'config_reload_debounce_ms': 500,
         'reload_ai_client_module': True,
         'reconnect_max_retries': 3,
         'reconnect_backoff_sec': 2.0,
         'reconnect_max_delay_sec': 20.0,
         'group_reply_only_when_at': False,
         'group_include_sender': True,
         'natural_chat_mode': True,
         'image_generation_enabled': True,
         'web_search_enabled': True,
         'web_search_always_on': False,
         'send_exact_match': True,
         'send_fallback_current_chat': False,
         'personalization_enabled': True,
         'profile_update_frequency': 10,
         'contact_prompt_update_frequency': 10,
         'remember_facts_enabled': True,
         'max_context_facts': 20,
         'profile_inject_in_prompt': True,
         'rag_enabled': False,
         'export_rag_enabled': False,
         'export_rag_dir': 'data/chat_exports/聊天记录',
         'export_rag_auto_ingest': True,
         'export_rag_max_chunks_per_chat': 500,
         'export_rag_chunk_messages': 6,
         'export_rag_top_k': 3,
         'export_rag_min_score': 1.0,
         'export_rag_max_context_chars': 900,
         'export_rag_prefer_recent': True,
         'safety_require_citations_for_rag': False,
         'safety_block_prompt_injection': False,
         'safety_block_pii': False,
         'control_commands_enabled': True,
         'control_command_prefix': '/',
         'control_allowed_users': [],
         'control_reply_visible': True,
         'quiet_hours_enabled': False,
         'quiet_hours_start': '23:00',
         'quiet_hours_end': '07:00',
         'quiet_hours_reply': '',
         'usage_tracking_enabled': False,
         'daily_token_limit': 0,
         'token_warning_threshold': 0.8,
         'emotion_detection_enabled': True,
         'emotion_detection_mode': 'ai',
         'emotion_inject_in_prompt': True,
         'emotion_log_enabled': True},
 'logging': {'level': 'INFO',
             'file': 'data/logs/bot.log',
             'max_bytes': 5242880,
             'backup_count': 5,
             'format': 'text',
             'log_message_content': False,
             'log_reply_content': False},
 'agent': {'enabled': True,
           'graph_mode': 'state_graph',
           'langsmith_enabled': False,
           'langsmith_project': 'wechat-chat',
           'langsmith_endpoint': '',
           'langsmith_api_key': '',
           'retriever_top_k': 3,
           'retriever_score_threshold': 1.0,
           'retriever_hybrid_enabled': False,
           'retriever_keyword_weight': 0.35,
           'retriever_rerank_mode': 'lightweight',
           'retriever_cross_encoder_model': '',
           'retriever_cross_encoder_device': '',
           'prepare_retrieval_timeout_sec': 0.25,
           'embedding_cache_ttl_sec': 300.0,
           'background_fact_extraction_enabled': True,
           'emotion_fast_path_enabled': True,
           'max_parallel_retrievers': 3,
           'llm_foreground_max_concurrency': 1,
           'model_routing': {},
           'response_cache': {'semantic_enabled': False},
           'background_ai_batch_time': '04:00',
           'background_ai_missed_window_policy': 'wait_until_next_day',
           'background_ai_defer_mode': 'defer_all'}}

DEFAULT_CONFIG = deepcopy(CONFIG)

# ═══════════════════════════════════════════════════════════════════════════════
#                               辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _load_api_keys() -> dict:
    """
    从 api_keys.py 文件加载 API 密钥。

    该函数尝试导入 api_keys 模块并读取其中的 API_KEYS 字典。
    如果导入失败或 API_KEYS 格式不正确，返回空字典。

    Returns:
        dict: 包含 API 密钥的字典，格式为:
            {
                "default": "默认密钥",
                "presets": {"预设名": "密钥", ...}
            }
    """
    try:
        from data.api_keys import API_KEYS
    except Exception:
        return {}
    if isinstance(API_KEYS, dict):
        return API_KEYS
    return {}


def _apply_api_keys(config: dict) -> None:
    """
    将加载的 API 密钥应用到配置字典中。

    该函数会：
    1. 用 default 密钥覆盖配置中的默认 api_key
    2. 遍历所有预设，用 presets 中对应的密钥覆盖各预设的 api_key

    Args:
        config: 全局配置字典（会被原地修改）
    """
    api_keys = _load_api_keys()
    if not api_keys:
        return

    api_cfg = config.get("api")
    if not isinstance(api_cfg, dict):
        return

    # 应用默认密钥
    default_key = api_keys.get("default")
    if default_key:
        api_cfg["api_key"] = default_key

    # 应用各预设的密钥
    preset_keys = api_keys.get("presets")
    if isinstance(preset_keys, dict):
        for preset in api_cfg.get("presets") or []:
            if not isinstance(preset, dict):
                continue
            name = preset.get("name")
            if not name:
                continue
            key = preset_keys.get(name)
            if key:
                preset["api_key"] = key


def _load_prompt_overrides() -> dict:
    """
    从 prompt_overrides.py 文件加载个性化 Prompt 覆盖配置。

    该函数尝试导入 prompt_overrides 模块并读取其中的 PROMPT_OVERRIDES 字典。
    如果导入失败或格式不正确，返回空字典。

    Returns:
        dict: 包含联系人名称到 system_prompt 的映射
    """
    try:
        from prompt_overrides import PROMPT_OVERRIDES
    except Exception:
        return {}
    if isinstance(PROMPT_OVERRIDES, dict):
        return PROMPT_OVERRIDES
    return {}


def _apply_prompt_overrides(config: dict) -> None:
    """
    将加载的个性化 Prompt 覆盖应用到配置字典中。

    Args:
        config: 全局配置字典（会被原地修改）
    """
    overrides = _load_prompt_overrides()
    if not overrides:
        return

    bot_cfg = config.get("bot")
    if not isinstance(bot_cfg, dict):
        return

    # 获取现有的 system_prompt_overrides
    existing = bot_cfg.get("system_prompt_overrides")
    if not isinstance(existing, dict):
        existing = {}

    # 合并：prompt_overrides.py 的内容会被现有配置覆盖（优先级更低）

# ═══════════════════════════════════════════════════════════════════════════════
#                               应用配置覆写
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_config_overrides(
    config_dict: dict,
    *,
    override_file: str = "",
    override_data: dict = None,
):
    """加载并应用 JSON 格式的配置覆写"""
    try:
        import json
        import os
        resolved_override = override_file or os.path.join("data", "config_override.json")
        if override_data is not None:
            overrides = override_data if isinstance(override_data, dict) else {}
        else:
            if not os.path.exists(resolved_override):
                return

            with open(resolved_override, "r", encoding="utf-8") as f:
                overrides = json.load(f)

        def _merge_preset_lists(default_presets, override_presets):
            if not isinstance(default_presets, list):
                return override_presets
            if not isinstance(override_presets, list):
                return default_presets

            merged = []
            default_map = {}
            for preset in default_presets:
                if isinstance(preset, dict) and preset.get("name"):
                    default_map[str(preset["name"])] = preset

            used_names = set()
            for preset in override_presets:
                if not isinstance(preset, dict):
                    continue
                name = str(preset.get("name") or "").strip()
                if not name:
                    merged.append(preset)
                    continue

                base = dict(default_map.get(name, {}))
                base.update(preset)
                merged.append(base)
                used_names.add(name)

            for preset in default_presets:
                if not isinstance(preset, dict):
                    continue
                name = str(preset.get("name") or "").strip()
                if not name or name in used_names:
                    continue
                merged.append(dict(preset))

            return merged

        # 递归更新配置 (目前仅支持一层字典合并，如需深层合并可扩展)
        for section, settings in overrides.items():
            if section in config_dict and isinstance(config_dict[section], dict) and isinstance(settings, dict):
                if section == "api" and "presets" in settings:
                    settings = dict(settings)
                    settings["presets"] = _merge_preset_lists(
                        config_dict[section].get("presets"),
                        settings.get("presets"),
                    )
                config_dict[section].update(settings)
            else:
                config_dict[section] = settings
    except Exception as e:
        print(f"❌ 加载配置覆写失败: {e}")


def _auto_select_active_preset(config: dict) -> None:
    api_cfg = config.get("api")
    if not isinstance(api_cfg, dict):
        return
    presets = api_cfg.get("presets")
    if not isinstance(presets, list):
        return
    active_name = str(api_cfg.get("active_preset") or "").strip()

    def is_usable(preset: dict) -> bool:
        if not isinstance(preset, dict):
            return False
        base_url = preset.get("base_url") or api_cfg.get("base_url")
        model = preset.get("model") or api_cfg.get("model")
        if not base_url or not model:
            return False
        summary = get_preset_auth_summary(
            {
                **api_cfg,
                **preset,
            }
        )
        return bool(summary.get("auth_ready"))

    if active_name:
        active_preset = next((p for p in presets if p.get("name") == active_name), None)
        if active_preset and is_usable(active_preset):
            return

    for preset in presets:
        if is_usable(preset):
            name = preset.get("name")
            if name:
                api_cfg["active_preset"] = name
            return

_apply_api_keys(CONFIG)
_apply_prompt_overrides(CONFIG)
_apply_config_overrides(CONFIG)
_auto_select_active_preset(CONFIG)

