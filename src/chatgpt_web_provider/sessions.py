"""Route complete OpenAI histories to browser conversations, persisting hashes only."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path


def fingerprint(message) -> str:
    text, images = message.parts()
    value = [message.role, text, [hashlib.sha256(image.data).hexdigest() for image in images]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


class SessionRegistry:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.records = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.records = {}

    def choose(self, messages, model, scope, conversation_id=None, new_session=None):
        history = [fingerprint(message) for message in messages]
        if conversation_id and new_session is True:
            raise ValueError("conversation_id and new_session=true cannot be combined.")
        if conversation_id:
            record = self.records.get(conversation_id)
            if not record or record["scope"] != scope or record["model"] != model:
                raise ValueError("Unknown conversation_id for this API key and model.")
            prefix = record["history"]
            if history[:len(prefix)] == prefix:
                return record, len(prefix), history
            if len(messages) == 1 and messages[0].role == "user":
                return record, 0, prefix + history
            raise ValueError("Conversation history differs from the saved branch; omit conversation_id to start a new branch.")
        if new_session is not True:
            matches = [r for r in self.records.values()
                       if r["scope"] == scope and r["model"] == model
                       and len(history) > len(r["history"])
                       and history[:len(r["history"])] == r["history"]]
            if matches:
                longest = max(len(r["history"]) for r in matches)
                best = [r for r in matches if len(r["history"]) == longest]
                if len(best) == 1:
                    record = best[0]
                    return record, len(record["history"]), history
        return None, 0, history

    def save(self, record, history, reply, url, model, scope):
        conversation_id = record["id"] if record else str(uuid.uuid4())
        self.records[conversation_id] = dict(id=conversation_id, history=history + [fingerprint(reply)], url=url, model=model, scope=scope)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        return conversation_id
