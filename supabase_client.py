import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
from config_manager import DATA_DIR, ensure_directories

SUPABASE_SCHEMA_SQL = """CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Platforms & Scrapers
CREATE TABLE IF NOT EXISTS scrapers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Master Catalog (Company Source of Truth)
CREATE TABLE IF NOT EXISTS master_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ean TEXT,
    sku TEXT UNIQUE,
    style_code TEXT,
    style TEXT NOT NULL,
    soh INT DEFAULT 0,
    platform_ids JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_master_style ON master_catalog(style);
CREATE INDEX IF NOT EXISTS idx_master_ean ON master_catalog(ean);

-- 3. Canonical Scraped Products
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_id UUID NOT NULL REFERENCES scrapers(id) ON DELETE CASCADE,
    source_product_id TEXT NOT NULL,
    sku TEXT,
    ean TEXT,
    style TEXT,
    title TEXT NOT NULL,
    url TEXT,
    image_url TEXT,
    available BOOLEAN NOT NULL DEFAULT true,
    current_price NUMERIC(12, 2),
    current_mrp NUMERIC(12, 2),
    stock INT DEFAULT 0,
    variants JSONB NOT NULL DEFAULT '[]'::jsonb,
    offers JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_product_scraper_source UNIQUE (scraper_id, source_product_id)
);
CREATE INDEX IF NOT EXISTS idx_products_scraper_id ON products(scraper_id);
CREATE INDEX IF NOT EXISTS idx_products_style ON products(style);
CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean);

-- 4. Scrape Executions & Run Tracking
CREATE TABLE IF NOT EXISTS scrape_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_id UUID NOT NULL REFERENCES scrapers(id) ON DELETE CASCADE,
    business_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'paused', 'completed', 'failed')),
    stage TEXT NOT NULL CHECK (stage IN ('listings', 'products', 'variants', 'finalizing')),
    total_products INT NOT NULL DEFAULT 0,
    completed_products INT NOT NULL DEFAULT 0,
    failed_products INT NOT NULL DEFAULT 0,
    report JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    last_error TEXT
);

-- 5. Historical Price Log (Graphs & Price Drop Alerts)
CREATE TABLE IF NOT EXISTS price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    scrape_run_id UUID REFERENCES scrape_runs(id) ON DELETE SET NULL,
    price NUMERIC(12, 2) NOT NULL,
    mrp NUMERIC(12, 2),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_price_history_pid ON price_history(product_id, recorded_at DESC);

-- 6. Disable Row Level Security (RLS) so desktop scraper client can insert/upsert
ALTER TABLE scrapers DISABLE ROW LEVEL SECURITY;
ALTER TABLE products DISABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_runs DISABLE ROW LEVEL SECURITY;
ALTER TABLE price_history DISABLE ROW LEVEL SECURITY;
ALTER TABLE master_catalog DISABLE ROW LEVEL SECURITY;

-- 7. Pre-seed Scrapers Registry
INSERT INTO scrapers (name, source, enabled)
VALUES 
    ('AJIO Columbia Scraper', 'ajio', true),
    ('Myntra Columbia Scraper', 'myntra', true)
ON CONFLICT (name) DO NOTHING;
"""


