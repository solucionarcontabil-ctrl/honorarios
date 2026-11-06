#!/bin/sh
# Faz um backup completo agora e depois a cada 24h. Mantém os últimos 30 dias.
while true; do
  arquivo="/backups/honorarios-$(date +%Y-%m-%d_%H%M).sql.gz"
  if pg_dump -h db -U honorarios honorarios | gzip > "$arquivo.tmp"; then
    mv "$arquivo.tmp" "$arquivo"
    echo "Backup OK: $arquivo"
  else
    rm -f "$arquivo.tmp"
    echo "ERRO no backup $(date)"
  fi
  find /backups -name 'honorarios-*.sql.gz' -mtime +30 -delete
  sleep 86400
done
