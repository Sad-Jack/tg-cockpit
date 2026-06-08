"""tg-cockpit — переиспользуемый CLI-фундамент для ведения Telegram-канала.

Три слоя:
1. Этот пакет (CLI, channel-agnostic) — вся работа с Telegram без AI.
2. ``channels/<name>/`` — изолированный контекст и память по каждому каналу.
3. Claude Code + Skills — «мозг», который дёргает CLI и обновляет память канала.
"""

from __future__ import annotations

__version__ = "0.1.0"
