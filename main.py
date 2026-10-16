import sys
import os

# Add local directory to python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from PySide6.QtWidgets import QApplication
from browser_manager import BrowserManager
from app_ui import MainWindow
from config_manager import load_config


def main():
    config = load_config()

    # 1. Initialize PySide6 Application
    app = QApplication(sys.argv)
    app.setApplicationName("ProductScraper")
    app.setOrganizationName("ProductScraper")

    # 2. Initialize BrowserManager with Google as default start URL
    # Fulfills: "On the first application launch, open a single visible browser window
    # with a general start page such as Google. Do NOT automatically open Myntra or AJIO,
    # and do NOT require separate Myntra or AJIO login steps."
    start_url = config.get("start_url", "https://www.google.com")
    browser_manager = BrowserManager(start_url=start_url)

    # 3. Create and show Main Window
    window = MainWindow(browser_manager)
    window.show()

    # 4. Automatically open the visible persistent browser on application launch
    # using the persistent profile at %LOCALAPPDATA%\ProductScraper\BrowserProfile
    if config.get("auto_open_browser_on_launch", True):
        browser_manager.open_browser()

    # 5. Clean teardown on application exit
    def cleanup():
        print("[App] Shutting down application...")
        # Note: Do NOT delete browser profile! It persists on the user's machine.
        # Only cleanly close the browser process and event loop.
        browser_manager.close_browser()

    app.aboutToQuit.connect(cleanup)

    # 6. Execute Qt Event Loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
