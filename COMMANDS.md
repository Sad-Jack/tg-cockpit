# COMMANDS.md — гид по CLI для агента

Это шпаргалка для тебя (агента-моста). По запросу пользователя найди тут готовую CLI-команду и выполни её — не изобретай флаги. Все команды запускаются как `uv run tgcockpit <команда>`.

**Ключевое** (полные правила — в `CLAUDE.md`, здесь только напоминания):
- `--channel NAME` — имя воркспейса (канал ИЛИ группа), папка `channels/NAME/`.
- **Тексты постов — HTML** (шпаргалка и крайние случаи — `/format-tg`).
- **Гард изучения:** `post`/`schedule`/`poll create`/`group reply` требуют `studied: true`; иначе сначала `study`.
- **Апрув:** из-под моста публикующие команды не выполняются сразу — встают в очередь на кнопку в Telegram. В прямом терминале (без моста) выполняются немедленно.

---

## Аутентификация / проверка

### auth
Когда: первичный вход в Telegram-аккаунт / переавторизация.
```
uv run tgcockpit auth
```

### whoami
Когда: проверить, под кем авторизованы.
```
uv run tgcockpit whoami
```

### version
Когда: узнать версию CLI.
```
uv run tgcockpit version
```

---

## Управление сущностями

### channel init
Когда: завести новый воркспейс под канал или группу (создаёт `channels/NAME/`).
```
uv run tgcockpit channel init --channel NAME --handle @h|id [--kind channel|group|supergroup] [--tz TZ] [--pillar P ...] [--frequency T] [--slot "mon 09:00" ...] [--overwrite]
```
Пример:
```
uv run tgcockpit channel init --channel mychan --handle @mychannel --kind channel --tz Europe/Berlin --pillar "новости" --pillar "гайды" --slot "mon 09:00" --slot "thu 18:00"
```

### study
Когда: изучить сущность (фетч + аналитика + скаффолд профиля голоса + Obsidian-хранилище постов). Снимает гард на постинг (`studied=true`).
```
uv run tgcockpit study --channel NAME [--full | --incremental]
```
Пример:
```
uv run tgcockpit study --channel mychan --full
```
Системно создаёт `channels/NAME/<@handle>/` — Obsidian-vault: каждый ТЕКСТОВЫЙ пост = отдельный `.md` + `index.md`. Только текст (медиа/опросы/аудио не сохраняются).

### vault
Когда: пересобрать Obsidian-хранилище из кэша истории (офлайн, без сети). Полезно после нового `fetch-history`.
```
uv run tgcockpit vault --channel NAME
```

### Список сущностей
Отдельной команды нет: сущности — это папки в `channels/`. Список черновиков сущности — `drafts list` (раздел «Контент»).

---

## Данные и аналитика

### fetch-history
Когда: подтянуть историю сообщений сущности (в `data/history.json`).
```
uv run tgcockpit fetch-history --channel NAME [--limit N] [--since ISO] [--full]
```
Пример:
```
uv run tgcockpit fetch-history --channel mychan --limit 500
```

### analytics
Когда: посчитать метрики по уже собранной истории. `--tier`: `auto` сам выберет (broadcast stats API, если доступен; иначе basic из истории); `basic` — только из `history.json`; `broadcast` — форсить серверный Tier-2.
```
uv run tgcockpit analytics --channel NAME [--tier auto|basic|broadcast] [--no-save]
```
Пример:
```
uv run tgcockpit analytics --channel mychan --tier auto
```

### export
Когда: выгрузить данные сущности наружу (JSON/CSV).
```
uv run tgcockpit export --channel NAME --format json|csv [--out PATH]
```
Пример:
```
uv run tgcockpit export --channel mychan --format csv --out ./mychan.csv
```

---

## Контент (черновики)

### drafts new
Когда: создать новый черновик поста. Тело — **HTML** (см. `/format-tg`). До 10 картинок через повтор `--image` (локальные пути JPEG/PNG/WebP).
```
uv run tgcockpit drafts new --channel NAME --title "T" [--pillar P] [--image PATH ... до 10]
```
Пример:
```
uv run tgcockpit drafts new --channel mychan --title "Релиз v2" --pillar "новости" --image ./img/hero.png --image ./img/diagram.png
```

### drafts list
Когда: посмотреть существующие черновики сущности (статус, время, заголовок, файл). Имя файла — аргумент для `post`/`schedule`.
```
uv run tgcockpit drafts list --channel NAME [--format auto|table|plain|json]
```
Под мостом `auto` сам выдаёт ПЛОСКИЙ список — пересказывай его в чат пунктами «N. заголовок — статус · файл: …», НЕ вставляй таблицу. `--format json` — машиночитаемо.

---

## Публикация

> Требует `studied=true`. Если нет — сначала `study`.

### post --now
Когда: опубликовать черновик немедленно. Работает в любой сущности — канал и группа одинаково (см. `CLAUDE.md`).
```
uv run tgcockpit post --channel NAME --draft FILE --now
```
Пример:
```
uv run tgcockpit post --channel mychan --draft release-v2.md --now
```

