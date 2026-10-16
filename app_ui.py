import os
import sys
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QFont, QColor, QClipboard, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar, QTabWidget, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QLineEdit, QGroupBox, QHeaderView, QMessageBox,
    QFileDialog, QSplitter, QFrame
)

from config_manager import (
    load_config, save_config, BROWSER_PROFILE_DIR, DATA_DIR, ensure_directories
)
from browser_manager import BrowserManager
from scraper_runner import ScraperRunner
from supabase_client import SupabaseSyncManager, SUPABASE_SCHEMA_SQL


class ScraperSignals(QObject):
    log_message = Signal(str)
    progress_update = Signal(dict)
    scraping_started = Signal(str)
    scraping_finished = Signal(str, list)
    scraping_error = Signal(str)
    browser_state_changed = Signal(bool)
    supabase_sync_completed = Signal(int, int, str)


class MainWindow(QMainWindow):
    def __init__(self, browser_manager: BrowserManager):
        super().__init__()
        self.setWindowTitle("Product Scraper — Browser-First Desktop Application")
        self.resize(1180, 780)
        self.setMinimumSize(950, 600)

        self.browser_manager = browser_manager
        self.signals = ScraperSignals()
        self.scraper_runner = ScraperRunner(browser_manager)

        self.config = load_config()
        self.supabase_manager = SupabaseSyncManager(
            supabase_url=self.config.get("supabase_url", ""),
            supabase_key=self.config.get("supabase_key", ""),
            table_name=self.config.get("supabase_table", "products")
        )

        self.all_scraped_products: List[Dict[str, Any]] = []
        self.active_job_name: Optional[str] = None
        self.is_scraping: bool = False

        self._connect_signals()
        self._setup_runner_callbacks()
        self._init_ui()
        self._apply_styles()

        # Timer to monitor browser state periodically
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._check_browser_status)
        self.status_timer.start(2000)

        # Notify initial browser status
        self._check_browser_status()

    def _connect_signals(self):
        self.signals.log_message.connect(self._on_log_message)
        self.signals.progress_update.connect(self._on_progress_update)
        self.signals.scraping_started.connect(self._on_scraping_started)
        self.signals.scraping_finished.connect(self._on_scraping_finished)
        self.signals.scraping_error.connect(self._on_scraping_error)
        self.signals.browser_state_changed.connect(self._on_browser_state_changed)
        self.signals.supabase_sync_completed.connect(self._on_supabase_sync_completed)

    def _setup_runner_callbacks(self):
        self.scraper_runner.on_log = lambda msg: self.signals.log_message.emit(msg)
        self.scraper_runner.on_progress = lambda p: self.signals.progress_update.emit(p)
        self.browser_manager.set_on_browser_closed_callback(
            lambda: self.signals.browser_state_changed.emit(False)
        )

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # 1. Header Section
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        app_title = QLabel("Columbia Product Scraper")
        app_title.setObjectName("AppTitle")
        app_subtitle = QLabel("Persistent Session Browser & Real-time Supabase Sync (Ajio + Myntra)")
        app_subtitle.setObjectName("AppSubtitle")
        title_box.addWidget(app_title)
        title_box.addWidget(app_subtitle)
        header_layout.addLayout(title_box)

        header_layout.addStretch()

        # Status Chips
        self.browser_chip = QLabel("● Browser: Offline")
        self.browser_chip.setObjectName("BrowserChipOffline")
        self.supabase_chip = QLabel("● Supabase: Checking...")
        self.supabase_chip.setObjectName("SupabaseChipOffline")
        self._update_supabase_chip()

        header_layout.addWidget(self.browser_chip)
        header_layout.addWidget(self.supabase_chip)
        main_layout.addLayout(header_layout)

        # 2. Controls Toolbar
        controls_group = QGroupBox("Operations & Actions")
        controls_layout = QHBoxLayout(controls_group)
        controls_layout.setContentsMargins(12, 12, 12, 12)
        controls_layout.setSpacing(10)

        self.btn_open_browser = QPushButton("🌐 Open Browser")
        self.btn_open_browser.setObjectName("BtnOpenBrowser")
        self.btn_open_browser.clicked.connect(self.action_open_browser)

        self.btn_scrape_myntra = QPushButton("🛒 Scrape Myntra")
        self.btn_scrape_myntra.setObjectName("BtnScrapeMyntra")
        self.btn_scrape_myntra.clicked.connect(self.action_scrape_myntra)

        self.btn_scrape_ajio = QPushButton("🛍️ Scrape AJIO")
        self.btn_scrape_ajio.setObjectName("BtnScrapeAjio")
        self.btn_scrape_ajio.clicked.connect(self.action_scrape_ajio)

        self.btn_scrape_both = QPushButton("⚡ Scrape Both")
        self.btn_scrape_both.setObjectName("BtnScrapeBoth")
        self.btn_scrape_both.clicked.connect(self.action_scrape_both)

        self.btn_stop_scraping = QPushButton("🛑 Stop Scraping")
        self.btn_stop_scraping.setObjectName("BtnStopScraping")
        self.btn_stop_scraping.setEnabled(False)
        self.btn_stop_scraping.clicked.connect(self.action_stop_scraping)

        controls_layout.addWidget(self.btn_open_browser)
        controls_layout.addWidget(self.btn_scrape_myntra)
        controls_layout.addWidget(self.btn_scrape_ajio)
        controls_layout.addWidget(self.btn_scrape_both)
        controls_layout.addWidget(self.btn_stop_scraping)
        main_layout.addWidget(controls_group)

        # 3. Progress Card
        progress_box = QFrame()
        progress_box.setObjectName("ProgressCard")
        p_layout = QVBoxLayout(progress_box)
        p_layout.setContentsMargins(14, 10, 14, 10)
        p_layout.setSpacing(6)

        p_info_layout = QHBoxLayout()
        self.lbl_progress_stage = QLabel("Status: Idle")
        self.lbl_progress_stage.setObjectName("ProgressStageLabel")
        self.lbl_progress_metrics = QLabel("Records: 0")
        self.lbl_progress_metrics.setObjectName("ProgressMetricsLabel")
        p_info_layout.addWidget(self.lbl_progress_stage)
        p_info_layout.addStretch()
        p_info_layout.addWidget(self.lbl_progress_metrics)
        p_layout.addLayout(p_info_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        p_layout.addWidget(self.progress_bar)
        main_layout.addWidget(progress_box)

        # 4. Main Tabs (Data, Logs, Settings)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")

        # Tab 1: Products Data Table
        tab_data = QWidget()
        data_layout = QVBoxLayout(tab_data)
        data_toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filter products by title, ID, or brand...")
        self.search_input.textChanged.connect(self._filter_table)
        data_toolbar.addWidget(self.search_input)

        self.btn_export_json = QPushButton("📥 Export Data (JSON)")
        self.btn_export_json.clicked.connect(self.action_export_json)
        self.btn_sync_supabase = QPushButton("☁️ Sync to Supabase")
        self.btn_sync_supabase.clicked.connect(self.action_sync_to_supabase)
        data_toolbar.addWidget(self.btn_export_json)
        data_toolbar.addWidget(self.btn_sync_supabase)
        data_layout.addLayout(data_toolbar)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(11)
        self.table_widget.setHorizontalHeaderLabels([
            "ID", "Source", "Title", "Price (₹)", "MRP (₹)", "Discount",
            "In Stock", "Stock Qty", "Size / Variant", "Offers", "URL"
        ])
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setAlternatingRowColors(True)
        data_layout.addWidget(self.table_widget)
        self.tabs.addTab(tab_data, "📊 Scraped Products Data (0)")

        # Tab 2: Live Logs
        tab_logs = QWidget()
        logs_layout = QVBoxLayout(tab_logs)
        logs_toolbar = QHBoxLayout()
        btn_clear_logs = QPushButton("Clear Logs")
        btn_clear_logs.clicked.connect(lambda: self.log_text.clear())
        btn_copy_logs = QPushButton("Copy Logs")
        btn_copy_logs.clicked.connect(self._copy_logs_to_clipboard)
        logs_toolbar.addStretch()
        logs_toolbar.addWidget(btn_clear_logs)
        logs_toolbar.addWidget(btn_copy_logs)
        logs_layout.addLayout(logs_toolbar)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("ConsoleLog")
        logs_layout.addWidget(self.log_text)
        self.tabs.addTab(tab_logs, "📜 Real-Time Scraper Console")

        # Tab 3: Supabase & Storage Settings
        tab_settings = QWidget()
        settings_layout = QVBoxLayout(tab_settings)

        sb_group = QGroupBox("Supabase Database Configuration")
        sb_layout = QVBoxLayout(sb_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("SUPABASE_URL:"))
        self.input_sb_url = QLineEdit(self.config.get("supabase_url", ""))
        self.input_sb_url.setPlaceholderText("https://xyzcompany.supabase.co")
        row1.addWidget(self.input_sb_url)
        sb_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("SUPABASE_SECRET_KEY:"))
        self.input_sb_secret_key = QLineEdit(self.config.get("supabase_secret_key", ""))
        self.input_sb_secret_key.setEchoMode(QLineEdit.Password)
        self.input_sb_secret_key.setPlaceholderText("sb_secret_... (Bypasses RLS, keeps RLS enabled)")
        row2.addWidget(self.input_sb_secret_key)
        sb_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("SUPABASE_PUBLISHABLE_KEY:"))
        self.input_sb_pub_key = QLineEdit(self.config.get("supabase_publishable_key", ""))
        self.input_sb_pub_key.setEchoMode(QLineEdit.Password)
        self.input_sb_pub_key.setPlaceholderText("sb_publishable_...")
        row3.addWidget(self.input_sb_pub_key)
        sb_layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("SUPABASE_JWKS_URL:"))
        self.input_sb_jwks = QLineEdit(self.config.get("supabase_jwks_url", ""))
        self.input_sb_jwks.setPlaceholderText("https://.../jwks.json")
        row4.addWidget(self.input_sb_jwks)
        sb_layout.addLayout(row4)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Target Table Name:"))
        self.input_sb_table = QLineEdit(self.config.get("supabase_table", "products"))
        row5.addWidget(self.input_sb_table)
        sb_layout.addLayout(row5)

        sb_btn_row = QHBoxLayout()
        btn_test_sb = QPushButton("🔍 Test Supabase Connection")
        btn_test_sb.clicked.connect(self.action_test_supabase)
        btn_save_sb = QPushButton("💾 Save Supabase Settings")
        btn_save_sb.clicked.connect(self.action_save_settings)
        btn_copy_sql = QPushButton("📋 Copy Supabase SQL Schema")
        btn_copy_sql.clicked.connect(self.action_copy_sql_schema)
        sb_btn_row.addWidget(btn_test_sb)
        sb_btn_row.addWidget(btn_save_sb)
        sb_btn_row.addWidget(btn_copy_sql)
        sb_layout.addLayout(sb_btn_row)
        settings_layout.addWidget(sb_group)

        # Paths Group
        paths_group = QGroupBox("Local Storage Paths (Strictly Local)")
        paths_layout = QVBoxLayout(paths_group)

        p1 = QHBoxLayout()
        p1.addWidget(QLabel("Browser Profile:"))
        lbl_profile = QLabel(BROWSER_PROFILE_DIR)
        lbl_profile.setStyleSheet("color: #718096; font-family: monospace;")
        btn_open_profile = QPushButton("Open Folder")
        btn_open_profile.clicked.connect(lambda: self._open_in_explorer(BROWSER_PROFILE_DIR))
        p1.addWidget(lbl_profile)
        p1.addStretch()
        p1.addWidget(btn_open_profile)
        paths_layout.addLayout(p1)

        p2 = QHBoxLayout()
        p2.addWidget(QLabel("Data Backups:"))
        lbl_data = QLabel(DATA_DIR)
        lbl_data.setStyleSheet("color: #718096; font-family: monospace;")
        btn_open_data = QPushButton("Open Folder")
        btn_open_data.clicked.connect(lambda: self._open_in_explorer(DATA_DIR))
        p2.addWidget(lbl_data)
        p2.addStretch()
        p2.addWidget(btn_open_data)
        paths_layout.addLayout(p2)

        settings_layout.addWidget(paths_group)
        settings_layout.addStretch()
        self.tabs.addTab(tab_settings, "⚙️ Supabase & Paths Settings")

        main_layout.addWidget(self.tabs)

        self._log(f"Application initialized. Dedicated browser profile: {BROWSER_PROFILE_DIR}")

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
                font-size: 13px;
                color: #2D3748;
            }
            QMainWindow {
                background-color: #F7FAFC;
            }
            #AppTitle {
                font-size: 20px;
                font-weight: 700;
                color: #1A202C;
            }
            #AppSubtitle {
                font-size: 12px;
                color: #718096;
            }
            #BrowserChipOnline {
                background-color: #DEF7EC;
                color: #03543F;
                font-weight: 600;
                padding: 5px 12px;
                border-radius: 12px;
            }
            #BrowserChipOffline {
                background-color: #EDF2F7;
                color: #4A5568;
                font-weight: 600;
                padding: 5px 12px;
                border-radius: 12px;
            }
            #SupabaseChipOnline {
                background-color: #DEF7EC;
                color: #03543F;
                font-weight: 600;
                padding: 5px 12px;
                border-radius: 12px;
            }
            #SupabaseChipOffline {
                background-color: #FEF08A;
                color: #713F12;
                font-weight: 600;
                padding: 5px 12px;
                border-radius: 12px;
            }
            QGroupBox {
                font-weight: 600;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                margin-top: 10px;
                background-color: #FFFFFF;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 600;
                background-color: #EDF2F7;
                border: 1px solid #CBD5E0;
            }
            QPushButton:hover {
                background-color: #E2E8F0;
            }
            QPushButton:pressed {
                background-color: #CBD5E0;
            }
            #BtnOpenBrowser {
                background-color: #3182CE;
                color: #FFFFFF;
                border: none;
            }
            #BtnOpenBrowser:hover {
                background-color: #2B6CB0;
            }
            #BtnScrapeMyntra {
                background-color: #E53E3E;
                color: #FFFFFF;
                border: none;
            }
            #BtnScrapeMyntra:hover {
                background-color: #C53030;
            }
            #BtnScrapeAjio {
                background-color: #2C5282;
                color: #FFFFFF;
                border: none;
            }
            #BtnScrapeAjio:hover {
                background-color: #2A4365;
            }
            #BtnScrapeBoth {
                background-color: #38A169;
                color: #FFFFFF;
                border: none;
            }
            #BtnScrapeBoth:hover {
                background-color: #2F855A;
            }
            #BtnStopScraping {
                background-color: #718096;
                color: #FFFFFF;
                border: none;
            }
            #BtnStopScraping:enabled {
                background-color: #E53E3E;
            }
            #BtnStopScraping:enabled:hover {
                background-color: #9B2C2C;
            }
            #ProgressCard {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            #ProgressStageLabel {
                font-weight: 600;
                color: #2D3748;
            }
            #ProgressMetricsLabel {
                color: #718096;
                font-size: 12px;
            }
            QProgressBar {
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                text-align: center;
                background-color: #EDF2F7;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #3182CE;
                border-radius: 3px;
            }
            QTableWidget {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                gridline-color: #EDF2F7;
            }
            QHeaderView::section {
                background-color: #EDF2F7;
                padding: 6px;
                font-weight: 600;
                border: none;
                border-right: 1px solid #CBD5E0;
            }
            #ConsoleLog {
                background-color: #1A202C;
                color: #E2E8F0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border-radius: 6px;
                padding: 8px;
            }
            QLineEdit {
                border: 1px solid #CBD5E0;
                border-radius: 6px;
                padding: 6px 10px;
                background-color: #FFFFFF;
            }
            QLineEdit:focus {
                border: 1px solid #3182CE;
            }
        """)

    def _check_browser_status(self):
        alive = self.browser_manager.is_browser_open()
        self.signals.browser_state_changed.emit(alive)

    def _on_browser_state_changed(self, alive: bool):
        if alive:
            self.browser_chip.setText("● Browser: Active")
            self.browser_chip.setObjectName("BrowserChipOnline")
        else:
            self.browser_chip.setText("● Browser: Closed")
            self.browser_chip.setObjectName("BrowserChipOffline")
        self.browser_chip.setStyle(self.browser_chip.style())

    def _update_supabase_chip(self):
        if self.supabase_manager.is_configured():
            self.supabase_chip.setText("● Supabase: Configured")
            self.supabase_chip.setObjectName("SupabaseChipOnline")
        else:
            self.supabase_chip.setText("● Supabase: Not Configured")
            self.supabase_chip.setObjectName("SupabaseChipOffline")
        self.supabase_chip.setStyle(self.supabase_chip.style())

    def _log(self, text: str):
        now = datetime.now().strftime("%H:%M:%S")
        line = f"[{now}] {text}"
        self.log_text.appendPlainText(line)

    def _on_log_message(self, text: str):
        self.log_text.appendPlainText(text)

    def _on_progress_update(self, p: Dict[str, Any]):
        stage = p.get("stage", "Scraping")
        current = p.get("current", 0)
        total = p.get("total", 0)
        count = p.get("products_count") or p.get("records") or len(self.all_scraped_products)
        eta = p.get("eta_seconds", 0)

        percent = int((current / total) * 100) if total > 0 else 0
        percent = max(0, min(100, percent))
        self.progress_bar.setValue(percent)

        eta_str = f" | ETA: {eta}s" if eta > 0 else ""
        self.lbl_progress_stage.setText(f"Status: {stage} (Page {current}/{total}){eta_str}")
        self.lbl_progress_metrics.setText(f"Products Extracted: {count}")

    def _on_scraping_started(self, job_name: str):
        self.is_scraping = True
        self.active_job_name = job_name
        self.btn_open_browser.setEnabled(True)
        self.btn_scrape_myntra.setEnabled(False)
        self.btn_scrape_ajio.setEnabled(False)
        self.btn_scrape_both.setEnabled(False)
        self.btn_stop_scraping.setEnabled(True)
        self.lbl_progress_stage.setText(f"Status: Starting {job_name}...")
        self.progress_bar.setValue(0)
        self._log(f"Started {job_name} job using persistent session.")

    def _on_scraping_finished(self, job_name: str, products: List[Dict[str, Any]]):
        self.is_scraping = False
        self.btn_scrape_myntra.setEnabled(True)
        self.btn_scrape_ajio.setEnabled(True)
        self.btn_scrape_both.setEnabled(True)
        self.btn_stop_scraping.setEnabled(False)
        self.progress_bar.setValue(100)
        self.lbl_progress_stage.setText(f"Status: {job_name} completed successfully!")
        self.lbl_progress_metrics.setText(f"Total Products: {len(products)}")

        self._log(f"Finished {job_name}. Extracted {len(products)} products.")

        if products:
            self._add_products_to_table(products)
            # Sync to Supabase directly
            self._log(f"Auto-syncing {len(products)} products to Supabase...")
            source = "myntra" if "myntra" in job_name.lower() else "ajio"
            upserted, total, msg = self.supabase_manager.upsert_products(products, source=source)
            self.signals.supabase_sync_completed.emit(upserted, total, msg)

    def _on_scraping_error(self, err_msg: str):
        self.is_scraping = False
        self.btn_scrape_myntra.setEnabled(True)
        self.btn_scrape_ajio.setEnabled(True)
        self.btn_scrape_both.setEnabled(True)
        self.btn_stop_scraping.setEnabled(False)
        self.lbl_progress_stage.setText(f"Status: Error occurred.")
        self._log(f"Scraping Error: {err_msg}")
        QMessageBox.warning(self, "Scraping Alert", f"Scraping encountered an issue:\n{err_msg}")

    def _on_supabase_sync_completed(self, upserted: int, total: int, msg: str):
        self._log(f"[Supabase Sync] {msg}")
        self._update_supabase_chip()

    def _add_products_to_table(self, products: List[Dict[str, Any]]):
        self.all_scraped_products.extend(products)
        self.tabs.setTabText(0, f"📊 Scraped Products Data ({len(self.all_scraped_products)})")
        self._populate_table(self.all_scraped_products)

    def _populate_table(self, records: List[Dict[str, Any]]):
        self.table_widget.setRowCount(len(records))
        for row, item in enumerate(records):
            pid = str(item.get("product_id") or item.get("id") or "")
            source = str(item.get("source") or "")
            title = str(item.get("title") or "")
            price = str(item.get("price") or item.get("sale_price") or 0)
            mrp = str(item.get("mrp") or 0)
            discount = str(item.get("discount_formatted") or f"{item.get('discount_percent', 0)}%")
            in_stock = "Yes" if item.get("available") else "No"
            stock = str(item.get("stock") or item.get("quantity") or 0)
            size = str(item.get("size") or (item.get("variants", [{}])[0].get("size") if item.get("variants") else ""))
            offers_count = str(len(item.get("offers") or []))
            url = str(item.get("url") or "")

            self.table_widget.setItem(row, 0, QTableWidgetItem(pid))
            self.table_widget.setItem(row, 1, QTableWidgetItem(source.upper()))
            self.table_widget.setItem(row, 2, QTableWidgetItem(title))
            self.table_widget.setItem(row, 3, QTableWidgetItem(price))
            self.table_widget.setItem(row, 4, QTableWidgetItem(mrp))
            self.table_widget.setItem(row, 5, QTableWidgetItem(discount))
            self.table_widget.setItem(row, 6, QTableWidgetItem(in_stock))
            self.table_widget.setItem(row, 7, QTableWidgetItem(stock))
            self.table_widget.setItem(row, 8, QTableWidgetItem(size))
            self.table_widget.setItem(row, 9, QTableWidgetItem(offers_count))
            self.table_widget.setItem(row, 10, QTableWidgetItem(url))

    def _filter_table(self, query: str):
        query = query.strip().lower()
        if not query:
            self._populate_table(self.all_scraped_products)
            return

        filtered = [
            p for p in self.all_scraped_products
            if query in str(p.get("title", "")).lower()
            or query in str(p.get("product_id", "")).lower()
            or query in str(p.get("source", "")).lower()
        ]
        self._populate_table(filtered)

    # -------------------------------------------------------------
    # User Actions
    # -------------------------------------------------------------
    def action_open_browser(self):
        """Opens or brings up the single persistent browser window with Google."""
        self._log("Opening persistent browser window (Start page: Google)...")
        try:
            self.browser_manager.open_browser()
            self._check_browser_status()
        except Exception as e:
            self._log(f"Error opening browser: {e}")
            QMessageBox.critical(self, "Browser Error", str(e))

    def action_scrape_myntra(self):
        if self.is_scraping:
            return

        self.signals.scraping_started.emit("Myntra Scraper")

        async def worker():
            try:
                products = await self.scraper_runner.run_myntra_scraper()
                self.signals.scraping_finished.emit("Myntra Scraper", products)
            except Exception as e:
                self.signals.scraping_error.emit(str(e))

        self.browser_manager.run_coroutine(worker())

    def action_scrape_ajio(self):
        if self.is_scraping:
            return

        self.signals.scraping_started.emit("AJIO Scraper")

        async def worker():
            try:
                products = await self.scraper_runner.run_ajio_scraper()
                self.signals.scraping_finished.emit("AJIO Scraper", products)
            except Exception as e:
                self.signals.scraping_error.emit(str(e))

        self.browser_manager.run_coroutine(worker())

    def action_scrape_both(self):
        if self.is_scraping:
            return

        self.signals.scraping_started.emit("Both Scrapers (Sequential)")

        async def worker():
            try:
                self.signals.log_message.emit("[Sequential Runner] Starting Part 1: Myntra Scraper...")
                myntra_products = await self.scraper_runner.run_myntra_scraper()
                self.signals.scraping_finished.emit("Myntra (Part 1)", myntra_products)

                if self.scraper_runner._cancel_requested:
                    self.signals.log_message.emit("[Sequential Runner] Stopped after Myntra.")
                    return

                self.signals.log_message.emit("[Sequential Runner] Starting Part 2: AJIO Scraper...")
                ajio_products = await self.scraper_runner.run_ajio_scraper()
                self.signals.scraping_finished.emit("AJIO (Part 2)", ajio_products)

                self.signals.log_message.emit("[Sequential Runner] Finished sequential scrape of both websites.")
            except Exception as e:
                self.signals.scraping_error.emit(str(e))

        self.browser_manager.run_coroutine(worker())

    def action_stop_scraping(self):
        self._log("Stop Scraping button clicked. Halting scraper cleanly...")
        self.scraper_runner.request_stop()
        self.btn_stop_scraping.setEnabled(False)

    def action_export_json(self):
        if not self.all_scraped_products:
            QMessageBox.information(self, "Export", "No products have been scraped yet.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Products JSON", os.path.join(DATA_DIR, "scraped_products_export.json"), "JSON Files (*.json)"
        )
        if file_path:
            import json
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.all_scraped_products, f, indent=2)
                QMessageBox.information(self, "Export Successful", f"Saved {len(self.all_scraped_products)} products to:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def action_sync_to_supabase(self):
        if not self.all_scraped_products:
            QMessageBox.information(self, "Sync", "No scraped products to sync.")
            return

        # Group by actual source (e.g. 'myntra', 'ajio')
        by_source: Dict[str, List[Dict[str, Any]]] = {}
        for p in self.all_scraped_products:
            s = str(p.get("source") or "myntra").lower()
            by_source.setdefault(s, []).append(p)

        total_upserted = 0
        total_records = len(self.all_scraped_products)
        messages = []

        for source, prods in by_source.items():
            self._log(f"Manual Sync requested for {len(prods)} {source.upper()} records...")
            upserted, count, msg = self.supabase_manager.upsert_products(prods, source=source)
            total_upserted += upserted
            messages.append(msg)

        final_msg = "\n\n".join(messages)
        self.signals.supabase_sync_completed.emit(total_upserted, total_records, final_msg)
        QMessageBox.information(self, "Supabase Sync Result", final_msg)

    def action_test_supabase(self):
        url = self.input_sb_url.text().strip()
        secret_key = self.input_sb_secret_key.text().strip()
        pub_key = self.input_sb_pub_key.text().strip()
        active_key = secret_key or pub_key
        table = self.input_sb_table.text().strip() or "products"
        self.supabase_manager.update_credentials(url, active_key, table)
        ok, msg = self.supabase_manager.test_connection()
        if ok:
            QMessageBox.information(self, "Connection Test", msg)
        else:
            QMessageBox.warning(self, "Connection Test Failed", msg)
        self._update_supabase_chip()

    def action_save_settings(self):
        self.config["supabase_url"] = self.input_sb_url.text().strip()
        self.config["supabase_secret_key"] = self.input_sb_secret_key.text().strip()
        self.config["supabase_publishable_key"] = self.input_sb_pub_key.text().strip()
        self.config["supabase_jwks_url"] = self.input_sb_jwks.text().strip()
        self.config["supabase_key"] = self.config["supabase_secret_key"] or self.config["supabase_publishable_key"]
        self.config["supabase_table"] = self.input_sb_table.text().strip() or "products"
        save_config(self.config)
        self.supabase_manager.update_credentials(
            self.config["supabase_url"],
            self.config["supabase_key"],
            self.config["supabase_table"]
        )
        self._update_supabase_chip()
        QMessageBox.information(self, "Settings Saved", "Supabase database settings and .env saved successfully.")

    def action_copy_sql_schema(self):
        cb: QClipboard = QGuiApplication.clipboard()
        cb.setText(SUPABASE_SCHEMA_SQL)
        QMessageBox.information(
            self,
            "SQL Schema Copied",
            "The Supabase PostgreSQL CREATE TABLE query has been copied to your clipboard!\n\n"
            "Paste it into the Supabase Dashboard -> SQL Editor and click 'Run'."
        )

    def _copy_logs_to_clipboard(self):
        cb: QClipboard = QGuiApplication.clipboard()
        cb.setText(self.log_text.toPlainText())
        self._log("Logs copied to clipboard.")

    def _open_in_explorer(self, folder_path: str):
        ensure_directories()
        if os.path.exists(folder_path):
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
            else:
                subprocess.Popen(["xdg-open", folder_path])
