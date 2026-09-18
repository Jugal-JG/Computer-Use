"""Minimal connectivity check. Never prints credentials or raw provider payloads."""

import asyncio
import os
import re

import httpx
from dotenv import load_dotenv


async def main():
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": key},
            json={"model": model, "input": "Reply OK.", "store": False},
        )
    print("HTTP status:", response.status_code)
    if response.status_code != 200:
        error = response.json().get("error", {})
        message = str(error.get("message", ""))
        if key:
            message = message.replace(key, "[REDACTED_KEY]")
        message = re.sub(r"AIza[\w-]+", "[REDACTED_KEY]", message)
        print("Provider status:", error.get("status"))
        print("Sanitized explanation:", message[:500])
    else:
        print("Model connectivity verified.")

        def shape(value, depth=0):
            if depth > 8:
                return type(value).__name__
            if isinstance(value, dict):
                return {k: shape(v, depth + 1) for k, v in value.items()}
            if isinstance(value, list):
                return [shape(v, depth + 1) for v in value[:2]]
            return type(value).__name__

        print("Response structure (no values):", shape(response.json()))
        print(
            "Step discriminators:",
            [
                (s.get("type"), [c.get("type") for c in s.get("content", [])])
                for s in response.json().get("steps", [])
            ],
        )


if __name__ == "__main__":
    asyncio.run(main())
