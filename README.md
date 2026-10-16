# Product Scraper — Browser-First Desktop Application

A modern desktop application built in **Python** using **PySide6** and **Playwright for Python** that executes your exact JavaScript scraper implementations (`new_myntra_scraper.js` and `New_ajio_scraper.js`) in a single persistent browser session, and syncs scraped records directly to **Supabase** via duplicate-safe upserts.

---

## 🌟 Key Architecture & Highlights

1. **Browser-First Persistent Profile**:
   - Stores all cookies, local storage, sessions, and credentials at `%LOCALAPPDATA%\ProductScraper\BrowserProfile`.
   - On the first application launch, opens a visible browser window to Google (`https://www.google.com`).
   - You can browse normally, sign into your Myntra / AJIO accounts manually, solve CAPTCHAs if any, and persist your login state across application restarts.
   - Scraping uses the exact same persistent browser session without profile-lock conflicts.

2. **Reuse of Exact Scrapers**:
   - `scrapers/New_ajio_scraper.js` and `scrapers/new_myntra_scraper.js` are preserved **verbatim**.
   - No rewriting, replacing, or dummy data.
   - Scrapers execute inside active origin tabs (`https://www.ajio.com` and `https://www.myntra.com`) with full access to `window`, `fetch`, DOM parser, and same-origin cookies.
   - Real-time progress (`window.updateScraperProgress` and `window.__SCRAPER_PROGRESS__`) bridges directly into Python and PySide6 progress indicators.

3. **Desktop GUI Operations**:
   - **Open Browser**: Launches or focuses the persistent browser window.
   - **Scrape Myntra**: Executes `new_myntra_scraper.js` on Myntra and syncs results to Supabase.
   - **Scrape AJIO**: Executes `New_ajio_scraper.js` on AJIO and syncs results to Supabase.
   - **Scrape Both**: Sequentially executes both scrapers.
   - **Stop Scraping**: Safely interrupts and stops the active scraping job without affecting the persistent profile.

4. **Duplicate-Safe Supabase Sync**:
   - Deterministic IDs: `ajio_{product_id}` and `myntra_{product_id}_{size}_{color}_{variant_id}`.
   - Upserts using `on_conflict="id"`.
   - Local JSON backups are always saved automatically under `%LOCALAPPDATA%\ProductScraper\Data\`.

---

## 📋 Supabase SQL Setup

Run the following SQL script inside the Supabase Dashboard SQL Editor:

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

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
```

---

## 🚀 How to Run

1. Open your terminal in the application directory:
   ```powershell
   cd C:\Users\raj55\.gemini\antigravity\scratch\product_scraper_app
   ```

2. Run the application:
   ```powershell
   python main.py
   ```