### schedule
Когда: поставить черновик в серверную очередь Telegram на конкретное время.
```
uv run tgcockpit schedule --channel NAME --draft FILE --at "2026-06-10T09:00"
```
Пример:
```
uv run tgcockpit schedule --channel mychan --draft release-v2.md --at "2026-06-10T09:00"
```

### scheduled list
Когда: посмотреть запланированные сообщения.
```
uv run tgcockpit scheduled list --channel NAME
```

### scheduled cancel
Когда: отменить запланированное сообщение по его MSGID.
```
uv run tgcockpit scheduled cancel --channel NAME --id MSGID
```
Пример:
```
uv run tgcockpit scheduled cancel --channel mychan --id 12345
```

---

## Редактирование

### message edit
Когда: отредактировать текст уже опубликованного сообщения. Текст — **HTML**.
```
uv run tgcockpit message edit --channel NAME --id MSGID --text "HTML"
```
Пример:
```
uv run tgcockpit message edit --channel mychan --id 12345 --text "<b>Обновлено:</b> новая дата релиза."
```

### message delete
Когда: удалить сообщение по MSGID.
```
uv run tgcockpit message delete --channel NAME --id MSGID
```
Пример:
```
uv run tgcockpit message delete --channel mychan --id 12345
```

---

## Опросы

### poll create
Когда: создать опрос. От 2 до 10 `--option`. Для викторины добавь `--quiz` и `--correct INDEX` (индекс правильного с 0). `--multiple` — мультивыбор. Опрос отправляется сразу (без `--at`); работает в канале и группе одинаково.
```
uv run tgcockpit poll create --channel NAME --question "Q" --option "A" --option "B" [--quiz --correct 0 --multiple]
```
Пример (обычный опрос):
```
uv run tgcockpit poll create --channel mychan --question "Когда удобнее эфир?" --option "Утро" --option "Вечер"
```
Пример (викторина):
```
uv run tgcockpit poll create --channel mychan --question "Столица Франции?" --option "Париж" --option "Лион" --quiz --correct 0
```

---

## Группы

### group read
Когда: прочитать сообщения группы.
```
uv run tgcockpit group read --channel NAME [--limit N] [--all]
```
Пример:
```
uv run tgcockpit group read --channel mygroup --limit 50
```

### group reply
Когда: ответить на конкретное сообщение в группе (по MSGID) — это ДОПОЛНение к обычному `post` (см. `CLAUDE.md`). Текст — HTML (`/format-tg`) или из черновика. Под гардом изучения.
```
uv run tgcockpit group reply --channel NAME --to MSGID (--text "HTML" | --draft FILE)
```
Пример:
```
uv run tgcockpit group reply --channel mygroup --to 678 --text "Спасибо за вопрос! Подробности <a href=\"https://...\">тут</a>."
```

### group exclude
Когда: настроить исключения (юзеры/ключевые слова/ID), чтобы их игнорировать. `--show` — показать текущие.
```
uv run tgcockpit group exclude --channel NAME [--user U] [--keyword K] [--id N] [--show]
```
Пример:
```
uv run tgcockpit group exclude --channel mygroup --user @spammer --keyword "реклама"
```

---

## Бот / аудио

### bot
Когда: запустить Telegram-бота-мост (постоянный процесс).
```
uv run tgcockpit bot
```

### transcribe
Когда: расшифровать аудиофайл в текст (Apple → Whisper).
```
uv run tgcockpit transcribe --file PATH [--lang ru]
```
Пример:
```
uv run tgcockpit transcribe --file ./voice.m4a --lang ru
```

---

## Частые сценарии

### Изучить новую сущность
1. `channel init --channel NAME --handle @h [--kind ...] [--slot ...]`
2. `study --channel NAME --full` (снимает гард на постинг).

### Запостить пост с картинкой по расписанию
1. (Если не изучена) `study --channel NAME --full`.
2. `drafts new --channel NAME --title "..." --pillar P --image ./img.png` — тело в HTML.
3. `schedule --channel NAME --draft FILE --at "2026-06-10T09:00"`.
4. Проверка: `scheduled list --channel NAME`.

### Запостить немедленно
1. `drafts new ...` (HTML, при нужде `--image`).
2. `post --channel NAME --draft FILE --now`.

### Ответить в группе
1. `group read --channel NAME --limit 50` — найти нужный MSGID.
2. (Если не изучена) `study --channel NAME --full`.
3. `group reply --channel NAME --to MSGID --text "HTML"`.

### Создать опрос / викторину
- Опрос: `poll create --channel NAME --question "Q" --option "A" --option "B"`.
- Викторина: добавь `--quiz --correct 0` (индекс правильного с 0).

### Поправить / удалить опубликованное
- Правка: `message edit --channel NAME --id MSGID --text "HTML"`.
- Удаление: `message delete --channel NAME --id MSGID`.

### Отменить запланированное
1. `scheduled list --channel NAME` — взять MSGID.
2. `scheduled cancel --channel NAME --id MSGID`.

---

**Напоминание:** тексты постов — HTML (`/format-tg`); `post`/`schedule`/`poll create`/`group reply` требуют `studied: true` и встают на кнопку-подтверждение; `message edit/delete` — без гарда. Полные правила — `CLAUDE.md`.
