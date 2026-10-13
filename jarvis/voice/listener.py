"""Spracheingabe: hört dauerhaft aufs Mikrofon und wandelt Sprache in Text um.

Nutzt das Paket SpeechRecognition mit der kostenlosen Google-Spracherkennung
(braucht Internet). Während Jarvis selbst spricht, wird nicht zugehört,
damit er sich nicht selbst versteht.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from jarvis import ui


def list_microphones() -> list[str]:
    import speech_recognition as sr

    return sr.Microphone.list_microphone_names()


class Listener:
    def __init__(self, cfg: dict[str, Any], on_text: Callable[[str], None]):
        self.cfg = cfg
        self.on_text = on_text
        self._muted = threading.Event()      # gesetzt = Jarvis spricht gerade
        self._stop = threading.Event()
        self._unmuted_at = 0.0
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Startet das Zuhören im Hintergrund. Gibt False zurück, wenn kein Mikrofon nutzbar ist."""
        try:
            import speech_recognition as sr

            index = self.cfg.get("mikrofon_index", -1)
            mic = sr.Microphone(device_index=None if index is None or index < 0 else index)
        except Exception as e:  # noqa: BLE001
            ui.warn(f"Mikrofon nicht verfügbar ({e}). Du kannst trotzdem tippen.")
            ui.info("Tipp: 'pip install pyaudio' installieren bzw. mikrofon_index in config.toml prüfen.")
            return False
        self._thread = threading.Thread(target=self._run, args=(sr, mic), daemon=True, name="listener")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def mute(self) -> None:
        self._muted.set()

    def unmute(self) -> None:
        self._unmuted_at = time.monotonic()
        self._muted.clear()

    def _run(self, sr: Any, mic: Any) -> None:
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = float(self.cfg.get("pause_sekunden", 0.8))
        recognizer.dynamic_energy_threshold = True
        language = self.cfg.get("sprache", "de-DE")
        try:
            with mic as source:
                ui.info("Kalibriere Mikrofon auf Umgebungsgeräusche …")
                recognizer.adjust_for_ambient_noise(source, duration=1)
                ui.info("🎤 Ich höre zu.")
                while not self._stop.is_set():
                    if self._muted.is_set():
                        time.sleep(0.05)
                        continue
                    started = time.monotonic()
                    try:
                        audio = recognizer.listen(source, timeout=1, phrase_time_limit=30)
                    except sr.WaitTimeoutError:
                        continue
                    # Aufnahme verwerfen, wenn Jarvis währenddessen gesprochen hat (Echo).
                    if self._muted.is_set() or self._unmuted_at > started:
                        continue
                    try:
                        text = recognizer.recognize_google(audio, language=language)
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                        ui.warn(f"Spracherkennung nicht erreichbar: {e}")
                        time.sleep(2)
                        continue
                    if isinstance(text, str) and text.strip():
                        self.on_text(text.strip())
        except Exception as e:  # noqa: BLE001
            ui.error(f"Zuhören wurde beendet: {e}")
