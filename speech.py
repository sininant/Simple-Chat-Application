"""Client-side Speech-to-Text adapters for the chat composer (SP1)."""

from __future__ import annotations

from collections.abc import Callable
from threading import Thread
from typing import Protocol


class SpeechTranscriptionError(Exception):
    """A recoverable microphone or speech-recognition failure."""


class SpeechTranscriber(Protocol):
    """Headless seam that turns one microphone utterance into chat text."""

    def transcribe(self) -> str: ...


class GoogleSpeechTranscriber:
    """Production adapter using SpeechRecognition's Google Web Speech API backend."""

    def transcribe(self) -> str:
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
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=30)
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

    def transcribe(self) -> str:
        if self.error:
            raise SpeechTranscriptionError(self.error)
        return self.transcript


class SpeechInputController:
    """Runs a transcriber off the GUI thread and reports a single outcome."""

    def __init__(self, transcriber: SpeechTranscriber) -> None:
        self.transcriber = transcriber

    def transcribe_async(
        self,
        *,
        on_listening: Callable[[], None],
        on_transcript: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        on_listening()

        def run() -> None:
            try:
                transcript = self.transcriber.transcribe()
            except SpeechTranscriptionError as error:
                on_error(str(error))
            except Exception:  # pragma: no cover - defensive boundary for hardware drivers
                on_error("Microphone transcription failed. Please try again.")
            else:
                if transcript:
                    on_transcript(transcript)
                else:
                    on_error("No speech was detected. Please try again.")

        Thread(target=run, name="speech-transcription", daemon=True).start()
