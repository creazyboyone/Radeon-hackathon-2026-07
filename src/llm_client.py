import json
import logging
import requests

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
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
                args = args if isinstance(args, dict) else json.loads(args or "{}")
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

    def chat_stream(self, messages, tools=None, tool_choice="auto",
                    max_tokens=2048, temperature=0.7, on_chunk=None):
        """流式输出 — 逐 token yield chunk, on_chunk 回调实时推送

        on_chunk(chunk_dict) 被调用时:
          {"type": "reasoning", "text": "..."}  — 思考增量
          {"type": "content", "text": "..."}    — 响应增量
        最终返回完整结果 (同 chat 方法的返回格式)
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        # 不复用 Session 连接池, 每次独立连接, 避免 stream response 未关闭导致连接池耗尽
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=(10, 120),
            stream=True,
        )
        resp.raise_for_status()

        content_buf = ""
        reasoning_buf = ""
        tool_calls_buf = []
        stream_done = False

        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    stream_done = True
                    break
                try:
                    chunk = json.loads(line)
                    delta = chunk["choices"][0].get("delta", {})

                    c = delta.get("content", "")
                    if c:
                        content_buf += c
                        if on_chunk:
                            on_chunk({"type": "content", "text": c})

                    r = delta.get("reasoning_content", "")
                    if r:
                        reasoning_buf += r
                        if on_chunk:
                            on_chunk({"type": "reasoning", "text": r})

                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            while len(tool_calls_buf) <= idx:
                                tool_calls_buf.append({"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                tool_calls_buf[idx]["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_calls_buf[idx]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tool_calls_buf[idx]["arguments"] += fn["arguments"]
                except Exception as e:
                    logger.warning(f"stream parse error (line may be incomplete): {e}")
                    continue
        finally:
            resp.close()  # 确保流式连接被释放, 防止连接池卡死

        if not stream_done:
            logger.warning("stream ended without [DONE] marker, output may be truncated")

        # 解析 tool_calls
        parsed_tool_calls = []
        for tc in tool_calls_buf:
            if tc["name"]:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except Exception:
                    args = {}
                parsed_tool_calls.append({
                    "id": tc["id"],
                    "name": tc["name"],
                    "arguments": args,
                })

        return {
            "content": content_buf,
            "reasoning": reasoning_buf,
            "tool_calls": parsed_tool_calls,
            "finish_reason": "tool_calls" if parsed_tool_calls else "stop",
            "usage": {},
            "timings": {},
        }
