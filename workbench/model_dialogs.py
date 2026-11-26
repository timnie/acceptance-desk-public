"""模型设置、发送预览和候选稿预览复用工作台现有的弹窗与按钮。"""

from __future__ import annotations

import asyncio
import html
from dataclasses import replace

from nicegui import run, ui

from acceptance_desk.feedback import SCOPE_LABELS

from .controls import SearchPicker, button, disclosure, focus_in, segmented, tag
from .downloads import download_content
from .model_catalog import API_PRESETS, API_PROTOCOLS, TIER_LABELS, api_efforts, claude_models, effort_label
from .model_cli import CLIS
from .model_connection import API_PROVIDERS, LOCAL_PROVIDERS, TEST_PROMPT, Connection, ConnectionError, validate_api_url
from .next_round import (DEFAULT_REVISION_INSTRUCTIONS, LAYOUT_OPTIONS, MAX_REVISION_INSTRUCTIONS, PENDING_VERSIONS,
                         CandidateCheck, RevisionRequest, generate_revision)
from .revision_references import REVISION_REFERENCES
from .view import number

PREFERENCES_KEY = "workbench_model_preferences"
CUSTOM_MODEL = "*"


class ModelDialogs:
    def __init__(self, storage=None) -> None:
        self.storage = storage if storage is not None else {}
        saved = self.storage.get(PREFERENCES_KEY)
        saved = saved if isinstance(saved, dict) else {}
        choices = saved.get("providers")
        self.preferences = dict(choices) if isinstance(choices, dict) else {}
        selected = saved.get("selected")
        self.selected = selected if isinstance(selected, str) else None
        self.connections: dict[str, Connection] = {}
        self._api_keys: dict[tuple[str, str, str], str] = {}
        self.task: asyncio.Task | None = None
        self.catalogs = {}
        self.failures = {}
        self.verified: dict[str, Connection] = {}
        tuning = saved.get("tuning")
        self.tuning = dict(tuning) if isinstance(tuning, dict) else {}
        if self.selected in LOCAL_PROVIDERS:
            previous = self.preference(self.selected)
            try:
                connection = Connection.local(self.selected).with_cli_model(
                    previous.get("model", ""), previous.get("label", ""), previous.get("effort", ""), previous.get("tier", ""))
                self.connections[self.selected] = connection
            except ConnectionError:
                pass

    @property
    def connection(self) -> Connection | None:
        return self.connections.get(self.selected)

    def preference(self, provider: str) -> dict:
        choice = self.preferences.get(provider)
        return {k: v for k, v in choice.items() if isinstance(v, str)} if isinstance(choice, dict) else {}

    @staticmethod
    def route_key(provider: str, model: str, protocol: str = "", address: str = "") -> str:
        return "|".join((provider, protocol, address.rstrip("/"), model))

    def _remember_key(self, connection: Connection) -> None:
        if connection.provider in API_PROVIDERS and connection.key:
            self._api_keys[connection.provider, connection.protocol, connection.base_url] = connection.key

    def api_key(self, provider: str, protocol: str, address: str) -> str:
        try:
            destination = validate_api_url(address)
        except ConnectionError:
            return ""
        return self._api_keys.get((provider, protocol, destination), "")

    def use(self, connection: Connection) -> None:
        self._remember_key(connection)
        self.selected = connection.provider
        self.connections[self.selected] = connection
        self.preferences[self.selected] = {"model": connection.model, "label": connection.model_label,
                                           "effort": connection.effort, "tier": connection.tier, "base_url": connection.base_url,
                                           "protocol": connection.protocol}
        route = self.route_key(connection.provider, connection.model, connection.protocol, connection.base_url)
        self.tuning[route] = {"effort": connection.effort, "tier": connection.tier}
        self.storage[PREFERENCES_KEY] = {"selected": self.selected, "providers": dict(self.preferences),
                                         "tuning": dict(self.tuning)}

    def close(self) -> None:
        if self.task is not None:
            self.task.cancel()

    def dispose(self) -> None:
        self.close()
        self.connections.clear()
        self._api_keys.clear()
        self.verified.clear()
        self.catalogs.clear()

    def settings(self, on_selected=None, *, focus_back=None, panel=None, go_back=None, local_review: str = "",
                 navigation=()) -> None:
        original = self.connection
        local_provider = self.selected if self.selected in LOCAL_PROVIDERS else "codex"
        api_provider = self.selected if self.selected in API_PROVIDERS else "deepseek"
        tab = "api" if self.selected in API_PROVIDERS else "local"
        installed = {}

        def rescan() -> None:
            for name in LOCAL_PROVIDERS:
                try:
                    installed[name] = Connection.local(name)
                except ConnectionError:
                    installed[name] = None

        rescan()
        busy = False

        @ui.refreshable
        def form() -> None:
            is_api = tab == "api"
            provider = api_provider if is_api else local_provider
            previous = self.connections.get(provider)
            saved = self.preference(provider)
            defaults = API_PRESETS[provider] if is_api else ("", "", "", "")
            catalog_key = self.route_key(provider, "", saved.get("protocol") or defaults[1], saved.get("base_url") or defaults[2])
            choices = self.catalogs.get(catalog_key, claude_models() if provider == "claude" else [])
            model_id = saved.get("model", API_PRESETS[provider][3] if is_api else "")
            controls = []

            def choose_local(value) -> None:
                nonlocal local_provider
                if value != local_provider and not busy:
                    local_provider = value
                    form.refresh()
                    focus_in(settings_body, ".wb-local-choice summary")

            def choose_api(value) -> None:
                nonlocal api_provider
                if value != api_provider and not busy:
                    api_provider = value
                    form.refresh()

            if is_api:
                _, default_protocol, default_url, _ = API_PRESETS[provider]
                controls.append(SearchPicker(API_PROVIDERS, provider, "API 供应商", choose_api).classes("wb-api-choice"))
                protocol = ui.select(API_PROTOCOLS, value=saved.get("protocol") or default_protocol,
                                     label="接口协议").props("outlined dense").classes("w-full")
                address = ui.input("API 根地址", value=saved.get("base_url") or default_url,
                                   placeholder="https://…/v1").props("outlined dense autocomplete=off").classes("w-full")
                address_locked = bool(default_url and provider not in {"ollama", "vllm", "lm_studio"})
                if address_locked:
                    address.disable()
                if provider != "compatible":
                    protocol.set_visibility(False)
                key = ui.input("API Key", password=True).props(
                    "outlined dense autocomplete=off").classes("w-full")
                controls.extend([key] + ([] if address_locked else [address]) + ([protocol] if provider == "compatible" else []))
                tag("p", "同一供应商和地址换模型可沿用本次会话的 Key。本机兼容接口可留空。", "wb-msg")
                if provider == "bedrock":
                    tag("p", "填写区域 Runtime 根地址、Bedrock API Key 和模型或推理配置 ID。", "wb-msg")
                if provider == "azure":
                    tag("p", "使用以 /openai/v1 结尾的资源地址。可手动输入 Azure 部署名称。", "wb-msg")
            else:
                cli_labels = {name: f"{label} · {'已安装' if installed[name] else '未检测到'}"
                              for name, label in LOCAL_PROVIDERS.items()}
                controls.append(SearchPicker(cli_labels, provider, "执行客户端", choose_local).classes("wb-local-choice"))
                cli = CLIS[provider]
                hint = f"在终端运行 {cli.login} 完成登录。" if installed[provider] else f"安装 {cli.label} 后，点「重新扫描 CLI」。"
                ui.label(hint).classes("wb-msg")

            def model_options() -> dict:
                options = {} if is_api else {"": "CLI 默认模型"}
                options.update({item.id: item.label for item in choices})
                options[CUSTOM_MODEL] = "手动输入模型 ID"
                return options

            def select_model(model: str):
                return model if model in model_options() else CUSTOM_MODEL

            model_field = SearchPicker(model_options(), select_model(model_id), "模型", lambda _: update_fields())
            custom_model = ui.input("模型 ID", value=model_id if is_api or model_id else "",
                                    placeholder="供应商提供的模型 ID").props("outlined dense autocomplete=off").classes("w-full")
            controls.extend([model_field, custom_model])
            effort = ui.select({"": "模型默认"}, value="", label="推理强度").props("outlined dense").classes("w-full")
            tier = ui.select({"": "默认"}, value="", label="服务档位").props("outlined dense").classes("w-full")
            controls.extend((effort, tier))

            def selected_model() -> str:
                return custom_model.value.strip() if model_field.value == CUSTOM_MODEL else model_field.value or ""

            def update_fields(preferred: dict | None = None) -> None:
                model = selected_model()
                custom_model.set_visibility(model_field.value == CUSTOM_MODEL)
                option = next((item for item in choices if item.id == model), None)
                route = self.route_key(provider, model, protocol.value if is_api else "", address.value if is_api else "")
                remembered = self.tuning.get(route)
                if preferred is None:
                    preferred = remembered if isinstance(remembered, dict) else saved if model == saved.get("model") else {}
                levels = api_efforts(provider, model, protocol.value) if is_api else option.efforts if option else ()
                strengths = {"": "模型默认", **{name: effort_label(name) for name in levels}}
                effort.set_options(strengths, value=preferred.get("effort") if preferred.get("effort") in strengths else "")
                effort.set_visibility(bool(levels))
                tiers = {"": "默认", **{name: TIER_LABELS[name] for name in option.tiers}} if option else {"": "默认"}
                tier.set_options(tiers, value=preferred.get("tier") if preferred.get("tier") in tiers else "")
                tier.set_visibility(len(tiers) > 1)

            custom_model.on_value_change(lambda: update_fields())
            if is_api:
                def show_key_status() -> None:
                    key.props("placeholder=留空沿用本次会话的Key" if self.api_key(provider, protocol.value, address.value)
                              else "placeholder=请输入APIKey")

                def update_endpoint() -> None:
                    nonlocal choices
                    key.set_value("")
                    show_key_status()
                    choices = self.catalogs.get(self.route_key(provider, "", protocol.value, address.value), [])
                    model = selected_model()
                    custom_model.set_value(model)
                    model_field.set_options(model_options(), value=select_model(model))
                    update_fields()

                address.on_value_change(update_endpoint)
                protocol.on_value_change(update_endpoint)
                show_key_status()
            update_fields()
            verified = previous is not None and self.verified.get(provider) == previous
            with ui.element("section").classes("wb-connection-health").props('aria-label="连接状态"'):
                if not is_api:
                    ui.label("安装 · 已检测到" if installed[provider] else "安装 · 未检测到")
                auth = ui.label("认证 · 待核对" if not verified else "认证 · 上次调用通过")
                execution = ui.label("调用 · 待测试" if not verified else "上次调用 · 已通过")
                status = ui.label("").classes("wb-model-status").props("role=status aria-live=polite")
                if failure := self.failures.get(provider):
                    status.set_text(f"上次{failure}")

            def api_connection(*, listing: bool = False, label: str = "") -> Connection:
                destination = validate_api_url(address.value)
                secret = (key.value or "").strip() or self.api_key(provider, protocol.value, destination)
                return Connection.api(provider, "" if listing else selected_model(), destination, secret, protocol=protocol.value,
                                      allow_missing_model=listing, label=label, effort="" if listing else effort.value)

            async def check(action: str) -> None:
                nonlocal busy, choices
                if busy or dialog.is_deleted:
                    return
                busy = True
                dialog.props("persistent")
                action_buttons = (*actions, *navigation)
                for control in controls:
                    control.disable()
                for button_ in action_buttons:
                    button_.props("disabled")
                status.set_text({"catalog": "正在加载模型……", "test": "正在测试连接……",
                                 "apply": "正在检查连接……"}[action])
                status.classes(remove="wb-err")
                try:
                    selected_id = selected_model()
                    label = next((item.label for item in choices if item.id == selected_id), "")
                    if is_api:
                        connection = api_connection(listing=action == "catalog", label=label)
                        feedback = "已选择此连接。"
                    else:
                        connection = Connection.local(provider)
                        if action != "catalog":
                            connection = connection.with_cli_model(selected_id, label, effort.value, tier.value)
                        feedback = await connection.check_login()
                        auth.set_text("认证 · 已登录" if "已登录" in feedback else "认证 · 请通过调用验证")
                    if action == "catalog":
                        preferred = {"effort": effort.value, "tier": tier.value}
                        choices, live = await connection.models()
                        self.catalogs[self.route_key(provider, "", connection.protocol, connection.base_url)] = choices
                        custom_model.set_value(selected_id)
                        model_field.set_options(model_options(), value=select_model(selected_id))
                        update_fields(preferred if any(preferred.values()) else None)
                        if is_api:
                            feedback = f"已加载 {len(choices)} 个模型。" if choices else "请手动输入模型 ID。"
                        elif provider in {"claude", "qwen-code"} and choices:
                            feedback += f" 已读取本机配置和别名，共 {len(choices)} 个模型。"
                        elif live:
                            feedback += f" 已读取 {len(choices)} 个模型。"
                        else:
                            feedback += " 可使用 CLI 默认模型或手动输入模型 ID。"
                            if choices:
                                feedback += " 下拉框中列出的是 CLI 内置备选。"
                    elif action == "test":
                        response = await connection.generate(TEST_PROMPT, timeout=40)
                        self.verified[provider] = connection
                        self.failures.pop(provider, None)
                        auth.set_text("认证 · 上次调用通过")
                        execution.set_text("上次调用 · 已通过")
                        feedback = f"已测试 {connection.model or connection.label}，模型返回了文本。"
                        if response.strip().strip(".。").upper() != "OK":
                            feedback += " 测试回复与预期格式不同。"
                    else:
                        self.use(connection)
                        if panel is not None:
                            dialog.props(remove="persistent")
                            on_selected()
                            return
                        ui.notify("已选为生成模型。可在「生成新稿」核对后发送。")
                        dialog.close()
                    if is_api and action != "apply":
                        self._remember_key(connection)
                        key.set_value("")
                        show_key_status()
                    status.set_text(feedback)
                except ConnectionError as exc:
                    phase = {"catalog": "检测", "test": "调用", "apply": "配置"}[action]
                    self.failures[provider] = f"{phase}失败 · {exc}"
                    if action == "test":
                        execution.set_text("调用 · 失败")
                    status.set_text(self.failures[provider])
                    status.classes(add="wb-err")
                finally:
                    busy = False
                    if not dialog.is_deleted and not settings_body.is_deleted:
                        dialog.props(remove="persistent")
                        for control in controls:
                            control.enable()
                        if is_api and address_locked:
                            address.disable()
                        for button_ in action_buttons:
                            button_.props(remove="disabled")

            async def load_models() -> None:
                await check("catalog")

            async def test_connection() -> None:
                await check("test")

            async def use_connection() -> None:
                await check("apply")

            with ui.element("div").classes("wb-actions wb-end wb-model-actions"):
                actions = [button("返回" if panel is not None else "取消", go_back if panel is not None else dialog.close)]
                if not is_api:
                    def scan_again() -> None:
                        rescan()
                        form.refresh()
                        focus_in(settings_body, ".wb-local-choice summary")
                    actions.append(button("重新扫描 CLI", scan_again))
                if provider != "bedrock":
                    actions.append(button("加载模型" if is_api else "检测并加载模型", load_models))
                actions.append(button("测试连接", test_connection))
                actions.append(button("使用此连接", use_connection, "wb-btn primary"))

        def pick(value: str) -> None:
            nonlocal tab
            if busy:
                picker.refresh()
                return
            if tab != value:
                tab = value
                form.refresh()

        @ui.refreshable
        def picker() -> None:
            segmented({"local": "本机 CLI", "api": "API 供应商"}, tab, pick, "wb-seg wb-connection-picker", label="连接方式")

        def closed() -> None:
            dialog.delete()
            if on_selected is not None and self.connection is not original:
                on_selected()
            if focus_back is not None:
                focus_in(*focus_back)

        if panel is not None:
            dialog = panel
            with ui.element("section").classes("wb-settings-fields") as settings_body:
                picker()
                form()
            return

        with ui.dialog().props('aria-label="模型连接"') as dialog, ui.element("div").classes("wb-dialog") as settings_body:
            tag("h3", "模型连接")
            if local_review:
                with disclosure("本地审阅 · 审阅雷达", "wb-task-notice"):
                    ui.label(local_review).classes("wb-msg")
            tag("p", "以下连接用于「生成新稿」。", "wb-msg")
            picker()
            form()
        dialog.on("hide", closed, [])
        dialog.open()

    def revision(self, request: RevisionRequest, *, options: dict, prepare,
                 matches, advance, filename: str) -> ui.dialog:
        candidate = ""
        candidate_check = CandidateCheck()
        approved: set[str] = set()
        error = ""
        working = False
        transitioning = False
        connection_used: Connection | None = None
        stage = 0
        preparing = False
        prepared_options = dict(options)
        editing_model = False
        executions = ()
        retry_status = ""

        def move(index: int, *, configure: bool = False) -> None:
            nonlocal stage, editing_model, error
            if dialog.is_deleted or dialog.props.get("persistent"):
                return
            if index == 2 and self.connection is None:
                index = 1
            if index == stage and configure == editing_model:
                return
            stage, editing_model, error = index, configure, ""
            content.refresh()
            focus_in(revision_body, ".wb-stage-title", scroll=True)

        async def select_step(index: int) -> None:
            nonlocal preparing, prepared_options, request, error
            if preparing or index == stage or dialog.is_deleted or dialog.props.get("persistent"):
                return
            if stage == 0 and options != prepared_options:
                selected = dict(options)
                preparing, error = True, ""
                content.refresh()
                try:
                    if not matches(request):
                        raise ValueError("审阅内容已变化。请重新打开「生成新稿」。")
                    updated = await prepare(selected)
                    if not matches(updated):
                        raise ValueError("审阅内容已变化。请重新打开「生成新稿」。")
                    request, prepared_options = updated, selected
                except ValueError as exc:
                    error = str(exc)
                finally:
                    preparing = False
                if dialog.is_deleted:
                    return
                if error:
                    content.refresh()
                    return
            move(index)

        async def generate(*, connection: Connection) -> None:
            nonlocal working, candidate, candidate_check, error, connection_used, executions, retry_status, request
            if working or transitioning or self.task is not None or connection is not self.connection or stage != 2:
                return
            if not matches(request):
                error = "审阅内容已变化。请关闭此窗口，再打开「生成新稿」。"
                content.refresh()
                return
            connection_used = connection
            working, error, retry_status = True, "", ""
            candidate, candidate_check, executions = "", CandidateCheck(), ()
            approved.clear()
            dialog.props("persistent")
            def retrying(reason: str) -> None:
                nonlocal retry_status
                retry_status = "首次结果未通过检查，正在自动重试一次。" + reason
                content.refresh()

            async def get_candidate():
                reply = await generate_revision(connection, request, retrying)
                return reply, await run.io_bound(request.check_reply, reply)

            self.task = asyncio.create_task(get_candidate())
            content.refresh()
            focus_in(revision_body, ".wb-cancel-generate")
            try:
                reply, candidate_check = await self.task
                candidate, executions = reply.text, reply.executions
                request = replace(request, execution_log=executions)
            except asyncio.CancelledError:
                error = "已取消生成。"
            except ConnectionError as exc:
                error = str(exc)
                self.failures[connection.provider] = f"调用失败 · {exc}"
            except ValueError as exc:
                error = str(exc) + " 可在这里补充要求或重试。"
            else:
                self.failures.pop(connection.provider, None)
            finally:
                self.task = None
                working = False
                if not dialog.is_deleted:
                    dialog.props(remove="persistent")
                    content.refresh()
                    focus_in(revision_body, ".wb-candidate-title" if candidate else ".wb-send-confirm", scroll=True)

        async def start_round() -> None:
            nonlocal transitioning, error
            if working or transitioning or not candidate or candidate_check.blocking_reason(approved):
                return
            if not matches(request):
                error = "审阅内容已变化。可以下载这份候选稿，或关闭后按当前意见重新生成。"
                content.refresh()
                return
            transitioning, error = True, ""
            dialog.props("persistent")
            content.refresh()
            try:
                overrides = tuple(conflict.change_id for conflict in candidate_check.conflicts)
                await advance(request, candidate, connection_used.destination, overrides)
            except ValueError as exc:
                error = str(exc)
            finally:
                if not dialog.is_deleted:
                    transitioning = False
                    dialog.props(remove="persistent")
                    content.refresh()

        def conversation() -> None:
            for task in request.author_notes:
                with ui.element("div").classes("wb-chat-message wb-chat-user"):
                    tag("strong", f"你的要求 · {task.change_id}")
                    ui.label(task.text).classes("wb-chat-text")
            for role, text in request.conversation:
                if role == "user":
                    with ui.element("div").classes("wb-chat-message wb-chat-user"):
                        tag("strong", "你的补充要求")
                        ui.label(text).classes("wb-chat-text")

        def composer() -> None:
            with ui.element("div").classes("wb-chat-composer"):
                followup = ui.textarea("继续告诉模型怎么改").props(
                    f"outlined autogrow maxlength={MAX_REVISION_INSTRUCTIONS} placeholder=例如：第二句再简短一些，保留数字和引文"
                ).classes("w-full wb-followup")

                async def submit() -> None:
                    nonlocal request, error
                    if working or transitioning:
                        return
                    try:
                        request = request.follow_up(followup.value or "", candidate)
                    except ValueError as exc:
                        error = str(exc)
                        followup_error.set_text(error)
                        return
                    if self.connection is not None:
                        await generate(connection=self.connection)

                followup_error = ui.label("").classes("wb-err").props("role=alert")
                with ui.element("div").classes("wb-actions wb-end"):
                    if self.connection is not None:
                        button("重新生成", lambda: generate(connection=self.connection), "wb-btn wb-regenerate")
                        button("发送补充要求", submit, "wb-btn primary wb-followup-send")

        @ui.refreshable
        def content() -> None:
            note_summary = f"{len(request.author_notes)} 条修改建议"
            tag("h3", "候选稿预览" if candidate else "生成新稿", "wb-candidate-title").props("tabindex=-1")
            connection = connection_used if candidate or working else self.connection
            if (working or candidate) and connection is not None:
                ui.label(connection.destination).classes("wb-msg wb-model-status")
            if error:
                ui.label(error).classes("wb-err").props("role=alert")
            if preparing:
                with ui.element("div").classes("wb-actions").props("role=status"):
                    ui.spinner(size="sm")
                    ui.label("正在准备对照稿……")
                button("取消", dialog.close)
                return
            if working:
                with ui.element("div").classes("wb-actions").props("role=status"):
                    ui.spinner(size="sm")
                    ui.label("正在按你的要求修改……")
                if connection_used is not None and connection_used.generation_timeout > 180:
                    tag("p", f"整篇生成每次最多等待 {connection_used.generation_timeout // 60} 分钟，可随时取消。", "wb-msg")
                if retry_status:
                    tag("p", retry_status, "wb-msg").props("role=status")
                with ui.element("div").classes("wb-actions wb-end"):
                    button("取消生成", self.close, "wb-btn wb-cancel-generate")
                return
            if candidate:
                shown_check = candidate_check
                if shown_check.issue:
                    ui.label(shown_check.issue).classes("wb-err").props("role=alert")
                elif shown_check.conflicts:
                    tag("p", "候选稿改动了已确认的内容。请逐处核对并勾选同意覆盖，或在下方要求模型重改。").props("role=status")
                else:
                    tag("p", "候选稿已生成。逐条说明由模型提供，请对照正文验收。")

                def select_override(change_id: str, checked: bool) -> None:
                    if working or transitioning or shown_check is not candidate_check:
                        return
                    if checked:
                        approved.add(change_id)
                    else:
                        approved.discard(change_id)
                    if shown_check.blocking_reason(approved):
                        start_button.props("disabled")
                    else:
                        start_button.props(remove="disabled")

                with ui.element("div").classes("wb-chat-thread"):
                    for conflict in shown_check.conflicts:
                        n = number(conflict.change_id)
                        with ui.element("section").classes("wb-chat-message wb-decision-conflict"):
                            tag("strong", f"第 {n} 处 · 核对裁决")
                            with ui.element("div").classes("wb-cols"):
                                for label, text in (
                                    ("已确认的写法", conflict.confirmed or "已确认删除这处文字"),
                                    ("候选稿的写法", conflict.candidate or "候选稿中未找到对应文字"),
                                ):
                                    with ui.element("div"):
                                        tag("strong", label)
                                        ui.label(text).classes("wb-chat-text")
                            ui.checkbox(f"同意覆盖第 {n} 处", value=conflict.change_id in approved,
                                        on_change=lambda event, cid=conflict.change_id: select_override(cid, event.value)
                                        ).set_enabled(not transitioning and not shown_check.issue)
                    conversation()
                    for execution in executions:
                        task = next(task for task in request.tasks if task.change_id == execution.instruction_id)
                        with ui.element("div").classes("wb-chat-message wb-chat-assistant"):
                            tag("strong", "模型报告已执行" if execution.status == "done" else "需要你补充")
                            ui.label(task.text).classes("wb-msg")
                            ui.label(execution.detail)
                    article(candidate)
                with ui.element("div").classes("wb-actions wb-end"):
                    close_button = button("关闭", dialog.close)
                    download_button = button("下载候选稿", lambda: download_content(candidate, filename))
                    start_button = button("正在建立下一轮…" if transitioning else "开始下一轮验收",
                                          start_round, "wb-btn primary")
                    if shown_check.blocking_reason(approved):
                        start_button.props("disabled")
                    if transitioning:
                        for control in (close_button, download_button, start_button):
                            control.props("disabled")
                composer()
                return
            step_buttons = []
            with ui.element("ol").classes("wb-steps").props('aria-label="生成步骤"'):
                for index, title in enumerate(("修订", "模型", "确认")):
                    style = "current" if stage == index else "done" if stage > index else ""
                    with ui.element("li"):
                        step_button = button(title, lambda index=index: select_step(index), f"wb-step {style}")
                        step_button.props(f'type=button aria-current={"step" if stage == index else "false"}')
                        step_buttons.append(step_button)

            heading = ("确定修订范围", "选择生成用的模型", "核对并发送")[stage]
            tag("h4", heading, "wb-stage-title").props("tabindex=-1")
            if stage == 0:
                def update_option(key: str, value) -> None:
                    if not preparing and stage == 0:
                        options[key] = value

                def toggle_reference(key: str, enabled: bool) -> None:
                    selected = set(options["reference_ids"])
                    if enabled:
                        selected.add(key)
                    else:
                        selected.discard(key)
                    update_option("reference_ids", tuple(ref for ref in REVISION_REFERENCES if ref in selected))

                with ui.element("div").classes("wb-revision-options"):
                    def select(label: str, key: str, items: dict) -> None:
                        tag("h5", label)
                        segmented(items, options[key], lambda value: update_option(key, value), label=label)

                    select("修订范围", "scope", SCOPE_LABELS)
                    with ui.element("div").classes("wb-task-notice"):
                        tag("strong", f"{note_summary}将全部自动发送")
                        tag("p", "每条建议可以修改它所在那一处。候选稿改动其他已确认内容时，会请你逐处确认。")
                    if request.pending:
                        ui.checkbox("同时发送没有建议的待定位置作参考", value=options["pending"],
                                    on_change=lambda e: update_option("pending", e.value)).props("dense")
                        select("下一轮对照稿的待定处", "policy", PENDING_VERSIONS)
                    if request.has_layout_diffs:
                        select("对照稿的排版", "layout", LAYOUT_OPTIONS)
                    with ui.element("div").classes("wb-chat-composer"):
                        tag("h5", "本次还想怎么改？")
                        tag("p", "直接写要求，就像聊天一样。这些要求也优先于之前的取舍；发生冲突时，以后写的要求为准。", "wb-msg")
                        instructions = ui.textarea("修订指令", value=options["instructions"]).props(
                            f"outlined autogrow maxlength={MAX_REVISION_INSTRUCTIONS}").classes("w-full")
                        instructions.on_value_change(lambda event: update_option("instructions", event.value or ""))
                        button("恢复默认指令", lambda: instructions.set_value(DEFAULT_REVISION_INSTRUCTIONS))
                    tag("h5", "学术修订参考")
                    for key, ref in REVISION_REFERENCES.items():
                        with ui.element("div"):
                            ui.checkbox(ref.label, value=key in options["reference_ids"],
                                        on_change=lambda event, key=key: toggle_reference(key, event.value)).props("dense")
                            with disclosure(f"查看「{ref.label}」的指引与来源", "wb-send-details"):
                                ui.label(ref.guidance)
                                ui.link(ref.source, ref.url, new_tab=True)
                with ui.element("div").classes("wb-actions wb-end"):
                    button("取消", dialog.close)
                    button("下一步：模型", lambda: select_step(1), "wb-btn primary")
                return
            if stage == 1:
                if connection is None or editing_model:
                    self.settings(lambda: move(2), panel=dialog,
                                  go_back=lambda: move(0) if connection is None else move(1), navigation=step_buttons)
                else:
                    ui.label(connection.destination).classes("wb-route-card")
                    with ui.element("div").classes("wb-actions wb-end"):
                        button("上一步", lambda: move(0))
                        button("更换模型", lambda: move(1, configure=True))
                        button("下一步：确认", lambda: move(2), "wb-btn primary")
                return

            sent = f"作者合并稿、全部 {note_summary}、修订指令和原有取舍"
            if request.references:
                sent += "，以及" + "、".join(request.reference_guidance)
            conversation()
            with disclosure("将发送的作者合并稿（也是下一轮对照稿）", "wb-send-details"):
                article(request.baseline)
            with disclosure("将发送的修改意见", "wb-send-details"):
                article(request.feedback)
            with disclosure("本次修订指令", "wb-send-details"):
                article(request.instructions)
            if request.references:
                with disclosure("将发送的学术修订参考", "wb-send-details"):
                    for label, guidance in request.reference_guidance.items():
                        tag("h5", label)
                        article(guidance)
            with disclosure("发送指令与完整请求", "wb-send-details"):
                article(request.prompt)

            with ui.element("dl").classes("wb-send-summary"):
                for label, value in (("发送至", connection.destination if connection else "请选择模型"),
                                     ("发送内容", sent), ("修订范围", SCOPE_LABELS[options["scope"]]),
                                     ("结果位置", "本窗口的「候选稿预览」")):
                    tag("dt", label)
                    with ui.element("dd"):
                        ui.label(value)
            if request.omitted_drafts:
                tag("p", f"{request.omitted_drafts} 处未提交的编辑草稿留在本轮。", "wb-msg")
            with ui.element("div").classes("wb-actions wb-end"):
                button("上一步", lambda: move(1))
                if connection is not None:
                    button("确认发送", lambda: generate(connection=connection), "wb-btn primary wb-send-confirm")
            if error:
                composer()

        with ui.dialog().props('aria-label="生成新稿"') as dialog, ui.element("div").classes(
                "wb-dialog wb-revision-panel wb-model-preview") as revision_body:
            content()
        dialog.on("hide", dialog.delete, [])
        dialog.open()
        return dialog


def article(text: str) -> None:
    with ui.element("div").classes("wb-preview-body"):
        tag("div", html.escape(text), "wb-preview-text wb-serif")
