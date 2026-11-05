"""Responsive CustomTkinter desktop UI for the Simple Chat Application.

The GUI intentionally keeps network state in :class:`client.ClientSession`.  Socket.IO
callbacks enqueue events there; only the Tk thread polls that queue through ``after``.
This keeps the interface safe to import and test without a display server.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

try:  # Keep headless CI able to import the controller without GUI dependencies installed.
    import customtkinter as ctk
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only on machines without GUI extras
    ctk = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]

from client import ClientSession
from protocol import (
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    RegisterAckPayload,
    UserListPayload,
)

BACKGROUND = "#FFFCF8"
PINK = "#FF9FBF"
PINK_LIGHT = "#FCECF5"
YELLOW = "#FFE79F"
BLUE = "#BAE7EF"
INK = "#5C4B41"
FONT = "Josefin Sans"


class SessionPort(Protocol):
    """The public ClientSession operations consumed by the GUI controller."""

    def connect(self, server_url: str) -> bool: ...

    def register(self, username: str) -> bool: ...

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None: ...


class GuiController:
    """Headless adapter that owns GUI-facing login and presence state."""

    def __init__(self, session: SessionPort, on_change: Callable[[], None] | None = None) -> None:
        self.session = session
        self.on_change = on_change
        self.username: str | None = None
        self.online_users: list[str] = []
        self.login_error: str | None = None

    def connect_and_register(self, host: str, port: str, username: str) -> bool:
        """Connect and request registration, reporting synchronous errors inline."""
        host, port = host.strip(), port.strip()
        if not host or not port or not username.strip():
            self.login_error = "Server host, port, and username are required."
            self._changed()
            return False
        try:
            port_number = int(port)
            if not 1 <= port_number <= 65535:
                raise ValueError
        except ValueError:
            self.login_error = "Port must be a number from 1 to 65535."
            self._changed()
            return False
        if not self.session.connect(f"http://{host}:{port_number}"):
            self.login_error = "Could not connect to the server."
            self._changed()
            return False
        self.login_error = None
        result = self.session.register(username)
        self._changed()
        return result

    def poll_events(self) -> None:
        """Drain ClientSession's queue from the GUI thread without blocking."""
        changed = False
        while event := self.session.get_next_event(block=False):
            event_name, payload = event
            if event_name == EVENT_REGISTER_ACK and isinstance(payload, RegisterAckPayload):
                if payload.success:
                    self.username = payload.username
                    self.login_error = None
                else:
                    self.login_error = payload.error or "Login was rejected."
                changed = True
            elif event_name == EVENT_USER_LIST and isinstance(payload, UserListPayload):
                self.online_users = list(payload.users)
                changed = True
        if changed:
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()


def _require_ctk() -> Any:
    if ctk is None:
        raise RuntimeError("Install GUI extras with: pip install customtkinter pillow")
    return ctk


def _load_avatars() -> dict[str, Any]:
    """Load the supplied character PNGs, returning an empty map when unavailable."""
    if ctk is None or Image is None:
        return {}
    root = Path(__file__).resolve().parent
    avatars: dict[str, Any] = {}
    for name in ("cat", "dog", "rabbit"):
        path = root / "assets" / f"avatar_{name}.png"
        if path.is_file():
            image = Image.open(path)
            avatars[name] = ctk.CTkImage(light_image=image, dark_image=image, size=(58, 58))
    return avatars


