@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title RVV Hunter v6.0 - LIVE Trading Ready
echo ============================================
echo   RVV Hunter v6.0 - LIVE Trading Ready
echo   http://127.0.0.1:8083
echo ============================================
echo.
echo NEW IN v6.0:
echo   + Binance USDM Futures LIVE trading
echo   + LONG/SHORT support for real trades
echo   + Safety limits and position controls
echo   + Agent LIVE trading tools
echo.
echo WARNING: LIVE mode uses REAL money!
echo Start with TESTNET first.
echo.
python app.py
pause
