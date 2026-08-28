@echo off
title Calibrazione Colore Sensore ASI294MC
echo ========================================================
echo   STRUMENTO CALIBRAZIONE COLORE / PUNTO DI BIANCO
echo ========================================================
echo.
python color_calibration.py
echo.
echo Premi un tasto per chiudere...
pause >nul
