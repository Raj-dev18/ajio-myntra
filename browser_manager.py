import asyncio
import os
import threading
from typing import Optional, Callable, Any
from playwright.async_api import async_playwright, BrowserContext, Page, Playwright
from config_manager import BROWSER_PROFILE_DIR, ensure_directories


class BrowserManager:
    """
    Coordinates a single persistent Chromium browser context across the application.
    Runs an asyncio event loop on a dedicated background thread so the browser remains
    responsive, interactive, and visible without blocking the PySide6 Qt GUI.
    """

    def __init__(self, start_url: str = "https://www.google.com"):
        self.start_url = start_url
        self.profile_dir = BROWSER_PROFILE_DIR
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._is_ready = threading.Event()
        self._on_browser_closed_callback: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()

        ensure_directories()
        self._start_loop_thread()

    def set_on_browser_closed_callback(self, cb: Callable[[], None]):
        self._on_browser_closed_callback = cb

    def _start_loop_thread(self):
        """Starts the dedicated background asyncio event loop."""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._is_ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run_loop, name="PlaywrightEventLoopThread", daemon=True)
        self._thread.start()
        self._is_ready.wait()

    def run_coroutine(self, coro):
        """Schedule a coroutine on the background thread and return its Future."""
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("Browser event loop is not running.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _async_launch_browser(self) -> BrowserContext:
        """Launches the persistent browser context if not already active."""
        if self._context and not self._is_context_closed():
            return self._context

        if not self._playwright:
            self._playwright = await async_playwright().start()

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized"
        ]

        # Clean up any stale Chromium lockfile if left over from an abnormal termination
        lockfile_path = os.path.join(self.profile_dir, "lockfile")
        if os.path.exists(lockfile_path):
            try:
                os.remove(lockfile_path)
            except Exception:
                pass

        # Launch persistent context
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=False,
            args=args,
            no_viewport=True,
            ignore_default_args=["--enable-automation"]
        )

        # Listen for context close (e.g. user manually closes browser)
        def handle_close(_):
            print("[BrowserManager] Browser context was closed by user.")
            self._context = None
            if self._on_browser_closed_callback:
                try:
                    self._on_browser_closed_callback()
                except Exception as e:
                    print(f"[BrowserManager] Error in browser closed callback: {e}")

        self._context.on("close", handle_close)

        # Open the default start page (Google) on first open
        pages = self._context.pages
        if pages:
            initial_page = pages[0]
            # If about:blank or empty, navigate to start URL
            if initial_page.url in ("", "about:blank"):
                try:
                    await initial_page.goto(self.start_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
        else:
            initial_page = await self._context.new_page()
            try:
                await initial_page.goto(self.start_url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

        return self._context

    def _is_context_closed(self) -> bool:
        if not self._context:
            return True
        try:
            # Check if any pages can be accessed
            _ = self._context.pages
            return False
        except Exception:
            return True

    def is_browser_open(self) -> bool:
        """Returns True if the persistent browser is currently alive and visible."""
        return not self._is_context_closed()

    def open_browser(self):
        """Launches or brings up the persistent browser window."""
        future = self.run_coroutine(self._async_open_or_focus())
        return future

    async def _async_open_or_focus(self):
        ctx = await self._async_launch_browser()
        pages = ctx.pages
        if not pages:
            p = await ctx.new_page()
            await p.goto(self.start_url, wait_until="domcontentloaded")
            await p.bring_to_front()
        else:
            await pages[0].bring_to_front()
        return ctx

    async def get_context(self) -> BrowserContext:
        """Retrieve the active persistent context, launching it if necessary."""
        return await self._async_launch_browser()

    def close_browser(self):
        """Closes the browser cleanly."""
        if self._loop and self._loop.is_running():
            future = self.run_coroutine(self._async_close())
            return future
        return None

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
