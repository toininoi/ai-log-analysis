from openai import OpenAI
from typing import List


class ChatGPTAnalyzer:
    def __init__(self, api_key: str, model: str = "gpt-3.5-turbo"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def analyze(self, logs: List[str], system_prompt: str = "You are a helpful assistant for analyzing system logs.") -> str:
        if not logs:
            return "No logs provided."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Analyze these logs and explain any issues:\n" + "\n".join(logs)}
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"[ERROR] GPT call failed: {str(e)}"
