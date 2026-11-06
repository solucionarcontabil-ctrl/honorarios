#!/bin/sh
# Uso: sh scripts/restaurar.sh backups/honorarios-AAAA-MM-DD_HHMM.sql.gz
# ATENÇÃO: substitui todos os dados atuais pelos do backup.
set -e
[ -f "$1" ] || { echo "Informe o arquivo de backup."; exit 1; }
printf "Isto APAGA os dados atuais e restaura %s. Digite SIM para continuar: " "$1"
read resp; [ "$resp" = "SIM" ] || exit 1
docker compose stop web lembretes
docker compose exec -T db psql -U honorarios -d postgres -c "DROP DATABASE honorarios WITH (FORCE);" -c "CREATE DATABASE honorarios OWNER honorarios;"
gunzip -c "$1" | docker compose exec -T db psql -U honorarios -d honorarios -q
docker compose start web lembretes
echo "Restaurado."
