from langfuse import Langfuse
from langfuse.callback import CallbackHandler
from langchain.prompts import ChatPromptTemplate
import os

class LangfusePromptManager:
    def __init__(self, secret_key=None, public_key=None, host=None):
        secret_key = secret_key or os.getenv("LANGFUSE_SECRET_KEY")
        public_key = public_key or os.getenv("LANGFUSE_PUBLIC_KEY")
        host = host or os.getenv("LANGFUSE_HOST")

        self.langfuse = Langfuse(secret_key=secret_key, public_key=public_key, host=host)
        self.langfuse_callback_handler = CallbackHandler(secret_key=secret_key, public_key=public_key, host=host)

        assert self.langfuse.auth_check()
        assert self.langfuse_callback_handler.auth_check()

    def add(self, prompt: str, name: str, config: dict = {}, labels: list[str] = ["production"]):
        self.langfuse.create_prompt(name=name, prompt=prompt, config=config, labels=labels)

    def get_prompt(self, prompt_name: str, version: int = None):
        return self.langfuse.get_prompt(prompt_name, version=version).get_langchain_prompt()

    def get(self, prompt_name: str, is_chat: bool = True, version: int = None):
        langfuse_prompt = self.langfuse.get_prompt(prompt_name, version=version)
        prompt = (
            ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
            if is_chat else
            ChatPromptTemplate.from_template(langfuse_prompt.get_langchain_prompt(), metadata={"langfuse_prompt": langfuse_prompt})
        )
        return prompt, langfuse_prompt.config
