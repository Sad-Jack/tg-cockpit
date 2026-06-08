"""Черновики постов: markdown-файл с YAML-frontmatter в ``channels/<name>/drafts/``.

Формат::

    ---
    status: draft            # draft | scheduled | posted
    pillar: обучение
    schedule_at: 2026-06-10T09:00
    media: []                # пути к файлам относительно корня репо
    scheduled_msg_id: null   # id в серверной очереди (заполняет schedule)
    created: 2026-06-07
    title: Заголовок
    ---
    Тело поста…

``scheduled_msg_id`` в frontmatter — ключ к реконсиляции: по нему отменяем/обновляем
отложенный пост без дублей.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .. import paths


class Draft(BaseModel):
    path: str
    title: str = ""
    status: str = "draft"
    pillar: str = ""
    schedule_at: str | None = None
    media: list[str] = Field(default_factory=list)
    scheduled_msg_id: int | None = None
    created: str = ""
    body: str = ""

    def to_text(self) -> str:
        meta = {
            "status": self.status,
            "pillar": self.pillar,
            "schedule_at": self.schedule_at,
            "media": self.media,
            "scheduled_msg_id": self.scheduled_msg_id,
            "created": self.created,
            "title": self.title,
        }
        fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n{self.body}"


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE).strip()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:50] or "post"


def parse(text: str, path: str = "") -> Draft:
    """Распарсить markdown с frontmatter в :class:`Draft`."""
    m = _FM_RE.match(text)
    if not m:
        # без frontmatter — весь текст это тело
        return Draft(path=path, body=text.strip())
    parsed = yaml.safe_load(m.group(1))
    meta = parsed if isinstance(parsed, dict) else {}  # frontmatter может быть пустым/списком/скаляром
    body = m.group(2).strip()
    return Draft(path=path, body=body, **{k: v for k, v in meta.items() if k != "body"})


def load(path: Path) -> Draft:
    return parse(path.read_text(encoding="utf-8"), path=str(path))


def resolve_in_channel(channel: str, draft_path: str) -> Path:
    """Безопасно резолвнуть путь черновика: итог ОБЯЗАН лежать в channels/<channel>/drafts/.

    Принимает разные формы пути (агент часто шлёт просто имя файла): абсолютный,
    относительный от корня репо, относительный от drafts, и голое имя файла. Любой кандидат
    обязан остаться внутри drafts канала И существовать (защита от подсовывания чужих файлов).
    """
    drafts_dir = paths.channel_drafts_dir(channel).resolve()
    raw = Path(draft_path)

    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(paths.repo_root() / raw)  # относительно корня репо (channels/.../drafts/x.md)
        candidates.append(drafts_dir / raw.name)     # голое имя файла → в drafts канала
        candidates.append(drafts_dir / raw)          # относительно самой drafts

    for c in candidates:
        rc = c.resolve()
        if rc.is_relative_to(drafts_dir) and rc.exists():
            return rc

    raise ValueError(
        f"черновик не найден в {drafts_dir}: {draft_path} "
        f"(передай имя файла или путь внутри drafts канала)"
    )


def save(draft: Draft) -> Path:
    p = Path(draft.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(draft.to_text(), encoding="utf-8")
    return p


def new_draft(
    channel: str,
    title: str,
    pillar: str = "",
    body: str = "",
    images: list[str] | None = None,
    today: str | None = None,
) -> Draft:
    """Создать новый черновик с уникальным именем ``<date>-<slug>.md``.

    ``images`` — пути к картинкам (относительно корня репо), попадают в frontmatter
    ``media`` и прикрепляются при post/schedule (один файл или альбом до 10).
    """
    today = today or date.today().isoformat()
    drafts_dir = paths.channel_drafts_dir(channel)
    drafts_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(title)
    name = f"{today}-{slug}.md"
    target = drafts_dir / name
    i = 2
    while target.exists():
        target = drafts_dir / f"{today}-{slug}-{i}.md"
        i += 1

    draft = Draft(
        path=str(target),
        title=title,
        pillar=pillar,
        created=today,
        media=list(images or []),
        body=body or f"<b>{title}</b>\n\n(черновик)",
    )
    save(draft)
    return draft


def list_drafts(channel: str) -> list[Draft]:
    drafts_dir = paths.channel_drafts_dir(channel)
    if not drafts_dir.exists():
        return []
    out = [load(p) for p in sorted(drafts_dir.glob("*.md"))]
    return out
