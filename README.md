<div align="center">

# 📊 Day Trader Studio PRO
### Real Market Multi-TF Chart & Script-Driven AI Prediction

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg?style=for-the-badge)](https://github.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Interface](https://img.shields.io/badge/GUI-Tkinter%20%2F%20Custom%20Canvas-ff2e93.svg?style=for-the-badge)](https://docs.python.org/3/library/tkinter.html)

<p align="center">
  <b>Komplexní desktopová analytická aplikace pro intradenní obchodování na amerických trzích s živým napojením na Yahoo Finance, reálnými kurzy ČNB, interaktivním kreslením grafů a kalkulátorem pozic pro brokery XTB a Trading 212.</b>
</p>

[Funkce](#-klíčové-funkce) •
[Instalace](#-rychlý-start) •
[Ovládání Grafu](#-interaktivní-graf--hotkeys) •
[Architektura](#-struktura-projektu) •
[Licence](#-licence)

</div>

---

## ⚡ Klíčové funkce

### 🔍 1. Live Pre-Market Scanner & Watchlist
* **Automatická detekce setupů:** Okamžité rozpoznání strategií **GAP & GO** (býčí průraz nad VWAP) a **GAP FADE** (medvědí návrat pod VWAP).
* **Technické metriky:** Výpočet intradenního VWAP, ATR(14), RVOL (relativní objem) a sledování seance High / Low.
* **Živý scraping & Yahoo API:** Možnost skenovat celý trh, filtrovat TOP 15 akcií s nejvyšší volatilitou, nebo manuálně analyzovat jakýkoliv ticker (`TSLA`, `NVDA`, `PLTR`, atd.).

### 📈 2. Multi-Timeframe Canvas Engine
* **Časové rámce na 1 klik:** Bleskový přechod mezi **1m, 2m, 5m, 15m, 30m a 1h** svíčkami.
* **4 styly zobrazení:**
  * Klasické Svíčky (Candlesticks)
  * Vyhlazené Heikin-Ashi
  * Čárový graf (Line)
  * Plošný graf (Area)
* **Indikátory v reálném čase:** Integrovaný **VWAP** (oranžová linka) a exponenciální klouzavý průměr **EMA(9)** (tyrkysová linka) spolu s objemovými histogramy.

### 🩷 3. Deterministická & Skriptová Predikce
* **Růžová prediktivní zóna:** Vypočítává trajektorii budoucích 5 svíček na základě polohy vůči VWAP, sklonu EMA(9) a lokální volatility ATR.
* **Integrace s TimeMachine (`timemachine.py`):** Spouštění 60denního historického replay testu na pozadí se statistickou úspěšností zasažení Targetu vs. Stop Lossu.

### 🛡️ 4. Risk Management s kurzem ČNB
* **Automatické stahování kurzovního lístku ČNB:** Přepočet velikosti pozice z CZK na USD v reálném čase (s fallbackem na Yahoo live forex).
* **Přesný Money Management:** Výpočet přesného počtu kusů akcií a maximální ztráty v CZK i USD na základě nastaveného kapitálu a % povoleného risku.
* **Generátor Promptů pro Claude / GPT:** Jedním klikem vygeneruje profesionálně naformátovaný příkaz do schránky s kompletními daty ze skeneru a parametry objednávky.

---

## 🎮 Interaktivní Graf & Hotkeys

Plátno grafu je plně interaktivní a umožňuje nastavovat cenové hladiny přímo kliknutím myši:

| Ovládací prvek | Akce v grafu |
| :--- | :--- |
| **Levé tlačítko myši** | Nastaví **Cenu vstupu (IN)** a ukotví čas vybrané svíčky pro modelování |
| **Pravé tlačítko myši** | Okamžitě nastaví hladinu **Stop Loss (SL)** |
| **Prostřední tl. / Dvojklik** | Nastaví úroveň **Take Profit (TP)** |
| **Pohyb kurzoru (Hover)** | Zobrazí přesný **nitkový kříž**, OHLC data dané svíčky a procentuální změnu |

---

## 🚀 Rychlý start

### Požadavky
* **Python 3.10** nebo novější
* Standardní knihovny Tkinter (obvykle součástí instalace Pythonu)

### Instalace a spuštění

1. **Naklonuj repozitář:**
   ```bash
   git clone https://github.com/uzivatel/day-trader-studio-pro.git
   cd day-trader-studio-pro
   ```

2. **Spusť aplikaci:**
   ```bash
   python main.py
   ```

*(Aplikace využívá výhradně vestavěné knihovny standardní distribuce Pythonu: `tkinter`, `urllib`, `json`, `zoneinfo`, takže není potřeba instalovat žádné externí `pip` balíčky.)*

---

## 📁 Struktura projektu

```text
day-trader-studio-pro/
│
├── main.py                     # Hlavní GUI aplikace s grafickým Canvas enginem
├── README.md                   # Dokumentace projektu
│
├── trader-scanner/             # (Volitelné) Skripty pro skenování trhu
│   ├── scan.py                 # Skenování gapů a načítání tickery z trhu
│   └── universe.txt            # Seznam sledovaných tickerů
│
└── trader-timemachine/          # (Volitelné) Backtestovací modul
    └── timemachine.py          # 60denní replay a statistika úspěšnosti
```

---

## ⚙️ Ukázka výpočtu pozice

Aplikace pracuje s exaktním vzorcem pro ochranu kapitálu:

$$\text{Risk (CZK)} = \text{Kapitál} \times \left(\frac{\text{Risk \%}}{100}\right)$$

$$\text{Risk (USD)} = \frac{\text{Risk (CZK)}}{\text{Kurz ČNB (USD/CZK)}}$$

$$\text{Počet akcií} = \left\lfloor \frac{\text{Risk (USD)}}{|\text{Vstup} - \text{Stop Loss}|} \right\rfloor$$

---

## 📝 Licence

Tento projekt je poskytován pod licencí **MIT**. Více informací naleznete v souboru [LICENSE](LICENSE).

<div align="center">
  <sub>Vytvořeno s důrazem na rychlost, čistý kód a disciplinovaný risk management.</sub>
</div>