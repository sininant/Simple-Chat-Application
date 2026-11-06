"""Client-side Speech-to-Text adapters for the chat composer (SP1)."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import Protocol


class SpeechTranscriptionError(Exception):
    """A recoverable microphone or speech-recognition failure."""


class SpeechTranscriptionPaused(Exception):
    """The user paused capture before a transcript was available."""


class SpeechTranscriber(Protocol):
    """Headless seam that turns one microphone utterance into chat text."""

    def transcribe(self, paused: Event) -> str: ...


class GoogleSpeechTranscriber:
    """Production adapter using SpeechRecognition's Google Web Speech API backend."""

    def transcribe(self, paused: Event) -> str:
        try:
            import speech_recognition as sr
        except ImportError as error:  # pragma: no cover - depends on local installation
            raise SpeechTranscriptionError(
                "Speech recognition is not installed. Please install the application dependencies."
            ) from error

        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                while not paused.is_set():
                    try:
                        audio = recognizer.listen(source, timeout=1, phrase_time_limit=30)
                    except sr.WaitTimeoutError:
                        continue
                    break
                else:
                    raise SpeechTranscriptionPaused
            if paused.is_set():
                raise SpeechTranscriptionPaused
            return recognizer.recognize_google(audio).strip()
        except sr.WaitTimeoutError as error:
            raise SpeechTranscriptionError("No speech was detected. Please try again.") from error
        except sr.UnknownValueError as error:
            raise SpeechTranscriptionError(
                "Sorry, I could not understand that. Please try again."
            ) from error
        except sr.RequestError as error:
            raise SpeechTranscriptionError(
                "Speech recognition is unavailable. Check your internet connection and try again."
            ) from error
        except OSError as error:
            raise SpeechTranscriptionError(
                "Microphone is unavailable. Please check it and try again."
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

    def pause(self) -> bool:
        """Request capture cancellation; the worker releases the microphone safely."""
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
