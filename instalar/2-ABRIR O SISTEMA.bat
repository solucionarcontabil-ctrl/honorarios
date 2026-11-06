@echo off
chcp 65001 >nul
title Sistema do escritório (feche esta janela para desligar)
cd /d "%~dp0.."
if not exist .venv (echo Rode primeiro o "1-INSTALAR.bat". & pause & exit /b 1)
call .venv\Scripts\activate.bat
python manage.py migrate >nul
echo Sistema rodando em http://127.0.0.1:8000
echo Não feche esta janela enquanto estiver usando. Para desligar, feche-a.
start "" http://127.0.0.1:8000/
python manage.py runserver 127.0.0.1:8000
