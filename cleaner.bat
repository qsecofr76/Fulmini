@echo off
title Fulmini - Pulizia Catture & Spazio Disco
echo ========================================================
echo         AVVIO FULMINI - UTILITY PULIZIA CATTURE
echo ========================================================
python cleaner_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Si e' verificato un errore nell'avvio dell'utility di pulizia.
    pause
)
