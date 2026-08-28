@echo off
title Fulmini - Calibrazione Colore Sensore
echo ========================================================
echo       AVVIO FULMINI - CALIBRAZIONE COLORE VIA MONITOR
echo ========================================================
echo.
python calibrate_colors.py
echo.
if %errorlevel% neq 0 (
    echo Si e' verificato un errore durante la calibrazione.
    pause
)
