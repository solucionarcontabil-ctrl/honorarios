@echo off
chcp 65001 >nul
title Instalar o sistema do escritório
cd /d "%~dp0.."
echo ============================================================
echo  INSTALAÇÃO DO SISTEMA (só precisa fazer uma vez)
echo ============================================================
echo.
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo Python não encontrado.
  echo Instale o Python 3.12 ou 3.13 em https://www.python.org/downloads/
  echo IMPORTANTE: na primeira tela da instalação marque "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist .venv (
  echo Criando o ambiente do sistema...
  %PY% -m venv .venv || (echo Falhou ao criar o ambiente. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo Instalando os componentes (pode levar alguns minutos)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo Falhou a instalação dos componentes. Verifique a internet. & pause & exit /b 1)
echo Preparando o banco de dados...
python manage.py migrate || (echo Falhou ao preparar o banco. & pause & exit /b 1)
echo.
echo ============================================================
echo  Agora vamos criar os usuários.
echo  Use o NOME COMPLETO igual ao campo "Contador" do Domínio,
echo  assim cada cliente já fica com o responsável certo.
echo ============================================================
python manage.py criar_usuario
echo.
set /p OUTRO=Criar outro usuário agora (ex.: seu pai)? (s/n):
if /i "%OUTRO%"=="s" python manage.py criar_usuario
echo.
echo Instalação concluída! Para usar, dê dois cliques em "2-ABRIR O SISTEMA.bat".
pause
