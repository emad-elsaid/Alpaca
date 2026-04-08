# openai_instances.py

from gi.repository import Adw, GLib

import openai, requests, json, logging, threading, re
from pydantic import BaseModel

from .. import dialog, tools, chat
from ...sql_manager import generate_uuid, Instance as SQL
from ...constants import MAX_TOKENS_TITLE_GENERATION, TITLE_GENERATION_PROMPT_OPENAI

logger = logging.getLogger(__name__)


# Base instance, don't use directly
class BaseInstance:
    instance_id = None
    description = None
    limitations = ()

    default_properties = {
        "name": _("Instance"),
        "api": "",
        "max_tokens": 2048,
        "override_parameters": True,
        "temperature": 0.7,
        "seed": 0,
        "default_model": None,
        "title_model": None,
    }

    def __init__(self, instance_id: str, properties: dict):
        self.row = None
        self.instance_id = instance_id
        self.available_models = None
        self.properties = {}
        for key in self.default_properties:
            self.properties[key] = properties.get(key, self.default_properties.get(key))
        if "no-seed" in self.limitations and "seed" in self.properties:
            del self.properties["seed"]
        self.properties["url"] = self.instance_url

        self.client = None

    def stop(self):
        self.client = None

    def start(self):
        if not self.client:
            arguments = {"api_key": self.properties.get("api")}
            if self.instance_type != "chatgpt":
                arguments["base_url"] = self.properties.get("url").strip()

            self.client = openai.OpenAI(**arguments)

    def get_active_lore(self, messages: list, lorebook: dict) -> str:
        if len(lorebook.get("entries", [])) == 0:
            return
        messages_to_scan = messages[-lorebook.get("scan_depth", 100) :]
        messages_str = "\n".join(
            [m.get("content") for m in messages_to_scan if m.get("role") != "system"]
        )
        active_lore_content = []

        for entry in lorebook.get("entries"):
            for key in entry.get("keys", []):
                clean_key = key.strip()
                if clean_key:
                    pattern = rf"\b{re.escape(clean_key)}\b"
                    if re.search(pattern, messages_str, flags=re.IGNORECASE):
                        content = "# {}\n\n{}".format(
                            clean_key.title(), entry.get("content", "")
                        )
                        if content not in active_lore_content:
                            active_lore_content.append(content)
                        break

        return "\n\n---\n\n".join(active_lore_content)

    def prepare_chat(self, bot_message, model: str):
        chat_element = bot_message.get_ancestor(chat.Chat)
        GLib.idle_add(bot_message.block_container.show_generating_block)
        if chat_element and chat_element.chat_id:
            GLib.idle_add(chat_element.row.spinner.set_visible, True)
            try:
                GLib.idle_add(
                    bot_message.get_root().global_footer.toggle_action_button, False
                )
            except:
                pass

            chat_element.busy = True
            GLib.idle_add(chat_element.set_visible_child_name, "content")

        messages = chat_element.convert_to_ollama()[
            : list(chat_element.container).index(bot_message)
        ]

        character_dict = SQL.get_model_preferences(model).get("character", {})
        if (
            character_dict.get("data", {})
            .get("extensions", {})
            .get("com.jeffser.Alpaca", {})
            .get("enabled", False)
        ):
            character_book = character_dict.get("data", {}).get("character_book", {})
            if len(character_book.get("entries", [])) > 0:
                lore_message = {
                    "role": "system",
                    "content": self.get_active_lore(messages, character_book),
                }
                if lore_message.get("content"):
                    index = 0
                    for msg in messages:
                        if msg.get("role") == "system":
                            index += 1
                        else:
                            break
                    messages.insert(index, lore_message)

        return chat_element, messages

    def generate_message(self, bot_message, model: str):
        chat, messages = self.prepare_chat(bot_message, model)

        if chat.chat_id and chat.get_name().startswith(_("New Chat")):
            threading.Thread(
                target=self.generate_chat_title,
                args=(chat, messages[-1].get("content"), model),
                daemon=True,
            ).start()

        self.generate_response(bot_message, chat, messages, model)

    def use_tools(self, bot_message, model: str, available_tools: dict):
        chat, messages = self.prepare_chat(bot_message, model)

        if chat.chat_id and chat.get_name().startswith(_("New Chat")):
            threading.Thread(
                target=self.generate_chat_title,
                args=(chat, messages[-1].get("content"), model),
                daemon=True,
            ).start()

        try:
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[v.get_metadata() for v in available_tools.values()],
            )
            if completion.choices[0] and completion.choices[0].message:
                if completion.choices[0].message.tool_calls:
                    for call in completion.choices[0].message.tool_calls:
                        # Parse arguments once
                        arguments = json.loads(call.function.arguments)

                        if available_tools.get(call.function.name):
                            tool_response = available_tools.get(call.function.name).run(
                                arguments, messages, bot_message
                            )

                            attachment_content = []

                            if len(arguments) > 0:
                                attachment_content += [
                                    "## {}".format(_("Arguments")),
                                    "| {} | {} |".format(_("Argument"), _("Value")),
                                    "| --- | --- |",
                                ]
                                attachment_content += [
                                    "| {} | {} |".format(k, v)
                                    for k, v in arguments.items()
                                ]

                            attachment_content += [
                                "## {}".format(_("Result")),
                                tool_response,
                            ]

                            attachment = bot_message.add_attachment(
                                file_id=generate_uuid(),
                                name=available_tools.get(call.function.name).name,
                                attachment_type="tool",
                                content="\n".join(attachment_content),
                            )
                            SQL.insert_or_update_attachment(bot_message, attachment)
                        else:
                            tool_response = ""

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": str(tool_response),
                            }
                        )

        except Exception as e:
            dialog.simple_error(
                parent=bot_message.get_root(),
                title=_("Tool Error"),
                body=_("An error occurred while running tool"),
                error_log=e,
            )
            logger.error(e)

        self.generate_response(bot_message, chat, messages, model)

    def generate_response(self, bot_message, chat, messages: list, model: str):
        if "no-system-messages" in self.limitations:
            for i in range(len(messages)):
                if messages[i].get("role") == "system":
                    messages[i]["role"] = "user"

        if "text-only" in self.limitations:
            for i in range(len(messages)):
                if "images" in messages[i]:
                    del messages[i]["images"]
        params = {"model": model, "messages": messages, "stream": True}

        if self.properties.get("max_tokens", 0) > 0:
            if "use_max_completion_tokens" in self.limitations:
                params["max_completion_tokens"] = int(
                    self.properties.get("max_tokens", 0)
                )
            else:
                params["max_tokens"] = int(self.properties.get("max_tokens", 0))

        if self.properties.get("override_parameters"):
            params["temperature"] = self.properties.get("temperature", 0.7)
            if self.properties.get("seed", 0) != 0:
                params["seed"] = self.properties.get("seed")

        if chat.busy:
            try:
                bot_message.block_container.clear()
                response = self.client.chat.completions.create(**params)
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            bot_message.update_message(delta.content)
                    if not chat.busy:
                        break
            except Exception as e:
                dialog.simple_error(
                    parent=bot_message.get_root(),
                    title=_("Instance Error"),
                    body=_("Message generation failed"),
                    error_log=e,
                )
                logger.error(e)
                if self.row:
                    GLib.idle_add(self.row.get_parent().unselect_all)
        bot_message.finish_generation()

    def generate_chat_title(self, chat, prompt: str, fallback_model: str):
        class ChatTitle(BaseModel):  # Pydantic
            title: str
            emoji: str = ""

        messages = [
            {
                "role": "user"
                if "no-system-messages" in self.limitations
                else "system",
                "content": TITLE_GENERATION_PROMPT_OPENAI,
            },
            {
                "role": "user",
                "content": "Generate a title for this prompt:\n{}".format(prompt),
            },
        ]
        model = self.get_title_model()
        params = {
            "temperature": 0.2,
            "model": model if model else fallback_model,
            "messages": messages,
            "max_tokens": MAX_TOKENS_TITLE_GENERATION,
        }
        new_chat_title = chat.get_name()

        try:
            completion = self.client.chat.completions.parse(
                **params, response_format=ChatTitle
            )
            response = completion.choices[0].message
            if response.parsed:
                emoji = response.parsed.emoji if len(response.parsed.emoji) == 1 else ""
                new_chat_title = "{} {}".format(emoji, response.parsed.title)
        except Exception as e:
            try:
                response = self.client.chat.completions.create(**params)
                new_chat_title = str(response.choices[0].message.content)
            except Exception as e:
                logger.error(e)

        new_chat_title = re.sub(r"<think>.*?</think>", "", new_chat_title).strip()

        if len(new_chat_title) > 30:
            new_chat_title = new_chat_title[:30].strip() + "..."

        GLib.idle_add(chat.row.edit, new_chat_title, chat.is_template)

    def get_default_model(self):
        local_models = self.get_local_models()
        if len(local_models) > 0:
            if not self.properties.get("default_model") or not self.properties.get(
                "default_model"
            ) in [m.get("name") for m in local_models]:
                self.properties["default_model"] = local_models[0].get("name")
            return self.properties.get("default_model")

    def get_title_model(self):
        local_models = self.get_local_models()
        if len(local_models) > 0:
            if self.properties.get("title_model") and not self.properties.get(
                "title_model"
            ) in [m.get("name") for m in local_models]:
                self.properties["title_model"] = local_models[0].get("name")
            return self.properties.get("title_model")

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                for m in self.client.models.list():
                    if all(
                        s not in m.id.lower()
                        for s in [
                            "embedding",
                            "davinci",
                            "dall",
                            "tts",
                            "whisper",
                            "image",
                        ]
                    ):
                        self.available_models[m.id] = {}
            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve added models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
            return {}

    def pull_model(self, model):
        SQL.append_online_instance_model_list(self.instance_id, model.get_name())
        GLib.timeout_add(5000, lambda: model.update_progressbar(-1) and False)

    def get_local_models(self) -> list:
        local_models = []
        for model in SQL.get_online_instance_model_list(self.instance_id):
            local_models.append({"name": model})
        return local_models

    def delete_model(self, model_name: str) -> bool:
        SQL.remove_online_instance_model_list(self.instance_id, model_name)
        return True

    def get_model_info(self, model_name: str) -> dict:
        return {}


