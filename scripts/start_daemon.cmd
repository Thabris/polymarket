@echo off
rem Auto-start launcher for the Polymarket scanner daemon.
rem Deployed to the user's Startup folder so the daemon survives reboots.
rem pythonw = no console window; logs go to var\daemon.log.
cd /d "%~dp0.."
start "" "C:\Python313\pythonw.exe" main.py
