#!/bin/sh
# Uso: sh scripts/restaurar_arquivos.sh backups/arquivos-AAAA-MM-DD_HHMM.tar.gz
# Restaura os arquivos do portal (os existentes com o mesmo nome são substituídos).
set -e
[ -f "$1" ] || { echo "Informe o arquivo de backup."; exit 1; }
nome="$(basename "$1")"
docker compose run --rm --no-deps --user root -v "$(cd "$(dirname "$1")" && pwd):/b:ro" web \
  sh -c "tar -xzf /b/$nome -C /app/media && chown -R app /app/media"
echo "Arquivos restaurados."
