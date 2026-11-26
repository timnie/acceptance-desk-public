"""按本机 CLI 或供应商提供的信息构造模型列表。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# 供应商预设沿用 OpenDesign 的接口地址。协议决定列表和生成请求的格式。
API_PRESETS = {
    "deepseek": ("DeepSeek", "chat", "https://api.deepseek.com", "deepseek-flash"),
    "openai": ("OpenAI", "responses", "https://api.openai.com/v1", ""),
    "anthropic": ("Anthropic", "anthropic", "https://api.anthropic.com/v1", ""),
    "gemini": ("Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta", ""),
    "azure": ("Azure OpenAI", "azure", "", ""),
    "openrouter": ("OpenRouter", "chat", "https://openrouter.ai/api/v1", ""),
    "ollama": ("Ollama（本机）", "chat", "http://127.0.0.1:11434/v1", ""),
    "ollama_cloud": ("Ollama Cloud", "chat", "https://ollama.com/v1", ""),
    "atlascloud": ("Atlas Cloud", "chat", "https://api.atlascloud.ai/v1", ""),
    "siliconflow": ("硅基流动（中国）", "chat", "https://api.siliconflow.cn/v1", ""),
    "siliconflow_global": ("SiliconFlow（国际）", "chat", "https://api.siliconflow.com/v1", ""),
    "ppio": ("PPIO", "chat", "https://api.ppinfra.com/v3/openai", ""),
    "novita": ("Novita AI", "chat", "https://api.novita.ai/openai", ""),
    "nvidia": ("NVIDIA", "chat", "https://integrate.api.nvidia.com/v1", ""),
    "stepfun": ("阶跃星辰", "chat", "https://api.stepfun.ai/v1", ""),
    "mistral": ("Mistral AI", "chat", "https://api.mistral.ai/v1", ""),
    "xai": ("xAI", "chat", "https://api.x.ai/v1", ""),
    "together": ("Together AI", "chat", "https://api.together.xyz/v1", ""),
    "huggingface": ("Hugging Face", "chat", "https://router.huggingface.co/v1", ""),
    "qwen": ("通义千问", "chat", "https://dashscope.aliyuncs.com/compatible-mode/v1", ""),
    "volcengine": ("火山方舟", "chat", "https://ark.cn-beijing.volces.com/api/v3", ""),
    "qianfan": ("百度千帆", "chat", "https://qianfan.baidubce.com/v2", ""),
    "lm_studio": ("LM Studio（本机）", "chat", "http://127.0.0.1:1234/v1", ""),
    "vllm": ("vLLM（本机）", "chat", "http://127.0.0.1:8000/v1", ""),
    "mimo": ("小米 MiMo", "chat", "https://token-plan-cn.xiaomimimo.com/v1", ""),
    "minimax": ("MiniMax（中国）", "anthropic", "https://api.minimaxi.com/anthropic/v1", ""),
    "moonshot": ("Moonshot", "chat", "https://api.moonshot.cn/v1", ""),
    "zhipu": ("智谱 AI", "chat", "https://open.bigmodel.cn/api/paas/v4", ""),
    "deepseek_anthropic": ("DeepSeek（Anthropic 接口）", "anthropic", "https://api.deepseek.com/anthropic/v1", "deepseek-flash"),
    "minimax_global": ("MiniMax（国际 · Anthropic）", "anthropic", "https://api.minimax.io/anthropic/v1", ""),
    "minimax_chat": ("MiniMax（国际 · OpenAI）", "chat", "https://api.minimax.io/v1", ""),
    "mimo_anthropic": ("小米 MiMo（Anthropic 接口）", "anthropic", "https://token-plan-cn.xiaomimimo.com/anthropic/v1", ""),
    "senseaudio": ("SenseAudio", "chat", "https://api.senseaudio.cn/v1", ""),
    "aihubmix": ("AIHubMix", "chat", "https://aihubmix.com/v1", ""),
    "bedrock": ("Amazon Bedrock", "bedrock", "", ""),
    "compatible": ("自定义供应商", "chat", "", ""),
}
API_PROTOCOLS = {"chat": "OpenAI Chat Completions", "responses": "OpenAI Responses",
                  "anthropic": "Anthropic Messages", "gemini": "Gemini API", "azure": "Azure OpenAI v1",
                 "bedrock": "Amazon Bedrock Converse"}
EFFORT_LABELS = {"none": "无", "minimal": "极低", "low": "低", "medium": "中", "high": "高",
                 "xhigh": "很高", "max": "最大", "ultra": "Ultra"}


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    efforts: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()


TIER_LABELS = {"priority": "快速"}
VARIANT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
STANDARD_EFFORT = ("low", "medium", "high")
PI_EFFORT = ("off", "minimal", *STANDARD_EFFORT, "xhigh")
EFFORT_LABELS["off"] = "关闭"


def effort_label(name: str) -> str:
    return EFFORT_LABELS.get(name, name)


def claude_efforts(model: str, *, aliases: bool = False) -> tuple[str, ...]:
    if aliases and model in {"opus", "sonnet", "fable"}:
        return STANDARD_EFFORT
    if re.fullmatch(r"claude-(?:(?:opus|sonnet)-(?:4-[6-9]|5(?:-[0-9])?)|(?:fable|mythos)-5(?:-[0-9])?|haiku-5-5)(?:-\d{8})?", model):
        return (*STANDARD_EFFORT, "max")
    return ()


def api_efforts(provider: str, model: str, protocol: str) -> tuple[str, ...]:
    # 兼容网关的协议相同，但可用档位由它的模型目录决定。
    if provider == "openai" and protocol == "responses" and re.fullmatch(r"gpt-5(?:-mini|-nano|-\d{4}-\d\d-\d\d)?", model):
        return ("minimal", *STANDARD_EFFORT)
    if provider == "openai" and protocol == "responses" and re.fullmatch(r"gpt-5\.6(?:-sol|-terra|-luna)?(?:-\d{4}-\d\d-\d\d)?", model):
        return ("none", *STANDARD_EFFORT, "xhigh", "max")
    if provider == "openai" and protocol == "responses" and re.fullmatch(r"gpt-5\.[1-5](?:-\d{4}-\d\d-\d\d)?", model):
        return ("none", *STANDARD_EFFORT, *(("xhigh",) if not model.startswith("gpt-5.1") else ()))
    if provider == "openai" and protocol == "responses" and re.fullmatch(r"o[134](?:-mini)?(?:-\d{4}-\d\d-\d\d)?", model):
        return STANDARD_EFFORT
    if provider == "anthropic":
        return claude_efforts(model)
    if provider in {"deepseek", "deepseek_anthropic"} and model in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"}:
        return ("none", "low", "high", "max")
    if provider == "gemini" and re.match(r"gemini-3(?:\.\d+)?-(?:pro|flash)(?:-|$)", model):
        return ("low", "high")  # 两个档位同时适用于 Gemini 3 Pro 与 Flash。
    return ()

def valid_model_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:/@+\-]{0,199}", value) is not None


def codex_models(value) -> list[ModelOption]:
    entries = value.get("models") if isinstance(value, dict) else value
    if not isinstance(entries, list):
        return []
    models = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("visibility") == "hidden":
            continue
        model = entry.get("slug") or entry.get("id")
        if not valid_model_id(model) or model == "default":
            continue
        label = entry.get("display_name") or entry.get("name")
        levels = entry.get("supported_reasoning_levels")
        effort = tuple(dict.fromkeys(level["effort"] for level in levels
                                    if isinstance(level, dict) and isinstance(level.get("effort"), str)
                                    and level["effort"] in EFFORT_LABELS)) if isinstance(levels, list) else ()
        tiers = entry.get("additional_speed_tiers")
        speed = ("priority",) if isinstance(tiers, list) and any(t in ("fast", "priority") for t in tiers) else ()
        service_tiers = entry.get("service_tiers")
        if isinstance(service_tiers, list) and any(isinstance(t, dict) and t.get("id") == "priority" for t in service_tiers):
            speed = ("priority",)
        models.setdefault(model, ModelOption(model, label.strip() if isinstance(label, str) and label.strip() else model, effort, speed))
    return list(models.values())


def claude_routes() -> dict:
    path = Path(os.environ.get("MMD_MODEL_ROUTES_FILE") or "~/.config/mms/model-routes.json").expanduser()
    try:
        with path.open("rb") as stream:
            data = json.loads(stream.read(1024 * 1024))
        routes = data.get("routes")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(routes, dict):
        return {}
    return {model: route["primary"] for model, route in routes.items()
            if valid_model_id(model) and isinstance(route, dict) and isinstance(route.get("primary"), dict)
            and isinstance(route["primary"].get("anthropic_base_url"), str)}


def claude_models() -> list[ModelOption]:
    choices = [ModelOption(model, model, claude_efforts(model)) for model in claude_routes()]
    aliases = {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku", "fable": "Fable"}
    known = {choice.id for choice in choices}
    return choices + [ModelOption(model, f"{label}（CLI 别名）", claude_efforts(model, aliases=True)) for model, label in aliases.items() if model not in known]


def api_models(value: dict, protocol: str) -> list[ModelOption]:
    entries = value.get("models" if protocol == "gemini" else "data")
    if not isinstance(entries, list):
        return []
    models = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("id")
        label = entry.get("display_name") or entry.get("name")
        if protocol == "gemini":
            methods = entry.get("supportedGenerationMethods") or []
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            name = entry.get("name")
            model = name.removeprefix("models/") if isinstance(name, str) else ""
            label = entry.get("displayName")
        if valid_model_id(model):
            models.setdefault(model, ModelOption(model, label if isinstance(label, str) and label else model))
    return list(models.values())