class LoginView:
    """Reusable Create User card for R3, styled after the supplied design."""

    def __init__(self, parent: Any, on_connect: Callable[[str, str, str], None]) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(
            parent,
            fg_color=BACKGROUND,
            border_color=INK,
            border_width=2,
            corner_radius=16,
        )
        self.frame.grid_columnconfigure(1, weight=1)
        self.avatars = _load_avatars()
        heading = ui.CTkLabel(
            self.frame,
            text="Create User",
            font=(FONT, 24, "bold"),
            text_color=INK,
            fg_color=PINK,
            corner_radius=14,
        )
        heading.grid(row=0, column=0, columnspan=2, padx=0, pady=0, sticky="ew")
        self.username = self._entry("Name", "", 1)
        ui.CTkLabel(self.frame, text="Choose your character", text_color=INK, anchor="w").grid(
            row=2, column=0, columnspan=2, padx=28, pady=(12, 2), sticky="w"
        )
        self.character = "rabbit"
        self.character_buttons: dict[str, Any] = {}
        character_row = ui.CTkFrame(self.frame, fg_color="transparent")
        character_row.grid(row=3, column=0, columnspan=2, padx=28, pady=(2, 12), sticky="ew")
        for index, name in enumerate(("cat", "dog", "rabbit")):
            button = ui.CTkButton(
                character_row,
                text=name.title(),
                image=self.avatars.get(name),
                width=78,
                height=62,
                fg_color=PINK_LIGHT if name == self.character else "white",
                hover_color="#F7D4E1",
                text_color=INK,
                command=lambda selected=name: self._choose_character(selected),
            )
            button.grid(row=0, column=index, padx=4, sticky="ew")
            self.character_buttons[name] = button
        connection_label = ui.CTkLabel(
            self.frame, text="Server connection", text_color=INK, anchor="w"
        )
        connection_label.grid(row=4, column=0, columnspan=2, padx=28, pady=(2, 2), sticky="w")
        self.host = self._entry("Host", "127.0.0.1", 5)
        self.port = self._entry("Port", "5000", 6)
        self.error = ui.CTkLabel(self.frame, text="", text_color="#C64B68", wraplength=300)
        self.error.grid(row=7, column=0, columnspan=2, padx=28, pady=(10, 4))
        ui.CTkButton(
            self.frame,
            text="Create",
            fg_color=PINK,
            hover_color="#DF6D98",
            text_color=INK,
            command=lambda: on_connect(self.host.get(), self.port.get(), self.username.get()),
        ).grid(row=8, column=0, columnspan=2, padx=28, pady=(10, 24), sticky="ew")

    def _choose_character(self, character: str) -> None:
        self.character = character
        for name, button in self.character_buttons.items():
            button.configure(fg_color=PINK_LIGHT if name == character else "white")

    def _entry(self, label: str, value: str, row: int) -> Any:
        ui = _require_ctk()
        ui.CTkLabel(self.frame, text=label, text_color=INK).grid(
            row=row, column=0, padx=(28, 10), pady=8, sticky="w"
        )
        entry = ui.CTkEntry(self.frame, placeholder_text=value, border_color=PINK, fg_color="white")
        if value:
            entry.insert(0, value)
        entry.grid(row=row, column=1, padx=(0, 28), pady=8, sticky="ew")
        return entry

    def show_error(self, message: str | None) -> None:
        self.error.configure(text=message or "")


class AnnouncementBanner:
    """Reusable, dismissible global announcement slot (SP3)."""

    def __init__(self, parent: Any) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color=YELLOW)
        self.label = ui.CTkLabel(self.frame, text="", text_color=INK, anchor="w")
        self.label.pack(side="left", fill="x", expand=True, padx=14, pady=7)
        ui.CTkButton(self.frame, text="Hide", width=55, command=self.frame.grid_remove).pack(
            side="right", padx=8, pady=4
        )

    def show(self, message: str) -> None:
        self.label.configure(text=f"📢  {message}")
        self.frame.grid()


