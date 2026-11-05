"""Responsive CustomTkinter desktop UI for the Simple Chat Application.

The GUI intentionally keeps network state in :class:`client.ClientSession`.  Socket.IO
callbacks enqueue events there; only the Tk thread polls that queue through ``after``.
This keeps the interface safe to import and test without a display server.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

try:  # Keep headless CI able to import the controller without GUI dependencies installed.
    import tkinter as tk

    import customtkinter as ctk
    from dotenv import load_dotenv
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - exercised only on machines without GUI extras
    ctk = None  # type: ignore[assignment]
    tk = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]
    ImageTk = None  # type: ignore[assignment,misc]

    def load_dotenv(dotenv_path: Any = None) -> None:
        del dotenv_path
        return None


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
WHITE = "#FFFFFF"
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


def _load_avatars(size: tuple[int, int] = (120, 120)) -> dict[str, Any]:
    """Load the supplied character PNGs, returning an empty map when unavailable."""
    if ctk is None or Image is None:
        return {}
    root = Path(__file__).resolve().parent
    avatars: dict[str, Any] = {}
    for name in ("cat", "dog", "rabbit"):
        path = root / "assets" / f"avatar_{name}.png"
        if path.is_file():
            image = Image.open(path)
            avatars[name] = ctk.CTkImage(light_image=image, dark_image=image, size=size)
    return avatars


def _load_avatar_photos(size: tuple[int, int] = (112, 112)) -> dict[str, Any]:
    """Load avatar photos for Canvas rendering without widget-level image clipping."""
    if Image is None or ImageTk is None:
        return {}
    root = Path(__file__).resolve().parent
    photos: dict[str, Any] = {}
    for name in ("cat", "dog", "rabbit"):
        path = root / "assets" / f"avatar_{name}.png"
        if path.is_file():
            image = Image.open(path).convert("RGBA").resize(size)
            photos[name] = ImageTk.PhotoImage(image)
    return photos


def _load_icons(size: tuple[int, int] = (28, 28)) -> dict[str, Any]:
    """Load the supplied UI icons as CustomTkinter images."""
    if ctk is None or Image is None:
        return {}
    root = Path(__file__).resolve().parent
    icons: dict[str, Any] = {}
    for name in ("friends", "community", "chat", "notification", "logout"):
        path = root / "assets" / f"icon_{name}.png"
        if path.is_file():
            image = Image.open(path).convert("RGBA")
            icons[name] = ctk.CTkImage(light_image=image, dark_image=image, size=size)
    return icons


class LoginView:
    """Reusable Create User card for R3, styled after the supplied design."""

    def __init__(
        self,
        parent: Any,
        on_connect: Callable[[str, str, str], None],
        host: str = "127.0.0.1",
        port: str = "5000",
    ) -> None:
        ui = _require_ctk()
        if tk is None:
            raise RuntimeError("This Python installation does not include Tk support")
        self.frame = ui.CTkFrame(
            parent,
            fg_color=WHITE,
            border_color=INK,
            border_width=3,
            corner_radius=16,
            width=600,
            height=560,
        )
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_propagate(False)
        self.host = host
        self.port = port
        self.avatar_photos = _load_avatar_photos()
        self.header = tk.Canvas(
            self.frame,
            height=68,
            bg=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        self.header.grid(row=0, column=0, columnspan=2, padx=0, pady=0, sticky="ew")
        self.header.bind("<Configure>", self._draw_header)
        ui.CTkLabel(
            self.frame, text="Name", font=(FONT, 24, "bold"), text_color=INK, anchor="w"
        ).grid(row=1, column=0, padx=60, pady=(24, 8), sticky="w")
        self.username = ui.CTkEntry(
            self.frame,
            placeholder_text="Username",
            border_color=PINK,
            border_width=2,
            fg_color="white",
            height=42,
            font=(FONT, 15),
        )
        self.username.grid(row=2, column=0, padx=54, pady=(0, 22), sticky="ew")
        ui.CTkLabel(
            self.frame,
            text="Choose your character",
            font=(FONT, 24, "bold"),
            text_color=INK,
            anchor="w",
        ).grid(row=3, column=0, padx=54, pady=(0, 14), sticky="w")
        self.character = "rabbit"
        self.character_buttons: dict[str, Any] = {}
        character_row = ui.CTkFrame(self.frame, fg_color="transparent")
        character_row.grid(row=4, column=0, padx=54, pady=(10, 10), sticky="ew")
        character_row.grid_columnconfigure((0, 1, 2), weight=1)
        for index, name in enumerate(("cat", "dog", "rabbit")):
            canvas = tk.Canvas(
                character_row,
                width=120,
                height=120,
                bg=WHITE,
                highlightthickness=0,
                bd=0,
            )
            canvas.grid(row=0, column=index, padx=3, sticky="ew")
            self._draw_avatar(canvas, name)
            canvas.bind(
                "<Button-1>", lambda _event, selected=name: self._choose_character(selected)
            )
            self.character_buttons[name] = canvas
        self.selection_line = ui.CTkFrame(self.frame, fg_color=INK, height=4, corner_radius=2)
        self.selection_line.grid(row=5, column=0, padx=56, pady=(10, 20), sticky="w")
        self.error = ui.CTkLabel(self.frame, text="", text_color="#C64B68", wraplength=300)
        self.error.grid(row=6, column=0, padx=54, pady=(1, 1))
        ui.CTkButton(
            self.frame,
            text="Create",
            fg_color=PINK,
            hover_color="#DF6D98",
            text_color="white",
            height=44,
            font=(FONT, 24, "bold"),
            command=lambda: on_connect(self.host, self.port, self.username.get()),
        ).grid(row=7, column=0, padx=54, pady=(24, 16), sticky="ew")

    def _choose_character(self, character: str) -> None:
        self.character = character
        for name, canvas in self.character_buttons.items():
            self._draw_avatar(canvas, name)

    def _draw_avatar(self, canvas: Any, name: str) -> None:
        canvas.delete("all")
        selected = name == self.character
        canvas.create_oval(
            2,
            2,
            118,
            118,
            fill=PINK_LIGHT if selected else BACKGROUND,
            outline=INK if selected else "",
            width=3,
        )
        photo = self.avatar_photos.get(name)
        if photo:
            canvas.create_image(60, 60, image=photo)

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

    def _draw_header(self, _event: Any = None) -> None:
        """Draw a pink header with rounded top corners and a straight bottom edge."""
        width = max(self.header.winfo_width(), 10)
        height = max(self.header.winfo_height(), 10)
        self.header.delete("all")

        def draw_top(fill: str, x: int, y: int, right: int, bottom: int, radius: int) -> None:
            self.header.create_rectangle(x, y + radius, right, bottom, fill=fill, outline=fill)
            self.header.create_rectangle(
                x + radius, y, right - radius, bottom, fill=fill, outline=fill
            )
            self.header.create_arc(
                x, y, x + radius * 2, y + radius * 2, start=90, extent=90, fill=fill, outline=fill
            )
            self.header.create_arc(
                right - radius * 2,
                y,
                right,
                y + radius * 2,
                start=0,
                extent=90,
                fill=fill,
                outline=fill,
            )

        radius = min(18, width // 8, height // 2)
        draw_top(INK, 0, 0, width, height, radius)
        inset = 2
        draw_top(PINK, inset, inset, width - inset, height - inset, max(radius - inset, 1))
        self.header.create_line(0, height - 1, width, height - 1, fill=INK, width=2)
        self.header.create_text(
            width // 2,
            height // 2 - 2,
            text="Create User",
            fill=INK,
            font=(FONT, 32, "bold"),
        )

    def show_error(self, message: str | None) -> None:
        self.error.configure(text=message or "")


class AnnouncementBanner:
    """Reusable, dismissible global announcement slot (SP3)."""

    def __init__(self, parent: Any) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color=YELLOW)
        self.label = ui.CTkLabel(
            self.frame,
            text="",
            image=_load_icons().get("notification"),
            compound="left",
            text_color=INK,
            anchor="w",
        )
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
        self.icons = _load_icons()
        self.friends_card = ui.CTkFrame(
            self.frame, fg_color=BACKGROUND, border_color=INK, border_width=2, corner_radius=14
        )
        self.friends_card.pack(fill="both", expand=True)
        friends_heading = ui.CTkLabel(
            self.friends_card,
            text="Friends",
            image=self.icons.get("friends"),
            compound="left",
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
            text="Community",
            image=self.icons.get("community"),
            compound="left",
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
            image=_load_icons().get("chat"),
            compound="left",
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
            text="Logout",
            image=_load_icons().get("logout"),
            compound="top",
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

    def __init__(
        self,
        session: ClientSession | None = None,
        host: str | None = None,
        port: str | None = None,
    ) -> None:
        load_dotenv(dotenv_path=Path(__file__).resolve().with_name(".env"))
        host = host or os.getenv("CHAT_SERVER_HOST", "127.0.0.1")
        port = port or os.getenv("CHAT_SERVER_PORT", "5000")
        ui = _require_ctk()
        ui.set_appearance_mode("light")
        self.root = ui.CTk()
        self.root.title("Simple Chat Application")
        self.root.minsize(760, 520)
        self.root.geometry("1080x700")
        self.root.configure(fg_color=BACKGROUND)
        self.controller = GuiController(session or ClientSession(), self._refresh)
        self.login = LoginView(self.root, self._connect, host=host, port=port)
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
    parser.add_argument("--host", default=None, help="Override CHAT_SERVER_HOST from .env")
    parser.add_argument("--port", default=None, help="Override CHAT_SERVER_PORT from .env")
    args = parser.parse_args()
    ChatApplication(host=args.host, port=args.port).run()


if __name__ == "__main__":
    main()