class ChatGPT(BaseInstance):
    instance_type = "chatgpt"
    instance_type_display = "OpenAI ChatGPT"
    instance_url = "https://api.openai.com/v1/"
    limitations = ("use_max_completion_tokens",)


class Gemini(BaseInstance):
    instance_type = "gemini"
    instance_type_display = "Google Gemini"
    instance_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    limitations = ("no-system-messages",)

    def __init__(self, instance_id: str, properties: dict):
        super().__init__(instance_id, properties)
        if "seed" in self.properties:
            del self.properties["seed"]

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models?key={}".format(
                        self.properties.get("api")
                    )
                )
                for model in response.json().get("models", []):
                    if "generateContent" in model.get(
                        "supportedGenerationMethods", []
                    ) and "deprecated" not in model.get("description", ""):
                        model["name"] = model.get("name").removeprefix("models/")
                        self.available_models[model.get("name")] = model
            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve added models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
        return {}

    def get_model_info(self, model_name: str) -> dict:
        try:
            response = requests.get(
                "https://generativelanguage.googleapis.com/v1beta/models/{}?key={}".format(
                    model_name, self.properties.get("api")
                )
            )
            data = response.json()
            data["capabilities"] = ["completion", "vision"]
            return data
        except Exception as e:
            logger.error(e)
        return {}


