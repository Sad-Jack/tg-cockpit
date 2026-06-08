#!/bin/bash
# Двойной клик в Finder → разовый вход в Telegram (если нужно) + запуск бота-моста.
# (Файл .command macOS открывает в Терминале автоматически — команды вводить не нужно.)

set -uo pipefail

# Папка проекта = папка, где лежит этот скрипт (работает при двойном клике из любого места)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

pause() { echo; read -r -n 1 -p "Нажми любую клавишу, чтобы закрыть окно..."; echo; }

echo "================================================"
echo "  tg-cockpit — запуск бота-моста к Claude"
echo "  Папка: $DIR"
echo "================================================"
echo

# --- uv в PATH (на случай запуска из Finder с урезанным PATH) ---
if ! command -v uv >/dev/null 2>&1; then
  [ -x "$HOME/.local/bin/uv" ] && export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "❌ uv не найден. Установи: https://docs.astral.sh/uv/"
  pause; exit 1
fi

# --- секреты ---
if [ ! -f "secrets/.env" ]; then
  echo "❌ Нет secrets/.env."
  echo "   Скопируй шаблон и заполни:  cp .env.example secrets/.env"
  echo "   Нужны: TGCOCKPIT_API_ID / API_HASH / BOT_TOKEN (+ ANTHROPIC_API_KEY для моста)."
  pause; exit 1
fi

# --- зависимости моста (aiogram + claude-agent-sdk); быстро, если уже стоят ---
echo "→ Проверяю зависимости (uv sync --extra bot)…"
if ! uv sync --extra bot --quiet; then
  echo "❌ Не удалось установить зависимости (uv sync --extra bot)."
  pause; exit 1
fi
# дальше ВЕЗДЕ uv run --extra bot, иначе uv сбрасывает extra (пропадёт aiogram)

# --- вход в Telegram (разово): user-сессия нужна, чтобы видеть каналы и постить ---
echo "→ Проверяю вход в Telegram…"
if ! uv run --extra bot tgcockpit whoami >/dev/null 2>&1; then
  echo "  user-сессия не авторизована — нужен РАЗОВЫЙ вход."
  echo "  Сейчас спросит: номер телефона (с кодом страны, напр. +7…), затем код из Telegram и пароль 2FA (если включён)."
  echo "------------------------------------------------"
  if ! uv run --extra bot tgcockpit auth; then
    echo "------------------------------------------------"
    echo "❌ Вход не выполнен. Запусти скрипт снова."
    pause; exit 1
  fi
  echo "------------------------------------------------"
fi

echo
echo "→ Запускаю бота. Останов — Ctrl+C."
echo "------------------------------------------------"
uv run --extra bot tgcockpit bot
code=$?

echo "------------------------------------------------"
if [ $code -ne 0 ]; then
  echo "⚠️  Бот завершился с кодом $code (см. сообщения выше / logs/tgcockpit.log)."
else
  echo "Бот остановлен."
fi
pause