class SidebarPanel:
    """Reusable responsive presence and group placeholder panel."""

    def __init__(self, parent: Any, on_chat: Callable[[str], None]) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color="transparent")
        self.on_chat = on_chat
        self.avatar = _load_avatars().get("rabbit")
        self.friends_card = ui.CTkFrame(
            self.frame, fg_color=BACKGROUND, border_color=INK, border_width=2, corner_radius=14
        )
        self.friends_card.pack(fill="both", expand=True)
        friends_heading = ui.CTkLabel(
            self.friends_card,
            text="☻  Friends",
            font=(FONT, 20, "bold"),
            text_color=INK,
            fg_color=YELLOW,
            corner_radius=10,
        )
        friends_heading.pack(fill="x", padx=2, pady=2)
        self.users = ui.CTkScrollableFrame(self.friends_card, fg_color=BACKGROUND, height=220)
        self.users.pack(fill="both", expand=True, padx=8, pady=8)
        self.community_card = ui.CTkFrame(
            self.frame, fg_color=BACKGROUND, border_color=INK, border_width=2, corner_radius=14
        )
        self.community_card.pack(fill="both", expand=True, pady=(14, 0))
        community_heading = ui.CTkLabel(
            self.community_card,
            text="♣  Community",
            font=(FONT, 18, "bold"),
            text_color=INK,
            fg_color=BLUE,
            corner_radius=10,
        )
        community_heading.pack(fill="x", padx=2, pady=(8, 2))
        ui.CTkButton(
            self.community_card,
            text="＋ New Group",
            width=120,
            fg_color="white",
            border_color=INK,
            border_width=1,
            text_color=INK,
            command=_noop,
        ).pack(anchor="e", padx=12, pady=(10, 4))
        ui.CTkLabel(self.community_card, text="Group rooms will appear here.", text_color=INK).pack(
            pady=(8, 16)
        )

    def set_users(self, users: list[str], current_user: str | None) -> None:
        for child in self.users.winfo_children():
            child.destroy()
        for user in users:
            label = f"{user} (You)" if user == current_user else user
            button = _require_ctk().CTkButton(
                self.users,
                text=label,
                image=self.avatar,
                anchor="w",
                fg_color=PINK_LIGHT if user == current_user else "transparent",
                text_color=INK,
                command=lambda name=user: self.on_chat(name),
            )
            button.pack(fill="x", pady=3)


class MessageBubble:
    """Reusable message row for a ChatWindow."""

    def __init__(self, parent: Any, sender: str, message: str, own: bool = False) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color="transparent")
        bubble = ui.CTkLabel(
            self.frame,
            text=f"{sender}\n{message}",
            justify="left",
            wraplength=310,
            text_color=INK,
            fg_color=PINK if own else PINK_LIGHT,
            corner_radius=16,
        )
        bubble.pack(anchor="e" if own else "w", padx=8, pady=4)


class ChatBoxBar:
    """Reusable message composer with requested special-feature hooks."""

    def __init__(
        self,
        parent: Any,
        on_send: Callable[[str], None],
        on_sticker: Callable[[], None] | None = None,
        on_microphone: Callable[[], None] | None = None,
    ) -> None:
        ui = _require_ctk()
        on_sticker = on_sticker or _noop
        on_microphone = on_microphone or _noop
        self.frame = ui.CTkFrame(parent, fg_color=PINK_LIGHT)
        self.entry = ui.CTkEntry(self.frame, placeholder_text="Type a message…", fg_color="white")
        self.entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        sticker_button = ui.CTkButton(self.frame, text="😊 Sticker", width=82, command=on_sticker)
        sticker_button.pack(side="left", padx=3)
        microphone_button = ui.CTkButton(self.frame, text="🎤", width=40, command=on_microphone)
        microphone_button.pack(side="left", padx=3)
        send_button = ui.CTkButton(
            self.frame, text="Send", width=62, command=lambda: on_send(self.entry.get())
        )
        send_button.pack(side="left", padx=(3, 8))


class ChatRoomTab:
    """Reusable R5/R6 tab containing a scrollable ChatWindow and ChatBoxBar."""

    def __init__(
        self, parent: Any, room_name: str, on_send: Callable[[str], None] | None = None
    ) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color=BACKGROUND, corner_radius=12)
        self.header = ui.CTkFrame(self.frame, fg_color=PINK, corner_radius=10)
        self.header.pack(fill="x", padx=6, pady=(6, 0))
        ui.CTkLabel(
            self.header,
            text=f"▤  {room_name}",
            font=(FONT, 20, "bold"),
            text_color=INK,
            anchor="w",
        ).pack(fill="x", padx=14, pady=9)
        self.window = ui.CTkScrollableFrame(self.frame, fg_color=BACKGROUND)
        self.window.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.on_send = on_send or self._send_local
        self.box = ChatBoxBar(self.frame, self._send)
        self.box.frame.pack(fill="x", padx=8, pady=(0, 8))
        self.room_name = room_name

    def _send(self, message: str) -> None:
        if message.strip():
            self.on_send(message)
            self.box.entry.delete(0, "end")

    def _send_local(self, message: str) -> None:
        """Render a local demonstration message until private transport (R7) is wired."""
        self.add_message("You", message, own=True)

    def add_message(self, sender: str, message: str, own: bool = False) -> None:
        bubble = MessageBubble(self.window, sender, message, own)
        bubble.frame.pack(fill="x")