class Together(BaseInstance):
    instance_type = "together"
    instance_type_display = "Together AI"
    instance_url = "https://api.together.xyz/v1/"

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get(
                    "https://api.together.xyz/v1/models",
                    headers={
                        "accept": "application/json",
                        "authorization": "Bearer {}".format(self.properties.get("api")),
                    },
                )
                for model in response.json():
                    if model.get("id") and model.get("type") == "chat":
                        self.available_models[model.get("id")] = {
                            "display_name": model.get("display_name")
                        }
            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve added models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
        return {}


class Venice(BaseInstance):
    instance_type = "venice"
    instance_type_display = "Venice"
    instance_url = "https://api.venice.ai/api/v1/"
    limitations = ("no-system-messages",)

    def __init__(self, instance_id: str, properties: dict):
        super().__init__(instance_id, properties)
        if "seed" in self.properties:
            del self.properties["seed"]


class Deepseek(BaseInstance):
    instance_type = "deepseek"
    instance_type_display = "Deepseek"
    instance_url = "https://api.deepseek.com/v1/"
    limitations = ("text-only",)

    def __init__(self, instance_id: str, properties: dict):
        super().__init__(instance_id, properties)
        if "seed" in self.properties:
            del self.properties["seed"]


class Groq(BaseInstance):
    instance_type = "groq"
    instance_type_display = "Groq Cloud"
    instance_url = "https://api.groq.com/openai/v1"
    limitations = ("text-only",)


