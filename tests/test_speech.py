"""Headless behavior tests for the client-side speech transcription seam."""

from __future__ import annotations

from queue import Queue
from threading import Event

from gui import FONT, PINK, ChatBoxBar
from speech import FakeSpeechTranscriber, GoogleSpeechTranscriber, SpeechInputController


def test_speech_input_controller_delivers_fake_transcript_without_microphone():
    """A completed transcription is made available to the chat composer, not sent."""
    completed = Event()
    received: list[str] = []
    controller = SpeechInputController(FakeSpeechTranscriber("Hello from the microphone"))

    controller.transcribe_async(
        on_listening=lambda: None,
        on_transcript=lambda text: (received.append(text), completed.set()),
        on_error=lambda message: (_ for _ in ()).throw(AssertionError(message)),
        on_paused=lambda: (_ for _ in ()).throw(AssertionError("capture was paused")),
    )

    assert completed.wait(timeout=1)
    assert received == ["Hello from the microphone"]


def test_google_transcriber_adapts_to_room_noise():
    class RecognizerSpy:
        dynamic_energy_threshold = False

    recognizer = RecognizerSpy()

    GoogleSpeechTranscriber._configure_recognizer(recognizer)

    assert recognizer.dynamic_energy_threshold is True


def test_google_transcriber_combines_all_audio_captured_before_pause():
    class Chunk:
        def __init__(self, frame_data: bytes) -> None:
            self.frame_data = frame_data
            self.sample_rate = 16_000
            self.sample_width = 2

    class SpeechRecognitionSpy:
        @staticmethod
        def AudioData(frame_data: bytes, sample_rate: int, sample_width: int):
            return frame_data, sample_rate, sample_width

    audio = GoogleSpeechTranscriber._combine_audio_chunks(
        SpeechRecognitionSpy(), [Chunk(b"first"), Chunk(b"second")]
    )

    assert audio == (b"firstsecond", 16_000, 2)


def test_enter_submits_the_current_draft_and_consumes_the_key_event():
    class EntrySpy:
        def get(self) -> str:
            return "Send this draft"

    sent: list[str] = []
    box = object.__new__(ChatBoxBar)
    box.entry = EntrySpy()
    box.on_send = sent.append

    assert box._submit_and_consume() == "break"
    assert sent == ["Send this draft"]


def test_speech_input_controller_reports_a_friendly_error_from_fake_transcriber():
    completed = Event()
    errors: list[str] = []
    controller = SpeechInputController(FakeSpeechTranscriber(error="Microphone is unavailable."))

    controller.transcribe_async(
        on_listening=lambda: None,
        on_transcript=lambda text: (_ for _ in ()).throw(AssertionError(text)),
        on_error=lambda message: (errors.append(message), completed.set()),
        on_paused=lambda: (_ for _ in ()).throw(AssertionError("capture was paused")),
    )

    assert completed.wait(timeout=1)
    assert errors == ["Microphone is unavailable."]


def test_speech_input_controller_transcribes_the_phrase_when_pause_is_pressed():
    started = Event()
    completed = Event()
    pauses: list[bool] = []

    class PausableTranscriber:
        def transcribe(self, paused: Event) -> str:
            started.set()
            assert paused.wait(timeout=1)
            return "Draft spoken before pause"

    controller = SpeechInputController(PausableTranscriber())
    controller.transcribe_async(
        on_listening=lambda: None,
        on_transcript=lambda text: (pauses.append(text), completed.set()),
        on_error=lambda message: (_ for _ in ()).throw(AssertionError(message)),
        on_paused=lambda: (_ for _ in ()).throw(AssertionError("capture was discarded")),
    )

    assert started.wait(timeout=1)
    assert controller.pause() is True
    assert completed.wait(timeout=1)
    assert pauses == ["Draft spoken before pause"]


