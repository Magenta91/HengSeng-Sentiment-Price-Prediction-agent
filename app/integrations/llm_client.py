"""LLM abstraction for structured extraction with Google Gemini support."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.exceptions import IntegrationError


class LLMClient(Protocol):
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 1000) -> str: ...


class GeminiLLMClient:
    """Google Gemini client using the official google-genai SDK.
    
    Implements structured extraction with system prompts combined with user content.
    Uses async SDK methods to avoid blocking the event loop.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise IntegrationError("GEMINI_API_KEY is not configured")

        try:
            from google import genai
        except ImportError as exc:
            raise IntegrationError(
                "google-genai package not installed. Run: pip install google-genai"
            ) from exc

        # Initialize the client
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout

    async def complete(self, system: str, user: str, *, max_tokens: int = 1000) -> str:
        """Generate completion by combining system and user prompts.
        
        Gemini doesn't have a separate system prompt field, so we prepend it to the user message.
        Uses the new SDK's async generate_content method.
        """
        # Combine system and user prompts
        combined_prompt = f"{system}\n\n{user}"

        # Configure generation parameters
        config = {
            "temperature": self.temperature,
            "max_output_tokens": max_tokens,
        }
        
        try:
            # Use async generate_content with timeout
            response = await asyncio.wait_for(
                self._generate_content_async(combined_prompt, config),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise IntegrationError(f"Gemini request timed out after {self.timeout}s") from exc
        except Exception as exc:
            raise IntegrationError(f"Gemini API error: {exc}") from exc

        # Extract text from response
        try:
            if not response or not response.text:
                raise IntegrationError("Gemini returned empty response")
            
            return response.text
        except (AttributeError, KeyError) as exc:
            raise IntegrationError(f"Unexpected Gemini response structure: {exc}") from exc

    async def _generate_content_async(self, prompt: str, config: dict):
        """Async wrapper for generate_content to avoid blocking.
        
        The new SDK's generate_content is synchronous, so we run it in a thread pool.
        """
        loop = asyncio.get_event_loop()
        
        # Ensure model name includes full path if not already present
        model_name = self.model if self.model.startswith('models/') else f'models/{self.model}'
        
        return await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
        )

