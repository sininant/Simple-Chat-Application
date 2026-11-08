"""Client-side Speech-to-Text adapters for the chat composer (SP1)."""

from __future__ import annotations

import os
from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import Protocol


class SpeechTranscriptionError(Exception):
    """A recoverable microphone or speech-recognition failure."""


class SpeechTranscriptionPaused(Exception):
    """The user stopped capture before saying a phrase."""


class SpeechTranscriber(Protocol):
    """Headless seam that turns one microphone utterance into chat text."""

    def transcribe(self, paused: Event) -> str: ...


class GoogleSpeechTranscriber:
    """Production adapter using SpeechRecognition's Google Web Speech API backend."""

    def __init__(
        self,
        language: str | None = None,
        listen_timeout: float = 5,
        phrase_time_limit: float = 60,
    ) -> None:
        """Configure recognition without baking a language into the application.

        ``CHAT_SPEECH_LANGUAGE`` accepts a BCP-47 locale such as ``th-TH``,
        ``en-US``, or ``en-GB``. Thai remains the default for this application.
        """
        self.language = language or os.getenv("CHAT_SPEECH_LANGUAGE", "th-TH")
        self.listen_timeout = listen_timeout
        self.phrase_time_limit = phrase_time_limit

    @staticmethod
    def _configure_recognizer(recognizer: object) -> None:
        """Adapt microphone sensitivity to the current room noise."""
        recognizer.dynamic_energy_threshold = True

    def transcribe(self, paused: Event) -> str:
        try:
            import speech_recognition as sr
        except ImportError as error:  # pragma: no cover - depends on local installation
            raise SpeechTranscriptionError(
                "Speech recognition is not installed. Please install the application dependencies."
            ) from error

        recognizer = sr.Recognizer()
        self._configure_recognizer(recognizer)
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.1)
                audio = recognizer.listen(
                    source,
                    timeout=self.listen_timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
            return recognizer.recognize_google(audio, language=self.language).strip()
        except sr.WaitTimeoutError as error:
            raise SpeechTranscriptionError("No speech was detected. Please try again.") from error
        except sr.UnknownValueError as error:
            raise SpeechTranscriptionError(
                "Sorry, I could not understand that.\n Please try again."
            ) from error
        except sr.RequestError as error:
            raise SpeechTranscriptionError(
                "Speech recognition is unavailable. Check your internet connection and try again."
            ) from error
        except OSError as error:
            raise SpeechTranscriptionError(
                "Microphone is unavailable.\n Please check it and try again."
            ) from error


class FakeSpeechTranscriber:
    """Deterministic test adapter that never opens a microphone."""

    def __init__(self, transcript: str = "", error: str | None = None) -> None:
        self.transcript = transcript
        self.error = error

    def transcribe(self, paused: Event) -> str:
        if paused.is_set():
            raise SpeechTranscriptionPaused
        if self.error:
            raise SpeechTranscriptionError(self.error)
        return self.transcript


class SpeechInputController:
    """Runs a transcriber off the GUI thread and reports a single outcome."""

    def __init__(self, transcriber: SpeechTranscriber) -> None:
        self.transcriber = transcriber
        self._lock = Lock()
        self._paused: Event | None = None

    @property
    def is_listening(self) -> bool:
        """Check whether the controller is actively capturing audio."""
        with self._lock:
            return self._paused is not None

    def pause(self) -> bool:
        """Stop waiting for a new phrase while preserving one already being captured."""
        with self._lock:
            if self._paused is None:
                return False
            self._paused.set()
            return True

    def transcribe_async(
        self,
        *,
        on_listening: Callable[[], None],
        on_transcript: Callable[[str], None],
        on_error: Callable[[str], None],
        on_paused: Callable[[], None],
    ) -> bool:
        paused = Event()
        with self._lock:
            if self._paused is not None:
                return False
            self._paused = paused
        on_listening()

        def run() -> None:
            try:
                transcript = self.transcriber.transcribe(paused)
            except SpeechTranscriptionPaused:
                on_paused()
            except SpeechTranscriptionError as error:
                on_error(str(error))
            except Exception:  # pragma: no cover - defensive boundary for hardware drivers
                on_error("Microphone transcription failed. Please try again.")
            else:
                if transcript:
                    on_transcript(transcript)
                else:
                    on_error("No speech was detected. Please try again.")
            finally:
                with self._lock:
                    if self._paused is paused:
                        self._paused = None

        Thread(target=run, name="speech-transcription", daemon=True).start()
        return True
