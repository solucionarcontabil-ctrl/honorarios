@echo off
chcp 65001 >nul
cd /d "%~dp0.."
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set DATA=%%i
if not exist backups mkdir backups
if exist db.sqlite3 copy /y db.sqlite3 "backups\banco-%DATA%.sqlite3" >nul
if exist media powershell -NoProfile -Command "Compress-Archive -Path media\* -DestinationPath 'backups\arquivos-%DATA%.zip' -Force"
echo Backup salvo na pasta "backups" (%DATA%).
echo Dica: copie essa pasta também para um pendrive ou para o Google Drive.
pause
