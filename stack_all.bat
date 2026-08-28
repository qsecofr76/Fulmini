@echo off
title Fulmini - Stacking Batch .SER in .TIFF (16-bit)
echo ========================================================
echo         FULMINI - STACKING BATCH .SER -> .TIFF 16-bit
echo ========================================================
echo.
python stacker.py --dir captures --method MAX
echo.
echo Operazione completata! Premi un tasto per chiudere...
pause >nul
