#!/usr/bin/env bash
set -e

# 1) instala electron si no está instalado
if [ ! -d "electron-gd/node_modules" ]; then
  echo "Instalando dependencias Electron..."
  (cd electron-gd && npm install)
fi

# 2) levanta backend (ajusta el comando si tu uvicorn es distinto)
echo "Levantando backend..."
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000 &
BACK_PID=$!

# 3) levanta electron
echo "Levantando Electron..."
(cd electron-gd && npm start) || true

# 4) si cierras electron, apaga backend
echo "Cerrando backend..."
kill $BACK_PID || true