class Anthropic(BaseInstance):
    instance_type = "anthropic"
    instance_type_display = "Anthropic"
    instance_url = "https://api.anthropic.com/v1/"
    limitations = ("no-system-messages",)


class OpenRouter(BaseInstance):
    instance_type = "openrouter"
    instance_type_display = "OpenRouter AI"
    instance_url = "https://openrouter.ai/api/v1/"

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get("https://openrouter.ai/api/v1/models")
                for model in response.json().get("data", []):
                    if model.get("id"):
                        self.available_models[model.get("id")] = {
                            "display_name": model.get("name")
                        }

            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
            return {}


class Qwen(BaseInstance):
    instance_type = "qwen"
    instance_type_display = "Qwen (DashScope)"
    instance_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    description = _("Alibaba Cloud Qwen large language models via DashScope")


class Fireworks(BaseInstance):
    instance_type = "fireworks"
    instance_type_display = "Fireworks AI"
    instance_url = "https://api.fireworks.ai/inference/v1/"
    description = _("Fireworks AI inference platform")

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get(
                    "https://api.fireworks.ai/inference/v1/models",
                    headers={"Authorization": f"Bearer {self.properties.get('api')}"},
                )
                for model in response.json().get("data", []):
                    if model.get("id") and "chat" in model.get("capabilities", []):
                        self.available_models[model.get("id")] = {
                            "display_name": model.get("name")
                        }

            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
            return {}


class LambdaLabs(BaseInstance):
    instance_type = "lambda_labs"
    instance_type_display = "Lambda Labs"
    instance_url = "https://api.lambdalabs.com/v1/"
    description = _("Lambda Labs cloud inference API")

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get(
                    "https://api.lambdalabs.com/v1/models",
                    headers={"Authorization": f"Bearer {self.properties.get('api')}"},
                )
                for model in response.json().get("data", []):
                    if model.get("id"):
                        self.available_models[model.get("id")] = {
                            "display_name": model.get("name")
                        }

            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
            return {}


class Cerebras(BaseInstance):
    instance_type = "cerebras"
    instance_type_display = "Cerebras AI"
    instance_url = "https://api.cerebras.ai/v1/"
    description = _("Cerebras AI cloud inference API")


class Klusterai(BaseInstance):
    instance_type = "klusterai"
    instance_type_display = "Kluster AI"
    instance_url = "https://api.kluster.ai/v1/"
    description = _("Kluster AI cloud inference API")


class Kimi(BaseInstance):
    instance_type = "kimi"
    instance_type_display = "Kimi (Moonshot AI)"
    instance_url = "https://api.moonshot.ai/v1/"
    description = _("Kimi large language models by Moonshot AI")
    limitations = ("no-seed",)


class Mistral(BaseInstance):
    instance_type = "mistral"
    instance_type_display = "Mistral AI"
    instance_url = "https://api.mistral.ai/v1/"
    description = _("Mistral AI large language models")
    limitations = ("text-only",)


class LlamaAPI(BaseInstance):
    instance_type = "llama-api"
    instance_type_display = "Llama API"
    instance_url = "https://api.llama.com/compat/v1/"
    description = _("Meta AI Llama API")


class NovitaAI(BaseInstance):
    instance_type = "novitaai"
    instance_type_display = "Novita AI"
    instance_url = "https://api.novita.ai/v3/openai/"
    description = _("Novita AI cloud inference API")
    limitations = ("no-seed",)


class DeepInfra(BaseInstance):
    instance_type = "deepinfra"
    instance_type_display = "DeepInfra"
    instance_url = "https://api.deepinfra.com/v1/openai"
    description = _("DeepInfra cloud inference API")


class CompactifAI(BaseInstance):
    instance_type = "compactifai"
    instance_type_display = "CompactifAI"
    instance_url = "https://your-compactifai-api-endpoint/v1"
    description = _("CompactifAI inference platform")

    def get_available_models(self) -> dict:
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}
                response = requests.get(
                    f"{self.instance_url}/models",
                    headers={"Authorization": f"Bearer {self.properties.get('api')}"},
                )
                for model in response.json().get("data", []):
                    if model.get("id"):
                        self.available_models[model.get("id")] = {
                            "display_name": model.get("name", model.get("id"))
                        }
            return self.available_models
        except Exception as e:
            dialog.simple_error(
                parent=self.row.get_root() if self.row else None,
                title=_("Instance Error"),
                body=_("Could not retrieve CompactifAI models"),
                error_log=e,
            )
            logger.error(e)
            if self.row:
                GLib.idle_add(self.row.get_parent().unselect_all)
            return {}


