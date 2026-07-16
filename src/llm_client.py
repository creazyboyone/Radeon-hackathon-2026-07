import logging
import requests

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def chat(self, messages, tools=None, tool_choice="auto",
             max_tokens=2048, temperature=0.7):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        resp = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=600,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        tool_calls_raw = message.get("tool_calls") or []

        parsed_tool_calls = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            try:
                args = fn.get("arguments")
                args = args if isinstance(args, dict) else __import__("json").loads(args or "{}")
            except Exception:
                args = {}
            parsed_tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            })

        return {
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning_content") or "",
            "tool_calls": parsed_tool_calls,
            "finish_reason": choice.get("finish_reason", ""),
            "usage": data.get("usage", {}),
            "timings": data.get("timings", {}),
        }
