"""The sole Playwright boundary: pixels and input, never DOM inspection."""

import asyncio
import io
import time
import uuid

from PIL import Image
from playwright.async_api import async_playwright

from .policy import PolicyDenied


class BrowserSurface:
    def __init__(
        self,
        policy,
        headed=False,
        viewport=(1280, 800),
        device_scale_factor=1.0,
        window_following=False,
    ):
        if viewport[0] < 320 or viewport[1] < 240 or not 0.5 <= device_scale_factor <= 4:
            raise ValueError("Invalid viewport or device scale factor")
        self.policy = policy
        self.headed = headed
        if window_following and not headed:
            raise ValueError("Window following requires --headed")
        if window_following and device_scale_factor != 1:
            raise ValueError(
                "Window following uses native window scaling; omit device scale emulation"
            )
        self.window_following = window_following
        self.capture_lock = asyncio.Lock()
        self.latest_png = None
        self.latest_capture_time = 0.0
        self.configured_viewport = viewport
        self.device_scale_factor = device_scale_factor
        self.dimensions = viewport
        self.logical_dimensions = viewport
        self.session_id = uuid.uuid4().hex
        self.blocked = []

    async def start(self):
        self.pw = await async_playwright().start()
        launch_args = (
            [f"--window-size={self.configured_viewport[0]},{self.configured_viewport[1]}"]
            if self.window_following
            else []
        )
        self.browser = await self.pw.chromium.launch(headless=not self.headed, args=launch_args)
        geometry = (
            {"no_viewport": True}
            if self.window_following
            else {
                "viewport": {
                    "width": self.configured_viewport[0],
                    "height": self.configured_viewport[1],
                },
                "device_scale_factor": self.device_scale_factor,
            }
        )
        self.context = await self.browser.new_context(
            **geometry,
            locale="en-US",
            service_workers="block",
            accept_downloads=False,
        )

        async def route(r):
            if self.policy.allows_url(r.request.url) and r.request.method == "GET":
                await r.continue_()
            else:
                self.blocked.append("BLOCKED_REQUEST")
                await r.abort()

        await self.context.route("**/*", route)
        self.page = await self.context.new_page()

        async def popup(p):
            if p != self.page:
                await p.close()

        self.context.on("page", popup)
        await self.page.goto(self.policy.origin, wait_until="load")

    async def observe(self, max_age=0.0):
        async with self.capture_lock:
            previous_size = self.dimensions
            png = await self._capture(max_age)
            if self.window_following and self.dimensions != previous_size:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    previous_size = self.dimensions
                    await asyncio.sleep(0.15)
                    png = await self._capture(0.0)
                    if self.dimensions == previous_size:
                        return png
                raise RuntimeError("VIEWPORT_UNSTABLE")
            return png

    async def _capture(self, max_age):
        if self.page.is_closed():
            raise RuntimeError("SESSION_LOST")
        if not self.policy.allows_url(self.page.url):
            raise PolicyDenied("OUTSIDE_ORIGIN")
        if self.latest_png and time.monotonic() - self.latest_capture_time <= max_age:
            return self.latest_png
        png = await self.page.screenshot(
            type="png", animations="allow", caret="initial", scale="css"
        )
        self.dimensions = Image.open(io.BytesIO(png)).size
        # CSS-scale screenshots and mouse input now use the same coordinate space,
        # including when page.viewport_size is None in a native resizable window.
        self.logical_dimensions = self.dimensions
        self.latest_png = png
        self.latest_capture_time = time.monotonic()
        return png

    def is_alive(self):
        return hasattr(self, "page") and not self.page.is_closed() and self.browser.is_connected()

    async def click(self, x, y):
        width, height = self.dimensions
        if not (0 <= x < width and 0 <= y < height):
            raise PolicyDenied("OUTSIDE_VIEWPORT")
        await self.page.mouse.click(x, y)
        self.latest_capture_time = 0.0

    async def type_text(self, text):
        await self.page.keyboard.press("ControlOrMeta+A")
        await self.page.keyboard.insert_text(text)
        self.latest_capture_time = 0.0

    async def key(self, key):
        await self.page.keyboard.press(key)
        self.latest_capture_time = 0.0

    async def scroll(self, delta):
        # Wheel events go to the pane under the pointer, including an iframe.
        # Move without clicking to the visible work area, away from side navigation.
        width, height = self.dimensions
        await self.page.mouse.move(width * 0.75, height * 0.65)
        await self.page.mouse.wheel(0, max(-500, min(500, delta)))
        self.latest_capture_time = 0.0

    async def close(self):
        if hasattr(self, "browser"):
            await self.browser.close()
        if hasattr(self, "pw"):
            await self.pw.stop()
