# Real-Agent
Bypass claude ai limit and create prediction on real market, based on trader-skill by Jakeschincariol

Day Trader Studio PRO 🚀📈
Day Trader Studio PRO is a professional, high-performance desktop application built in Python using Tkinter. It is engineered for intraday stock traders to scan pre-market movers, analyze real-time multi-timeframe charts with integrated VWAP and EMA indicators, execute backtests with script-driven AI predictions, and accurately manage risk using live Czech National Bank (ČNB) exchange rates.

✨ Key Features
📊 Live Market Scanner & Watchlist

Scans pre-market gaps, relative volume (RVOL), ATR, and session extremes.

Quick filters for Gap & Go (Bullish) and Gap Fade (Bearish) setups.

Direct integration with Yahoo Finance data streams for live pricing and volume updates.

📈 Interactive Multi-Timeframe Charting Engine

Supports multiple intervals: 1m, 2m, 5m, 15m, 30m, and 1h.

Multiple chart styles: Candlesticks, Heikin-Ashi, Line, and Area.

Calculates and visualizes intraday VWAP (yellow) and EMA(9) (cyan) curves.

Interactive mouse controls to place and adjust Entry (IN), Stop Loss (SL), and Take Profit (TP) levels directly on the canvas.

🤖 Script-Driven Backtesting & Predictions

Integrates with a 60-day historical replay engine (timemachine.py) to calculate statistical win/loss probabilities (target_first_pct vs stop_first_pct).

Visual "Pink Prediction" projection zone mapping out expected price trajectories based on real volatility and momentum.

💱 Live Risk & Position Management

Real-time conversion using ČNB (Czech National Bank) foreign exchange rates (or fallback live forex feeds).

Calculates exact position sizing (number of shares), total position value in CZK/USD, and risk parameters based on your account size and risk tolerance percentage.

📋 Claude AI Prompt Integration

One-click generation of comprehensive trading prompts containing exact order tickets, risk parameters, and top scanner setups ready to paste directly into AI assistants like Claude.

🚀 Installation & Quick Start
Clone the repository:

🛠️ Tech Stack
Language: Python 3.10+

GUI Framework: Tkinter / ttk

Data Sources: Yahoo Finance API, Czech National Bank (ČNB) RSS/Devizový trh

Concurrency: Multi-threading (threading, subprocess) for non-blocking UI responsiveness

day-trader-studio-pro/
│
├── main.py                # Main application entry point & GUI controller
├── timemachine.py         # 60-day historical backtest / replay engine (optional backend)
├── scan.py                # Pre-market and daily scanner script (optional backend)
├── universe.txt           # Stock universe list
└── README.md              # Project documentation

Ensure Python 3.10+ is installed on your system.

Run the application:

python main.py

How to Use
Configure Account Settings (Top Bar):

Select your broker (XTB or Trading 212).

Enter your account capital in CZK and your desired risk percentage per trade (e.g., 1.0%).

Check or refresh the live USD/CZK exchange rate from ČNB.

Tab 1: Live Scanner & Search:

Click ▶ 1. SPUSTIT SKEN TRHU to load pre-market movers or enter custom tickers (e.g., TSLA, NVDA, AAPL).

Click any stock row in the table to instantly load its live metrics and calculate risk/position sizing.

Tab 2: Multi-Timeframe Chart & Predictions:

Switch between timeframes (1m, 5m, 15m, etc.) instantly using the quick buttons.

Mouse Controls on Chart:

Left Click: Set Entry price & candle time.

Right Click: Set Stop Loss (SL).

Double Click / Middle Click: Set Take Profit (TP).

Click ▶ Spustit Skript TimeMachine to run a full 60-day statistical backtest for the active setup.

🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check issues page or submit a pull request.

📄 License
This project is open-source and available under the MIT License.
