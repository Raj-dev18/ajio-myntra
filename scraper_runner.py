import sys
import os
import asyncio
from typing import Callable, Optional, List, Dict, Any
from playwright.async_api import Page, BrowserContext
from browser_manager import BrowserManager

def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

SCRAPERS_DIR = os.path.join(get_base_dir(), "scrapers")
AJIO_SCRIPT_PATH = os.path.join(SCRAPERS_DIR, "New_ajio_scraper.js")
MYNTRA_SCRIPT_PATH = os.path.join(SCRAPERS_DIR, "new_myntra_scraper.js")


class ScraperRunner:
    """
    Executes the exact supplied JavaScript scrapers in the persistent Chromium browser
    context, routing their progress and console logs to PySide6 in real-time.
    """

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager
        self.active_page: Optional[Page] = None
        self.is_running: bool = False
        self._cancel_requested: bool = False

        # Callbacks for UI updates
        self.on_log: Optional[Callable[[str], None]] = None
        self.on_progress: Optional[Callable[[Dict[str, Any]], None]] = None

    def log(self, message: str):
        if self.on_log:
            self.on_log(message)
        else:
            print(message)

    def progress(self, payload: Dict[str, Any]):
        if self.on_progress:
            self.on_progress(payload)

    def request_stop(self):
        """Signals the current scraping job to safely terminate."""
        self._cancel_requested = True
        self.log("[ScraperRunner] Stop requested by user. Terminating scraper tab...")
        if self.active_page and not self.active_page.is_closed():
            # Schedule page closure on the browser thread to interrupt running JS
            self.browser_manager.run_coroutine(self._close_active_page())

    async def _close_active_page(self):
        try:
            if self.active_page and not self.active_page.is_closed():
                await self.active_page.close()
        except Exception:
            pass
        self.active_page = None

    async def run_ajio_scraper(self) -> List[Dict[str, Any]]:
        """
        Executes New_ajio_scraper.js inside an Ajio tab within the persistent browser context.
        """
        self._cancel_requested = False
        self.is_running = True
        self.log("[ScraperRunner] Preparing AJIO scraping session...")

        if not os.path.exists(AJIO_SCRIPT_PATH):
            raise FileNotFoundError(f"AJIO scraper script not found at {AJIO_SCRIPT_PATH}")

        with open(AJIO_SCRIPT_PATH, "r", encoding="utf-8") as f:
            js_code = f.read()

        context = await self.browser_manager.get_context()
        page = await context.new_page()
        self.active_page = page

        # Hook console messages
        page.on("console", lambda msg: self.log(f"[Browser Console] {msg.text}"))

        try:
            self.log("[ScraperRunner] Navigating to https://www.ajio.com to establish origin and session...")
            # Navigate to Ajio to inherit cookies/session and ensure same-origin fetch works
            try:
                await page.goto("https://www.ajio.com", wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                self.log(f"[ScraperRunner] Navigation note: {e}. Continuing with page state...")

            if self._cancel_requested:
                self.log("[ScraperRunner] Job cancelled before script execution.")
                return []

            # Expose progress function to browser window
            async def py_update_progress(payload):
                self.progress(payload)

            await page.expose_function("updateScraperProgress", py_update_progress)

            self.log("[ScraperRunner] Evaluating exact New_ajio_scraper.js...")
            page.set_default_timeout(0)  # No timeout for long-running pagination

            # Evaluate script in page
            result = await page.evaluate(js_code)

            if self._cancel_requested:
                self.log("[ScraperRunner] Scrape was stopped by user.")
                return []

            # Retrieve collected products
            products = []
            if isinstance(result, dict) and "products" in result:
                products = result["products"]
            else:
                products = await page.evaluate("() => window.__AJIO_PRODUCTS__ || []")

            self.log(f"[ScraperRunner] AJIO scraping completed successfully. Extracted {len(products)} products.")
            return products

        except Exception as e:
            if self._cancel_requested or "Target closed" in str(e) or "context was destroyed" in str(e):
                self.log("[ScraperRunner] AJIO scraping safely halted.")
                return []
            self.log(f"[ScraperRunner] Error during AJIO scraping: {e}")
            raise
        finally:
            self.is_running = False
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass
            self.active_page = None

    async def run_myntra_scraper(self) -> List[Dict[str, Any]]:
        """
        Executes new_myntra_scraper.js inside a Myntra tab within the persistent browser context.
        """
        self._cancel_requested = False
        self.is_running = True
        self.log("[ScraperRunner] Preparing Myntra scraping session...")

        if not os.path.exists(MYNTRA_SCRIPT_PATH):
            raise FileNotFoundError(f"Myntra scraper script not found at {MYNTRA_SCRIPT_PATH}")

        with open(MYNTRA_SCRIPT_PATH, "r", encoding="utf-8") as f:
            js_code = f.read()

        context = await self.browser_manager.get_context()
        page = await context.new_page()
        self.active_page = page

        # Hook console messages
        page.on("console", lambda msg: self.log(f"[Browser Console] {msg.text}"))

        try:
            self.log("[ScraperRunner] Navigating to https://www.myntra.com to establish origin and session...")
            try:
                await page.goto("https://www.myntra.com", wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                self.log(f"[ScraperRunner] Navigation note: {e}. Continuing with page state...")

            if self._cancel_requested:
                self.log("[ScraperRunner] Job cancelled before script execution.")
                return []

            # Expose progress function to browser window
            async def py_update_progress(payload):
                self.progress(payload)

            await page.expose_function("updateScraperProgress", py_update_progress)

            # Inject property listener on window.__SCRAPER_PROGRESS__ so updates from Myntra script reach Python
            await page.evaluate("""() => {
                let _prog = null;
                Object.defineProperty(window, '__SCRAPER_PROGRESS__', {
                    set: function(val) {
                        _prog = val;
                        if (typeof window.updateScraperProgress === 'function') {
                            try { window.updateScraperProgress(val); } catch(e) {}
                        }
                    },
                    get: function() { return _prog; },
                    configurable: true
                });
            }""")

            self.log("[ScraperRunner] Evaluating exact new_myntra_scraper.js...")
            page.set_default_timeout(0)  # Disable timeout for full catalog & offer extraction

            # Evaluate script in page
            await page.evaluate(js_code)

            if self._cancel_requested:
                self.log("[ScraperRunner] Scrape was stopped by user.")
                return []

            # Retrieve uniqueProducts saved in window.__MYNTRA_PRODUCTS__
            products = await page.evaluate("() => window.__MYNTRA_PRODUCTS__ || []")

            self.log(f"[ScraperRunner] Myntra scraping completed successfully. Extracted {len(products)} products.")
            return products

        except Exception as e:
            if self._cancel_requested or "Target closed" in str(e) or "context was destroyed" in str(e):
                self.log("[ScraperRunner] Myntra scraping safely halted.")
                return []
            self.log(f"[ScraperRunner] Error during Myntra scraping: {e}")
            raise
        finally:
            self.is_running = False
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass
            self.active_page = None
