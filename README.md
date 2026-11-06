# Day Trader Studio PRO (Desktop Plugin & Engine)

> **DŮLEŽITÉ UPOZORNĚNÍ:**  
> Tento repozitář slouží výhradně jako **rozšiřující desktopový plugin / běhové prostředí** pro základní engine:  
> 👉 **[Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)**  
>  
> Samostatně bez propojení na výše uvedený repozitář není systém funkční. Pro plnou funkcionalitu je nutné mít nasazenou základní bázi z původního repozitáře.

---

## ⚡ Proč tento plugin používat? (Bypass Claude limitů & Paywallu)

Originální implementace skriptů a AI predikcí v prostředí Claude naráží na tvrdé komerční a technické překážky:

* **Bypass Claude Paywallu:** Oficiální webové rozhraní Claude vyžaduje placené předplatné (Claude Pro / Team) pro spouštění složitého generování a provádění Python kódu. Tento plugin převádí celou výpočetní i analytickou logiku lokálně do tvého PC – **bez nutnosti platit Claude předplatné**.
* **Odstranění restriktivních limitů zpráv:** Žádné hlášky typu *"You've reached your message limit until 3 PM"*. Běžíš na vlastním hardwaru bez umělých časových oken.
* **Neomezené skenování trhu v reálném čase:** Původní skripty z `day-trader-skill` jsou zde zabaleny do samostatného desktopového GUI (multi-timeframe analýza, live data z Yahoo Finance, scanner a time-machine simulace), což umožňuje nepřetržitý běh bez timeoutů cloudového API.

---

## 🚀 Rychlá instalace a spuštění

### Varianta A: Stažení hotové aplikace (.exe)
1. Přejdi do záložky **[Releases](../../releases)**.
2. Stáhni nejnovější sestavení a spusť pomocí `start.bat`..

### Varianta B: Spuštění ze zdrojových kódů (Python)
1. Naklonuj tento repozitář společně se závislostmi z mateřského projektu:
   ```bash
      git clone https://github.com/Machy15/Day-Trader-Studio/

