@echo off
title Fulmini - Interfaccia Grafica Cattura ZWO ASI
echo ========================================================
echo               AVVIO APPLICAZIONE FULMINI
echo ========================================================
echo.
python gui.py
echo.
if %errorlevel% neq 0 (
    echo Si e' verificato un errore nell'avvio della GUI.
    pause
)
