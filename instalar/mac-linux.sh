#!/bin/sh
# Mac / Linux:  sh instalar/mac-linux.sh        (instala na 1ª vez e abre o sistema)
cd "$(dirname "$0")/.." || exit 1
PY=$(command -v python3 || command -v python) || { echo "Instale o Python 3.12+ em python.org"; exit 1; }
if [ ! -d .venv ]; then
  "$PY" -m venv .venv && . .venv/bin/activate && pip install -q --upgrade pip && pip install -q -r requirements.txt \
    && python manage.py migrate && python manage.py criar_usuario || exit 1
else
  . .venv/bin/activate && python manage.py migrate >/dev/null
fi
echo "Sistema em http://127.0.0.1:8000  (Ctrl+C para desligar)"
(sleep 2; command -v open >/dev/null && open http://127.0.0.1:8000/ || xdg-open http://127.0.0.1:8000/ 2>/dev/null) &
python manage.py runserver 127.0.0.1:8000