def _noop() -> None:
    """Default UI hook until a later feature supplies a concrete callback."""


class PopupDialog:
    """Reusable modal shell for future sticker and group dialogs."""

    def __init__(self, parent: Any, title: str) -> None:
        ui = _require_ctk()
        self.window = ui.CTkToplevel(parent)
        self.window.title(title)
        self.window.transient(parent)
        self.frame = ui.CTkFrame(self.window, fg_color=BACKGROUND)
        self.frame.pack(fill="both", expand=True, padx=12, pady=12)


class MainChatWindow:
    """Reusable responsive main view for authenticated sessions."""

    def __init__(self, parent: Any, controller: GuiController) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color=BACKGROUND)
        self.controller = controller
        self.frame.grid_columnconfigure(0, weight=0)
        self.frame.grid_columnconfigure(1, weight=1)
        self.frame.grid_rowconfigure(2, weight=1)
        self.title = ui.CTkLabel(
            self.frame, text="Logged in", font=(FONT, 22, "bold"), text_color=INK
        )
        self.title.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 6))
        self.logout = ui.CTkButton(
            self.frame,
            text="⇥\nLogout",
            width=70,
            height=58,
            fg_color="#FFC8B7",
            hover_color="#F3AE9A",
            text_color=INK,
            command=parent.destroy,
        )
        self.logout.grid(row=0, column=1, sticky="e", padx=18, pady=(10, 6))
        self.banner = AnnouncementBanner(self.frame)
        self.banner.frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=6)
        self.banner.frame.grid_remove()
        self.sidebar = SidebarPanel(self.frame, self.open_private_room)
        self.sidebar.frame.grid(row=2, column=0, sticky="nsew", padx=(18, 8), pady=(6, 18))
        self.tabs = ui.CTkTabview(self.frame, fg_color=PINK_LIGHT)
        self.tabs.grid(row=2, column=1, sticky="nsew", padx=(8, 18), pady=(6, 18))
        self.rooms: dict[str, ChatRoomTab] = {}

    def refresh(self) -> None:
        name = self.controller.username or "…"
        self.title.configure(text=f"Logged in as: {name} (Online)")
        self.sidebar.set_users(self.controller.online_users, self.controller.username)
        peers = [user for user in self.controller.online_users if user != self.controller.username]
        if peers and not self.rooms:
            self.open_private_room(peers[0])

    def open_private_room(self, user: str) -> None:
        if user == self.controller.username:
            return
        if user not in self.rooms:
            tab = self.tabs.add(user)
            self.rooms[user] = ChatRoomTab(tab, user)
            self.rooms[user].frame.pack(fill="both", expand=True)
        self.tabs.set(user)


class ChatApplication:
    """Application composition root and Tk ``after`` event poller."""

    def __init__(self, session: ClientSession | None = None) -> None:
        ui = _require_ctk()
        ui.set_appearance_mode("light")
        self.root = ui.CTk()
        self.root.title("Simple Chat Application")
        self.root.minsize(760, 520)
        self.root.geometry("1080x700")
        self.root.configure(fg_color=BACKGROUND)
        self.controller = GuiController(session or ClientSession(), self._refresh)
        self.login = LoginView(self.root, self._connect)
        self.login.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.main: MainChatWindow | None = None
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._poll()

    def _connect(self, host: str, port: str, username: str) -> None:
        self.controller.connect_and_register(host, port, username)
        self.login.show_error(self.controller.login_error)

    def _poll(self) -> None:
        self.controller.poll_events()
        self.root.after(75, self._poll)

    def _refresh(self) -> None:
        if self.controller.username and self.main is None:
            self.login.frame.place_forget()
            self.main = MainChatWindow(self.root, self.controller)
            self.main.frame.pack(fill="both", expand=True)
        if self.main:
            self.main.refresh()
        else:
            self.login.show_error(self.controller.login_error)

    def _close(self) -> None:
        session = self.controller.session
        disconnect = getattr(session, "disconnect", None)
        if disconnect:
            disconnect()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple Chat Application GUI")
    parser.parse_args()
    ChatApplication().run()


if __name__ == "__main__":
    main()
