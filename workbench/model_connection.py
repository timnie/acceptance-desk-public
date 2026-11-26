"""本机模型连接。对话内容经 stdin 或 HTTPS 传递，错误使用本地文案。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from .model_catalog import (API_PRESETS, API_PROTOCOLS, EFFORT_LABELS, PI_EFFORT, TIER_LABELS, VARIANT_ID,
                            ModelOption, api_efforts, api_models, claude_efforts, claude_models, effort_label,
                            claude_routes, codex_models, valid_model_id)

from . import model_cli
from .model_cli import CLIS, LOCAL_PROVIDERS
API_PROVIDERS = {provider: preset[0] for provider, preset in API_PRESETS.items()}
CODEX_OVERRIDES = ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_ACCESS_TOKEN")
MAX_REPLY = 4 * 1024 * 1024
TEST_PROMPT = "连接测试。请只回复 OK。"
CODEX_EXIT_GRACE = 2


class ConnectionError(ValueError):
    """可直接显示给用户的连接错误。"""


def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Connection:
    provider: str
    model: str = ""
    base_url: str = ""
    key: str = field(default="", repr=False)
    executable: str = ""
    protocol: str = ""
    effort: str = ""
    model_label: str = ""
    route_env: dict = field(default_factory=dict, repr=False, compare=False)
    tier: str = ""

    @property
    def label(self) -> str:
        return LOCAL_PROVIDERS.get(self.provider) or API_PROVIDERS[self.provider]

    @property
    def destination(self) -> str:
        model = self.model_label or self.model or "CLI 默认模型"
        if self.model_label and self.model_label != self.model:
            model = f"{self.model_label}（{self.model}）"
        if self.effort:
            model += f" · 推理强度 {effort_label(self.effort)}"
        if self.tier:
            model += f" · 服务档位 {TIER_LABELS[self.tier]}"
        if self.provider in API_PROVIDERS:
            return f"{self.label} · {model} · {self.base_url}"
        identity = "本机 ChatGPT 账户" if self.provider == "codex" else "本机配置"
        route = self.route_env.get("ANTHROPIC_BASE_URL")
        return f"{self.label} · {identity} · {model}" + (f" · {route}" if route else "")

    @classmethod
    def api(cls, provider: str, model: str, base_url: str, key: str, *, protocol: str = "",
            allow_missing_model: bool = False, label: str = "", effort: str = "") -> Connection:
        model, key = model.strip(), key.strip()
        _, preset_protocol, _, _ = API_PRESETS[provider]
        protocol = (protocol or preset_protocol) if provider == "compatible" else preset_protocol
        if protocol not in API_PROTOCOLS:
            raise ConnectionError("请选择供应商的接口协议。")
        address = validate_api_url(base_url)
        if protocol == "azure" and not address.endswith("/openai/v1"):
            raise ConnectionError("Azure API 根地址应以 /openai/v1 结尾。")
        if protocol == "bedrock" and not re.fullmatch(r"https://bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?", address):
            raise ConnectionError("请填写所在地的 Bedrock Runtime HTTPS 根地址。")
        keyless = loopback(urlsplit(address).hostname or "") and protocol == "chat"
        if (not key and not keyless) or (key and (not key.isascii() or any(not 33 <= ord(c) <= 126 for c in key))):
            raise ConnectionError("请填写有效的 API Key。")
        if not (allow_missing_model and not model) and not valid_model_id(model):
            raise ConnectionError("请填写供应商提供的模型 ID。")
        if effort and effort not in api_efforts(provider, model, protocol):
            raise ConnectionError("请选择此模型支持的推理强度。")
        return cls(provider, model, address, key, protocol=protocol, model_label=label, effort=effort)

    async def models(self) -> tuple[list[ModelOption], bool]:
        if self.provider == "claude":
            return claude_models(), False
        if self.provider == "codex":
            for args, live in ((["debug", "models"], True), (["debug", "models", "--bundled"], False)):
                try:
                    code, stdout, _ = await _process(self.executable, args, timeout=6, omit_env=CODEX_OVERRIDES)
                    choices = codex_models(json.loads(stdout)) if code == 0 else []
                except (ConnectionError, ValueError):
                    choices = []
                if choices:
                    return choices, live
            return [], False
        if self.provider == "qwen-code":
            return model_cli.qwen_models(), False
        if self.provider in CLIS:
            catalog = CLIS[self.provider].catalog
            if not catalog:
                return [], False
            for args in (catalog, CLIS[self.provider].basic_catalog):
                if args:
                    code, stdout, _ = await _process(self.executable, list(args), timeout=12)
                    choices = model_cli.model_options(self.provider, stdout) if code == 0 else []
                    if choices:
                        return choices, True
            return [], False
        if self.protocol == "bedrock":
            return [], False
        choices = {}
        path = "/models"
        seen = set()
        for _ in range(8):
            data = await _api_json(self, "GET", path, 15)
            for option in api_models(data, self.protocol):
                choices.setdefault(option.id, option)
            if self.protocol == "gemini" and isinstance(data.get("nextPageToken"), str) and data["nextPageToken"]:
                path = "/models?pageToken=" + quote(data["nextPageToken"], safe="")
            elif self.protocol == "anthropic" and data.get("has_more") is True and isinstance(data.get("last_id"), str):
                path = "/models?after_id=" + quote(data["last_id"], safe="")
            else:
                break
            if path in seen:
                break
            seen.add(path)
        if not choices:
            raise ConnectionError("供应商没有返回可选模型。可以手动填写模型 ID。")
        return list(choices.values()), True

    def with_cli_model(self, model: str, label: str, effort: str, tier: str = "") -> Connection:
        if model and not valid_model_id(model):
            raise ConnectionError("请输入有效的模型 ID。")
        allowed = {"codex": EFFORT_LABELS.keys() - {"off"}, "pi": PI_EFFORT,
                   "claude": claude_efforts(model, aliases=True), "antigravity": ("low", "medium", "high", "xhigh")}
        if effort and (not model or not (VARIANT_ID.fullmatch(effort) if self.provider == "opencode"
                                         else effort in allowed.get(self.provider, ()))):
            raise ConnectionError("请选择此模型支持的推理强度。")
        if tier and (self.provider != "codex" or not model or tier not in TIER_LABELS):
            raise ConnectionError("请选择此模型支持的服务档位。")
        route_env = {}
        if self.provider == "claude" and model and (route := claude_routes().get(model)):
            route_env["ANTHROPIC_BASE_URL"] = validate_api_url(route["anthropic_base_url"])
            token = route.get("api_key")
            if isinstance(token, str) and token.strip():
                route_env["ANTHROPIC_AUTH_TOKEN"] = token.strip()
        return Connection(self.provider, model, executable=self.executable, effort=effort,
                          model_label=label, route_env=route_env, tier=tier)

    @classmethod
    def local(cls, provider: str) -> Connection:
        if os.name == "nt":
            binary = model_cli.windows_executable(CLIS[provider].binaries)
            if binary:
                return cls(provider, executable=binary)
            raise ConnectionError(f"未找到 {LOCAL_PROVIDERS[provider]}。安装后重新检测。")
        home = Path.home()
        directories = (home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                       home / ".opencode/bin", home / ".volta/bin", home / ".bun/bin", home / ".npm-global/bin",
                       home / ".local/share/mise/shims", home / ".local/share/pnpm", home / "Library/pnpm")
        binary = ""
        for name in CLIS[provider].binaries:
            paths = [directory / name for directory in directories]
            if provider == "codex":
                cli = "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
                paths.extend((Path("/Applications") / cli, home / "Applications" / cli))
            binary = shutil.which(name) or next((str(p) for p in paths if p.is_file() and os.access(p, os.X_OK)), "")
            if binary:
                break
        if not binary:
            raise ConnectionError(f"未找到 {LOCAL_PROVIDERS[provider]}。安装后重新检测。")
        return cls(provider, executable=os.path.abspath(binary))

    async def check_login(self) -> str:
        if self.provider not in {"codex", "claude"}:
            cli = CLIS[self.provider]
            await self._cli_help()
            if not cli.auth:
                return "已检测到 CLI。可用「测试连接」核对登录与模型。"
            code, stdout, stderr = await _process(self.executable, list(cli.auth), timeout=10)
            if code or re.search(r"not logged in|not authenticated|not signed in", stdout + stderr, re.I):
                raise ConnectionError(f"请在终端运行 {cli.login}，再重新检测。")
            return "CLI 已登录，使用它当前配置的账户或供应商。"
        args = ["login", "status"] if self.provider == "codex" else ["auth", "status"]
        code, stdout, stderr = await _process(self.executable, args, timeout=10, extra_env=self.route_env,
                                              omit_env=CODEX_OVERRIDES if self.provider == "codex" else ())
        if code:
            command = "codex login" if self.provider == "codex" else "claude auth login"
            raise ConnectionError(f"CLI 已安装。请在终端运行 {command} 登录，然后重新检测。")
        status = stdout + stderr
        if self.provider == "codex":
            if "logged in using chatgpt" in status.lower():
                return "已登录 ChatGPT，可用账户当前的 Codex 权益。"
            raise ConnectionError("Codex 当前未使用 ChatGPT 登录。请在终端切换到 ChatGPT 登录，或选择 API Key 连接。")
        if self.provider == "claude":
            try:
                details = json.loads(stdout)
            except ValueError:
                details = {}
            if isinstance(details, dict) and details.get("authMethod") == "claude.ai":
                return "已登录 Claude，可用账户当前的 Claude Code 权益。"
        return "CLI 已登录，使用它当前配置的账户或供应商。"

    async def _cli_help(self) -> str:
        cli = CLIS[self.provider]
        code, stdout, stderr = await _process(self.executable, list(cli.help_args), timeout=8)
        usage = stdout + stderr
        if code or not all(re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", usage) for flag in cli.flags):
            raise ConnectionError(f"当前 {cli.label} 版本缺少工作台需要的选项。请在终端核对版本。")
        return usage

    async def _check_model_options(self) -> None:
        if (not self.effort and not self.tier) or self.provider == "claude":
            return
        options, _ = await self.models()
        current = next((item for item in options if item.id == self.model), None)
        if current is None or (self.effort and self.effort not in current.efforts) or (
            self.tier and self.tier not in current.tiers
        ):
            raise ConnectionError("当前模型目录不支持所选档位。请重新加载模型并核对档位。")

    def _append_cli_effort(self, args: list[str], help_text: str) -> None:
        if not self.effort:
            return
        flag = CLIS[self.provider].effort_flag
        if not flag or not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text):
            raise ConnectionError("当前 CLI 版本不支持设置推理强度。请用模型默认档位重试。")
        args.extend((flag, self.effort))

    async def _generate_cli(self, prompt: str, timeout: int) -> str:
        cli = CLIS[self.provider]
        usage = await self._cli_help()
        args = list(cli.args)
        self._append_cli_effort(args, usage)
        env = {}
        if self.provider == "opencode":
            v2 = "--standalone" in usage
            if v2:
                args.extend(("--standalone", "--archived"))
            env = {"OPENCODE_CONFIG_CONTENT": model_cli.OPENCODE_CONFIG if v2 else model_cli.OPENCODE_V1_CONFIG,
                   "OPENCODE_PERMISSION": '{"*":"deny"}', "OPENCODE_DISABLE_EXTERNAL_PLUGINS": "1",
                   "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                   "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_AUTOCOMPACT": "1"}
        if self.model:
            args.extend(("--model", self.model))
        code, stdout, stderr = await _process(self.executable, args, prompt=model_cli.request_text(self.provider, prompt),
                                              timeout=timeout, extra_env=env, files=cli.files)
        if code:
            raise _cli_failure(stderr + "\n" + stdout[:4000])
        try:
            result = model_cli.result(self.provider, stdout)
        except (ValueError, TypeError, KeyError, StopIteration, AttributeError) as error:
            raise _cli_failure(str(error) + "\n" + stderr) from None
        return _nonempty(result)

    @property
    def generation_timeout(self) -> int:
        return 600 if self.provider == "codex" and os.name == "nt" else 180

    async def generate(self, prompt: str, *, timeout: int | None = None) -> str:
        if timeout is None:
            timeout = self.generation_timeout
        if self.provider in API_PROVIDERS:
            return await _request(self, prompt, timeout)
        await self._check_model_options()
        if self.provider not in {"codex", "claude"}:
            return await self._generate_cli(prompt, timeout)
        if self.provider == "codex":
            await self.check_login()
            args = ["-a", "never", "exec", "--sandbox", "read-only", "--ephemeral", "--ignore-user-config",
                    "--ignore-rules", "--skip-git-repo-check", "-c", 'cli_auth_credentials_store="auto"',
                    "-c", 'web_search="disabled"', "-c", 'shell_environment_policy.inherit="none"',
                    "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "apps",
                    "--disable", "hooks", "--json"]
            if self.effort:
                args.extend(("-c", f'model_reasoning_effort="{self.effort}"'))
            if self.tier:
                args.extend(("-c", f'service_tier="{self.tier}"'))
        else:
            args = ["-p", "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*",
                    "--no-session-persistence", "--output-format", "json"]
            if self.effort:
                _, stdout, stderr = await _process(self.executable, ["--help"], timeout=8)
                self._append_cli_effort(args, stdout + stderr)
        if self.model:
            args.extend(("--model", self.model))
        if self.provider == "codex":
            args.append("-")
        code, stdout, stderr = await _process(self.executable, args, prompt=prompt, timeout=timeout,
                                              omit_env=CODEX_OVERRIDES if self.provider == "codex" else (),
                                              extra_env=self.route_env,
                                              finish_on_codex_turn=self.provider == "codex" and os.name == "nt")
        if code:
            raise _cli_failure(stderr + "\n" + stdout[:2000])
        return _codex_result(stdout) if self.provider == "codex" else _claude_result(stdout)


async def _process(executable: str, args: list[str], *, prompt: str = "", timeout: float,
                   omit_env: tuple[str, ...] = (), extra_env: dict | None = None,
                   files: tuple[tuple[str, str], ...] = (),
                   finish_on_codex_turn: bool = False) -> tuple[int, str, str]:
    with TemporaryDirectory(prefix="acceptance-desk-model-") as cwd:
        for name, content in files:
            path = Path(cwd) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        windows = os.name == "nt"
        env = {key: value for key, value in os.environ.items() if (key.upper() if windows else key) not in omit_env}
        env.update(extra_env or {})
        if windows:
            env["PATH"] = os.pathsep.join((os.path.dirname(executable), model_cli.windows_search_path()))
            process_options = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            env["PATH"] = os.pathsep.join([os.path.dirname(executable), "/opt/homebrew/bin", "/usr/local/bin",
                                         env.get("PATH", os.defpath)])
            process_options = {"start_new_session": True}
        try:
            command = model_cli.windows_cli_command(executable, args, env["PATH"]) if windows else [
                executable, *args,
            ]
            process = await asyncio.create_subprocess_exec(*command, cwd=cwd, env=env,
                                                           stdin=asyncio.subprocess.PIPE,
                                                           stdout=asyncio.subprocess.PIPE,
                                                           stderr=asyncio.subprocess.PIPE,
                                                           **process_options)
        except (OSError, ValueError):
            raise ConnectionError("无法启动本地 CLI。请确认它仍可在终端运行。") from None

        completed = asyncio.get_running_loop().create_future() if finish_on_codex_turn else None

        async def read(stream, *, keep: bool = True, completion=None):
            data = bytearray()
            line_start = 0
            scanned = 0
            while chunk := await stream.read(65536):
                if keep:
                    data.extend(chunk)
                    if len(data) > MAX_REPLY:
                        raise ConnectionError("模型返回内容过长。请缩小文稿范围后重试。")
                if completion is not None and not completion.done():
                    while (end := data.find(b"\n", scanned)) != -1:
                        line = data[line_start:end]
                        line_start = scanned = end + 1
                        if _codex_turn_completed(line):
                            completion.set_result(data[:line_start].decode("utf-8", errors="replace"))
                            break
                    scanned = len(data)
            return data.decode("utf-8", errors="replace")

        async def send():
            try:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [asyncio.create_task(operation) for operation in
                 (read(process.stdout, completion=completed), read(process.stderr), send(), process.wait())]
        pipes = asyncio.gather(*tasks)
        finished = False
        try:
            if completed is not None:
                ready, _ = await asyncio.wait((pipes, completed), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not ready:
                    raise asyncio.TimeoutError
                if pipes not in ready:
                    # Windows Codex can leave its pipes open after reporting a completed turn.
                    await asyncio.wait((pipes,), timeout=CODEX_EXIT_GRACE)
                    if not pipes.done():
                        return 0, completed.result(), ""
            stdout, stderr, _, code = await asyncio.wait_for(pipes, timeout)
            finished = True
            return code, stdout, stderr
        except asyncio.TimeoutError:
            raise ConnectionError("等待模型响应超时。") from None
        finally:
            if not finished:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    elif process.returncode is None:
                        process.kill()
                except ProcessLookupError:
                    pass
                for task in tasks:
                    task.cancel()
                await asyncio.gather(pipes, return_exceptions=True)
                await asyncio.gather(*tasks, return_exceptions=True)
                # Drain the pipes after termination, even if a reader hit the output limit.
                try:
                    await asyncio.wait_for(asyncio.gather(read(process.stdout, keep=False),
                                                         read(process.stderr, keep=False), process.wait()), 3)
                except asyncio.TimeoutError:
                    pass


def _codex_turn_completed(line: bytes) -> bool:
    if b'"turn.completed"' not in line:
        return False
    try:
        event = json.loads(line)
    except ValueError:
        return False
    return isinstance(event, dict) and event.get("type") == "turn.completed"


def _cli_failure(message: str) -> ConnectionError:
    if re.search(r"insufficient.quota|usage.limit|rate.limit|\b429\b", message, re.I):
        return ConnectionError("模型额度不足或暂时限流。请检查 CLI 账户用量。")
    if re.search(r"not logged in|authentication|unauthorized|\b401\b", message, re.I):
        return ConnectionError("CLI 认证失败。请在终端重新登录后重试。")
    if re.search(r"unexpected argument|unknown option|unrecognized option", message, re.I):
        return ConnectionError("当前 CLI 版本不支持工作台需要的执行选项。")
    if re.search(r"model.{0,80}(not supported|unavailable|not found|does not exist)|invalid model", message, re.I):
        return ConnectionError("所选模型当前不可用，请在模型连接中重新选择。")
    return ConnectionError("本地 CLI 执行失败。请在终端检查账户、CLI 版本和网络。")


def _codex_result(stdout: str) -> str:
    result = ""
    completed = False
    try:
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event["type"]
            if kind == "turn.failed":
                raise _cli_failure(str(event.get("error", "")))
            if kind == "turn.completed":
                completed = True
            if kind == "item.completed" and event["item"]["type"] == "agent_message":
                result = event["item"]["text"]
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError("无法读取 Codex 返回的结果。") from None
    if not completed:
        raise ConnectionError("Codex 没有完成本次生成。")
    return _nonempty(result)


def _claude_result(stdout: str) -> str:
    try:
        result = json.loads(stdout)
        if result.get("is_error"):
            raise _cli_failure(result.get("result") or "")
        if result["subtype"] != "success" or result.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
            raise ConnectionError("Claude Code 没有返回完整文稿。")
        return _nonempty(result["result"])
    except (AttributeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError("无法读取 Claude Code 返回的结果。") from None


def _nonempty(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ConnectionError("模型返回了空内容，请重试。")
    return text


def validate_api_url(address: str) -> str:
    address = address.strip()
    try:
        parts = urlsplit(address)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        raise ConnectionError("请填写有效的 API 地址。") from None
    if (not hostname or any(c.isspace() or ord(c) < 32 for c in address)
            or not (parts.scheme == "https" or parts.scheme == "http" and loopback(hostname))
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or port == 0 or parts.path.rstrip("/").endswith(("/chat/completions", "/models", "/responses", "/messages"))):
        raise ConnectionError("请输入 API 根地址。远程服务使用 HTTPS，地址末尾可包含 /v1。")
    return urlunsplit(parts).rstrip("/")


def _api_headers(connection: Connection) -> dict:
    key = connection.key
    if connection.protocol == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if connection.protocol == "gemini":
        return {"x-goog-api-key": key}
    if connection.protocol == "azure":
        return {"api-key": key}
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _api_json(connection: Connection, method: str, path: str, timeout: int, body: dict | None = None) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=min(15, timeout))) as client:
            async with client.request(method, connection.base_url + path, json=body, headers=_api_headers(connection),
                                      allow_redirects=False) as response:
                errors = {401: "API Key 无效或已过期。", 402: "账户余额不足。",
                          403: "这个账户无权调用此接口或所选模型。", 404: "未找到接口或模型。请核对 API 地址和模型 ID。",
                          429: "额度不足或暂时限流。请检查供应商账户用量。"}
                if response.status != 200:
                    raise ConnectionError(errors.get(response.status, f"供应商返回 HTTP {response.status}。"))
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_REPLY:
                        raise ConnectionError("供应商返回内容过长。")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except asyncio.TimeoutError:
        raise ConnectionError("等待供应商响应超时。") from None
    except aiohttp.ClientError:
        raise ConnectionError("无法连接到供应商。请检查网络和 API 地址。") from None
    except ValueError as exc:
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError("供应商返回了无法识别的内容。") from None


async def _request(connection: Connection, prompt: str, timeout: int) -> str:
    messages = [{"role": "user", "content": prompt}]
    model, protocol = connection.model, connection.protocol
    if protocol == "anthropic":
        path, body = "/messages", {"model": model, "messages": messages, "max_tokens": 16384}
    elif protocol == "gemini":
        path = f"/models/{quote(model, safe='')}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    elif protocol == "bedrock":
        path = f"/model/{quote(model, safe='')}/converse"
        body = {"messages": [{"role": "user", "content": [{"text": prompt}]}]}
    elif protocol == "responses":
        path, body = "/responses", {"model": model, "input": prompt, "store": False}
    else:
        path, body = "/chat/completions", {"model": model, "messages": messages, "stream": False}
    effort = connection.effort
    if effort:
        if protocol == "responses":
            body["reasoning"] = {"effort": effort}
        elif protocol == "gemini":
            body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": effort.upper()}}
        elif protocol == "anthropic":
            body["thinking"] = {"type": "disabled" if effort == "none" else
                                "enabled" if connection.provider == "deepseek_anthropic" else "adaptive"}
            if effort != "none":
                body["output_config"] = {"effort": effort}
        elif protocol == "chat" and connection.provider == "deepseek":
            body["reasoning_effort"] = effort
    response = await _api_json(connection, "POST", path, timeout, body)
    try:
        if protocol == "anthropic":
            complete = response["stop_reason"] == "end_turn"
            text = "".join(block["text"] for block in response["content"] if block.get("type") == "text")
        elif protocol == "gemini":
            candidate = response["candidates"][0]
            complete = candidate["finishReason"] == "STOP"
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"] if not part.get("thought"))
        elif protocol == "bedrock":
            complete = response["stopReason"] == "end_turn"
            text = "".join(block["text"] for block in response["output"]["message"]["content"] if "text" in block)
        elif protocol == "responses":
            complete = response["status"] == "completed"
            text = "".join(part["text"] for message in response["output"] if message.get("type") == "message"
                           for part in message["content"] if part.get("type") == "output_text")
        else:
            choice = response["choices"][0]
            complete, text = choice.get("finish_reason") == "stop", choice["message"]["content"]
    except (AttributeError, KeyError, IndexError, TypeError):
        raise ConnectionError("供应商没有返回可读的正文。请核对模型和接口协议。") from None
    if not complete:
        raise ConnectionError("模型没有返回完整文稿。请核对模型限制或缩小文稿范围。")
    return _nonempty(text)
