from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings
from .models import ChatMessage, CompletionResult
from .sessions import SessionRegistry

COMPOSER = "#prompt-textarea, div[contenteditable='true'][role='textbox']"
SEND_BUTTON = "button[data-testid='send-button'], button[aria-label='发送'], button[aria-label='Send'], button[aria-label='发送提示'], button[aria-label='Send prompt']"


class Backend(ABC):
    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def complete(self, messages: list[ChatMessage], model: str | None = None, new_session: bool = False, level: str | None = None) -> CompletionResult:
        raise NotImplementedError

    async def health(self) -> dict:
        return {"ok": True, "backend": self.settings.backend}

    async def complete_request(self, messages, model=None, new_session=None, level=None, conversation_id=None, scope=""):
        return await self.complete(messages, model=model, new_session=new_session, level=level)


class MockBackend(Backend):
    async def complete(self, messages: list[ChatMessage], model: str | None = None, new_session: bool = False, level: str | None = None) -> CompletionResult:
        last_user = next((m.text() for m in reversed(messages) if m.role == "user"), "")
        text = f"[mock:{self.settings.model_id}] {last_user}"
        return CompletionResult(text=text, model=model or self.settings.model_id, level=level, prompt_tokens=sum(len(m.text().split()) for m in messages), completion_tokens=len(text.split()))


