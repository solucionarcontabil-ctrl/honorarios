@echo off
chcp 65001 >nul
title Redefinir senha
cd /d "%~dp0.."
if not exist .venv (echo Rode primeiro o "1-INSTALAR.bat". & pause & exit /b 1)
call .venv\Scripts\activate.bat
python manage.py redefinir_senha
pause
