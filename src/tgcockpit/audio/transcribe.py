"""Транскрибация аудио: цепочка бэкендов Telegram → Apple → Whisper.

Порядок (первый успешный побеждает):
1. **Telegram (Premium)** — ``messages.transcribeAudio`` через user-сессию: заливаем аудио
   как голосовое в «Избранное», транскрибируем, удаляем. Серверно, точно, без локальных
   моделей. Premium — без лимитов; обычный аккаунт — несколько пробных раз.
2. **Apple Shortcuts** — шорткат с action «Transcribe Audio» (on-device, оффлайн).
   Имя шортката — env ``TGCOCKPIT_TRANSCRIBE_SHORTCUT`` (по умолч. ``TgCockpitTranscribe``).
3. **Apple Swift** — SFSpeechRecognizer on-device (хрупко: разрешения/ассеты), best-effort.
4. **faster-whisper** — локальный фолбэк (CPU int8, читает .ogg напрямую через PyAV).

Telegram-голосовые приходят в .ogg/opus. Telegram-бэкенд и whisper читают .ogg сам;
Apple-бэкенды требуют .wav (конверт ffmpeg).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..util.logging import get_logger

log = get_logger("transcribe")

DEFAULT_SHORTCUT = os.environ.get("TGCOCKPIT_TRANSCRIBE_SHORTCUT", "TgCockpitTranscribe")


class TranscriptionError(RuntimeError):
    """Ни один бэкенд не смог транскрибировать."""


# --- утилиты ----------------------------------------------------------------


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _to_wav(src: Path) -> Path:
    """Сконвертировать аудио в wav 16kHz mono через ffmpeg (нужно Apple-бэкендам)."""
    if not _have("ffmpeg"):
        raise TranscriptionError("нет ffmpeg — не могу подготовить wav для Apple-бэкенда")
    _fd, _p = tempfile.mkstemp(suffix=".wav")
    os.close(_fd)
    out = Path(_p)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(out)],
        check=True,
        capture_output=True,
    )
    return out


def _shortcut_exists(name: str) -> bool:
    if not _have("shortcuts"):
        return False
    try:
        res = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return False
    return name in res.stdout.splitlines()


# --- бэкенды ----------------------------------------------------------------


def _apple_shortcut(path: Path, lang: str, shortcut: str = DEFAULT_SHORTCUT) -> str:
    """Транскрибация через Apple Shortcuts (action «Transcribe Audio»)."""
    if not _shortcut_exists(shortcut):
        raise TranscriptionError(f"нет шортката '{shortcut}' (создай его в приложении Shortcuts)")
    wav = _to_wav(path)
    try:
        res = subprocess.run(
            ["shortcuts", "run", shortcut, "-i", str(wav), "-o", "-"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if res.returncode != 0:
            raise TranscriptionError(f"shortcuts run упал: {res.stderr.strip()}")
        text = res.stdout.strip()
        if not text:
            raise TranscriptionError("шорткат вернул пустой результат")
        return text
    finally:
        wav.unlink(missing_ok=True)


_SWIFT_SRC = r"""
import Foundation
import Speech
guard CommandLine.arguments.count > 2 else { exit(1) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let lang = CommandLine.arguments[2]
let sema = DispatchSemaphore(value: 0)
var code: Int32 = 0
SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized,
          let rec = SFSpeechRecognizer(locale: Locale(identifier: lang)), rec.isAvailable else {
        code = 2; sema.signal(); return
    }
    let req = SFSpeechURLRecognitionRequest(url: url)
    req.requiresOnDeviceRecognition = true
    req.shouldReportPartialResults = false
    rec.recognitionTask(with: req) { result, error in
        if error != nil { code = 3; sema.signal(); return }
        if let result = result, result.isFinal {
            print(result.bestTranscription.formattedString); sema.signal()
        }
    }
}
sema.wait()
exit(code)
"""


def _apple_swift(path: Path, lang: str) -> str:
    """Транскрибация через Swift + Speech framework (on-device). Хрупко (разрешения)."""
    if not _have("swift"):
        raise TranscriptionError("нет swift — пропускаю Apple Swift бэкенд")
    wav = _to_wav(path)
    _fd, _sp = tempfile.mkstemp(suffix=".swift")
    os.close(_fd)
    script = Path(_sp)
    script.write_text(_SWIFT_SRC, encoding="utf-8")
    locale = "ru-RU" if lang == "ru" else lang
    try:
        res = subprocess.run(
            ["swift", str(script), str(wav), locale],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if res.returncode != 0:
            raise TranscriptionError(f"swift STT упал (rc={res.returncode}): {res.stderr.strip()[:200]}")
        text = res.stdout.strip()
        if not text:
            raise TranscriptionError("swift STT вернул пусто")
        return text
    finally:
        wav.unlink(missing_ok=True)
        script.unlink(missing_ok=True)


def _telegram_premium(path: Path, lang: str) -> str:
    """Транскрибация через Telegram (messages.transcribeAudio) — серверная, точная.

    Грузим аудио как голосовое в «Избранное» своей user-сессии, транскрибируем по msg_id,
    затем удаляем временное сообщение. Требует авторизованную сессию; для Telegram Premium
    — без лимитов, для обычного аккаунта — несколько пробных раз (trial_remains_num).
    """
    return asyncio.run(_telegram_transcribe(Path(path)))


async def _telegram_transcribe(path: Path) -> str:
    from telethon.tl.functions.messages import TranscribeAudioRequest

    from ..telegram.client import connected
    from ..util.ratelimit import with_floodwait

    async with connected() as client:
        input_me = await client.get_input_entity("me")
        msg = await client.send_file("me", file=str(path), voice_note=True)
        try:
            text = ""
            for _ in range(12):  # опрос статуса (pending) ~12с
                res = await with_floodwait(
                    client, TranscribeAudioRequest(peer=input_me, msg_id=msg.id)
                )
                text = res.text or ""
                if not getattr(res, "pending", False):
                    break
                await asyncio.sleep(1)
            if not text.strip():
                raise TranscriptionError("Telegram вернул пустой транскрипт (ещё в обработке?)")
            return text.strip()
        finally:
            try:
                await client.delete_messages("me", [msg.id])
            except Exception:  # noqa: BLE001 — не критично, чистим по возможности
                pass


def _whisper(path: Path, lang: str, model_size: str = "small") -> str:
    """Фолбэк: faster-whisper (локально, CPU int8). Читает .ogg напрямую."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscriptionError(
            "faster-whisper не установлен. Поставь: uv sync --extra audio"
        ) from e
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(path), language=lang, beam_size=5)
    return "".join(seg.text for seg in segments).strip()


