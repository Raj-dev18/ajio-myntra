import asyncio
import os
import sys

# Ensure local modules are found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from browser_manager import BrowserManager
from supabase_client import SupabaseSyncManager

async def test_pipeline():
    print("=== Testing Pipeline with 5-Table Schema ===")
    bm = BrowserManager()
    
    # 1. Test Browser Context
    print("Checking browser context...")
    ctx = await bm.get_context()
    print("Browser context launched successfully!")
    print("User Data Dir:", bm.profile_dir)
    assert os.path.exists(bm.profile_dir)
    
    # 2. Test Supabase Schema Normalization
    sm = SupabaseSyncManager()
    print("Testing record normalization for schema...")
    raw_ajio = [
        {
            "source": "ajio",
            "product_id": "469032120",
            "title": "Men Omni-Freeze Zero Graphic Crew-Neck T-Shirt",
            "price": 2499,
            "mrp": 3499,
            "available": True,
            "stock": 4,
            "variants": [{"size": "M", "available": True, "stock": 4}],
            "offers": [{"title": "Flat 10% Extra Off"}]
        }
    ]
    norm_ajio = sm.normalize_products_for_schema(raw_ajio, "ajio", "dummy-scraper-uuid")
    print("Normalized AJIO item:", norm_ajio[0]["source_product_id"], norm_ajio[0]["current_price"])
    assert norm_ajio[0]["source_product_id"] == "469032120"
    assert norm_ajio[0]["current_price"] == 2499.0
    assert norm_ajio[0]["current_mrp"] == 3499.0
    assert len(norm_ajio[0]["variants"]) == 1

    raw_myntra = [
        {
            "source": "myntra",
            "product_id": "1894231",
            "title": "Columbia Men Omni-Shade T-Shirt",
            "price": 1999,
            "mrp": 2999,
            "size": "L",
            "color": "Navy",
            "variant_id": "SKU89123",
            "available": True,
            "quantity": 5
        },
        {
            "source": "myntra",
            "product_id": "1894231",
            "title": "Columbia Men Omni-Shade T-Shirt",
            "price": 1999,
            "mrp": 2999,
            "size": "XL",
            "color": "Navy",
            "variant_id": "SKU89124",
            "available": True,
            "quantity": 3
        }
    ]
    norm_myntra = sm.normalize_products_for_schema(raw_myntra, "myntra", "dummy-scraper-uuid")
    print("Normalized Myntra aggregated product count:", len(norm_myntra))
    assert len(norm_myntra) == 1
    assert norm_myntra[0]["source_product_id"] == "1894231"
    assert norm_myntra[0]["stock"] == 8
    assert len(norm_myntra[0]["variants"]) == 2

    # 3. Test Local Backup Writing
    backup_file = sm.save_local_backup(raw_ajio, "test_ajio")
    print("Backup file written to:", backup_file)
    assert os.path.exists(backup_file)

    # 4. Clean up browser
    await bm._async_close()
    print("=== All 5-Table Schema Tests Passed Successfully! ===")

if __name__ == "__main__":
    asyncio.run(test_pipeline())
