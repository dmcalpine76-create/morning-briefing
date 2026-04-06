@echo off
:: ─────────────────────────────────────────────────────────────
:: run_briefing.bat  —  Daily Morning Briefing runner
:: ─────────────────────────────────────────────────────────────

:: Move to project folder
cd /d "C:\Users\dmcal\OneDrive - State Gas\Documents\Current document editing\new AI projects\morning briefing system"
if errorlevel 1 (
    echo ERROR: Could not find project folder.
    pause
    exit /b 1
)

:: Create output folder if needed
if not exist "output" mkdir output

:: Run the briefing (briefing.py loads .env automatically)
py briefing.py

if "%1"=="manual" pause
