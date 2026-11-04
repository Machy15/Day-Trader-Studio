@echo off
title Day Trader Studio Launcher
color 0A

:: Prepnuti do slozky, ve ktere se nachazi tento bat soubor
cd /d "%~dp0"

echo ========================================================
echo        SPUSTENI DAY TRADER STUDIO (gen.py)
echo ========================================================
echo.

:: Kontrola, zda je nainstalovan Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    color 0C
    echo [CHYBA] Python nebyl nalezen v promennych prostredi PATH!
    echo Ujistete se, ze mate nainstalovany Python a zaskrtnute "Add Python to PATH".
    echo.
    pause
    exit /b
)

:: Kontrola existence souboru gen.py
if not exist "gen.py" (
    color 0C
    echo [CHYBA] Soubor gen.py nebyl v teto slozce nalezen!
    echo Umistete tento .bat soubor primo do slozky k gen.py.
    echo.
    pause
    exit /b
)

echo Spoustim aplikaci...
echo.
python gen.py

if %errorlevel% neq 0 (
    color 0C
    echo.
    echo [UPOZORNENI] Skript skoncil s chybou.
    pause
)