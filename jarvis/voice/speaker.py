"""Sprachausgabe.

Engines:
  "edge"    – natürliche Microsoft-Neural-Stimmen über edge-tts (braucht Internet)
  "offline" – Systemstimme über pyttsx3 (funktioniert ohne Internet, klingt robotischer)
  "aus"     – nur Text, keine Sprache
Fällt "edge" aus, wird automatisch "offline" versucht.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from jarvis import ui

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


def clean_for_speech(text: str) -> str:
    """Entfernt Markdown, Links und Emojis, damit der Text gut vorlesbar ist."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [Text](link) -> Text
    text = re.sub(r"https?://(www\.)?([^/\s]+)\S*", r"\2", text)   # URL -> Domain
    text = re.sub(r"[*_#`>|~]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, cfg: dict[str, Any]):
        self.engine = cfg.get("engine", "edge")
        self.voice = cfg.get("stimme", "de-DE-ConradNeural")
        self.rate = cfg.get("tempo", "+0%")
        # Ein fester Audio-Thread: pyttsx3/COM unter Windows mögen keine wechselnden Threads.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio")
        self._pyttsx3_engine: Any = None
        self._pygame_ready: bool | None = None
        self.on_start: Callable[[], None] = lambda: None
        self.on_end: Callable[[], None] = lambda: None

    @property
    def enabled(self) -> bool:
        return self.engine != "aus"

    async def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text or not self.enabled:
            return
        self.on_start()
        try:
            if self.engine == "edge":
                try:
                    await self._say_edge(text)
                    return
                except Exception as e:  # noqa: BLE001
                    ui.warn(f"Online-Stimme nicht verfügbar ({e}) – nutze Offline-Stimme.")
            await asyncio.get_running_loop().run_in_executor(self._executor, self._say_offline, text)
        except Exception as e:  # noqa: BLE001
            ui.warn(f"Sprachausgabe fehlgeschlagen: {e}")
        finally:
            self.on_end()

    async def abspielen(self, datei: Path) -> None:
        """Spielt eine Audiodatei (mp3/wav) ab – auf demselben Audio-Thread wie die Stimme."""
        if self.engine == "aus":
            return
        self.on_start()
        try:
            await asyncio.get_running_loop().run_in_executor(self._executor, self._play_mp3, Path(datei))
        except Exception as e:  # noqa: BLE001
            ui.warn(f"Abspielen fehlgeschlagen: {e}")
        finally:
            self.on_end()

    # --- edge-tts ---------------------------------------------------------
    async def _say_edge(self, text: str) -> None:
        import edge_tts

        fd, name = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_")
        os.close(fd)
        path = Path(name)
        try:
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(str(path))
            await asyncio.get_running_loop().run_in_executor(self._executor, self._play_mp3, path)
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def _play_mp3(self, path: Path) -> None:
        if self._pygame_ready is not False:
            try:
                import pygame

                if not self._pygame_ready:
                    pygame.mixer.init()
                    self._pygame_ready = True
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(50)
                pygame.mixer.music.unload()
                return
            except Exception:  # noqa: BLE001 – pygame fehlt oder kein Audiogerät
                self._pygame_ready = False
        _play_native(path)

    # --- pyttsx3 ----------------------------------------------------------
    def _say_offline(self, text: str) -> None:
        import pyttsx3

        if self._pyttsx3_engine is None:
            self._pyttsx3_engine = pyttsx3.init()
            for v in self._pyttsx3_engine.getProperty("voices"):
                langs = " ".join(str(x) for x in (getattr(v, "languages", []) or []))
                if "de" in (v.id + v.name + langs).lower():
                    self._pyttsx3_engine.setProperty("voice", v.id)
                    break
        self._pyttsx3_engine.say(text)
        self._pyttsx3_engine.runAndWait()


def _play_native(path: Path) -> None:
    """MP3 ohne Zusatzpakete abspielen (Fallback, falls pygame nicht geht)."""
    if sys.platform == "win32":
        import ctypes

        mci = ctypes.windll.winmm.mciSendStringW  # type: ignore[attr-defined]
        mci(f'open "{path}" type mpegvideo alias jarvis_tts', None, 0, None)
        mci("play jarvis_tts wait", None, 0, None)
        mci("close jarvis_tts", None, 0, None)
        return
    if sys.platform == "darwin":
        subprocess.run(["afplay", str(path)], check=False)
        return
    for cmd in (["mpg123", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["mpv", "--really-quiet"]):
        if shutil.which(cmd[0]):
            subprocess.run([*cmd, str(path)], check=False)
            return
    raise RuntimeError("Kein Audioplayer gefunden (pygame, mpg123, ffplay oder mpv installieren).")
