#!/bin/sh
# Backup completo agora e depois a cada 24h: banco (inclui XMLs) + arquivos do portal.
# Mantém os últimos 30 dias.
while true; do
  data="$(date +%Y-%m-%d_%H%M)"
  arquivo="/backups/honorarios-$data.sql.gz"
  if pg_dump -h db -U honorarios honorarios | gzip > "$arquivo.tmp"; then
    mv "$arquivo.tmp" "$arquivo"
    echo "Backup do banco OK: $arquivo"
  else
    rm -f "$arquivo.tmp"
    echo "ERRO no backup do banco $(date)"
  fi
  if [ -d /media ] && [ -n "$(ls -A /media 2>/dev/null)" ]; then
    if tar -czf "/backups/arquivos-$data.tar.gz.tmp" -C /media .; then
      mv "/backups/arquivos-$data.tar.gz.tmp" "/backups/arquivos-$data.tar.gz"
      echo "Backup dos arquivos OK"
    else
      rm -f "/backups/arquivos-$data.tar.gz.tmp"
      echo "ERRO no backup dos arquivos $(date)"
    fi
  fi
  find /backups \( -name 'honorarios-*.sql.gz' -o -name 'arquivos-*.tar.gz' \) -mtime +30 -delete
  sleep 86400
done
