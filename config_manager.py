import sys
import json
import os
from typing import Dict, Any

def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    # In frozen mode, look next to the .exe first, then bundled
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        exe_env = os.path.join(exe_dir, ".env")
        if os.path.exists(exe_env):
            load_dotenv(exe_env, override=True)
        else:
            load_dotenv(os.path.join(get_base_dir(), ".env"), override=True)
    else:
        env_path = os.path.join(get_base_dir(), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
except ImportError:
    pass

LOCAL_APPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
APP_DATA_DIR = os.path.join(LOCAL_APPDATA, "ProductScraper")
BROWSER_PROFILE_DIR = os.path.join(APP_DATA_DIR, "BrowserProfile")
DATA_DIR = os.path.join(APP_DATA_DIR, "Data")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")

def get_env_supabase_key() -> str:
    # SUPABASE_SECRET_KEY bypasses RLS natively without closing/disabling RLS
    return os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY") or ""

DEFAULT_CONFIG: Dict[str, Any] = {
    "supabase_url": os.environ.get("SUPABASE_URL", ""),
    "supabase_secret_key": os.environ.get("SUPABASE_SECRET_KEY", ""),
    "supabase_publishable_key": os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
    "supabase_jwks_url": os.environ.get("SUPABASE_JWKS_URL", ""),
    "supabase_key": get_env_supabase_key(),
    "supabase_table": os.environ.get("SUPABASE_TABLE", "products"),
    "start_url": "https://www.google.com",
    "auto_open_browser_on_launch": True,
}


def ensure_directories():
    """Ensure that the local application directories exist."""
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json or environment variables."""
    ensure_directories()
    config = dict(DEFAULT_CONFIG)

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"[ConfigManager] Error reading config file: {e}")

    # Allow environment variables from .env to override
    if os.environ.get("SUPABASE_URL"):
        config["supabase_url"] = os.environ["SUPABASE_URL"]
    if os.environ.get("SUPABASE_SECRET_KEY"):
        config["supabase_secret_key"] = os.environ["SUPABASE_SECRET_KEY"]
        config["supabase_key"] = os.environ["SUPABASE_SECRET_KEY"]
    if os.environ.get("SUPABASE_PUBLISHABLE_KEY"):
        config["supabase_publishable_key"] = os.environ["SUPABASE_PUBLISHABLE_KEY"]
        if not config.get("supabase_key"):
            config["supabase_key"] = os.environ["SUPABASE_PUBLISHABLE_KEY"]
    if os.environ.get("SUPABASE_JWKS_URL"):
        config["supabase_jwks_url"] = os.environ["SUPABASE_JWKS_URL"]
    if os.environ.get("SUPABASE_TABLE"):
        config["supabase_table"] = os.environ["SUPABASE_TABLE"]

    return config


def save_config(config_data: Dict[str, Any]):
    """Persist configuration to config.json and .env."""
    ensure_directories()
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"[ConfigManager] Error saving config file: {e}")

    # Also update .env file
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        lines = []
        if config_data.get("supabase_url"):
            lines.append(f"SUPABASE_URL={config_data['supabase_url']}")
        if config_data.get("supabase_publishable_key"):
            lines.append(f"SUPABASE_PUBLISHABLE_KEY={config_data['supabase_publishable_key']}")
        if config_data.get("supabase_secret_key"):
            lines.append(f"SUPABASE_SECRET_KEY={config_data['supabase_secret_key']}")
        if config_data.get("supabase_jwks_url"):
            lines.append(f"SUPABASE_JWKS_URL={config_data['supabase_jwks_url']}")
        if lines:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[ConfigManager] Error saving .env file: {e}")