class Grok(BaseInstance):
    instance_type = "grok"
    instance_type_display = "Grok"
    instance_url = "https://api.x.ai/v1"
    description = _("Grok instance from X.ai")


class SarvamAI(BaseInstance):
    instance_type = "sarvam"
    instance_type_display = "Sarvam AI"
    instance_url = "https://api.sarvam.ai/"
    description = "Sarvam AI"

    def start(self):
        if not self.client:
            self.client = openai.OpenAI(
                api_key=self.properties.get("api"),
                base_url=self.properties.get("url").strip(),
                default_headers={"api-subscription-key": self.properties.get("api")},
            )


class Bedrock(BaseInstance):
    instance_type = "bedrock"
    instance_type_display = "AWS Bedrock"
    instance_url = ""  # Empty string, not None (Bedrock doesn't use a URL)
    description = _("AWS Bedrock - Claude, Titan, Llama, Command, Mistral and more")
    limitations = ("no-seed",)  # Bedrock doesn't support seed parameter

    default_properties = {
        **BaseInstance.default_properties,
        "aws_region": "us-east-1",
        "aws_profile": "",  # Optional AWS profile name
    }

    def __init__(self, instance_id: str, properties: dict):
        super().__init__(instance_id, properties)
        self.litellm_available = False
        self._completion = None

    def start(self):
        """Initialize LiteLLM for Bedrock access"""
        if not self.client:
            try:
                from litellm import completion

                self._completion = completion
                self.litellm_available = True
                # Set a dummy client to satisfy BaseInstance expectations
                self.client = True
                logger.info(
                    f"Bedrock instance initialized (region: {self.properties.get('aws_region')})"
                )
            except ImportError:
                logger.error("LiteLLM not installed. Install with: pip install litellm")
                self.litellm_available = False
                dialog.simple_error(
                    parent=self.row.get_root() if self.row else None,
                    title=_("Dependency Missing"),
                    body=_(
                        "LiteLLM is required for Bedrock. Install with: pip install litellm"
                    ),
                    error_log="ImportError: No module named 'litellm'",
                )

    def generate_response(self, bot_message, chat, messages: list, model: str):
        """Generate response using LiteLLM's Bedrock integration"""
        if not self.litellm_available or not self._completion:
            GLib.idle_add(bot_message.error_message, _("LiteLLM not available"))
            bot_message.finish_generation()
            return

        # Handle message preprocessing (same as BaseInstance)
        if "no-system-messages" in self.limitations:
            for i in range(len(messages)):
                if messages[i].get("role") == "system":
                    messages[i]["role"] = "user"

        if "text-only" in self.limitations:
            for i in range(len(messages)):
                if "images" in messages[i]:
                    del messages[i]["images"]

        # Build LiteLLM parameters
        params = {
            "model": f"bedrock/{model}",  # LiteLLM requires bedrock/ prefix
            "messages": messages,
            "stream": True,
            "aws_region_name": self.properties.get("aws_region", "us-east-1"),
        }

        # Add AWS profile if specified
        if self.properties.get("aws_profile"):
            params["aws_profile_name"] = self.properties.get("aws_profile")

        # Add generation parameters
        if self.properties.get("max_tokens", 0) > 0:
            params["max_tokens"] = int(self.properties.get("max_tokens", 0))

        if self.properties.get("override_parameters"):
            params["temperature"] = self.properties.get("temperature", 0.7)
            # Note: Bedrock doesn't support seed, so we skip it (handled by limitations)

        if chat.busy:
            try:
                bot_message.block_container.clear()
                response = self._completion(**params)

                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            bot_message.update_message(delta.content)
                    if not chat.busy:
                        break
            except Exception as e:
                error_message = str(e)

                # Provide helpful error messages for common issues
                if (
                    "credentials" in error_message.lower()
                    or "access" in error_message.lower()
                ):
                    body = _(
                        "AWS credentials not found or invalid. Configure with: aws configure"
                    )
                elif (
                    "not found" in error_message.lower()
                    or "does not exist" in error_message.lower()
                ):
                    body = _(
                        "Model not available in this region. Check AWS Bedrock console for model access."
                    )
                elif "throttl" in error_message.lower():
                    body = _("Request throttled. AWS Bedrock rate limit reached.")
                else:
                    body = _(
                        "Bedrock request failed. Check AWS credentials and model availability."
                    )

                dialog.simple_error(
                    parent=bot_message.get_root(),
                    title=_("Bedrock Error"),
                    body=body,
                    error_log=e,
                )
                logger.error(f"Bedrock error: {e}")
                if self.row:
                    GLib.idle_add(self.row.get_parent().unselect_all)

        bot_message.finish_generation()

    def get_available_models(self) -> dict:
        """
        Fetch available Bedrock models from AWS.
        Falls back to a default list if AWS API is unavailable.
        """
        try:
            if not self.available_models or len(self.available_models) == 0:
                self.available_models = {}

                try:
                    import boto3

                    # Create Bedrock client
                    session_kwargs = {
                        "region_name": self.properties.get("aws_region", "us-east-1")
                    }
                    if self.properties.get("aws_profile"):
                        session_kwargs["profile_name"] = self.properties.get(
                            "aws_profile"
                        )

                    session = boto3.Session(**session_kwargs)
                    bedrock = session.client("bedrock")

                    # Fetch foundation models
                    response = bedrock.list_foundation_models()

                    for model_info in response.get("modelSummaries", []):
                        # Only include models that support on-demand inference
                        if "ON_DEMAND" in model_info.get("inferenceTypesSupported", []):
                            model_id = model_info.get("modelId")
                            self.available_models[model_id] = {
                                "display_name": model_info.get("modelName", model_id),
                                "provider": model_info.get("providerName"),
                            }

                    logger.info(f"Fetched {len(self.available_models)} Bedrock models")

                except Exception as boto_error:
                    logger.warning(
                        f"Could not fetch models from AWS: {boto_error}. Using default list."
                    )
                    # Fall back to default model list
                    self.available_models = self._get_default_bedrock_models()

            return self.available_models

        except Exception as e:
            logger.error(f"Error getting Bedrock models: {e}")
            return self._get_default_bedrock_models()

    def _get_default_bedrock_models(self) -> dict:
        """Default Bedrock model list when API fetch fails"""
        return {
            "anthropic.claude-3-5-sonnet-20241022-v2:0": {
                "display_name": "Claude 3.5 Sonnet v2"
            },
            "anthropic.claude-3-5-sonnet-20240620-v1:0": {
                "display_name": "Claude 3.5 Sonnet"
            },
            "anthropic.claude-3-opus-20240229-v1:0": {"display_name": "Claude 3 Opus"},
            "anthropic.claude-3-sonnet-20240229-v1:0": {
                "display_name": "Claude 3 Sonnet"
            },
            "anthropic.claude-3-haiku-20240307-v1:0": {
                "display_name": "Claude 3 Haiku"
            },
            "anthropic.claude-v2:1": {"display_name": "Claude 2.1"},
            "anthropic.claude-v2": {"display_name": "Claude 2.0"},
            "amazon.titan-text-premier-v1:0": {"display_name": "Titan Text Premier"},
            "amazon.titan-text-express-v1": {"display_name": "Titan Text Express"},
            "amazon.titan-text-lite-v1": {"display_name": "Titan Text Lite"},
            "meta.llama3-1-405b-instruct-v1:0": {
                "display_name": "Llama 3.1 405B Instruct"
            },
            "meta.llama3-1-70b-instruct-v1:0": {
                "display_name": "Llama 3.1 70B Instruct"
            },
            "meta.llama3-1-8b-instruct-v1:0": {"display_name": "Llama 3.1 8B Instruct"},
            "mistral.mistral-large-2407-v1:0": {
                "display_name": "Mistral Large 2 (24.07)"
            },
            "mistral.mistral-large-2402-v1:0": {
                "display_name": "Mistral Large (24.02)"
            },
            "cohere.command-r-plus-v1:0": {"display_name": "Command R+"},
            "cohere.command-r-v1:0": {"display_name": "Command R"},
        }


class GenericOpenAI(BaseInstance):
    instance_type = "openai:generic"
    instance_type_display = _("OpenAI Compatible Instance")
    instance_url = ""
    description = _("AI instance compatible with OpenAI library")

    def __init__(self, instance_id: str, properties: dict):
        self.instance_url = properties.get("url", "")
        super().__init__(instance_id, properties)
