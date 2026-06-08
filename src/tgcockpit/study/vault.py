"""Obsidian-хранилище постов сущности — строится на уровне СИСТЕМЫ (не агента).

При изучении внутри ``channels/<name>/`` создаётся папка с именем = ``@handle`` сущности —
полноценный Obsidian-vault (есть ``.obsidian/``, вики-ссылки, frontmatter). Каждый
ТЕКСТОВЫЙ пост = отдельный файл ``posts/<id>.md`` (имя по id — стабильное). Плюс
``index.md`` (MOC) для навигации.

Синхронизация инкрементальная: новые посты добавляются, изменённые (по тексту/дате)
перезаписываются, удалённые из канала — удаляются из хранилища, неизменённые не трогаются.

Правило: берём ТОЛЬКО текст. Картинки/видео/вложения/опросы/аудио не обрабатываем —
посты без текста в хранилище не попадают (а если такой пост раньше был текстовым и стал
без текста — он будет удалён из хранилища при синхронизации).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

from .. import paths
from ..config import ChannelConfig
from ..storage import jsonstore, workspace
from ..util.logging import get_logger

log = get_logger("vault")


def _safe_handle(handle: str) -> str:
    """Имя папки из @handle: оставляем @/буквы/цифры/._-; числовой id → id<num>."""
    h = handle.strip()
    if h.lstrip("-").isdigit():
        return f"id{h.lstrip('-')}"
    return re.sub(r"[^\w@.\-]", "_", h, flags=re.UNICODE) or "entity"


def vault_dir(channel: str) -> Path:
    cfg = ChannelConfig.load(channel)
    return paths.channel_dir(channel) / _safe_handle(cfg.handle)


def _title(text: str) -> str:
    return (text.strip().splitlines() or ["(без текста)"])[0][:80]


def _post_content(post: dict[str, Any]) -> str:
    """Содержимое файла поста: стабильное (id, дата, текст), БЕЗ дрейфующих метрик.

    Метрики (просмотры/реакции) меняются постоянно — если класть их в файл, каждый
    рефетч помечал бы все посты как «изменённые». Поэтому метрики идут только в index.md
    (он пересобирается целиком), а файлы постов стабильны → чистое определение правок.
    """
    pid = int(post.get("id", 0))
    dt = post.get("date") or ""
    text = (post.get("text") or "").strip()
    front = f"---\nid: {pid}\ndate: {dt}\n---"
    return f"{front}\n\n{text}\n"


def _ensure_obsidian(vdir: Path) -> None:
    """Сделать папку распознаваемым Obsidian-vault (создать .obsidian, если его нет)."""
    odir = vdir / ".obsidian"
    if odir.exists():
        return  # не трогаем существующий конфиг Obsidian
    odir.mkdir(parents=True, exist_ok=True)
    # минимальные файлы — остальное Obsidian допишет при открытии
    jsonstore.write_json(odir / "app.json", {})
    jsonstore.write_json(odir / "core-plugins.json", ["file-explorer", "search", "backlink", "tag-pane"])


def _write_index(vdir: Path, cfg: ChannelConfig, posts: list[dict[str, Any]]) -> None:
    """Пересобрать index.md (MOC): вики-ссылки + текущие метрики, новые сверху."""
    rows = sorted(posts, key=lambda p: int(p.get("id", 0)), reverse=True)
    lines = [
        f"# {cfg.handle} — хранилище постов",
        "",
        f"Текстовых постов: **{len(posts)}**. Обновлено: {date.today().isoformat()}.",
        "Только текст (медиа/опросы/аудио не сохраняются). Файлы постов — `posts/<id>.md`.",
        "",
        "## Все посты (новые сверху)",
    ]
    for p in rows:
        pid = int(p.get("id", 0))
        title = _title(p.get("text") or "")
        meta = f"{(p.get('date') or '')[:10]} · 👁{p.get('views', 0)} ❤{p.get('reactions', 0)}"
        lines.append(f"- [[posts/{pid}|{title}]] — {meta}")
    lines += ["", "## Темы", "_Сгруппируй посты по темам здесь (заполняет агент при изучении)._", ""]
    (vdir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _hash(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def build_vault(channel: str) -> dict[str, Any]:
    """Синхронизировать Obsidian-хранилище с кэшем истории (инкрементально, только текст).

    Масштабируемо на десятки тысяч постов: вместо чтения каждого ``.md`` сравниваем
    хэши через манифест ``posts/.sync.json`` (в памяти). На диск пишем только новые и
    изменённые посты; неизменённые не трогаем вообще. Удалённые — по разнице с манифестом.
    """
    cfg = ChannelConfig.load(channel)
    raw = jsonstore.read_json(workspace.history_file(channel), default={}) or {}
    all_posts = list(raw.get("posts", {}).values())
    posts = [p for p in all_posts if (p.get("text") or "").strip()]  # только текст

    vdir = paths.channel_dir(channel) / _safe_handle(cfg.handle)
    posts_dir = vdir / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    _ensure_obsidian(vdir)

    manifest_path = posts_dir / ".sync.json"
    manifest: dict[str, str] = jsonstore.read_json(manifest_path, default={}) or {}
    new_manifest: dict[str, str] = {}

    added = updated = unchanged = 0
    for p in posts:
        pid = str(int(p.get("id", 0)))
        content = _post_content(p)
        h = _hash(content)
        new_manifest[pid] = h
        path = posts_dir / f"{pid}.md"
        if manifest.get(pid) == h and path.exists():
            unchanged += 1  # не читаем и не пишем файл
            continue
        path.write_text(content, encoding="utf-8")
        if pid in manifest:
            updated += 1  # текст/дата изменились
        else:
            added += 1  # новый

    # удалённые из канала (или ставшие без текста) → убрать файл; плюс подчистка по факту
    deleted = 0
    desired = {f"{pid}.md" for pid in new_manifest}
    for gone in set(manifest) - set(new_manifest):
        f = posts_dir / f"{gone}.md"
        if f.exists():
            f.unlink()
            deleted += 1
    # подстраховка от старых файлов вне манифеста (напр. слаг-имена прошлых версий)
    for f in posts_dir.glob("*.md"):
        if f.name not in desired:
            f.unlink()
            deleted += 1

    jsonstore.write_json(manifest_path, new_manifest)
    _write_index(vdir, cfg, posts)

    log.info(
        "vault '%s': +%s ~%s -%s =%s (всего %s)", channel, added, updated, deleted, unchanged, len(posts)
    )
    return {
        "vault": str(vdir),
        "posts_written": len(posts),
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
        "skipped_non_text": len(all_posts) - len(posts),
    }
