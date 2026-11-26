"""编程 CLI 的检测、请求参数与整稿结果格式。正文通过标准输入传输。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .model_catalog import EFFORT_LABELS, PI_EFFORT, VARIANT_ID, ModelOption, valid_model_id


@dataclass(frozen=True)
class CLI:
    label: str
    binaries: tuple[str, ...]
    login: str
    args: tuple[str, ...] = ()
    help_args: tuple[str, ...] = ("--help",)
    flags: tuple[str, ...] = ()
    auth: tuple[str, ...] = ()
    catalog: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    effort_flag: str = ""
    basic_catalog: tuple[str, ...] = ()


AGY_SETTINGS = json.dumps({
    "agentMode": "", "toolPermission": "strict", "allowNonWorkspaceAccess": False,
    "historySize": 0,
    "permissions": {"deny": [f"{action}(*)" for action in
                             ("command", "read_file", "write_file", "read_url", "mcp")]},
})
CURSOR_CONFIG = json.dumps({
    "permissions": {"deny": ["Shell(*)", "Read(**)", "Write(**)", "WebFetch(*)", "Mcp(*:*)"]},
    "sandbox": {"readBoundary": "workspace"},
})
OPENCODE_CONFIG = json.dumps({
    "autoupdate": False, "share": "disabled", "permission": {"*": "deny"},
    "policies": [{"action": action, "resource": "*", "effect": "deny"}
                 for action in ("tool.use", "mcp.use", "plugin.use", "skill.use")],
})
# OpenCode 1.x rejects the v2-only policies; 2.x uses policies to lock down the run.
OPENCODE_V1_CONFIG = json.dumps({"autoupdate": False, "share": "disabled", "permission": {"*": "deny"}})

CLIS = {
    "codex": CLI("Codex", ("codex",), "codex login"),
    "claude": CLI("Claude Code", ("claude",), "claude auth login", effort_flag="--effort"),
    "antigravity": CLI("Antigravity", ("agy",), "agy",
                       ("--settings", AGY_SETTINGS, "--input-format", "stream-json",
                        "--output-format", "stream-json", "--disable-slash-commands"),
                       help_args=("--settings", AGY_SETTINGS, "--help"),
                       flags=("--input-format", "--model", "--disable-slash-commands"),
                       catalog=("models",), effort_flag="--effort"),
    "cursor-agent": CLI("Cursor Agent", ("cursor-agent", "agent"), "agent login",
                        ("--print", "--mode", "ask", "--output-format", "json"),
                        flags=("--mode", "--output-format"), auth=("status",), catalog=("models",),
                        files=((".cursor/cli.json", CURSOR_CONFIG),)),
    "opencode": CLI("OpenCode", ("opencode",), "opencode auth login", ("run", "--format", "json"),
                    help_args=("run", "--help"), flags=("--format", "--model"), catalog=("models", "--verbose"),
                    basic_catalog=("models",), effort_flag="--variant"),
    "copilot": CLI("GitHub Copilot CLI", ("copilot",), "copilot（输入 /login）",
                   ("--output-format", "json", "--no-auto-update", "--disable-builtin-mcps",
                    "--disable-mcp-server", "computer-use", "--no-custom-instructions",
                    "--available-tools", "report_intent"),
                   flags=("--output-format", "--available-tools", "--disable-builtin-mcps", "--no-auto-update",
                          "--disable-mcp-server", "--no-custom-instructions")),
    "qwen-code": CLI("Qwen Code", ("qwen",), "qwen（选择登录方式）",
                     ("--safe-mode", "--max-tool-calls", "0", "--output-format", "json"),
                     flags=("--safe-mode", "--max-tool-calls", "--output-format")),
    "pi": CLI("Pi", ("pi",), "pi（输入 /login）",
              ("--print", "--mode", "json", "--no-tools", "--no-extensions", "--no-skills",
               "--no-prompt-templates", "--no-context-files", "--no-session"),
              flags=("--no-tools", "--no-extensions", "--no-context-files", "--no-session"),
              catalog=("--list-models",), effort_flag="--thinking"),
}

LOCAL_PROVIDERS = {name: cli.label for name, cli in CLIS.items()}

_NODE_SHIM = re.compile(r'"%_prog%"\s+"%dp0%\\([^"%\r\n]+)"\s+%\*', re.IGNORECASE)
_SAFE_BATCH_ARG = re.compile(r"[A-Za-z0-9._:/+*@=-]+")


def windows_cli_command(executable: str, args: list[str], path: str) -> list[str]:
    """Resolve standard npm shims without interpreting arguments in cmd.exe."""
    shim = Path(executable)
    if shim.suffix.lower() in {".exe", ".com"}:
        return [executable, *args]
    if shim.suffix.lower() not in {".bat", ".cmd"}:
        raise ValueError("unsupported Windows CLI")
    match = _NODE_SHIM.search(shim.read_text(encoding="utf-8-sig", errors="replace"))
    if match:
        script = shim.parent / Path(*match[1].split("\\"))
        bundled_node = shim.with_name("node.exe")
        node = str(bundled_node) if bundled_node.is_file() else shutil.which("node.exe", path=path)
        if node and script.is_file():
            return [node, str(script), *args]
        raise ValueError("Windows npm shim target is unavailable")
    # The remaining vendor batch launchers may only receive plain argument tokens.
    if any(character in executable for character in '%!^"\r\n') or not all(
        _SAFE_BATCH_ARG.fullmatch(arg) for arg in args
    ):
        raise ValueError("unsupported Windows batch arguments")
    return [executable, *args]


def windows_search_path() -> str:
    home = Path.home()
    app_data = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local_data = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    directories = (local_data / "agy/bin", local_data / "Microsoft/WinGet/Links",
                   local_data / "Microsoft/WindowsApps", app_data / "npm",
                   local_data / "pnpm", home / ".local/bin", home / ".claude/bin",
                   home / ".opencode/bin", home / ".bun/bin", home / "scoop/shims")
    inherited = (entry.strip('"') for entry in os.environ.get("PATH", "").split(os.pathsep))
    bundled = getattr(sys, "_MEIPASS", None)
    return os.pathsep.join(dict.fromkeys(
        os.path.normpath(entry) for entry in (*inherited, *directories) if os.path.isabs(entry)
        and (not bundled or not Path(entry).is_relative_to(bundled))
    ))


def windows_executable(names: tuple[str, ...]) -> str:
    directories = windows_search_path().split(os.pathsep)
    for name in names:
        for directory in directories:
            for suffix in (".exe", ".com", ".cmd", ".bat"):
                candidate = Path(directory) / (name + suffix)
                if candidate.is_file():
                    return str(candidate)
    return ""


def model_options(provider: str, stdout: str) -> list[ModelOption]:
    choices = {}
    lines = re.sub(r"\x1b\[[0-9;]*m", "", stdout).splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        efforts = ()
        if provider == "pi":
            parts = line.split()
            # Pi prints provider, model, context, maximum output, thinking, and images.
            if len(parts) < 6 or parts[-1] not in {"yes", "no", "true", "false"}:
                continue
            identifier = f"{parts[0]}/{parts[1]}"
            label = identifier
            if parts[-2] in {"yes", "true"}:
                efforts = PI_EFFORT
        elif provider == "opencode":
            identifier = label = line
            if "/" not in identifier or not valid_model_id(identifier):
                continue
            if index + 1 < len(lines) and lines[index + 1].lstrip().startswith("{"):
                # JSON metadata belongs to the immediately preceding model.
                end = next((i for i in range(index + 1, len(lines))
                            if lines[i] and not lines[i].startswith((" ", "\t", "{", "}"))), len(lines))
                try:
                    metadata, _ = json.JSONDecoder().raw_decode("\n".join(lines[index + 1:end]).lstrip())
                except ValueError:
                    metadata = {}
                variants = metadata.get("variants") if isinstance(metadata, dict) else None
                if isinstance(variants, dict):
                    efforts = tuple(name for name in variants if VARIANT_ID.fullmatch(name))
        else:
            identifier, _, label = line.partition(" - ")
            identifier = identifier.lstrip("-• ").rstrip()
            if provider == "antigravity":
                match = re.fullmatch(r"([a-z][a-z0-9-]+)\s{2,}(.+)", line)
                if match:
                    identifier, label = match.groups()
                if "-" not in identifier:
                    continue
            label = label.strip() or identifier
        if valid_model_id(identifier) and identifier.lower() not in {"default", "models", "model", "available"}:
            choices.setdefault(identifier, ModelOption(identifier, label, efforts))
    if provider == "antigravity":
        for identifier, option in list(choices.items()):
            levels = tuple(level for level in ("low", "medium", "high", "xhigh")
                           if f"{identifier}-{level}" in choices)
            if levels:
                choices[identifier] = ModelOption(identifier, option.label, levels)
    return list(choices.values())


def qwen_models() -> list[ModelOption]:
    path = Path(os.environ.get("QWEN_SETTINGS_FILE") or "~/.qwen/settings.json").expanduser()
    try:
        with path.open("rb") as stream:
            data = json.loads(stream.read(1024 * 1024))
    except (ValueError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    providers = data.get("modelProviders")
    configured = data.get("model")
    candidates = []
    if isinstance(providers, dict):
        for models in providers.values():
            if isinstance(models, list):
                candidates.extend(row.get("id") for row in models if isinstance(row, dict))
    candidates.append((configured.get("id") or configured.get("name")) if isinstance(configured, dict) else configured)
    return [ModelOption(model, model) for model in dict.fromkeys(model for model in candidates if valid_model_id(model))]


def request_text(provider: str, prompt: str) -> str:
    if provider == "antigravity":
        return json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
    return prompt


def events(stdout: str) -> list[dict]:
    """接受单个 JSON 对象、JSON 数组或每行一个事件。"""
    try:
        value = json.loads(stdout)
        records = value if isinstance(value, list) else [value]
    except ValueError:
        records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("invalid CLI events")
    return records


def result(provider: str, stdout: str) -> str:
    records = events(stdout)
    if provider == "antigravity":
        final = next(event["result"] for event in reversed(records) if event.get("event") == "result")
        if final["status"] != "SUCCESS":
            raise ValueError(final.get("error", ""))
        return final["response"]
    if provider in {"cursor-agent", "qwen-code"}:
        final = next(e for e in reversed(records) if e.get("type") == "result")
        if final.get("subtype") != "success" or final.get("is_error") or final.get("stop_reason") == "max_tokens":
            raise ValueError(final.get("result", ""))
        return final["result"]
    if provider == "opencode":
        finish = next(e for e in reversed(records) if e.get("type") in {"step_finish", "error"})
        if finish["type"] == "error" or finish["part"]["reason"] not in {"stop", "end_turn"}:
            raise ValueError(str(finish))
        return "".join(e["part"]["text"] for e in records if e.get("type") == "text")
    if provider == "pi":
        end = next(e for e in reversed(records) if e.get("type") == "agent_end" and not e.get("willRetry"))
        final = next(message for message in reversed(end["messages"]) if message.get("role") == "assistant")
        if final["stopReason"] != "stop":
            raise ValueError(final.get("errorMessage", ""))
        return "".join(block["text"] for block in final["content"] if block.get("type") == "text")
    if provider == "copilot":
        final = next(e for e in reversed(records) if e.get("type") == "result")
        if final.get("success") is False or not (final.get("success") or final.get("exitCode") == 0):
            raise ValueError(str(final))
        reply = next((e["data"]["content"] for e in reversed(records) if e.get("type") == "assistant.message"
                      and not e["data"].get("toolRequests") and e["data"].get("content")), "")
        return reply or "".join(e["data"]["deltaContent"] for e in records
                                if e.get("type") == "assistant.message_delta")
    raise ValueError("unknown CLI")
