from __future__ import annotations

from pydantic import BaseModel, Field
from .content import parse_content


class ChatMessage(BaseModel):
    role: str
    content: str | list | dict | None = ""

    def text(self) -> str:
        return self.parts()[0]

    def parts(self):
        return parse_content(self.content)


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    new_session: bool | None = None
    conversation_id: str | None = None
    level: str | None = None
    reasoning_effort: str | None = None


class ResponsesRequest(BaseModel):
    model: str | None = None
    input: str | list | dict
    temperature: float | None = None
    max_output_tokens: int | None = None
    stream: bool = False
    new_session: bool | None = None
    conversation_id: str | None = None
    level: str | None = None
    reasoning_effort: str | None = None


class CompletionResult(BaseModel):
    text: str
    model: str
    level: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    conversation_id: str | None = None


class ErrorBody(BaseModel):
    error: dict[str, str] = Field(default_factory=dict)
