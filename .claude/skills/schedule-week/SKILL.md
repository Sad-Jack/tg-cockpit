---
name: schedule-week
description: Поставить готовые черновики сущности в серверную очередь по слотам.
---

# /schedule-week — постановка черновиков в серверную очередь по слотам

Серверная очередь Telegram (лимит 100 на чат). Перед любым планированием делай реконсиляцию. `NAME` — имя воркспейса сущности (канал ИЛИ группа).

## Шаги

1. Гард изучения (см. CLAUDE.md): post/schedule/poll create/group reply требуют studied: true; если false — предложи /study-entity и остановись.

2. РЕКОНСИЛЯЦИЯ (ОБЯЗАТЕЛЬНО первым делом). Запроси текущую серверную очередь:

   ```
   uv run tgcockpit scheduled list --channel NAME
   ```

   - Это источник истины: запомни, какие посты и с какими `id` уже отложены.
   - Проверь лимит: если в очереди близко к 100 — не превышай, планируй меньше.

3. Выбери черновики со `status: draft`:

   ```
   uv run tgcockpit drafts list --channel NAME
   ```

   - Бери только черновики с frontmatter `status: draft`.
   - Пропускай `scheduled` и `posted`.

4. Сопоставь черновики со слотами расписания. Слоты могут жить в `channels/NAME/config.yaml` (заданы через `--slot` при `channel init`) ИЛИ в `channels/NAME/CLAUDE.md`, секция «Расписание». При конфликте `config.yaml` главнее. Если слота нет нигде — уточни у пользователя, не выдумывай. Для каждого выбранного черновика на свой слот вызови:

   ```
   uv run tgcockpit schedule --channel NAME --draft FILE --at "ISO"
   ```

   - `FILE` — путь к файлу черновика из `channels/NAME/drafts/`.
   - `"ISO"` — время слота в формате `2026-06-10T09:00` (в таймзоне сущности из config.yaml).
   - Один черновик → один слот, не дублируй.

5. АНТИ-ДУБЛЬ. Перед `schedule` проверь frontmatter черновика: поле `scheduled_msg_id`.
   - Если у черновика уже есть живой `scheduled_msg_id` (не `null` И этот id присутствует в выводе `scheduled list` из шага 2) — пост уже стоит в очереди. Сначала сними его:

     ```
     uv run tgcockpit scheduled cancel --channel NAME --id MSGID
     ```

   - Только после отмены повторно ставь в очередь командой `schedule`.
   - Пример: `scheduled list` показывает `id=890`, а у черновика во frontmatter `scheduled_msg_id: 890` → пост уже в очереди. Сначала `scheduled cancel --channel NAME --id 890`, потом заново `schedule`.

6. Проверь запись `scheduled_msg_id`. Команда `schedule` сама пишет `scheduled_msg_id` в frontmatter черновика и переводит `status` в `scheduled`. Перезапусти

   ```
   uv run tgcockpit drafts list --channel NAME
   ```

   и проверь, что для каждого поставленного черновика:
   - `status: scheduled`,
   - `scheduled_msg_id` — целое число (не `null`),
   - `schedule_at` соответствует слоту.

## Отчёт о неделе

Отчитайся: сколько постов в очереди на неделю, по каким слотам (дата/время + заголовок/файл), что снято анти-дублем, какие черновики остались со `status: draft`, сколько мест осталось до лимита 100.

Команда встанет в очередь на кнопку — скажи пользователю, что готово и ждёт подтверждения; не утверждай, что опубликовано (см. CLAUDE.md).
