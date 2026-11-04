import os
import sys
import json
import time
import subprocess
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NY_TZ = ZoneInfo("America/New_York")


class DayTraderMasterStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("Day Trader Studio ULTRA - By Machy")
        self.root.geometry("1420x950")
        self.root.configure(bg="#0b0e14")

        self.items_map = {}
        self.selected_symbol = None
        self.chart_candles = []
        self.chart_meta = {}
        self.tm_real_stats = {}
        self.quick_filter_mode = "ALL"
        self.chart_load_id = 0

        # Stav Zoomu a Posunu (Pan) v grafu
        self.visible_bars_count = 75
        self.pan_offset = 0  # 0 = přichyceno na nejnovější / kotevní svíčku, >0 = posun do historie
        self._drag_start_x = None
        self._drag_start_offset = 0

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0b0e14", foreground="#d1d4dc")
        style.configure("TNotebook", background="#0b0e14", borderwidth=0)
        style.configure("TNotebook.Tab", background="#131722", foreground="#787b86", padding=[18, 9], font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#1e222d")], foreground=[("selected", "#ff2e93")])
        style.configure("Treeview", background="#131722", foreground="#d1d4dc", fieldbackground="#131722", rowheight=29, font=("Consolas", 10), borderwidth=0)
        style.configure("Treeview.Heading", background="#1e222d", foreground="#2962ff", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2962ff")], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", background="#ff2e93", troughcolor="#131722", bordercolor="#1e222d")

        # ----------------- HORNÍ PANEL -----------------
        top_bar = tk.Frame(root, bg="#131722", padx=16, pady=10, highlightbackground="#2a2e39", highlightthickness=1)
        top_bar.pack(fill=tk.X, padx=12, pady=(10, 4))

        tk.Label(top_bar, text="BROKER:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.broker_var = tk.StringVar(value="XTB")
        cb = ttk.Combobox(top_bar, textvariable=self.broker_var, values=["XTB", "Trading 212"], width=12, state="readonly")
        cb.pack(side=tk.LEFT, padx=(6, 16))
        cb.bind("<<ComboboxSelected>>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="KAPITÁL (CZK):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.account_size = tk.Entry(top_bar, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=10, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.account_size.insert(0, "50000")
        self.account_size.pack(side=tk.LEFT, padx=(6, 16))
        self.account_size.bind("<KeyRelease>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="RISK (%):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.risk_pct = tk.Entry(top_bar, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=5, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.risk_pct.insert(0, "1.0")
        self.risk_pct.pack(side=tk.LEFT, padx=(6, 16))
        self.risk_pct.bind("<KeyRelease>", lambda e: self.recalc_active())

        tk.Label(top_bar, text="USD/CZK (ČNB):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.usd_czk = tk.Entry(top_bar, bg="#0b0e14", fg="#089981", insertbackground="white", width=7, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.usd_czk.insert(0, "23.50")
        self.usd_czk.pack(side=tk.LEFT, padx=(6, 4))
        self.usd_czk.bind("<KeyRelease>", lambda e: self.recalc_active())

        self.btn_fx = tk.Button(
            top_bar, text="ČNB ↻", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#d1d4dc", activebackground="#363a45", activeforeground="white",
            padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=self.fetch_cnb_rate_async
        )
        self.btn_fx.pack(side=tk.LEFT, padx=(2, 12))

        self.fx_status_lbl = tk.Label(top_bar, text="Načítám kurz ČNB...", font=("Segoe UI", 8), fg="#787b86", bg="#131722")
        self.fx_status_lbl.pack(side=tk.LEFT)

        self.auto_pm_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top_bar, text="Auto Pre-Market + ATR (TOP 15)",
            variable=self.auto_pm_var, bg="#131722", fg="#089981", selectcolor="#0b0e14",
            activebackground="#131722", activeforeground="#089981", font=("Segoe UI", 9, "bold")
        ).pack(side=tk.RIGHT)

        # ----------------- ZÁLOŽKY -----------------
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        self.tab_scanner = tk.Frame(self.notebook, bg="#0b0e14")
        self.notebook.add(self.tab_scanner, text="  📊 1. Live Scanner & Vyhledávání Akcií  ")
        self.build_scanner_tab(self.tab_scanner)

        self.tab_timemachine = tk.Frame(self.notebook, bg="#0b0e14")
        self.notebook.add(self.tab_timemachine, text="  📈 2. Interactive Multi-TF Graf (Zoom, Pan & Skriptová Predikce)  ")
        self.build_timemachine_tab(self.tab_timemachine)

        # Klávesové zkratky pro posun a zoom v grafu
        self.root.bind("<Left>", lambda e: self.pan_chart(10))
        self.root.bind("<Right>", lambda e: self.pan_chart(-10))

        self.root.after(150, self.fetch_cnb_rate_async)
        self.root.after(300, self.autoload_existing_watchlist)

    # =========================================================================
    # ONLINE KURZ ČNB
    # =========================================================================
    def fetch_cnb_rate_async(self):
        self.fx_status_lbl.config(text="Stahuji ČNB...", fg="#2962ff")

        def worker():
            rate = None
            source_name = ""
            headers = {"User-Agent": "Mozilla/5.0"}
            try:
                cnb_url = f"https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt?_ts={int(time.time())}"
                req = urllib.request.Request(cnb_url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    lines = resp.read().decode("utf-8", errors="replace").splitlines()
                date_header = lines[0].split()[0] if lines else ""
                for line in lines:
                    parts = line.split("|")
                    if len(parts) >= 5 and parts[3].strip() == "USD":
                        qty = float(parts[2].replace(",", "."))
                        val = float(parts[4].replace(",", "."))
                        rate = val / qty
                        source_name = f"ČNB střed ({date_header})"
                        break
            except Exception:
                pass

            if rate is None:
                try:
                    y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/USDCZK=X?interval=1d&range=1d&_ts={int(time.time())}"
                    req2 = urllib.request.Request(y_url, headers=headers)
                    with urllib.request.urlopen(req2, timeout=6) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    meta = data["chart"]["result"][0]["meta"]
                    rate = float(meta.get("regularMarketPrice") or meta.get("previousClose"))
                    source_name = "Live Forex (Yahoo)"
                except Exception:
                    pass

            def update_ui():
                if rate:
                    self.usd_czk.delete(0, tk.END)
                    self.usd_czk.insert(0, f"{rate:.3f}")
                    self.fx_status_lbl.config(text=f"✓ {source_name}", fg="#089981")
                    self.recalc_active()
                else:
                    self.fx_status_lbl.config(text="Offline kurz", fg="#f23645")

            self.root.after(0, update_ui)

        threading.Thread(target=worker, daemon=True).start()

    def recalc_active(self):
        if self.selected_symbol and self.selected_symbol in self.items_map:
            self.update_prediction_banner(self.selected_symbol, update_tm_entries=False)
        self.redraw_chart()

    # =========================================================================
    # ZÁLOŽKA 1: SCANNER & SEARCH BAR
    # =========================================================================
    def build_scanner_tab(self, parent):
        ctrl_frame = tk.Frame(parent, bg="#131722", padx=14, pady=8, highlightbackground="#2a2e39", highlightthickness=1)
        ctrl_frame.pack(fill=tk.X, pady=(6, 4))

        self.scan_mode_var = tk.StringVar(value="premarket")
        tk.Radiobutton(ctrl_frame, text="Pre-Market sken", variable=self.scan_mode_var, value="premarket", bg="#131722", fg="#089981", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(ctrl_frame, text="Denní sken", variable=self.scan_mode_var, value="daily", bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(ctrl_frame, text="Vlastní tickery:", variable=self.scan_mode_var, value="custom", bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722", font=("Segoe UI", 9)).pack(side=tk.LEFT)

        self.custom_symbols_entry = tk.Entry(ctrl_frame, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=26, relief=tk.FLAT, font=("Consolas", 10), highlightbackground="#2a2e39", highlightthickness=1)
        self.custom_symbols_entry.insert(0, "GOOGL, TSLA, NVDA, AMD, AAPL, PLTR")
        self.custom_symbols_entry.pack(side=tk.LEFT, padx=(6, 12))

        self.btn_run_scan = tk.Button(
            ctrl_frame, text="▶ 1. SPUSTIT SKEN TRHU", font=("Segoe UI", 9, "bold"),
            bg="#089981", fg="white", activebackground="#0aa68c", padx=16, pady=4, relief=tk.FLAT, cursor="hand2", command=self.start_scan
        )
        self.btn_run_scan.pack(side=tk.RIGHT, padx=(6, 0))

        tk.Button(
            ctrl_frame, text="Obnovit watchlist.json", font=("Segoe UI", 9),
            bg="#2a2e39", fg="#d1d4dc", padx=10, pady=4, relief=tk.FLAT, cursor="hand2", command=self.autoload_existing_watchlist
        ).pack(side=tk.RIGHT)

        search_bar = tk.Frame(parent, bg="#181c27", padx=14, pady=8, highlightbackground="#ff2e93", highlightthickness=1)
        search_bar.pack(fill=tk.X, pady=(2, 6))

        tk.Label(search_bar, text="🔍 HLEDAT STOCK / FILTR:", font=("Segoe UI", 9, "bold"), fg="#ff2e93", bg="#181c27").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_bar, textvariable=self.search_var, bg="#0b0e14", fg="#ffffff",
            insertbackground="#ff2e93", width=24, relief=tk.FLAT, font=("Consolas", 11, "bold"),
            highlightbackground="#2a2e39", highlightthickness=1
        )
        self.search_entry.pack(side=tk.LEFT, padx=(8, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_table_filter())
        self.search_entry.bind("<Return>", lambda e: self.search_or_fetch_yahoo())

        tk.Button(
            search_bar, text="✕ Vyčistit", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#d1d4dc", padx=8, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: (self.search_var.set(""), self.set_quick_filter("ALL"))
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            search_bar, text="+ Stáhnout Ticker z Yahoo", font=("Segoe UI", 8, "bold"),
            bg="#ff2e93", fg="white", activebackground="#ff5ca8", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=self.search_or_fetch_yahoo
        ).pack(side=tk.LEFT, padx=(0, 18))

        tk.Label(search_bar, text="Rychlý filtr:", font=("Segoe UI", 9), fg="#787b86", bg="#181c27").pack(side=tk.LEFT, padx=(4, 6))
        tk.Button(search_bar, text="Vše", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("ALL")).pack(side=tk.LEFT, padx=2)
        tk.Button(search_bar, text="🟢 Jen GAP & GO", font=("Segoe UI", 8, "bold"), bg="#089981", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("BULL")).pack(side=tk.LEFT, padx=2)
        tk.Button(search_bar, text="🔴 Jen GAP FADE", font=("Segoe UI", 8, "bold"), bg="#f23645", fg="white", padx=8, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_quick_filter("BEAR")).pack(side=tk.LEFT, padx=2)

        self.filter_count_lbl = tk.Label(search_bar, text="Zobrazeno: 0 akcií", font=("Consolas", 9, "bold"), fg="#d1d4dc", bg="#181c27")
        self.filter_count_lbl.pack(side=tk.RIGHT)

        self.scan_status = tk.Label(parent, text="Stav: Připraveno", font=("Segoe UI", 9, "bold"), fg="#2962ff", bg="#0b0e14", anchor="w")
        self.scan_status.pack(fill=tk.X, pady=(0, 2))

        self.scan_progress = ttk.Progressbar(parent, orient="horizontal", mode="indeterminate", style="Horizontal.TProgressbar")
        self.scan_progress.pack(fill=tk.X, pady=(0, 6))

        table_frame = tk.Frame(parent, bg="#131722", highlightbackground="#2a2e39", highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("ticker", "price", "pm_price", "gap", "pmh", "pml", "vwap", "atr", "rvol", "pm_vol", "setup")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)

        headers = {
            "ticker": ("Ticker", 75),
            "price": ("Close $", 75),
            "pm_price": ("Live Cena $", 85),
            "gap": ("Gap %", 75),
            "pmh": ("Session High", 85),
            "pml": ("Session Low", 85),
            "vwap": ("VWAP", 80),
            "atr": ("ATR(14)", 75),
            "rvol": ("RVOL", 65),
            "pm_vol": ("Objem", 85),
            "setup": ("Technická Predikce / Pravidlo", 400)
        }
        for col, (text, w) in headers.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w" if col == "setup" else "center")

        self.tree.tag_configure("bull", foreground="#089981")
        self.tree.tag_configure("bear", foreground="#f23645")
        self.tree.tag_configure("neutral", foreground="#d1d4dc")

        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        pred_card = tk.Frame(parent, bg="#131722", padx=14, pady=10, highlightbackground="#ff2e93", highlightthickness=1)
        pred_card.pack(fill=tk.X, pady=(8, 4))

        top_pred = tk.Frame(pred_card, bg="#131722")
        top_pred.pack(fill=tk.X)

        tk.Label(top_pred, text="AKTIVNÍ TICKER:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.manual_ticker_entry = tk.Entry(top_pred, bg="#0b0e14", fg="#ff2e93", insertbackground="white", width=10, relief=tk.FLAT, font=("Consolas", 11, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.manual_ticker_entry.pack(side=tk.LEFT, padx=(6, 10))

        self.btn_live_pm = tk.Button(
            top_pred, text="⚡ Načíst reálná tržní data & Predikci", font=("Segoe UI", 9, "bold"),
            bg="#ff2e93", fg="white", activebackground="#ff5ca8", padx=12, pady=4, relief=tk.FLAT, cursor="hand2", command=self.fetch_single_ticker_live
        )
        self.btn_live_pm.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            top_pred, text="📈 Otevřít v Multi-TF Grafu", font=("Segoe UI", 9, "bold"),
            bg="#2a2e39", fg="#ff77bc", activebackground="#363a45", padx=12, pady=4, relief=tk.FLAT, cursor="hand2",
            command=lambda: (self.notebook.select(self.tab_timemachine), self.load_interactive_chart(auto_reset_levels=True))
        ).pack(side=tk.LEFT)

        self.btn_copy_prompt = tk.Button(
            top_pred, text="📋 3. ZKOPÍROVAT PROMPT & POKYN DO CLAUDE", font=("Segoe UI", 10, "bold"),
            bg="#2962ff", fg="white", activebackground="#1e53e5", padx=18, pady=5, relief=tk.FLAT, cursor="hand2", command=self.copy_comprehensive_prompt
        )
        self.btn_copy_prompt.pack(side=tk.RIGHT)

        self.pred_summary_lbl = tk.Label(
            pred_card,
            text="Klikni na akcii v tabulce – načtou se reálná data z burzy, spočítá se pozice podle ČNB a připraví se reálný graf.",
            font=("Consolas", 10), fg="#d1d4dc", bg="#0b0e14", padx=12, pady=8, anchor="w", justify=tk.LEFT
        )
        self.pred_summary_lbl.pack(fill=tk.X, pady=(8, 2))

        log_frame = tk.Frame(parent, bg="#0b0e14")
        log_frame.pack(fill=tk.X, pady=(2, 0))
        self.scan_log = tk.Text(log_frame, height=3, bg="#06080c", fg="#089981", font=("Consolas", 9), relief=tk.FLAT, padx=8, pady=4)
        self.scan_log.pack(fill=tk.X)

    def set_quick_filter(self, mode):
        self.quick_filter_mode = mode
        self.apply_table_filter()

    def apply_table_filter(self):
        query = self.search_var.get().strip().upper()
        for item in self.tree.get_children():
            self.tree.delete(item)

        shown = 0
        for sym, d in self.items_map.items():
            pred = d.get("prediction", "")
            if self.quick_filter_mode == "BULL" and "GAP & GO" not in pred:
                continue
            if self.quick_filter_mode == "BEAR" and "GAP FADE" not in pred:
                continue

            if query:
                if query not in sym and query not in pred.upper():
                    continue

            self._insert_or_update_tree_row(sym)
            shown += 1

        self.filter_count_lbl.config(text=f"Zobrazeno: {shown} / {len(self.items_map)} akcií")

    def search_or_fetch_yahoo(self):
        q = self.search_var.get().strip().upper()
        if not q:
            return
        self.manual_ticker_entry.delete(0, tk.END)
        self.manual_ticker_entry.insert(0, q)
        self.fetch_single_ticker_live()

    # =========================================================================
    # ZÁLOŽKA 2: MULTI-TIMEFRAME GRAF SE ZOOMEM, POSUNEM (PAN) A SKRIPTOVOU PREDIKCÍ
    # =========================================================================
    def build_timemachine_tab(self, parent):
        ctrl = tk.Frame(parent, bg="#131722", padx=12, pady=8, highlightbackground="#2a2e39", highlightthickness=1)
        ctrl.pack(fill=tk.X, pady=(6, 4))

        # Řádek 1: Symbol, Rychlé přepínače Timeframu, Styl, Seance, Zoom & Pan ovládání
        r1 = tk.Frame(ctrl, bg="#131722")
        r1.pack(fill=tk.X, pady=2)

        tk.Label(r1, text="Symbol:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_symbol = tk.Entry(r1, bg="#0b0e14", fg="#ffffff", insertbackground="white", width=7, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_symbol.insert(0, "GOOGL")
        self.tm_symbol.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_symbol.bind("<Return>", lambda e: self.load_interactive_chart(auto_reset_levels=True))

        tk.Label(r1, text="TF:", font=("Segoe UI", 9, "bold"), fg="#ff2e93", bg="#131722").pack(side=tk.LEFT)
        self.tf_var = tk.StringVar(value="5m")
        self.tf_buttons = {}
        for tf_opt in ["1m", "2m", "5m", "15m", "30m", "1h"]:
            b = tk.Button(
                r1, text=tf_opt, font=("Consolas", 9, "bold"), width=3, pady=2, relief=tk.FLAT, cursor="hand2",
                command=lambda t=tf_opt: self.switch_timeframe(t)
            )
            b.pack(side=tk.LEFT, padx=1)
            self.tf_buttons[tf_opt] = b
        self._update_tf_button_styles()

        tk.Label(r1, text="Typ:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT, padx=(8, 2))
        self.chart_type_var = tk.StringVar(value="Svíčky (Candles)")
        type_cb = ttk.Combobox(r1, textvariable=self.chart_type_var, values=["Svíčky (Candles)", "Heikin-Ashi", "Čárový (Line)", "Plošný (Area)"], width=14, state="readonly", font=("Segoe UI", 9))
        type_cb.pack(side=tk.LEFT, padx=(2, 8))
        type_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw_chart())

        tk.Label(r1, text="Seance:", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_date = ttk.Combobox(r1, width=13, state="readonly", font=("Consolas", 9, "bold"))
        self.tm_date.pack(side=tk.LEFT, padx=(4, 6))
        self.tm_date.bind("<<ComboboxSelected>>", lambda e: self.on_chart_date_change())

        self.include_pm_chart_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1, text="Pre-Market", variable=self.include_pm_chart_var,
            bg="#131722", fg="#d1d4dc", selectcolor="#0b0e14", activebackground="#131722",
            font=("Segoe UI", 8), command=lambda: self.load_interactive_chart(auto_reset_levels=False)
        ).pack(side=tk.LEFT, padx=(2, 8))

        # OVLÁDÁNÍ ZOOMU A POSUNU (PAN)
        zoom_box = tk.Frame(r1, bg="#131722")
        zoom_box.pack(side=tk.LEFT, padx=(4, 4))

        tk.Button(zoom_box, text="◀", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.pan_chart(15)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="▶", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.pan_chart(-15)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="🔍+", font=("Segoe UI", 8, "bold"), bg="#2962ff", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_zoom_step(0.75)).pack(side=tk.LEFT, padx=2)
        tk.Button(zoom_box, text="🔍-", font=("Segoe UI", 8, "bold"), bg="#2962ff", fg="white", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_zoom_step(1.35)).pack(side=tk.LEFT, padx=1)
        tk.Button(zoom_box, text="60 sv.", font=("Segoe UI", 8, "bold"), bg="#2a2e39", fg="#089981", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_exact_zoom(60)).pack(side=tk.LEFT, padx=2)
        tk.Button(zoom_box, text="Celý den", font=("Segoe UI", 8), bg="#2a2e39", fg="#d1d4dc", padx=6, pady=2, relief=tk.FLAT, cursor="hand2", command=lambda: self.set_exact_zoom(999)).pack(side=tk.LEFT, padx=1)

        self.btn_load_chart = tk.Button(
            r1, text="↻ Obnovit Graf", font=("Segoe UI", 9, "bold"),
            bg="#2962ff", fg="white", activebackground="#1e53e5", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: self.load_interactive_chart(auto_reset_levels=False)
        )
        self.btn_load_chart.pack(side=tk.RIGHT)

        # Řádek 2: Hladiny Vstup, SL, TP, Čas svíčky a Backtest
        r2 = tk.Frame(ctrl, bg="#131722")
        r2.pack(fill=tk.X, pady=(6, 2))

        tk.Label(r2, text="Vstup ($):", font=("Segoe UI", 9, "bold"), fg="#2962ff", bg="#131722").pack(side=tk.LEFT)
        self.tm_entry = tk.Entry(r2, bg="#0b0e14", fg="#2962ff", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_entry.insert(0, "290.72")
        self.tm_entry.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_entry.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Stop Loss ($):", font=("Segoe UI", 9, "bold"), fg="#f23645", bg="#131722").pack(side=tk.LEFT)
        self.tm_stop = tk.Entry(r2, bg="#0b0e14", fg="#f23645", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_stop.insert(0, "288.50")
        self.tm_stop.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_stop.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Target ($):", font=("Segoe UI", 9, "bold"), fg="#089981", bg="#131722").pack(side=tk.LEFT)
        self.tm_target = tk.Entry(r2, bg="#0b0e14", fg="#089981", insertbackground="white", width=8, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_target.insert(0, "294.20")
        self.tm_target.pack(side=tk.LEFT, padx=(4, 10))
        self.tm_target.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Label(r2, text="Čas svíčky (NY):", font=("Segoe UI", 9, "bold"), fg="#787b86", bg="#131722").pack(side=tk.LEFT)
        self.tm_time = tk.Entry(r2, bg="#0b0e14", fg="#ff2e93", insertbackground="white", width=6, relief=tk.FLAT, font=("Consolas", 10, "bold"), highlightbackground="#2a2e39", highlightthickness=1)
        self.tm_time.insert(0, "15:43")
        self.tm_time.pack(side=tk.LEFT, padx=(4, 8))
        self.tm_time.bind("<KeyRelease>", lambda e: self.redraw_chart())

        tk.Button(
            r2, text="🎯 Reset Hladin k aktuální svíčce & ATR", font=("Segoe UI", 8, "bold"),
            bg="#2a2e39", fg="#ffffff", activebackground="#363a45", padx=10, pady=3, relief=tk.FLAT, cursor="hand2",
            command=lambda: self.auto_set_levels_from_chart(redraw=True)
        ).pack(side=tk.LEFT, padx=(4, 10))

        self.show_pink_pred_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r2, text="🩷 Růžová Predikce (VWAP + EMA9 + Skript)", variable=self.show_pink_pred_var,
            bg="#131722", fg="#ff2e93", selectcolor="#0b0e14", activebackground="#131722",
            activeforeground="#ff2e93", font=("Segoe UI", 9, "bold"), command=self.redraw_chart
        ).pack(side=tk.LEFT, padx=(2, 6))

        self.btn_run_tm = tk.Button(
            r2, text="▶ Spustit Skript TimeMachine (60d Backtest)", font=("Segoe UI", 9, "bold"),
            bg="#089981", fg="white", activebackground="#0aa68c", padx=14, pady=3, relief=tk.FLAT, cursor="hand2", command=self.start_timemachine
        )
        self.btn_run_tm.pack(side=tk.RIGHT)

        # Informační lišta grafu
        chart_bar = tk.Frame(parent, bg="#1e222d", padx=10, pady=4)
        chart_bar.pack(fill=tk.X)

        self.chart_info_lbl = tk.Label(
            chart_bar, text="Kolečko myši = ZOOM   |   Šipky ◀ ▶ (nebo Shift + tažení myší) = POSUN V ČASE",
            font=("Consolas", 9, "bold"), fg="#d1d4dc", bg="#1e222d"
        )
        self.chart_info_lbl.pack(side=tk.LEFT)

        tk.Label(
            chart_bar,
            text="[Kolečko = Zoom]  [Levé tl. = Vstup]  [Pravé tl. = SL]  [Dvojklik = TP]  |  Žlutá = VWAP, Modrá = EMA(9)",
            font=("Segoe UI", 8, "bold"), fg="#787b86", bg="#1e222d"
        ).pack(side=tk.RIGHT)

        # Jednotný čistý Canvas
        chart_container = tk.Frame(parent, bg="#0b0e14", highlightbackground="#2a2e39", highlightthickness=1)
        chart_container.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self.chart_canvas = tk.Canvas(chart_container, bg="#0b0e14", height=435, highlightthickness=0, cursor="crosshair")
        self.chart_canvas.pack(fill=tk.BOTH, expand=True)

        self.chart_canvas.bind("<Configure>", lambda e: self.redraw_chart())
        self.chart_canvas.bind("<Motion>", self.on_chart_hover)
        self.chart_canvas.bind("<Button-1>", self.on_chart_left_click)
        self.chart_canvas.bind("<B1-Motion>", self.on_chart_drag)
        self.chart_canvas.bind("<ButtonRelease-1>", self.on_chart_release)
        self.chart_canvas.bind("<Button-3>", self.on_chart_right_click)
        self.chart_canvas.bind("<Button-2>", self.on_chart_middle_click)
        self.chart_canvas.bind("<Double-Button-1>", self.on_chart_middle_click)
        self.chart_canvas.bind("<MouseWheel>", self.on_chart_zoom)

        self.tm_progress = ttk.Progressbar(parent, orient="horizontal", mode="indeterminate", style="Horizontal.TProgressbar")
        self.tm_progress.pack(fill=tk.X, pady=(0, 4))

        tm_frame = tk.Frame(parent, bg="#0b0e14", height=135)
        tm_frame.pack(fill=tk.X)
        self.tm_log = tk.Text(tm_frame, height=6, bg="#06080c", fg="#d1d4dc", font=("Consolas", 9), relief=tk.FLAT, padx=10, pady=6)
        tm_scroll = ttk.Scrollbar(tm_frame, orient=tk.VERTICAL, command=self.tm_log.yview)
        self.tm_log.configure(yscroll=tm_scroll.set)
        self.tm_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tm_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # =========================================================================
    # FUNKCE PRO ZOOM A POSUN GRAFU (PANNING)
    # =========================================================================
    def on_chart_zoom(self, event):
        if event.delta > 0:
            self.set_zoom_step(0.80)
        else:
            self.set_zoom_step(1.25)

    def set_zoom_step(self, factor):
        total = max(len(self.chart_candles), 30)
        new_val = int(self.visible_bars_count * factor)
        if new_val == self.visible_bars_count:
            new_val += (-5 if factor < 1 else 5)
        self.visible_bars_count = max(20, min(total, new_val))
        self.redraw_chart()

    def set_exact_zoom(self, count):
        total = max(len(self.chart_candles), 20)
        self.visible_bars_count = min(total, max(20, count))
        if count >= total:
            self.pan_offset = 0
        self.redraw_chart()

    def pan_chart(self, delta_bars):
        if not self.chart_candles:
            return
        max_off = max(0, len(self.chart_candles) - 15)
        self.pan_offset = max(0, min(max_off, self.pan_offset + delta_bars))
        self.redraw_chart()

    def switch_timeframe(self, tf_val):
        self.tf_var.set(tf_val)
        self.pan_offset = 0
        # Rozumný výchozí zoom podle intervalu
        default_zoom_map = {"1m": 75, "2m": 75, "5m": 78, "15m": 60, "30m": 60, "1h": 65}
        self.visible_bars_count = default_zoom_map.get(tf_val, 75)
        self._update_tf_button_styles()
        self.load_interactive_chart(auto_reset_levels=True)

    def _update_tf_button_styles(self):
        cur = self.tf_var.get()
        for tf_opt, btn in self.tf_buttons.items():
            if tf_opt == cur:
                btn.config(bg="#ff2e93", fg="#ffffff")
            else:
                btn.config(bg="#2a2e39", fg="#d1d4dc")

    # =========================================================================
    # STAHOVÁNÍ REÁLNÝCH SVÍČEK Z YAHOO PRO KAŽDÝ TIMEFRAME
    # =========================================================================
    def load_interactive_chart(self, auto_reset_levels=False):
        sym = self.tm_symbol.get().strip().upper()
        if not sym:
            return
        tf = self.tf_var.get().strip() or "5m"
        inc_pm = "true" if self.include_pm_chart_var.get() else "false"

        range_map = {
            "1m": "5d",
            "2m": "5d",
            "5m": "1mo",
            "15m": "1mo",
            "30m": "1mo",
            "1h": "3mo"
        }
        y_range = range_map.get(tf, "5d")

        self.chart_load_id += 1
        current_req_id = self.chart_load_id

        self.btn_load_chart.config(state=tk.DISABLED, bg="#2a2e39")
        self.chart_info_lbl.config(text=f"Stahuji reálný [{tf}] graf z Yahoo Finance pro {sym}...", fg="#ff2e93")

        def worker():
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                    f"?interval={tf}&range={y_range}&includePrePost={inc_pm}&_ts={int(time.time()*1000)}"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                if current_req_id != self.chart_load_id:
                    return

                res = data.get("chart", {}).get("result", [])
                if not res:
                    raise ValueError(f"Yahoo nevrátilo žádná data pro {sym} ({tf}).")

                timestamps = res[0].get("timestamp", [])
                q = res[0].get("indicators", {}).get("quote", [{}])[0]
                opens = q.get("open", [])
                highs = q.get("high", [])
                lows = q.get("low", [])
                closes = q.get("close", [])
                vols = q.get("volume", [])

                by_date = {}
                all_bars = []
                use_pm = self.include_pm_chart_var.get()

                for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, vols):
                    if None in (o, h, l, c):
                        continue
                    dt_ny = datetime.fromtimestamp(ts, tz=NY_TZ)
                    hm = dt_ny.hour * 100 + dt_ny.minute
                    if not use_pm and (hm < 930 or hm >= 1600):
                        continue
                    d_str = dt_ny.strftime("%Y-%m-%d")
                    t_str = dt_ny.strftime("%H:%M")
                    bar_obj = {
                        "date": d_str,
                        "time": t_str,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": int(v or 0)
                    }
                    by_date.setdefault(d_str, []).append(bar_obj)
                    all_bars.append(bar_obj)

                available_dates = sorted(by_date.keys())
                if not available_dates:
                    raise ValueError("Nebyly nalezeny žádné obchodní svíčky.")

                self.chart_meta["by_date"] = by_date
                self.chart_meta["all_bars"] = all_bars
                self.chart_meta["dates"] = available_dates
                self.chart_meta["symbol"] = sym
                self.chart_meta["tf"] = tf

                def update_ui():
                    if current_req_id != self.chart_load_id:
                        return
                    combo_vals = ["Multi-Day (Vše)"] + available_dates
                    self.tm_date["values"] = combo_vals

                    prev_sel = self.tm_date.get().strip()
                    if tf in ("30m", "1h"):
                        self.tm_date.set("Multi-Day (Vše)")
                        self.chart_candles = all_bars[-160:]
                    else:
                        target_day = prev_sel if prev_sel in by_date else available_dates[-1]
                        self.tm_date.set(target_day)
                        self.chart_candles = by_date[target_day]

                    self.pan_offset = 0
                    if self.chart_candles:
                        last_c = self.chart_candles[-1]
                        self.tm_time.delete(0, tk.END)
                        self.tm_time.insert(0, last_c["time"])

                        cur_entry = self._to_float(self.tm_entry.get())
                        if auto_reset_levels or not cur_entry or abs(cur_entry - last_c["close"]) / max(last_c["close"], 1) > 0.08:
                            self.auto_set_levels_from_chart(redraw=False)

                    self.chart_info_lbl.config(
                        text=f"✓ [{sym} | {tf}] Načteno {len(self.chart_candles)} svíček (Zobrazeno posledních {min(self.visible_bars_count, len(self.chart_candles))} – Kolečko myši = Zoom).",
                        fg="#089981"
                    )
                    self.redraw_chart()
                    self.run_silent_script_prediction()

                self.root.after(0, update_ui)

            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda m=err: self.chart_info_lbl.config(text=f"Chyba načítání [{tf}] grafu: {m}", fg="#f23645"))
            finally:
                self.root.after(0, lambda: self.btn_load_chart.config(state=tk.NORMAL, bg="#2962ff"))

        threading.Thread(target=worker, daemon=True).start()

    def on_chart_date_change(self):
        sel = self.tm_date.get().strip()
        by_date = self.chart_meta.get("by_date", {})
        all_bars = self.chart_meta.get("all_bars", [])
        if sel == "Multi-Day (Vše)":
            self.chart_candles = all_bars[-200:]
        elif sel in by_date:
            self.chart_candles = by_date[sel]

        self.pan_offset = 0
        if self.chart_candles:
            self.tm_time.delete(0, tk.END)
            self.tm_time.insert(0, self.chart_candles[-1]["time"])
            self.auto_set_levels_from_chart(redraw=False)
        self.redraw_chart()
        self.run_silent_script_prediction()

    def auto_set_levels_from_chart(self, redraw=True):
        """Spočítá realistický Vstup, SL a TP přímo z měřítka aktuálního Timeframu (aby pasovaly na 1m i 1h graf)."""
        if not self.chart_candles:
            return
        t_target = self.tm_time.get().strip()
        anchor = self.chart_candles[-1]
        for b in self.chart_candles:
            if b["time"] == t_target:
                anchor = b
                break

        recent = self.chart_candles[-20:]
        bar_atr = sum(b["high"] - b["low"] for b in recent) / max(len(recent), 1)
        bar_atr = max(bar_atr, anchor["close"] * 0.0015)

        vwap_val, ema9_val = self._compute_vwap_and_ema9(self.chart_candles)
        cur_p = anchor["close"]

        entry_p = round(cur_p, 2)
        sl_p = round(cur_p - (bar_atr * 2.2), 2)
        tp_p = round(cur_p + (bar_atr * 3.8), 2)

        self.tm_entry.delete(0, tk.END)
        self.tm_entry.insert(0, f"{entry_p:.2f}")
        self.tm_stop.delete(0, tk.END)
        self.tm_stop.insert(0, f"{sl_p:.2f}")
        self.tm_target.delete(0, tk.END)
        self.tm_target.insert(0, f"{tp_p:.2f}")

        if redraw:
            self.redraw_chart()
            self.run_silent_script_prediction()

    def _compute_vwap_and_ema9(self, bars):
        cum_pv = 0.0
        cum_v = 0
        ema9 = bars[0]["close"] if bars else 0.0
        k = 2.0 / 10.0
        for b in bars:
            typ = (b["high"] + b["low"] + b["close"]) / 3.0
            v = b["volume"]
            cum_pv += typ * v
            cum_v += v
            ema9 = (b["close"] - ema9) * k + ema9
        vwap = (cum_pv / cum_v) if cum_v > 0 else (bars[-1]["close"] if bars else 0.0)
        return vwap, ema9

    def run_silent_script_prediction(self):
        if not self.chart_candles:
            return
        sym = self.tm_symbol.get().strip().upper()
        entry = self.tm_entry.get().strip().replace(",", ".")
        stop = self.tm_stop.get().strip().replace(",", ".")
        target = self.tm_target.get().strip().replace(",", ".")
        bar = self.chart_candles[-1]
        for b in self.chart_candles:
            if b["time"] == self.tm_time.get().strip():
                bar = b
                break

        def worker():
            tm_candidates = [
                os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py"),
                os.path.join(BASE_DIR, "timemachine.py"),
                os.path.join(BASE_DIR, "trader-scanner", "timemachine.py"),
            ]
            tm_script = next((p for p in tm_candidates if os.path.exists(p)), None)
            if not tm_script:
                return
            try:
                cmd = [
                    sys.executable, tm_script, "run",
                    "--symbol", sym,
                    "--entry", entry,
                    "--stop", stop,
                    "--target", target,
                    "--date", bar["date"],
                    "--entry-time", bar["time"],
                    "--json"
                ]
                proc = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
                out = (proc.stdout or "").strip()
                if out.startswith("{"):
                    j = json.loads(out)
                    self.tm_real_stats[sym] = j
                    self.root.after(0, self.redraw_chart)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # VĚDECKÁ TECHNICKÁ & SKRIPTOVÁ PREDIKCE
    # =========================================================================
    def _build_real_predicted_candles(self, bars, anchor_idx, entry_v, sl_v, tp_v):
        sub = bars[:anchor_idx + 1]
        anchor = sub[-1]
        start_p = anchor["close"]

        cum_pv, cum_v = 0.0, 0
        ema9 = sub[0]["close"]
        ema_history = []
        k = 2.0 / 10.0
        for b in sub:
            typ = (b["high"] + b["low"] + b["close"]) / 3.0
            cum_pv += typ * b["volume"]
            cum_v += b["volume"]
            ema9 = (b["close"] - ema9) * k + ema9
            ema_history.append(ema9)
        vwap = (cum_pv / cum_v) if cum_v > 0 else start_p

        recent = sub[-14:]
        tf_atr = sum(b["high"] - b["low"] for b in recent) / max(len(recent), 1)
        tf_atr = max(tf_atr, start_p * 0.0012)

        ema_slope = (ema_history[-1] - ema_history[max(0, len(ema_history) - 4)]) / max(tf_atr, 0.01)
        avg_v = sum(b["volume"] for b in recent) / max(len(recent), 1)
        last_v = sum(b["volume"] for b in sub[-2:]) / min(len(sub), 2)
        vol_boost = min(max(last_v / max(avg_v, 1), 0.6), 1.8)

        bias = 0.0
        bias += 0.35 if start_p >= vwap else -0.35
        bias += 0.30 if start_p >= ema9 else -0.30
        bias += max(min(ema_slope * 0.25, 0.35), -0.35)

        sym = self.tm_symbol.get().strip().upper()
        tm_stat = self.tm_real_stats.get(sym)
        stat_label = ""
        if isinstance(tm_stat, dict):
            t_rate = tm_stat.get("target_first_pct") or tm_stat.get("win_rate") or tm_stat.get("Summary", {}).get("target_first_pct")
            s_rate = tm_stat.get("stop_first_pct") or tm_stat.get("Summary", {}).get("stop_first_pct")
            if t_rate is not None and s_rate is not None:
                if float(t_rate) > float(s_rate):
                    bias = max(bias, 0.45)
                elif float(s_rate) > float(t_rate):
                    bias = min(bias, -0.45)
                stat_label = f" | 60d Replay: TP {float(t_rate):.0f}% vs SL {float(s_rate):.0f}%"

        pred_bars = []
        cur_o = start_p
        for step_i in range(1, 6):
            vwap_pull = (vwap - cur_o) * (0.16 if vol_boost < 0.9 else 0.04)
            trend_step = bias * tf_atr * 0.60 * vol_boost * (0.92 ** (step_i - 1))
            cur_c = cur_o + trend_step + vwap_pull
            if bias >= 0:
                cur_h = max(cur_o, cur_c) + tf_atr * 0.42
                cur_l = min(cur_o, cur_c) - tf_atr * 0.22
            else:
                cur_h = max(cur_o, cur_c) + tf_atr * 0.22
                cur_l = min(cur_o, cur_c) - tf_atr * 0.42

            pred_bars.append({
                "step": f"+{step_i}",
                "open": cur_o,
                "high": cur_h,
                "low": cur_l,
                "close": cur_c
            })
            cur_o = cur_c

        verdict = "BULLISH (Nad VWAP & EMA9)" if bias >= 0.15 else ("BEARISH / FADE (Pod VWAP & EMA9)" if bias <= -0.15 else "NEUTRAL U VWAP")
        summary_info = f"Model: {verdict} | ATR({self.tf_var.get()}): ${tf_atr:.2f} | RVOL: {vol_boost:.1f}x{stat_label}"
        return pred_bars, summary_info

    # =========================================================================
    # VYKRESLENÍ GRAFU SE ZOOMEM A POSUNEM (PANNING)
    # =========================================================================
    def redraw_chart(self, hover_x=None, hover_y=None):
        c = self.chart_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 100 or h < 100:
            return

        if not self.chart_candles:
            c.create_text(
                w // 2, h // 2,
                text="Klikni na '↻ Obnovit Graf' nebo vyber časový rámec (1m, 2m, 5m, 15m, 30m, 1h).",
                fill="#ff77bc", font=("Segoe UI", 11, "bold")
            )
            return

        pad_left, pad_right, pad_top, pad_bot = 15, 92, 28, 34
        plot_w = w - pad_left - pad_right
        plot_h = h - pad_top - pad_bot

        # 1. Výpočet celodenního VWAP a EMA(9) ze všech svíček seance
        full_vwap = []
        full_ema9 = []
        cum_pv, cum_v = 0.0, 0
        ema9 = self.chart_candles[0]["close"]
        k_ema = 2.0 / 10.0
        for b in self.chart_candles:
            typ = (b["high"] + b["low"] + b["close"]) / 3.0
            cum_pv += typ * b["volume"]
            cum_v += b["volume"]
            full_vwap.append(cum_pv / cum_v if cum_v > 0 else b["close"])
            ema9 = (b["close"] - ema9) * k_ema + ema9
            full_ema9.append(ema9)

        # 2. Kotevní svíčka podle zadaného času
        active_time = self.tm_time.get().strip()
        anchor_full_idx = len(self.chart_candles) - 1
        for idx, b in enumerate(self.chart_candles):
            if b["time"] == active_time:
                anchor_full_idx = idx
                break

        entry_v = self._to_float(self.tm_entry.get())
        sl_v = self._to_float(self.tm_stop.get())
        tp_v = self._to_float(self.tm_target.get())

        show_pink = self.show_pink_pred_var.get()
        pred_bars, pred_summary = ([], "")
        if show_pink:
            pred_bars, pred_summary = self._build_real_predicted_candles(
                self.chart_candles, anchor_full_idx, entry_v, sl_v, tp_v
            )

        # 3. Výřez viditelných svíček podle ZOOMU a POSUNU (pan_offset)
        total_len = len(self.chart_candles)
        zoom_n = min(max(20, self.visible_bars_count), total_len)
        end_idx = max(zoom_n, total_len - self.pan_offset)
        end_idx = min(total_len, end_idx)
        start_idx = max(0, end_idx - zoom_n)

        vis_candles = self.chart_candles[start_idx:end_idx]
        vis_vwap = full_vwap[start_idx:end_idx]
        vis_ema9 = full_ema9[start_idx:end_idx]

        # Zda je kotevní svíčka ve viditelném okně
        anchor_vis_idx = anchor_full_idx - start_idx if start_idx <= anchor_full_idx < end_idx else (len(vis_candles) - 1)

        # 4. Měřítko osy Y čistě podle viditelného okna (aby se graf nikdy nesmrsknul)
        highs = [b["high"] for b in vis_candles] + ([pb["high"] for pb in pred_bars] if show_pink else [])
        lows = [b["low"] for b in vis_candles] + ([pb["low"] for pb in pred_bars] if show_pink else [])
        vols = [b["volume"] for b in vis_candles]

        p_max = max(highs)
        p_min = min(lows)
        candle_span = max(p_max - p_min, p_max * 0.002, 0.05)

        # Hladiny IN/SL/TP zahrneme do osy Y jen pokud leží blízko viditelných svíček (do 25 % rozpětí)
        for lvl in (entry_v, sl_v, tp_v):
            if lvl and (p_min - candle_span * 0.25) <= lvl <= (p_max + candle_span * 0.25):
                p_max = max(p_max, lvl)
                p_min = min(p_min, lvl)

        margin = (p_max - p_min) * 0.08 or 0.05
        p_max += margin
        p_min -= margin
        max_vol = max(vols) if vols and max(vols) > 0 else 1

        def price_to_y(p):
            return pad_top + plot_h * (1.0 - (p - p_min) / (p_max - p_min))

        def y_to_price(y_px):
            ratio = 1.0 - ((y_px - pad_top) / max(plot_h, 1))
            return p_min + ratio * (p_max - p_min)

        pred_count = len(pred_bars) if show_pink else 0
        n_vis = len(vis_candles)
        total_slots = n_vis + pred_count
        step_x = plot_w / max(total_slots, 1)
        body_w = max(int(step_x * 0.72), 2)

        self.chart_meta["y_to_price"] = y_to_price
        self.chart_meta["pad_left"] = pad_left
        self.chart_meta["step_x"] = step_x
        self.chart_meta["vis_candles"] = vis_candles

        # Vodorovná mřížka
        steps = 7
        for i in range(steps + 1):
            p_val = p_min + (p_max - p_min) * (i / steps)
            y = price_to_y(p_val)
            c.create_line(pad_left, y, w - pad_right, y, fill="#161a25")
            c.create_text(w - pad_right + 8, y, text=f"${p_val:.2f}", fill="#787b86", font=("Consolas", 8), anchor="w")

        chart_style = self.chart_type_var.get()
        line_pts, vwap_pts, ema9_pts = [], [], []
        ha_prev_o = vis_candles[0]["open"]
        ha_prev_c = vis_candles[0]["close"]

        for i, bar in enumerate(vis_candles):
            cx = pad_left + (i + 0.5) * step_x

            if chart_style == "Heikin-Ashi":
                ha_c = (bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4.0
                ha_o = (ha_prev_o + ha_prev_c) / 2.0 if i > 0 else bar["open"]
                ha_h = max(bar["high"], ha_o, ha_c)
                ha_l = min(bar["low"], ha_o, ha_c)
                ha_prev_o, ha_prev_c = ha_o, ha_c
                o_val, h_val, l_val, c_val = ha_o, ha_h, ha_l, ha_c
            else:
                o_val, h_val, l_val, c_val = bar["open"], bar["high"], bar["low"], bar["close"]

            yo, yh, yl, yc = price_to_y(o_val), price_to_y(h_val), price_to_y(l_val), price_to_y(c_val)
            color = "#089981" if c_val >= o_val else "#f23645"

            if i == anchor_vis_idx:
                c.create_rectangle(cx - step_x / 2, pad_top, cx + step_x / 2, pad_top + plot_h, fill="#1f1429", outline="#ff2e93", dash=(2, 2))

            vh = (bar["volume"] / max_vol) * (plot_h * 0.20)
            vol_col = "#0d3d38" if c_val >= o_val else "#421820"
            c.create_rectangle(cx - body_w / 2, pad_top + plot_h - vh, cx + body_w / 2, pad_top + plot_h, fill=vol_col, outline="")

            if chart_style in ("Svíčky (Candles)", "Heikin-Ashi"):
                c.create_line(cx, yh, cx, yl, fill=color, width=1)
                top_b, bot_b = min(yo, yc), max(yo, yc)
                if bot_b - top_b < 1.5:
                    bot_b = top_b + 1.5
                c.create_rectangle(cx - body_w / 2, top_b, cx + body_w / 2, bot_b, fill=color, outline=color)
            else:
                line_pts.extend([cx, yc])

            vwap_pts.extend([cx, price_to_y(vis_vwap[i])])
            ema9_pts.extend([cx, price_to_y(vis_ema9[i])])

            if i % max(1, n_vis // 8) == 0 or i == n_vis - 1:
                c.create_line(cx, pad_top, cx, pad_top + plot_h, fill="#151922")
                lbl_x = bar["time"] if self.tm_date.get() != "Multi-Day (Vše)" else f"{bar['date'][5:]} {bar['time']}"
                c.create_text(cx, h - 14, text=lbl_x, fill="#787b86", font=("Consolas", 8))

        if chart_style in ("Čárový (Line)", "Plošný (Area)") and len(line_pts) >= 4:
            if chart_style == "Plošný (Area)":
                poly_pts = [line_pts[0], pad_top + plot_h] + line_pts + [line_pts[-2], pad_top + plot_h]
                c.create_polygon(*poly_pts, fill="#16294a", outline="")
            c.create_line(*line_pts, fill="#2962ff", width=2.2, smooth=True)

        if len(vwap_pts) >= 4:
            c.create_line(*vwap_pts, fill="#f5a623", width=1.6, smooth=True)
        if len(ema9_pts) >= 4:
            c.create_line(*ema9_pts, fill="#00bcd4", width=1.2, smooth=True, dash=(4, 2))

        sym_lbl = self.tm_symbol.get().strip().upper()
        tf_lbl = self.tf_var.get()
        c.create_text(
            pad_left + 8, pad_top + 6,
            text=f"{sym_lbl} • {tf_lbl} (Zoom: {n_vis}/{total_len} svíček)  |  {pred_summary}",
            fill="#ffffff", font=("Segoe UI", 9, "bold"), anchor="nw"
        )

        # Růžová predikce vpravo od viditelných svíček
        if show_pink and pred_bars:
            zone_x1 = pad_left + n_vis * step_x
            zone_x2 = pad_left + (n_vis + len(pred_bars)) * step_x
            c.create_rectangle(zone_x1, pad_top, zone_x2, pad_top + plot_h, fill="#1a0d21", outline="#ff2e93", width=1, dash=(4, 4))
            c.create_text((zone_x1 + zone_x2) / 2, pad_top + 22, text=f"🩷 PREDIKCE ({tf_lbl})", fill="#ff2e93", font=("Segoe UI", 8, "bold"))

            anchor_bar = vis_candles[-1]
            pink_curve = [pad_left + (n_vis - 0.5) * step_x, price_to_y(anchor_bar["close"])]

            for idx_p, pb in enumerate(pred_bars):
                px = pad_left + (n_vis + idx_p + 0.5) * step_x
                pyo, pyh, pyl, pyc = price_to_y(pb["open"]), price_to_y(pb["high"]), price_to_y(pb["low"]), price_to_y(pb["close"])
                c.create_line(px, pyh, px, pyl, fill="#ff77bc", width=1, dash=(2, 2))
                ptop, pbot = min(pyo, pyc), max(pyo, pyc)
                if pbot - ptop < 2:
                    pbot = ptop + 2
                c.create_rectangle(px - body_w / 2, ptop, px + body_w / 2, pbot, fill="#ff2e93", outline="#ff99cc")
                pink_curve.extend([px, pyc])

            if len(pink_curve) >= 4:
                c.create_line(*pink_curve, fill="#ff2e93", width=2.5, smooth=True, arrow=tk.LAST, arrowshape=(10, 12, 5))

        def draw_level(val, label, col):
            if val is None:
                return
            clamped = max(min(val, p_max), p_min)
            y = price_to_y(clamped)
            is_outside = (val < p_min or val > p_max)
            c.create_line(pad_left, y, w - pad_right, y, fill=col, width=1.5, dash=(2, 4) if is_outside else (5, 3))
            c.create_rectangle(w - pad_right + 2, y - 9, w - 2, y + 9, fill=col, outline="")
            prefix = "▲ " if val > p_max else ("▼ " if val < p_min else "")
            c.create_text(w - pad_right + 5, y, text=f"{prefix}{label} {val:.2f}", fill="#ffffff", font=("Consolas", 8, "bold"), anchor="w")

        draw_level(entry_v, "IN", "#2962ff")
        draw_level(sl_v, "SL", "#f23645")
        draw_level(tp_v, "TP", "#ff2e93" if show_pink else "#089981")

        if hover_x and hover_y and pad_left <= hover_x <= (w - pad_right) and pad_top <= hover_y <= (pad_top + plot_h):
            c.create_line(hover_x, pad_top, hover_x, pad_top + plot_h, fill="#ff77bc", dash=(2, 2))
            c.create_line(pad_left, hover_y, w - pad_right, hover_y, fill="#ff77bc", dash=(2, 2))
            hp = y_to_price(hover_y)
            c.create_rectangle(w - pad_right + 2, hover_y - 9, w - 2, hover_y + 9, fill="#363a45", outline="#ff2e93")
            c.create_text(w - pad_right + 5, hover_y, text=f"${hp:.2f}", fill="#ffffff", font=("Consolas", 8, "bold"), anchor="w")

    def _get_candle_at_x(self, x_px):
        vis = self.chart_meta.get("vis_candles", self.chart_candles)
        if not vis:
            return None
        pad_left = self.chart_meta.get("pad_left", 15)
        step_x = self.chart_meta.get("step_x", 10)
        idx = int((x_px - pad_left) / max(step_x, 1))
        if 0 <= idx < len(vis):
            return vis[idx]
        return None

    def on_chart_hover(self, event):
        bar = self._get_candle_at_x(event.x)
        y_fn = self.chart_meta.get("y_to_price")
        tf = self.tf_var.get()
        if bar and y_fn:
            cur_p = y_fn(event.y)
            chg = ((bar["close"] - bar["open"]) / bar["open"]) * 100.0
            self.chart_info_lbl.config(
                text=f"🕒 [{tf} Svíčka] {bar['date']} {bar['time']} NY  |  O: ${bar['open']:.2f}  H: ${bar['high']:.2f}  L: ${bar['low']:.2f}  C: ${bar['close']:.2f} ({chg:+.2f}%)  |  Vol: {bar['volume']:,}  |  Kurzor: ${cur_p:.2f}",
                fg="#d1d4dc"
            )
            self.redraw_chart(hover_x=event.x, hover_y=event.y)

    def on_chart_left_click(self, event):
        self._drag_start_x = event.x
        self._drag_start_offset = self.pan_offset
        bar = self._get_candle_at_x(event.x)
        y_fn = self.chart_meta.get("y_to_price")
        if bar and y_fn:
            clicked_price = round(y_fn(event.y), 2)
            entry_price = round(bar["close"], 2) if bar["low"] <= clicked_price <= bar["high"] else clicked_price
            self.tm_entry.delete(0, tk.END)
            self.tm_entry.insert(0, f"{entry_price:.2f}")
            self.tm_time.delete(0, tk.END)
            self.tm_time.insert(0, bar["time"])
            self.redraw_chart()
            self.run_silent_script_prediction()

    def on_chart_drag(self, event):
        """Tažení myší posouvá graf v čase (Panning)."""
        if self._drag_start_x is None or not self.chart_candles:
            return
        dx = event.x - self._drag_start_x
        if abs(dx) < 8:
            return
        step_x = max(self.chart_meta.get("step_x", 8), 2)
        bars_moved = int(dx / step_x)
        max_off = max(0, len(self.chart_candles) - 15)
        self.pan_offset = max(0, min(max_off, self._drag_start_offset + bars_moved))
        self.redraw_chart()

    def on_chart_release(self, event):
        self._drag_start_x = None

    def on_chart_right_click(self, event):
        y_fn = self.chart_meta.get("y_to_price")
        if y_fn:
            sl_price = round(y_fn(event.y), 2)
            self.tm_stop.delete(0, tk.END)
            self.tm_stop.insert(0, f"{sl_price:.2f}")
            self.redraw_chart()
            self.run_silent_script_prediction()

    def on_chart_middle_click(self, event):
        y_fn = self.chart_meta.get("y_to_price")
        if y_fn:
            tp_price = round(y_fn(event.y), 2)
            self.tm_target.delete(0, tk.END)
            self.tm_target.insert(0, f"{tp_price:.2f}")
            self.redraw_chart()
            self.run_silent_script_prediction()

    # =========================================================================
    # ŽIVÝ FETCH Z YAHOO A VÝPOČET POZICE
    # =========================================================================
    def fetch_yahoo_direct(self, symbol):
        headers = {"User-Agent": "Mozilla/5.0"}
        result = {
            "symbol": symbol,
            "prev_close": None,
            "pm_price": None,
            "pmh": None,
            "pml": None,
            "vwap": None,
            "atr": None,
            "rvol": None,
            "pm_vol": 0,
            "gap_pct": None,
            "prediction": "Čeká na výpočet"
        }

        url_daily = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=1mo&_ts={int(time.time())}"
        req1 = urllib.request.Request(url_daily, headers=headers)
        with urllib.request.urlopen(req1, timeout=10) as resp:
            d_json = json.loads(resp.read().decode("utf-8"))

        res1 = d_json.get("chart", {}).get("result", [])
        avg_vol = None
        if res1:
            meta1 = res1[0].get("meta", {})
            result["prev_close"] = meta1.get("chartPreviousClose") or meta1.get("previousClose")
            q1 = res1[0].get("indicators", {}).get("quote", [{}])[0]
            highs = q1.get("high", [])
            lows = q1.get("low", [])
            closes = q1.get("close", [])
            vols = [v for v in q1.get("volume", []) if v is not None and v > 0]

            if len(vols) >= 2:
                avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)

            trs = []
            for i in range(1, len(closes)):
                h, l, pc = highs[i], lows[i], closes[i - 1]
                if h is not None and l is not None and pc is not None:
                    trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            if trs:
                result["atr"] = sum(trs[-14:]) / min(len(trs), 14)
            if closes and closes[-1] is not None and result["prev_close"] is None:
                result["prev_close"] = closes[-1]

        url_intra = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1m&range=2d&includePrePost=true&_ts={int(time.time())}"
        req2 = urllib.request.Request(url_intra, headers=headers)
        with urllib.request.urlopen(req2, timeout=10) as resp:
            i_json = json.loads(resp.read().decode("utf-8"))

        res2 = i_json.get("chart", {}).get("result", [])
        if res2:
            timestamps = res2[0].get("timestamp", [])
            q2 = res2[0].get("indicators", {}).get("quote", [{}])[0]
            ihighs = q2.get("high", [])
            ilows = q2.get("low", [])
            icloses = q2.get("close", [])
            ivols = q2.get("volume", [])

            if timestamps:
                last_dt = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).date()
                pm_h, pm_l, pm_c, pm_v = [], [], [], []
                vwap_num = 0.0
                vwap_den = 0

                for ts, h, l, c, v in zip(timestamps, ihighs, ilows, icloses, ivols):
                    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if dt_utc.date() == last_dt and c is not None:
                        pm_c.append(c)
                        if h is not None:
                            pm_h.append(h)
                        if l is not None:
                            pm_l.append(l)
                        vol_val = v or 0
                        pm_v.append(vol_val)
                        if h is not None and l is not None and vol_val > 0:
                            typical = (h + l + c) / 3.0
                            vwap_num += typical * vol_val
                            vwap_den += vol_val

                if pm_c:
                    result["pm_price"] = pm_c[-1]
                    result["pmh"] = max(pm_h) if pm_h else pm_c[-1]
                    result["pml"] = min(pm_l) if pm_l else pm_c[-1]
                    result["pm_vol"] = int(sum(pm_v))
                    if vwap_den > 0:
                        result["vwap"] = vwap_num / vwap_den

        pc = result["prev_close"]
        cur = result["pm_price"] or pc
        if pc and cur:
            result["gap_pct"] = ((cur - pc) / pc) * 100.0
        if avg_vol and result["pm_vol"]:
            result["rvol"] = max(result["rvol"] or 0.0, result["pm_vol"] / (avg_vol * 0.15))

        if result["pmh"] and result["pml"] and cur:
            vwap_val = result["vwap"] or ((result["pmh"] + result["pml"]) / 2.0)
            rng = max(result["pmh"] - result["pml"], 0.01)
            pos_in_range = (cur - result["pml"]) / rng

            if cur >= vwap_val and pos_in_range >= 0.60:
                result["prediction"] = f"🟢 GAP & GO (Nad VWAP ${vwap_val:.2f}, útok na High ${result['pmh']:.2f})"
            elif cur < vwap_val and pos_in_range <= 0.40:
                result["prediction"] = f"🔴 GAP FADE (Pod VWAP ${vwap_val:.2f}, tlak k Low ${result['pml']:.2f})"
            else:
                result["prediction"] = f"⚪ KONSOLIDACE u VWAP ${vwap_val:.2f} (Rozpětí ${result['pml']:.2f}-${result['pmh']:.2f})"

        return result

    def autoload_existing_watchlist(self):
        candidates = [
            os.path.join(BASE_DIR, "trader", "watchlist.json"),
            os.path.join(BASE_DIR, "trader-scanner", "watchlist.json"),
            os.path.join(BASE_DIR, "watchlist.json")
        ]
        wl_file = next((p for p in candidates if os.path.exists(p)), None)
        if not wl_file:
            return
        try:
            with open(wl_file, "r", encoding="utf-8", errors="replace") as f:
                raw = json.load(f)
            self.populate_from_json(raw)
            self.scan_status.config(text=f"✓ Načteno {len(self.items_map)} akcií z {os.path.relpath(wl_file, BASE_DIR)}.", fg="#089981")
        except Exception as exc:
            self.scan_status.config(text=f"Chyba čtení watchlist.json: {str(exc)}", fg="#f23645")

    def extract_rows_recursive(self, data):
        if isinstance(data, list):
            return [el if isinstance(el, dict) else {"symbol": str(el)} for el in data]
        if isinstance(data, dict):
            for key in ["hits", "watchlist", "candidates", "results", "stocks", "data", "items", "rows"]:
                if key in data and isinstance(data[key], list):
                    return self.extract_rows_recursive(data[key])
            for k, v in data.items():
                if isinstance(v, list) and len(v) > 0:
                    return self.extract_rows_recursive(v)
            out = []
            for k, v in data.items():
                if isinstance(v, dict):
                    v["symbol"] = v.get("symbol") or v.get("ticker") or k
                    out.append(v)
            if out:
                return out
        return []

    def populate_from_json(self, raw_data):
        rows = self.extract_rows_recursive(raw_data)
        self.items_map.clear()

        for r in rows:
            sym = (r.get("symbol") or r.get("ticker") or r.get("Symbol") or r.get("name") or "").strip().upper()
            if not sym:
                continue
            close_p = r.get("price") or r.get("close") or r.get("last") or r.get("prev_close")
            pm_p = r.get("premarket_price") or r.get("pm_price") or close_p
            gap = r.get("gap_pct") or r.get("gap") or r.get("change_pct")
            pmh = r.get("pmh") or r.get("premarket_high") or r.get("high")
            pml = r.get("pml") or r.get("premarket_low") or r.get("low")
            vwap = r.get("vwap") or r.get("pm_vwap")
            atr = r.get("atr") or r.get("atr14")
            rvol = r.get("rvol") or r.get("relative_volume")
            pm_vol = r.get("pm_vol") or r.get("premarket_volume") or r.get("volume") or 0
            note = r.get("prediction") or r.get("catalyst") or r.get("setup") or r.get("rule") or "Ze skeneru"

            self.items_map[sym] = {
                "symbol": sym,
                "prev_close": self._to_float(close_p),
                "pm_price": self._to_float(pm_p),
                "gap_pct": self._to_float(gap),
                "pmh": self._to_float(pmh),
                "pml": self._to_float(pml),
                "vwap": self._to_float(vwap),
                "atr": self._to_float(atr),
                "rvol": self._to_float(rvol),
                "pm_vol": int(self._to_float(pm_vol) or 0),
                "prediction": str(note)
            }
        self.apply_table_filter()

    def _to_float(self, val):
        if val is None or val == "":
            return None
        try:
            if isinstance(val, str):
                val = val.replace("%", "").replace("$", "").replace(",", ".").strip()
            return float(val)
        except Exception:
            return None

    def _insert_or_update_tree_row(self, sym):
        d = self.items_map[sym]
        pred = d.get("prediction", "")
        tag = "bull" if "GAP & GO" in pred else ("bear" if "GAP FADE" in pred else "neutral")

        vals = (
            sym,
            f"${d['prev_close']:.2f}" if d["prev_close"] else "-",
            f"${d['pm_price']:.2f}" if d["pm_price"] else "-",
            f"{d['gap_pct']:+.2f}%" if d["gap_pct"] is not None else "-",
            f"${d['pmh']:.2f}" if d["pmh"] else "-",
            f"${d['pml']:.2f}" if d["pml"] else "-",
            f"${d['vwap']:.2f}" if d["vwap"] else "-",
            f"${d['atr']:.2f}" if d["atr"] else "-",
            f"{d['rvol']:.1f}x" if d["rvol"] else "-",
            f"{d['pm_vol']/1000:.0f}k" if d["pm_vol"] else "-",
            pred
        )
        if self.tree.exists(sym):
            self.tree.item(sym, values=vals, tags=(tag,))
        else:
            self.tree.insert("", tk.END, iid=sym, values=vals, tags=(tag,))

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        sym = sel[0]
        self.selected_symbol = sym
        self.manual_ticker_entry.delete(0, tk.END)
        self.manual_ticker_entry.insert(0, sym)

        d = self.items_map.get(sym, {})
        if d.get("pmh") is None or d.get("atr") is None:
            self.fetch_single_ticker_live()
        else:
            self.update_prediction_banner(sym, update_tm_entries=True)
            self.load_interactive_chart(auto_reset_levels=True)

    def fetch_single_ticker_live(self):
        sym = self.manual_ticker_entry.get().strip().upper()
        if not sym:
            return
        self.selected_symbol = sym
        self.btn_live_pm.config(state=tk.DISABLED, bg="#2a2e39")
        self.pred_summary_lbl.config(text=f"Stahuji reálná tržní data, VWAP, ATR a predikci pro {sym}...", fg="#ff2e93")

        def worker():
            try:
                live = self.fetch_yahoo_direct(sym)
                if sym in self.items_map:
                    for k, v in live.items():
                        if v is not None:
                            self.items_map[sym][k] = v
                else:
                    self.items_map[sym] = live

                self.root.after(0, self.apply_table_filter)
                self.root.after(0, lambda: self.update_prediction_banner(sym, update_tm_entries=True))
                self.root.after(0, lambda: self.load_interactive_chart(auto_reset_levels=True))
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda m=err: self.pred_summary_lbl.config(text=f"Chyba Yahoo dat: {m}", fg="#f23645"))
            finally:
                self.root.after(0, lambda: self.btn_live_pm.config(state=tk.NORMAL, bg="#ff2e93"))

        threading.Thread(target=worker, daemon=True).start()

    def calculate_order_params(self, sym):
        d = self.items_map.get(sym, {})
        cur = d.get("pm_price") or d.get("prev_close") or 10.0
        pmh = d.get("pmh") or cur
        pml = d.get("pml") or (cur * 0.97)
        vwap = d.get("vwap") or ((pmh + pml) / 2.0)
        atr = d.get("atr") or max((pmh - pml), cur * 0.025)

        cap_czk = self._to_float(self.account_size.get()) or 50000.0
        risk_p = self._to_float(self.risk_pct.get()) or 1.0
        fx = self._to_float(self.usd_czk.get()) or 23.50

        risk_czk = cap_czk * (risk_p / 100.0)
        risk_usd = risk_czk / fx

        intraday_step = max(atr * 0.22, cur * 0.006)
        if cur >= vwap:
            scenario = "GAP & GO (Cena drží nad VWAP – Long pokračování trendu)"
            entry = round(cur, 2)
            sl = round(cur - intraday_step, 2)
        else:
            scenario = "GAP FADE / PULLBACK (Cena pod VWAP – opatrný obchod u VWAP/Low)"
            entry = round(cur, 2)
            sl = round(cur - intraday_step, 2)

        risk_per_share = max(abs(entry - sl), 0.05)
        tp1 = round(entry + (1.8 * risk_per_share), 2)
        tp2 = round(entry + (3.0 * risk_per_share), 2)

        shares = max(int(risk_usd / risk_per_share), 1)
        pos_usd = shares * entry
        pos_czk = pos_usd * fx

        return {
            "scenario": scenario,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "shares": shares,
            "risk_usd": risk_usd,
            "risk_czk": risk_czk,
            "pos_usd": pos_usd,
            "pos_czk": pos_czk,
            "fx": fx
        }

    def update_prediction_banner(self, sym, update_tm_entries=True):
        d = self.items_map.get(sym, {})
        calc = self.calculate_order_params(sym)
        broker = self.broker_var.get()

        pmh_s = f"${d['pmh']:.2f}" if d.get("pmh") else "N/A"
        pml_s = f"${d['pml']:.2f}" if d.get("pml") else "N/A"
        vwap_s = f"${d['vwap']:.2f}" if d.get("vwap") else "N/A"
        atr_s = f"${d['atr']:.2f}" if d.get("atr") else "N/A"
        cur_s = f"${d['pm_price']:.2f}" if d.get("pm_price") else "N/A"

        text = (
            f"[{sym}] Live Cena: {cur_s}  |  High: {pmh_s}  |  Low: {pml_s}  |  VWAP: {vwap_s}  |  ATR(14): {atr_s}  |  Kurz ČNB: {calc['fx']:.2f} CZK/USD\n"
            f"🩷 TECHNICKÁ PREDIKCE: {calc['scenario']}\n"
            f"POKYN PRO {broker}:  Vstup @ ${calc['entry']:.2f}  |  SL: ${calc['sl']:.2f}  |  TP1: ${calc['tp1']:.2f}  |  TP2: ${calc['tp2']:.2f}  |  "
            f"POZICE: {calc['shares']} ks ({calc['pos_czk']:.0f} CZK / ${calc['pos_usd']:.0f})  |  Max Risk: {calc['risk_czk']:.0f} CZK (${calc['risk_usd']:.1f})"
        )
        self.pred_summary_lbl.config(text=text, fg="#ff77bc")

        if update_tm_entries:
            self.tm_symbol.delete(0, tk.END)
            self.tm_symbol.insert(0, sym)
            self.tm_entry.delete(0, tk.END)
            self.tm_entry.insert(0, f"{calc['entry']:.2f}")
            self.tm_stop.delete(0, tk.END)
            self.tm_stop.insert(0, f"{calc['sl']:.2f}")
            self.tm_target.delete(0, tk.END)
            self.tm_target.insert(0, f"{calc['tp1']:.2f}")

    def run_stream(self, cmd_args, log_widget):
        process = subprocess.Popen(
            cmd_args, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1
        )
        for line in iter(process.stdout.readline, ''):
            if line:
                cleaned = line.rstrip()
                self.root.after(0, lambda t=cleaned: (log_widget.insert(tk.END, t + "\n"), log_widget.see(tk.END)))
        process.stdout.close()
        process.wait()
        return process.returncode

    def start_scan(self):
        self.btn_run_scan.config(state=tk.DISABLED, bg="#2a2e39")
        self.scan_log.delete("1.0", tk.END)
        self.scan_progress.start(10)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            mode = self.scan_mode_var.get()
            scan_candidates = [
                os.path.join(BASE_DIR, "trader-scanner", "scan.py"),
                os.path.join(BASE_DIR, "scan.py"),
                os.path.join(BASE_DIR, "trader", "scan.py")
            ]
            scan_script = next((p for p in scan_candidates if os.path.exists(p)), None)

            if mode == "custom":
                raw_syms = [s.strip().upper() for s in self.custom_symbols_entry.get().split(",") if s.strip()]
                if not raw_syms:
                    raise ValueError("Zadej alespoň jeden ticker.")
                self.items_map.clear()

                for idx, s in enumerate(raw_syms, 1):
                    self.root.after(0, lambda sym=s, i=idx, tot=len(raw_syms): self.scan_status.config(
                        text=f"Stahuji živá data & ATR ({i}/{tot}): {sym}...", fg="#ff2e93"
                    ))
                    live = self.fetch_yahoo_direct(s)
                    self.items_map[s] = live
                    self.root.after(0, self.apply_table_filter)

                if raw_syms:
                    first = raw_syms[0]
                    self.root.after(0, lambda: self.update_prediction_banner(first, update_tm_entries=True))
                    self.root.after(0, lambda: self.load_interactive_chart(auto_reset_levels=True))
                self.root.after(0, lambda: self.scan_status.config(text="✓ Hotovo! Všechna reálná data jsou v tabulce.", fg="#089981"))
                return

            if not scan_script:
                raise FileNotFoundError("Soubor scan.py nebyl nalezen.")

            uni_path = os.path.join(BASE_DIR, "trader", "universe.txt")
            if not os.path.exists(uni_path):
                self.root.after(0, lambda: self.scan_status.config(text="Stahuji universe.txt...", fg="#2962ff"))
                self.run_stream([sys.executable, scan_script, "universe"], self.scan_log)

            sub_cmd = "premarket" if mode == "premarket" else "daily"
            self.root.after(0, lambda: self.scan_status.config(text=f"Spouštím scan.py {sub_cmd}...", fg="#2962ff"))
            code = self.run_stream([sys.executable, scan_script, sub_cmd], self.scan_log)
            if code != 0 and sub_cmd == "premarket":
                self.run_stream([sys.executable, scan_script, "daily"], self.scan_log)

            self.root.after(0, self.autoload_existing_watchlist)

            if self.auto_pm_var.get():
                time.sleep(0.4)
                top_symbols = list(self.items_map.keys())[:15]
                for idx, s in enumerate(top_symbols, 1):
                    self.root.after(0, lambda sym=s, i=idx, tot=len(top_symbols): self.scan_status.config(
                        text=f"Obohacuji TOP akcie o živá data & ATR ({i}/{tot}): {sym}...", fg="#ff2e93"
                    ))
                    try:
                        live = self.fetch_yahoo_direct(s)
                        for k, v in live.items():
                            if v is not None:
                                self.items_map[s][k] = v
                        self.root.after(0, self.apply_table_filter)
                    except Exception:
                        pass

            self.root.after(0, lambda: self.scan_status.config(text="✓ Kompletní sken dokončen!", fg="#089981"))

        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda m=err: self.scan_status.config(text=f"Chyba: {m}", fg="#f23645"))
        finally:
            self.root.after(0, self.scan_progress.stop)
            self.root.after(0, lambda: self.btn_run_scan.config(state=tk.NORMAL, bg="#089981"))

    def start_timemachine(self):
        sym = self.tm_symbol.get().strip().upper()
        if not sym:
            return
        self.btn_run_tm.config(state=tk.DISABLED, bg="#2a2e39")
        self.tm_log.delete("1.0", tk.END)
        self.tm_progress.start(10)
        self.redraw_chart()
        threading.Thread(target=self._tm_worker, daemon=True).start()

    def _tm_worker(self):
        try:
            sym = self.tm_symbol.get().strip().upper()
            tm_candidates = [
                os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py"),
                os.path.join(BASE_DIR, "timemachine.py"),
                os.path.join(BASE_DIR, "trader-scanner", "timemachine.py"),
            ]
            tm_script = next((p for p in tm_candidates if os.path.exists(p)), None)
            if not tm_script:
                raise FileNotFoundError("Soubor timemachine.py nebyl nalezen.")

            t_sel = self.tm_time.get().strip()
            target_bar = self.chart_candles[-1] if self.chart_candles else None
            if self.chart_candles:
                for b in self.chart_candles:
                    if b["time"] == t_sel:
                        target_bar = b
                        break

            entry = self.tm_entry.get().strip().replace(",", ".")
            stop = self.tm_stop.get().strip().replace(",", ".")
            target = self.tm_target.get().strip().replace(",", ".")
            dt_date = target_bar["date"] if target_bar else ""
            dt_time = target_bar["time"] if target_bar else t_sel

            cmd = [sys.executable, tm_script, "run", "--symbol", sym]
            if entry:
                cmd.extend(["--entry", entry])
            if stop:
                cmd.extend(["--stop", stop])
            if target:
                cmd.extend(["--target", target])
            if dt_date:
                cmd.extend(["--date", dt_date])
            if dt_time:
                cmd.extend(["--entry-time", dt_time])

            cmd_str = " ".join(cmd)
            self.root.after(0, lambda t=cmd_str: self.tm_log.insert(tk.END, f">> Spouštím reálný 60denní replay: {t}\n\n"))
            code = self.run_stream(cmd, self.tm_log)

            self.run_silent_script_prediction()

            if code == 0:
                def copy_tm():
                    out = self.tm_log.get("1.0", tk.END).strip()
                    self.root.clipboard_clear()
                    self.root.clipboard_append(f"Zhodnoť výsledek 60denního replaye Time Machine pro {sym} ({dt_date} {dt_time} NY):\n\n{out}")
                    self.tm_log.insert(tk.END, "\n[OK] Výstup Time Machine byl zkopírován do schránky pro Claude!\n")
                self.root.after(0, copy_tm)

        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda m=err: self.tm_log.insert(tk.END, f"\nChyba: {m}\n"))
        finally:
            self.root.after(0, self.tm_progress.stop)
            self.root.after(0, lambda: self.btn_run_tm.config(state=tk.NORMAL, bg="#089981"))

    def copy_comprehensive_prompt(self):
        broker = self.broker_var.get()
        cap = self.account_size.get().strip() or "50000"
        risk = self.risk_pct.get().strip() or "1.0"

        sym = self.manual_ticker_entry.get().strip().upper() or self.selected_symbol
        if sym and sym in self.items_map:
            d = self.items_map[sym]
            calc = self.calculate_order_params(sym)
            target_block = (
                f"HLAVNÍ VYBRANÁ AKCIE PRO OBCHOD: {sym}\n"
                f"- Včerejší Close: ${d.get('prev_close') or 0:.2f} | Aktuální cena: ${d.get('pm_price') or 0:.2f} (Gap: {d.get('gap_pct') or 0:+.2f}%)\n"
                f"- Session High: ${d.get('pmh') or 0:.2f} | Session Low: ${d.get('pml') or 0:.2f} | VWAP: ${d.get('vwap') or 0:.2f}\n"
                f"- 14denní ATR: ${d.get('atr') or 0:.2f} | Objem: {d.get('pm_vol', 0):,} akcií\n"
                f"- Středový kurz ČNB: {calc['fx']:.3f} CZK/USD\n"
                f"- Technická predikce: {calc['scenario']}\n"
                f"- Spočítaný pokyn ({broker}): Vstup ${calc['entry']:.2f} | SL ${calc['sl']:.2f} | TP1 ${calc['tp1']:.2f} | TP2 ${calc['tp2']:.2f} | "
                f"Kusů: {calc['shares']} ks (Hodnota pozice {calc['pos_czk']:.0f} CZK, Max Risk {calc['risk_czk']:.0f} CZK / ${calc['risk_usd']:.1f})\n\n"
            )
        else:
            target_block = "Uživatel nevybral konkrétní akcii – vyber 2 nejlepší setupy z tabulky níže.\n\n"

        top_list = list(self.items_map.values())[:25]
        clean_json = json.dumps(top_list, indent=2, ensure_ascii=False)

        prompt = (
            "Jsi seniorní intradenní trader. Zde jsou kompletní reálná tržní data a přepočet přes aktuální kurz ČNB.\n\n"
            f"{target_block}"
            "TVŮJ ÚKOL:\n"
            "1. Potvrď statisticky nejpravděpodobnější scénář (Gap & Go nad VWAP vs. Gap Fade pod VWAP).\n"
            f"2. Vypiš finální exekuční lístek pro brokera {broker} (Kapitál {cap} CZK, Risk {risk} %):\n"
            "   - Typ příkazu (Buy Stop / Buy Limit)\n"
            "   - Přesná cena Vstupu, Stop Lossu, TP1 a TP2\n"
            "   - Přesný počet kusů akcií\n\n"
            "DATA TOP AKCIÍ ZE SKENERU:\n"
            f"{clean_json}\n"
        )

        self.root.clipboard_clear()
        self.root.clipboard_append(prompt)
        messagebox.showinfo("Zkopírováno!", f"Kompletní analýza, kurz ČNB i pokyn pro {broker} jsou ve schránce!\n\nStiskni Ctrl + V v Claude.")


# =====================================================================
# ONLINE OVĚŘENÍ LICENCE (KEYAUTH)
# =====================================================================
KEYAUTH_NAME = "SEM_ZADEJ_NAZEV_APLIKACE"   # Název aplikace z KeyAuth dashboardu
KEYAUTH_OWNER_ID = "pitE014VXe"             # Tvůj OwnerID z profilu
KEYAUTH_SECRET = "SEM_ZADEJ_SECRET"         # Secret z Application Settings v KeyAuth
KEYAUTH_VERSION = "1.0"

def enforce_license_check():
    import urllib.request
    import urllib.parse
    import json
    import os
    import sys
    import tkinter as tk

    app_data = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "DayTraderStudio")
    lic_file = os.path.join(app_data, "license.key")

    def check_keyauth(license_key):
        clean_key = license_key.strip()
        if not clean_key:
            return False, "Zadej licenční klíč."

        api_url = "https://keyauth.win/api/1.2/"

        # 1. Init
        init_params = {
            "type": "init",
            "name": "Day Trader Ultimate",
            "ownerid": "pitE014VXe",
            "secret": "2786a81be538d1045d9430292393374dcd754f52a6ad51b9ef48a0c3c5a60f3b",
            "version": "1.0"
        }
        init_data = urllib.parse.urlencode(init_params).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "KeyAuth"
        }

        try:
            req_init = urllib.request.Request(api_url, data=init_data, headers=headers)
            with urllib.request.urlopen(req_init, timeout=8) as resp:
                raw_init = resp.read().decode("utf-8", errors="replace")

            try:
                res_init = json.loads(raw_init)
            except Exception:
                return False, f"Chyba serveru (init): {raw_init[:60]}"

            if not res_init.get("success"):
                return False, f"Chyba init: {res_init.get('message', 'Neznámá chyba')}"

            session_id = res_init.get("sessionid")

            # 2. License check
            lic_params = {
                "type": "license",
                "key": clean_key,
                "sessionid": session_id,
                "name": "Day Trader Ultimate",
                "ownerid": "pitE014VXe"
            }
            lic_data = urllib.parse.urlencode(lic_params).encode("utf-8")
            req_lic = urllib.request.Request(api_url, data=lic_data, headers=headers)

            with urllib.request.urlopen(req_lic, timeout=8) as resp:
                raw_lic = resp.read().decode("utf-8", errors="replace")

            try:
                res_lic = json.loads(raw_lic)
            except Exception:
                return False, f"Chyba serveru (lic): {raw_lic[:60]}"

            if res_lic.get("success"):
                return True, "Licence aktivována!"
            else:
                return False, res_lic.get("message", "Neplatný klíč.")

        except Exception as e:
            return False, f"Chyba připojení: {e}"

    # 1. Kontrola uložené licence z minula
    if os.path.exists(lic_file):
        try:
            with open(lic_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            ok, _ = check_keyauth(saved)
            if ok:
                return True
        except Exception:
            pass

    # 2. Vyskakovací okno pro zadání klíče
    auth_win = tk.Tk()
    auth_win.title("Aktivace – Day Trader Studio")
    auth_win.geometry("480x240")
    auth_win.configure(bg="#0b0e14")
    auth_win.resizable(False, False)

    is_authorized = [False]

    tk.Label(auth_win, text="AKTIVACE PRODUKTU", font=("Segoe UI", 12, "bold"), fg="#ff2e93", bg="#0b0e14").pack(pady=(22, 6))
    tk.Label(auth_win, text="Zadej licenční klíč k odemknutí aplikace:", font=("Segoe UI", 9), fg="#787b86", bg="#0b0e14").pack(pady=(0, 10))

    entry_k = tk.Entry(
        auth_win, width=38, font=("Consolas", 10, "bold"), justify="center",
        bg="#131722", fg="#ffffff", insertbackground="white",
        highlightbackground="#2a2e39", highlightthickness=1, relief=tk.FLAT
    )
    entry_k.pack(pady=4, ipady=4)
    entry_k.focus()

    status_lbl = tk.Label(auth_win, text="", font=("Segoe UI", 8), fg="#f23645", bg="#0b0e14", wraplength=440)
    status_lbl.pack(pady=4)

    def do_activate():
        k = entry_k.get().strip()
        status_lbl.config(text="Ověřuji licenci přes KeyAuth...", fg="#2962ff")
        auth_win.update()

        ok, msg = check_keyauth(k)
        if ok:
            try:
                os.makedirs(app_data, exist_ok=True)
                with open(lic_file, "w", encoding="utf-8") as f:
                    f.write(k)
            except Exception:
                pass
            is_authorized[0] = True
            auth_win.destroy()
        else:
            status_lbl.config(text=f"Chyba: {msg}", fg="#f23645")

    tk.Button(
        auth_win, text="AKTIVOVAT LICENCI", font=("Segoe UI", 9, "bold"),
        bg="#089981", fg="white", activebackground="#0aa68c",
        padx=18, pady=5, relief=tk.FLAT, cursor="hand2", command=do_activate
    ).pack(pady=10)

    auth_win.bind("<Return>", lambda e: do_activate())
    auth_win.mainloop()

    if not is_authorized[0]:
        sys.exit(0)

# =====================================================================
# SPUŠTĚNÍ PROGRAMU
# =====================================================================
if __name__ == "__main__":
    enforce_license_check()

    root = tk.Tk()
    app = DayTraderMasterStudio(root)
    root.mainloop()