# --- публичный API ----------------------------------------------------------

# порядок бэкендов; можно переопределить аргументом.
# telegram (Premium) — первым: серверная транскрибация, точная, без локальных моделей.
_BACKENDS = {
    "telegram": _telegram_premium,
    "shortcut": _apple_shortcut,
    "swift": _apple_swift,
    "whisper": _whisper,
}
DEFAULT_ORDER = ("telegram", "shortcut", "swift", "whisper")


def transcribe(path: str | Path, lang: str = "ru", order: tuple[str, ...] = DEFAULT_ORDER) -> str:
    """Транскрибировать аудиофайл, перебирая бэкенды по порядку. Возвращает текст.

    Бросает :class:`TranscriptionError`, если ни один бэкенд не сработал.
    """
    p = Path(path)
    if not p.exists():
        raise TranscriptionError(f"файл не найден: {p}")

    errors: list[str] = []
    for name in order:
        backend = _BACKENDS.get(name)
        if backend is None:
            continue
        try:
            text = backend(p, lang)
            log.info("transcribe: бэкенд '%s' ок (%s симв.)", name, len(text))
            return text
        except TranscriptionError as e:
            log.info("transcribe: бэкенд '%s' пропущен: %s", name, e)
            errors.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001 — любой бэкенд может упасть неожиданно
            log.warning("transcribe: бэкенд '%s' ошибка: %s", name, e)
            errors.append(f"{name}: {e}")

    raise TranscriptionError("все бэкенды не сработали:\n  " + "\n  ".join(errors))