class SupabaseSyncManager:
    """
    Manages synchronization with Supabase following the 5-table enterprise architecture:
    1. scrapers (platform registry)
    2. master_catalog (source of truth)
    3. products (canonical products with unique constraint (scraper_id, source_product_id))
    4. scrape_runs (run tracking & audit)
    5. price_history (historical time-series pricing logs)
    """

    def __init__(self, supabase_url: str = "", supabase_key: str = "", table_name: str = "products"):
        self.supabase_url = supabase_url.strip()
        self.supabase_key = supabase_key.strip()
        self.table_name = "products"  # Canonical table name
        self._client = None
        self._scraper_cache: Dict[str, str] = {}  # source -> scraper_id UUID
        self._init_client()

    def update_credentials(self, supabase_url: str, supabase_key: str, table_name: str = "products"):
        self.supabase_url = supabase_url.strip()
        self.supabase_key = supabase_key.strip()
        self.table_name = "products"
        self._client = None
        self._scraper_cache.clear()
        self._init_client()

    def _init_client(self):
        if self.supabase_url and self.supabase_key:
            try:
                from supabase import create_client
                self._client = create_client(self.supabase_url, self.supabase_key)
            except Exception as e:
                print(f"[SupabaseSyncManager] Failed to initialize client: {e}")
                self._client = None

    def is_configured(self) -> bool:
        return bool(self._client and self.supabase_url and self.supabase_key)

    def test_connection(self) -> Tuple[bool, str]:
        if not self.supabase_url or not self.supabase_key:
            return False, "Supabase URL and API Key are not configured."
        if not self._client:
            self._init_client()
        if not self._client:
            return False, "Failed to initialize Supabase client."

        try:
            # Check products table
            res = self._client.table("products").select("id").limit(1).execute()
            # Also check scrapers table
            _ = self._client.table("scrapers").select("id").limit(1).execute()
            return True, "Successfully connected to Supabase architecture (products, scrapers, scrape_runs, price_history)!"
        except Exception as e:
            err = str(e)
            if "relation" in err.lower() and "does not exist" in err.lower():
                return False, f"Connected to Supabase, but schema tables are missing. Please run the SQL schema script in your Supabase SQL Editor."
            return False, f"Supabase error: {err}"

    def get_or_create_scraper_id(self, source: str) -> Tuple[Optional[str], Optional[str]]:
        """Ensures the scraper platform exists in the `scrapers` table and returns (UUID, error_message)."""
        source_clean = source.strip().lower()
        if source_clean in self._scraper_cache:
            return self._scraper_cache[source_clean], None

        if not self.is_configured():
            return None, "Supabase client is not configured."

        name_map = {
            "ajio": "AJIO Columbia Scraper",
            "myntra": "Myntra Columbia Scraper"
        }
        scraper_name = name_map.get(source_clean, f"{source.capitalize()} Scraper")

        try:
            # Query existing scraper by name or source
            res = self._client.table("scrapers").select("id").or_(f"name.eq.{scraper_name},source.eq.{source_clean}").execute()
            if res.data and len(res.data) > 0:
                s_id = res.data[0]["id"]
                self._scraper_cache[source_clean] = s_id
                return s_id, None

            # Insert if not existing
            new_res = self._client.table("scrapers").insert({
                "name": scraper_name,
                "source": source_clean,
                "enabled": True
            }).execute()

            if new_res.data and len(new_res.data) > 0:
                s_id = new_res.data[0]["id"]
                self._scraper_cache[source_clean] = s_id
                return s_id, None
        except Exception as e:
            err = str(e)
            print(f"[SupabaseSyncManager] Error in get_or_create_scraper_id for {source}: {err}")
            if "42501" in err or "row-level security" in err.lower():
                return None, "Row-Level Security (RLS) policy is blocking inserts on table 'scrapers'.\nRun 'ALTER TABLE scrapers DISABLE ROW LEVEL SECURITY;' in your Supabase SQL Editor."
            return None, f"Failed to register scraper: {err}"

        return None, "Could not determine scraper_id."

    def start_scrape_run(self, scraper_id: str, total_products: int = 0) -> Optional[str]:
        """Creates a record in `scrape_runs` to track the execution."""
        if not self.is_configured() or not scraper_id:
            return None

        today = datetime.now(timezone.utc).date().isoformat()
        try:
            res = self._client.table("scrape_runs").insert({
                "scraper_id": scraper_id,
                "business_date": today,
                "status": "running",
                "stage": "listings",
                "total_products": total_products,
                "completed_products": 0,
                "failed_products": 0
            }).execute()

            if res.data and len(res.data) > 0:
                return res.data[0]["id"]
        except Exception as e:
            print(f"[SupabaseSyncManager] Failed to start scrape_run: {e}")
        return None

    def finish_scrape_run(self, run_id: str, status: str, total: int, completed: int, failed: int = 0, err: str = None, report: dict = None):
        """Updates the `scrape_runs` execution record upon completion or failure."""
        if not self.is_configured() or not run_id:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "status": status,
            "stage": "finalizing" if status == "completed" else "products",
            "total_products": total,
            "completed_products": completed,
            "failed_products": failed,
            "updated_at": now_iso,
            "completed_at": now_iso if status in ("completed", "failed") else None
        }
        if err:
            payload["last_error"] = str(err)
        if report:
            payload["report"] = report

        try:
            self._client.table("scrape_runs").update(payload).eq("id", run_id).execute()
        except Exception as e:
            print(f"[SupabaseSyncManager] Failed to update scrape_run: {e}")

    def normalize_products_for_schema(self, raw_products: List[Dict[str, Any]], source: str, scraper_id: str) -> List[Dict[str, Any]]:
        """
        Normalizes raw scraped records into the canonical `products` table schema.
        Handles both AJIO (product-level) and Myntra (variant-level aggregated to product-level).
        """
        source_lower = source.strip().lower()
        now_iso = datetime.now(timezone.utc).isoformat()

        if source_lower == "ajio":
            normalized_list = []
            for raw in raw_products:
                pid = str(raw.get("product_id") or raw.get("code") or raw.get("id") or "").strip()
                if not pid:
                    continue

                price = float(raw.get("price") or raw.get("sale_price") or 0.0)
                mrp = float(raw.get("mrp") or price)
                stock = int(raw.get("stock") or raw.get("total_sellable_stock") or 0)
                variants = raw.get("variants") or []
                offers = raw.get("offers") or []

                normalized_list.append({
                    "scraper_id": scraper_id,
                    "source_product_id": pid,
                    "sku": str(raw.get("sku") or pid),
                    "ean": raw.get("ean"),
                    "style": str(raw.get("brick_name") or raw.get("title") or ""),
                    "title": str(raw.get("title") or "Columbia Product").strip(),
                    "url": str(raw.get("url") or ""),
                    "image_url": str(raw.get("image_url") or ""),
                    "available": bool(raw.get("available", True)),
                    "current_price": price,
                    "current_mrp": mrp,
                    "stock": stock,
                    "variants": variants,
                    "offers": offers,
                    "updated_at": now_iso
                })
            return normalized_list

        elif source_lower == "myntra":
            # Myntra outputs variant-level items; aggregate into canonical product records
            grouped: Dict[str, Dict[str, Any]] = {}
            for item in raw_products:
                pid = str(item.get("product_id") or item.get("id") or "").strip()
                if not pid:
                    continue

                if pid not in grouped:
                    price = float(item.get("price") or 0.0)
                    mrp = float(item.get("mrp") or price)
                    grouped[pid] = {
                        "scraper_id": scraper_id,
                        "source_product_id": pid,
                        "sku": str(item.get("variant_id") or item.get("sku") or pid),
                        "ean": None,
                        "style": str(item.get("title") or ""),
                        "title": str(item.get("title") or "Columbia Product").strip(),
                        "url": str(item.get("url") or ""),
                        "image_url": str(item.get("image_url") or ""),
                        "available": False,
                        "current_price": price,
                        "current_mrp": mrp,
                        "stock": 0,
                        "variants": [],
                        "offers": item.get("offers") or [],
                        "updated_at": now_iso
                    }

                # Aggregate variant details
                is_available = bool(item.get("available"))
                qty = item.get("quantity") or (1 if is_available else 0)
                if is_available:
                    grouped[pid]["available"] = True
                if isinstance(qty, (int, float)):
                    grouped[pid]["stock"] += int(qty)

                grouped[pid]["variants"].append({
                    "size": item.get("size", ""),
                    "color": item.get("color", ""),
                    "variant_id": item.get("variant_id", ""),
                    "available": is_available,
                    "quantity": qty,
                    "price": item.get("price"),
                    "mrp": item.get("mrp")
                })

                if not grouped[pid]["offers"] and item.get("offers"):
                    grouped[pid]["offers"] = item.get("offers")

            return list(grouped.values())

        else:
            # Fallback for mixed or generic
            result = []
            for raw in raw_products:
                pid = str(raw.get("source_product_id") or raw.get("product_id") or raw.get("id") or "")
                if pid:
                    result.append({
                        "scraper_id": scraper_id,
                        "source_product_id": pid,
                        "sku": str(raw.get("sku") or pid),
                        "ean": raw.get("ean"),
                        "style": str(raw.get("style") or raw.get("title") or ""),
                        "title": str(raw.get("title") or "Columbia Product"),
                        "url": str(raw.get("url") or ""),
                        "image_url": str(raw.get("image_url") or ""),
                        "available": bool(raw.get("available", True)),
                        "current_price": float(raw.get("price") or raw.get("current_price") or 0.0),
                        "current_mrp": float(raw.get("mrp") or raw.get("current_mrp") or 0.0),
                        "stock": int(raw.get("stock") or 0),
                        "variants": raw.get("variants") or [],
                        "offers": raw.get("offers") or [],
                        "updated_at": now_iso
                    })
            return result

    def save_local_backup(self, records: List[Dict[str, Any]], source: str) -> str:
        """Saves a local JSON backup under %LOCALAPPDATA%\\ProductScraper\\Data."""
        ensure_directories()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{source}_backup_{len(records)}_{ts}.json"
        filepath = os.path.join(DATA_DIR, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "source": source,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "total_records": len(records),
                    "products": records
                }, f, indent=2)
            return filepath
        except Exception as e:
            print(f"[SupabaseSyncManager] Failed to write local backup: {e}")
            return ""

    def upsert_products(self, raw_products: List[Dict[str, Any]], source: str, batch_size: int = 50) -> Tuple[int, int, str]:
        """
        Full enterprise upsert workflow following your Supabase schema:
        1. Ensures scraper entry in `scrapers` table -> returns `scraper_id`.
        2. Logs run execution in `scrape_runs` table -> returns `scrape_run_id`.
        3. Upserts products into `products` table using unique constraint `(scraper_id, source_product_id)`.
        4. Logs price snapshot for each product into `price_history` table.
        5. Updates `scrape_runs` to 'completed'.
        """
        if not raw_products:
            return 0, 0, "No records to sync."

        # Always save local JSON backup first
        backup_path = self.save_local_backup(raw_products, source)

        if not self.is_configured():
            msg = f"Supabase is not configured. Saved {len(raw_products)} records locally to: {backup_path}"
            return 0, len(raw_products), msg

        # 1. Get or create scraper_id
        scraper_id, err_msg = self.get_or_create_scraper_id(source)
        if not scraper_id:
            msg = f"{err_msg or 'Could not register scraper in scrapers table'}\n\nLocal backup is safely preserved at:\n{backup_path}"
            return 0, len(raw_products), msg

        # 2. Normalize records to products table schema
        canonical_products = self.normalize_products_for_schema(raw_products, source, scraper_id)
        total_count = len(canonical_products)

        # 3. Create run tracking in `scrape_runs`
        run_id = self.start_scrape_run(scraper_id, total_products=total_count)

        upserted_count = 0
        price_history_records = []
        errors = []

        # 4. Batch upsert into `products` table
        for i in range(0, total_count, batch_size):
            chunk = canonical_products[i:i + batch_size]
            try:
                # Upsert on unique constraint (scraper_id, source_product_id)
                res = self._client.table("products").upsert(
                    chunk,
                    on_conflict="scraper_id,source_product_id"
                ).execute()

                upserted_count += len(chunk)

                # Prepare price_history rows from returned or queried data
                if res.data:
                    for row in res.data:
                        price_history_records.append({
                            "product_id": row["id"],
                            "scrape_run_id": run_id,
                            "price": row["current_price"],
                            "mrp": row["current_mrp"],
                            "recorded_at": datetime.now(timezone.utc).isoformat()
                        })
            except Exception as e:
                err_text = str(e)
                print(f"[SupabaseSyncManager] Upsert batch error ({i}-{i+len(chunk)}): {err_text}")
                errors.append(err_text)

        # 5. Insert price_history snapshot
        if price_history_records:
            try:
                for j in range(0, len(price_history_records), batch_size):
                    hist_chunk = price_history_records[j:j + batch_size]
                    self._client.table("price_history").insert(hist_chunk).execute()
            except Exception as e:
                print(f"[SupabaseSyncManager] Failed to insert price_history: {e}")

        # 6. Finalize scrape_runs
        final_status = "completed" if not errors else "failed"
        self.finish_scrape_run(
            run_id=run_id,
            status=final_status,
            total=total_count,
            completed=upserted_count,
            failed=total_count - upserted_count,
            err="; ".join(errors[:2]) if errors else None,
            report={"backup_file": backup_path, "price_history_logged": len(price_history_records)}
        )

        if errors:
            err_summary = "; ".join(errors[:2])
            return upserted_count, total_count, f"Upserted {upserted_count}/{total_count} products. Run ID: {run_id}. Issues: {err_summary}"

        return upserted_count, total_count, f"Successfully upserted {upserted_count}/{total_count} products into Supabase (products, price_history, scrape_runs). Run ID: {run_id}!"