def test_recognized_text_is_inserted_into_chat_box_without_sending():
    """The microphone flow fills the composer and leaves Send under user control."""

    class EntrySpy:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, text: str) -> None:
            self.value = text

        def focus_set(self) -> None:
            self.focused = True

    class WidgetSpy:
        def __init__(self) -> None:
            self.options: dict[str, object] = {}

        def configure(self, **options: object) -> None:
            self.options.update(options)

    box = object.__new__(ChatBoxBar)
    box.entry = EntrySpy()
    box.action_button = WidgetSpy()
    box.send_icon = "send"
    box.microphone_icon = "microphone"
    box._update_action_icon = lambda: None

    box.insert_transcript("Draft from speech")

    assert box.entry.value == "Draft from speech"
    assert box.entry.focused is True
    assert box.action_button.options["state"] == "normal"


def test_multiline_composer_keeps_a_long_transcript_as_wrapped_text():
    class TextboxSpy:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def get(self, start: str, end: str) -> str:
            assert (start, end) == ("1.0", "end-1c")
            return self.value

        def delete(self, start: str, end: str) -> None:
            assert (start, end) == ("1.0", "end")
            self.value = ""

        def insert(self, index: str, text: str) -> None:
            assert index == "1.0"
            self.value = text

        def focus_set(self) -> None:
            self.focused = True

    long_transcript = (
        "A long transcript that remains in the multiline composer instead of truncating."
    )
    box = object.__new__(ChatBoxBar)
    box.entry = TextboxSpy()
    box.action_button = type("WidgetSpy", (), {"configure": lambda *_args, **_kwargs: None})()
    box.send_icon = "send"
    box.microphone_icon = "microphone"
    box._is_listening = True
    box._update_action_icon = lambda: None

    box.insert_transcript(long_transcript)

    assert box._entry_text() == long_transcript
    assert box.entry.focused is True


def test_microphone_click_disables_action_then_inserts_fake_transcript():
    """The ChatBoxBar click path schedules the fake result without calling Send."""

    class EntrySpy:
        def __init__(self) -> None:
            self.value = ""
            self.inserted = Event()
            self.focused = False

        def get(self) -> str:
            return self.value

        def delete(self, _start: int, _end: str) -> None:
            self.value = ""

        def insert(self, _index: int, text: str) -> None:
            self.value = text
            self.inserted.set()

        def focus_set(self) -> None:
            self.focused = True

    class WidgetSpy:
        def __init__(self) -> None:
            self.options: dict[str, object] = {}

        def configure(self, **options: object) -> None:
            self.options.update(options)

    class FrameSpy:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def after(self, _delay: int, callback: object) -> None:
            assert callable(callback)
            self.callbacks.append(callback)

    box = object.__new__(ChatBoxBar)
    box.entry = EntrySpy()
    box.action_button = WidgetSpy()
    box.frame = FrameSpy()
    box.send_icon = "send"
    box.microphone_icon = "microphone"
    box.pause_icon = "pause"
    box.speech_input = SpeechInputController(FakeSpeechTranscriber("Draft from microphone"))
    box._speech_results = Queue()
    box._is_listening = False
    box._update_action_icon = lambda: None

    box._handle_action()

    assert box._speech_results.get(timeout=1) == ("transcript", "Draft from microphone")
    box._speech_results.put(("transcript", "Draft from microphone"))
    box._poll_speech_results()

    assert box.entry.value == "Draft from microphone"
    assert box.entry.focused is True
    assert box.action_button.options["state"] == "normal"


def test_listening_state_turns_the_microphone_button_into_pause():
    class WidgetSpy:
        def __init__(self) -> None:
            self.options: dict[str, object] = {}

        def configure(self, **options: object) -> None:
            self.options.update(options)

    box = object.__new__(ChatBoxBar)
    box.action_button = WidgetSpy()
    box.pause_icon = "pause"
    box._is_listening = False

    box._set_listening()

    assert box.action_button.options == {
        "state": "normal",
        "text": "",
        "image": "pause",
        "width": 30,
        "anchor": "center",
        "fg_color": PINK,
        "font": (FONT, 14),
    }
    assert box._is_listening is True