class BrowserBackend(Backend):
    """Browser-backed ChatGPT.com worker.

    This intentionally keeps the first implementation conservative. It launches a
    persistent Chromium profile and drives ChatGPT through Playwright. Real
    production use should pin selectors with browser-level regression tests.
    """

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._lock = asyncio.Lock()
        self._playwright = None
        self._context = None
        self._page = None
        self._sessions = SessionRegistry(Path(settings.profile_dir) / 'provider-sessions.json')

    async def _ensure_page(self):
        if self._page:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - depends on optional browser env
            raise RuntimeError("playwright is not installed") from exc

        Path(self.settings.profile_dir).mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self.settings.profile_dir,
            channel="chrome",
            headless=self.settings.headless,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000)
        return self._page

    async def complete(self, messages: list[ChatMessage], model: str | None = None, new_session: bool | None = None, level: str | None = None) -> CompletionResult:
        return await self.complete_request(messages, model=model, new_session=new_session, level=level)

    async def complete_request(self, messages, model=None, new_session=None, level=None, conversation_id=None, scope=""):
        async with self._lock:
            model = model or self.settings.model_id
            record, offset, history = self._sessions.choose(messages, model, scope, conversation_id, new_session)
            pending = messages[offset:]
            if not pending:
                raise ValueError("The request contains no new messages.")
            prompt = self._render_prompt(pending)
            images = [image for message in pending for image in message.parts()[1]]
            page = await self._ensure_page()
            if "log in" in (await page.title()).lower():
                raise RuntimeError("ChatGPT browser profile is not logged in; run setup with a visible browser first")
            if record:
                target = urlparse(record['url'])
                if target.scheme != 'https' or target.netloc != 'chatgpt.com' or not target.path.startswith('/c/'):
                    raise RuntimeError("Saved conversation URL is invalid.")
                if page.url.split('?')[0] != record['url']:
                    await page.goto(record['url'], wait_until='domcontentloaded', timeout=60000)
                    await page.locator(COMPOSER).last.wait_for(timeout=30000)
            else:
                await self._start_new_session(page)
                await self._apply_preferences(page, model, level)

            composer = page.locator(COMPOSER).last
            await composer.wait_for(timeout=30_000)
            await composer.fill(prompt or 'Please examine the attached image.')
            if images:
                upload = page.locator('input[type=file][accept*="image"]').first
                if not await upload.count():
                    raise RuntimeError("The ChatGPT image upload control was not found.")
                await upload.set_input_files([image.payload() for image in images])
            before = await self._assistant_count(page)
            send = page.locator(SEND_BUTTON).last
            await send.wait_for(timeout=30000)
            # Playwright waits for the button to become enabled after uploads.
            await send.click(timeout=90000)
            text = await self._wait_for_answer(page, before)
            await page.wait_for_url(re.compile(r'https://chatgpt\.com/c/[^/?]+'), timeout=15000)
            saved_id = self._sessions.save(record, history, ChatMessage(role='assistant', content=text), page.url.split('?')[0], model, scope)
            return CompletionResult(text=text, model=model, level=level, conversation_id=saved_id)

    async def health(self) -> dict:
        try:
            page = await self._ensure_page()
            title = await page.title()
            return {"ok": True, "backend": "browser", "title": title, "logged_in_hint": "log in" not in title.lower()}
        except Exception as exc:
            return {"ok": False, "backend": "browser", "error": str(exc)}

    async def _apply_preferences(self, page, model: str, level: str | None) -> None:  # pragma: no cover - browser integration
        """Best-effort model / reasoning-level selection in the ChatGPT UI.

        ChatGPT UI changes often. Failure to locate a control is non-fatal: the
        request still runs with the currently selected browser model, while the
        API response records the requested model/level.
        """
        await self._try_select_label(page, self.settings.model_label(model))
        if level:
            await self._try_select_label(page, self.settings.level_label(level))

    @staticmethod
    async def _try_select_label(page, label: str) -> bool:  # pragma: no cover - browser integration
        if not label:
            return False
        candidates = (
            f"button:has-text('{label}')",
            f"[role='button']:has-text('{label}')",
            f"[role='menuitem']:has-text('{label}')",
            f"text={label}",
        )
        # First try a direct visible option (already expanded menu or direct button).
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                    await loc.click(timeout=3000)
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        # Then try common model/menu buttons and search again.
        for opener in (
            "[data-testid='model-switcher-dropdown-button']",
            "button[aria-haspopup='menu']",
            "button:has-text('ChatGPT')",
            "button:has-text('GPT')",
        ):
            try:
                button = page.locator(opener).first
                if await button.count() > 0 and await button.is_visible(timeout=1000):
                    await button.click(timeout=3000)
                    await page.wait_for_timeout(700)
                    for selector in candidates:
                        loc = page.locator(selector).first
                        if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                            await loc.click(timeout=3000)
                            await page.wait_for_timeout(500)
                            return True
            except Exception:
                continue
        return False

    @staticmethod
    def _render_prompt(messages: list[ChatMessage]) -> str:
        if len(messages) == 1 and messages[0].role == 'user':
            return messages[0].text()
        return "\n\n".join(f"{m.role.upper()}: {m.text()}" for m in messages)

    @staticmethod
    async def _assistant_locator(page):
        current = page.locator("[data-conversation-role='assistant'] + div")
        if await current.count():
            return current
        return page.locator("[data-message-author-role='assistant']")

    @classmethod
    async def _assistant_count(cls, page):
        return await (await cls._assistant_locator(page)).count()

    async def _wait_for_answer(self, page, before):
        deadline = asyncio.get_running_loop().time() + self.settings.request_timeout_seconds
        previous, stable = None, 0
        while asyncio.get_running_loop().time() < deadline:
            answers = await self._assistant_locator(page)
            stop = page.locator("button[aria-label*='Stop']:visible, button[data-testid*='stop']:visible, button[aria-label*='停止']:visible")
            if await answers.count() > before:
                text = (await answers.last.inner_text()).strip()
                group = answers.last.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' group ')][1]")
                done = group.locator(".turn-action-controls button[aria-label='复制'], .turn-action-controls button[aria-label='Copy'], .turn-action-controls button[aria-label='重新生成回复']")
                if text and text == previous and not await stop.count() and await done.count():
                    stable += 1
                    if stable >= 2:
                        return text
                else:
                    stable = 0
                previous = text
            await page.wait_for_timeout(1000)
        raise RuntimeError("Timed out waiting for a completed assistant reply. The message may already have been sent; do not automatically retry.")

    @staticmethod
    async def _start_new_session(page) -> None:  # pragma: no cover - browser integration
        """Move ChatGPT to a fresh conversation before sending the prompt."""
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000)
        await page.locator(COMPOSER).last.wait_for(timeout=60000)
        for selector in (
            "[data-testid='create-new-chat-button']",
            "button[aria-label*='New chat']",
            "button[aria-label*='New conversation']",
            "button[aria-label*='新聊天']",
            "button[aria-label*='新对话']",
            "a[aria-label*='New chat']",
            "button:has-text('新聊天')",
            "button:has-text('新对话')",
            "a:has-text('新聊天')",
            "a:has-text('新对话')",
        ):
            candidate = page.locator(selector + ":visible").first
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=1000):
                    await candidate.click(timeout=5_000)
                    await page.wait_for_timeout(1500)
                    if "/c/" in page.url:
                        continue
                    await page.locator("#prompt-textarea, div[contenteditable='true']").last.wait_for(timeout=15_000)
                    return
            except Exception:
                continue
        raise RuntimeError("could not start a new ChatGPT conversation; the browser UI may have changed")

    @classmethod
    async def _extract_last_answer(cls, page) -> str:
        candidates = await cls._assistant_locator(page)
        if not await candidates.count():
            raise RuntimeError("Could not find an assistant reply; refusing to return page text.")
        text = (await candidates.last.inner_text()).strip()
        if not text:
            raise RuntimeError("The assistant reply is empty.")
        return text


def build_backend(settings: Settings) -> Backend:
    if settings.backend == "browser":
        return BrowserBackend(settings)
    return MockBackend(settings)
