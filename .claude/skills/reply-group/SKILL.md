---
name: reply-group
description: Прочитать сообщения группы и ответить на нужное (с учётом исключений).
---

# reply-group

Чтение сообщений группы NAME и ответ на выбранное сообщение в голосе сущности.

Гард изучения (см. CLAUDE.md): post/schedule/poll create/group reply требуют studied: true; если false — предложи /study-entity и остановись.

## Шаги

0. **Early-return по типу.** Открой `channels/NAME/config.yaml`. Если `kind` ≠ `group` и ≠ `supergroup` — сразу остановись: скилл только для групп, у каналов нет групповых сообщений для ответа.

1. **Проверь гард изучения** (`studied: true` в `config.yaml`) — см. блок гарда выше.

2. **Прочитай сообщения.** Запусти:
   ```
   uv run tgcockpit group read --channel NAME --limit N
   ```
   Исключения (`config.exclusions`: пользователи, ключевые слова, id) уже отфильтрованы внутри `group read` — отдельно вычищать не надо. Редкий случай: если просочившийся автор/тема всё же мелькнул в выдаче — добавь правило и перечитай:
   ```
   uv run tgcockpit group exclude --channel NAME --user U
   uv run tgcockpit group exclude --channel NAME --keyword K
   uv run tgcockpit group exclude --channel NAME --id N
   ```
   Текущие правила можно посмотреть: `group exclude --channel NAME --show`.

3. **Выбери сообщение.** Из прочитанного выбирай прямой вопрос, просьбу о помощи или наблюдение, требующее контекста; пропускай уже отвеченные. Один вызов = один `MSGID` — запомни его.

4. **Сформулируй ответ.** Если voice.md пустой/скаффолд — предложи /study-entity или скажи, что стиль ещё не выведен (см. CLAUDE.md). Иначе напиши ответ в голосе сущности по `channels/NAME/skills/voice.md`. Формат текста — по /format-tg (посты — HTML руками; в чат можно markdown). Целевая длина 200–600 знаков, короче полного поста. Не затрагивай темы/пользователей из exclusions.

5. **Отправь ответ.** Команда встанет в очередь на кнопку — скажи пользователю, что готово и ждёт подтверждения; не утверждай, что опубликовано (см. CLAUDE.md). Текстом напрямую:
   ```
   uv run tgcockpit group reply --channel NAME --to MSGID --text "HTML"
   ```
   Либо из подготовленного черновика:
   ```
   uv run tgcockpit group reply --channel NAME --to MSGID --draft FILE
   ```
