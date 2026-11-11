"""Claude API beyni. Agent'ın kullandığı mesaj biçimini (Ollama tarzı) Anthropic biçimine çevirir.

Agent döngüsü değişmeden kalır: _chat() aynı sözlüğü döner
  {"role": "assistant", "content": str, "tool_calls": [{"id", "function": {"name", "arguments"}}]}
"""
from __future__ import annotations

import json
import logging

from . import config, tools

log = logging.getLogger("nova.claude")


class ClaudeBrain:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=2, timeout=60)

    @staticmethod
    def _tools() -> list[dict]:
        out = [{"name": s["function"]["name"], "description": s["function"]["description"],
                "input_schema": s["function"]["parameters"]} for s in tools.schemas()]
        out[-1]["cache_control"] = {"type": "ephemeral"}   # araç listesi önbelleğe alınır: daha ucuz ve hızlı
        return out

    @staticmethod
    def convert(messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """Ollama tarzı mesajları (system, messages) Anthropic biçimine çevirir."""
        system = [{"type": "text", "text": m["content"]} for m in messages if m["role"] == "system"]
        if system:
            system[-1]["cache_control"] = {"type": "ephemeral"}
        out: list[dict] = []

        def push(role: str, blocks: list[dict]):
            blocks = [b for b in blocks if not (b.get("type") == "text" and not b.get("text", "").strip())]
            if not blocks:
                return
            if out and out[-1]["role"] == role:     # Anthropic aynı rolün art arda gelmesini istemez: birleştir
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})

        for m in messages:
            role = m["role"]
            if role == "system":
                continue
            if role == "tool":
                push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                               "content": m.get("content") or "(boş)"}])
            elif role == "assistant":
                blocks = [{"type": "text", "text": m.get("content") or ""}]
                for c in m.get("tool_calls") or []:
                    fn = c.get("function", {})
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({"type": "tool_use", "id": c.get("id", ""), "name": fn.get("name", ""), "input": args})
                push("assistant", blocks)
            else:
                push("user", [{"type": "text", "text": m.get("content") or ""}])
        if out and out[0]["role"] != "user":         # konuşma kullanıcıyla başlamalı
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(önceki konuşma)"}]})
        return system, out

    def chat(self, messages: list[dict], on_text=None) -> dict:
        system, msgs = self.convert(messages)
        kwargs = dict(model=config.CLAUDE_MODEL, max_tokens=config.CLAUDE_MAX_TOKENS, system=system,
                      tools=self._tools(), messages=msgs)
        content, calls = "", []
        with self.client.messages.stream(**kwargs) as stream:
            cut = False
            for text in stream.text_stream:
                content += text
                if on_text and on_text(text) is False:
                    cut = True
                    break
            if not cut:
                final = stream.get_final_message()
                content = "".join(b.text for b in final.content if b.type == "text")
                calls = [{"id": b.id, "function": {"name": b.name, "arguments": b.input}}
                         for b in final.content if b.type == "tool_use"]
                u = final.usage
                log.info("Claude: %s giriş (%s önbellekten), %s çıkış token",
                         u.input_tokens, getattr(u, "cache_read_input_tokens", 0), u.output_tokens)
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = calls
        return msg
