"""Responsive CustomTkinter desktop UI for the Simple Chat Application.

The GUI intentionally keeps network state in :class:`client.ClientSession`.  Socket.IO
callbacks enqueue events there; only the Tk thread polls that queue through ``after``.
This keeps the interface safe to import and test without a display server.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from datetime import datetime
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
    EVENT_CONNECT,
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    GroupErrorPayload,
    GroupListPayload,
    PrivateMessageServerPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    UserListPayload,
)

BACKGROUND = "#FFFCF8"
PINK = "#FF9FBF"
PINK_LIGHT = "#F9EEF4"
YELLOW = "#FFE79F"
BLUE = "#BAE7EF"
INK = "#5C4B41"
WHITE = "#FFFFFF"
FONT = "Josefin Sans"


class SessionPort(Protocol):
    """The public ClientSession operations consumed by the GUI controller."""

    def connect(self, server_url: str) -> bool: ...

    def register(self, username: str, avatar: str = "rabbit") -> bool: ...

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None: ...

    def send_private_message(
        self,
        target: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None: ...

    def send_read_ack(self, message_id: str, target: str) -> bool: ...

    def create_group(self, group_name: str) -> bool: ...

    def join_group(self, group_name: str) -> bool: ...


class GuiController:
    """Headless adapter that owns GUI-facing login, presence, and chat message state."""

    def __init__(self, session: SessionPort, on_change: Callable[[], None] | None = None) -> None:
        self.session = session
        self.on_change = on_change
        self.username: str | None = None
        self.avatar = "rabbit"
        self.online_users: list[str] = []
        self.online_avatars: dict[str, str] = {}
        self.groups: list[dict[str, Any]] = []
        self.group_error: str | None = None
        self.login_error: str | None = None
        self.private_messages: dict[str, list[PrivateMessageServerPayload]] = {}
        self.on_private_message: Callable[[str, PrivateMessageServerPayload], None] | None = None
        self.on_private_message_error: Callable[[str, str], None] | None = None
        self.on_read_ack: Callable[[str, str], None] | None = None
        self.on_reconnect: Callable[[], None] | None = None

    def send_private_message(self, target: str, message: str) -> str | None:
        """Send a 1-to-1 private message through the underlying session adapter (R7)."""
        send_fn = getattr(self.session, "send_private_message", None)
        if send_fn is None:
            return None
        return send_fn(target, message)

    def send_read_ack(self, message_id: str, target: str) -> bool:
        """Send a read receipt acknowledgment through the session adapter (SP4)."""
        send_fn = getattr(self.session, "send_read_ack", None)
        if send_fn is None:
            return False
        return send_fn(message_id, target)

    def create_group(self, group_name: str) -> bool:
        """Request a group whose initial member is the local user (R8)."""
        create_fn = getattr(self.session, "create_group", None)
        return bool(create_fn and create_fn(group_name))

    def join_group(self, group_name: str) -> bool:
        """Request that the local user voluntarily joins a group (R10)."""
        join_fn = getattr(self.session, "join_group", None)
        return bool(join_fn and join_fn(group_name))

    def connect_and_register(
        self, host: str, port: str, username: str, avatar: str = "rabbit"
    ) -> bool:
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
        result = self.session.register(username, avatar)
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
                    if payload.username not in self.online_users:
                        self.online_users.append(payload.username)
                    self.login_error = None
                    if self.on_reconnect:
                        self.on_reconnect()
                else:
                    self.login_error = payload.error or "Login was rejected."
                changed = True
            elif event_name == EVENT_USER_LIST and isinstance(payload, UserListPayload):
                self.online_users = list(payload.users)
                self.online_avatars = dict(payload.avatars)
                changed = True
            elif event_name == EVENT_GROUP_LIST and isinstance(payload, GroupListPayload):
                self.groups = [group.to_dict() for group in payload.groups]
                self.group_error = None
                changed = True
            elif event_name == EVENT_GROUP_ERROR and isinstance(payload, GroupErrorPayload):
                self.group_error = payload.error
                changed = True
            elif event_name == EVENT_PRIVATE_MSG and isinstance(
                payload, PrivateMessageServerPayload
            ):
                peer = payload.target if payload.sender == self.username else payload.sender
                self.private_messages.setdefault(peer, []).append(payload)
                if self.on_private_message:
                    self.on_private_message(peer, payload)
                changed = True
            elif event_name == EVENT_PRIVATE_MSG_ERROR and isinstance(payload, dict):
                error_msg = str(payload.get("error", "Failed to deliver message"))
                target_peer = str(payload.get("target", ""))
                if self.on_private_message_error:
                    self.on_private_message_error(target_peer, error_msg)
                changed = True
            elif event_name == EVENT_READ_ACK and isinstance(payload, ReadAckServerPayload):
                if self.on_read_ack:
                    self.on_read_ack(payload.message_id, payload.reader)
                changed = True
            elif event_name in (EVENT_CONNECT, "connect"):
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
    for name in (
        "friends",
        "community",
        "chat",
        "notification",
        "logout",
        "sticker",
        "send",
        "microphone",
    ):
        path = root / "assets" / f"icon_{name}.png"
        if path.is_file():
            image = Image.open(path).convert("RGBA")
            intrinsic_sizes = {
                "friends": (25, 25),
                "community": (30, 15),
                "chat": (25, 25),
                "notification": (17, 20),
                "logout": (23, 23),
                "sticker": (24, 24),
                "send": (28, 24),
                "microphone": (20, 24),
            }
            icon_size = intrinsic_sizes.get(name, size)
            icons[name] = ctk.CTkImage(light_image=image, dark_image=image, size=icon_size)
    return icons


def _load_icon_photos() -> dict[str, Any]:
    """Load exact-size icon photos for Tk Canvas headers."""
    if Image is None or ImageTk is None:
        return {}
    root = Path(__file__).resolve().parent
    sizes = {"friends": (25, 25), "community": (30, 15), "chat": (25, 25)}
    photos: dict[str, Any] = {}
    for name, size in sizes.items():
        path = root / "assets" / f"icon_{name}.png"
        if path.is_file():
            image = Image.open(path).convert("RGBA").resize(size)
            photos[name] = ImageTk.PhotoImage(image)
    return photos


class LoginView:
    """Reusable Create User card for R3, styled after the supplied design."""

    def __init__(
        self,
        parent: Any,
        on_connect: Callable[[str, str, str, str], None],
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
        self.avatar_photos_hover = _load_avatar_photos((122, 122))
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
            font=(FONT, 24),
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
        self.selected_character: str | None = None
        self.hovered_character: str | None = None
        self.character_buttons: dict[str, Any] = {}
        character_row = ui.CTkFrame(self.frame, fg_color="transparent")
        character_row.grid(row=4, column=0, padx=54, pady=(10, 10), sticky="ew")
        character_row.grid_columnconfigure((0, 1, 2), weight=1)
        for index, name in enumerate(("cat", "dog", "rabbit")):
            canvas = tk.Canvas(
                character_row,
                width=128,
                height=128,
                bg=WHITE,
                highlightthickness=0,
                bd=0,
            )
            canvas.grid(row=0, column=index, padx=3, sticky="ew")
            self._draw_avatar(canvas, name)
            canvas.bind(
                "<Button-1>", lambda _event, selected=name: self._choose_character(selected)
            )
            canvas.bind("<Enter>", lambda _event, selected=name: self._hover_character(selected))
            canvas.bind("<Leave>", lambda _event: self._hover_character(None))
            self.character_buttons[name] = canvas
        self.error = ui.CTkLabel(self.frame, text="", text_color="#C64B68", wraplength=300)
        self.error.grid(row=5, column=0, padx=54, pady=(1, 1))
        ui.CTkButton(
            self.frame,
            text="Create",
            fg_color=PINK,
            hover_color="#DF6D98",
            text_color="white",
            height=44,
            font=(FONT, 24, "bold"),
            command=lambda: on_connect(self.host, self.port, self.username.get(), self.character),
        ).grid(row=6, column=0, padx=54, pady=(20, 16), sticky="ew")

    def _choose_character(self, character: str) -> None:
        self.character = character
        self.selected_character = character
        for name, canvas in self.character_buttons.items():
            self._draw_avatar(canvas, name)

    def _hover_character(self, character: str | None) -> None:
        self.hovered_character = character
        for name, canvas in self.character_buttons.items():
            self._draw_avatar(canvas, name)

    def _draw_avatar(self, canvas: Any, name: str) -> None:
        canvas.delete("all")
        selected = name == self.selected_character
        hovered = name == self.hovered_character
        canvas.create_oval(
            2,
            2,
            126,
            126,
            fill=PINK_LIGHT if selected or hovered else BACKGROUND,
            outline=INK if selected else "",
            width=3,
        )
        photo = (self.avatar_photos_hover if hovered else self.avatar_photos).get(name)
        if photo:
            canvas.create_image(64, 64, image=photo)

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
    """Reusable responsive presence and server-backed group discovery panel."""

    def __init__(
        self,
        parent: Any,
        on_chat: Callable[[str], None],
        on_create_group: Callable[[str], bool],
        on_join_group: Callable[[str], bool],
        on_open_group: Callable[[str], None],
    ) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, width=380, fg_color="transparent")
        self.on_chat = on_chat
        self.on_create_group = on_create_group
        self.on_join_group = on_join_group
        self.on_open_group = on_open_group
        self.avatars = _load_avatars((42, 42))
        self.avatar = self.avatars.get("rabbit")
        self.icons = _load_icons((30, 30))
        self.icon_photos = _load_icon_photos()
        self.friends_card = ui.CTkFrame(
            self.frame,
            height=270,
            fg_color=WHITE,
            border_color=INK,
            border_width=2,
            corner_radius=14,
        )
        self.friends_card.pack(fill="x")
        self.friends_card.pack_propagate(False)
        self.friends_header = tk.Canvas(
            self.friends_card, height=48, bg=BACKGROUND, highlightthickness=0, bd=0
        )
        self.friends_header.pack(fill="x")
        self.friends_header.bind(
            "<Configure>",
            lambda _event: self._draw_card_header(
                self.friends_header, YELLOW, "Friends", "friends"
            ),
        )
        self.users = ui.CTkScrollableFrame(self.friends_card, fg_color=BACKGROUND, corner_radius=0)
        self.users.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.community_card = ui.CTkFrame(
            self.frame,
            height=270,
            fg_color=WHITE,
            border_color=INK,
            border_width=2,
            corner_radius=14,
        )
        self.community_card.pack(fill="x", pady=(14, 0))
        self.community_card.pack_propagate(False)
        self.community_header = tk.Canvas(
            self.community_card, height=48, bg=BACKGROUND, highlightthickness=0, bd=0
        )
        self.community_header.pack(fill="x")
        self.community_header.bind(
            "<Configure>",
            lambda _event: self._draw_card_header(
                self.community_header, BLUE, "Community", "community"
            ),
        )
        self.new_group_button = ui.CTkButton(
            self.community_card,
            text="＋ New Group",
            font=(FONT, 13),
            width=114,
            height=30,
            corner_radius=15,
            bg_color=BLUE,
            fg_color=WHITE,
            hover_color=BLUE,
            border_color=INK,
            border_width=1,
            text_color=INK,
            command=self._open_group_dialog,
        )
        self.new_group_button.place(relx=1.0, x=-10, y=9, anchor="ne")
        self.groups = ui.CTkScrollableFrame(
            self.community_card, fg_color=BACKGROUND, corner_radius=10
        )
        self.groups.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.group_placeholder = ui.CTkLabel(
            self.groups,
            text="Group rooms will appear here.",
            text_color=INK,
        )
        self.group_placeholder.pack(pady=(12, 0))
        self.group_rows: dict[str, Any] = {}

    def _open_group_dialog(self) -> None:
        """Open the dialog for a server-backed group creation request."""
        ui = _require_ctk()
        if (
            hasattr(self, "_active_dialog")
            and self._active_dialog
            and self._active_dialog.winfo_exists()
        ):
            self._active_dialog.lift()
            self._active_dialog.focus_force()
            return

        dialog = ui.CTkToplevel(self.frame.winfo_toplevel())
        self._active_dialog = dialog
        dialog.title("Create New Group")
        dialog.withdraw()
        width, height = 630, 320
        dialog.geometry(f"{width}x{height}")
        dialog.resizable(False, False)
        dialog.transient(self.frame.winfo_toplevel())

        def _close_dialog() -> None:
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)
        dialog.bind("<Escape>", lambda _event: _close_dialog())

        card = ui.CTkFrame(
            dialog, fg_color=WHITE, border_color=INK, border_width=2, corner_radius=14
        )
        card.pack(fill="both", expand=True)
        header = tk.Canvas(card, height=84, bg=WHITE, highlightthickness=0, bd=0)
        header.pack(fill="x")
        header.bind("<Configure>", lambda _event: self._draw_group_dialog_header(header))

        name_entry = ui.CTkEntry(
            card,
            placeholder_text="Enter Group Name",
            height=60,
            font=(FONT, 19),
            fg_color=PINK_LIGHT,
            border_color=INK,
            border_width=2,
            corner_radius=30,
        )
        name_entry.pack(fill="x", padx=46, pady=(46, 20))

        btn_row = ui.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(8, 12))

        ui.CTkButton(
            btn_row,
            text="Cancel",
            width=92,
            height=54,
            fg_color="#E0E0E0",
            hover_color="#D0D0D0",
            text_color=INK,
            corner_radius=16,
            font=(FONT, 14, "bold"),
            command=_close_dialog,
        ).pack(side="left", padx=(0, 14))

        ui.CTkButton(
            btn_row,
            text="Create",
            width=228,
            height=62,
            fg_color=BLUE,
            hover_color="#91D5DF",
            text_color=INK,
            border_color=INK,
            border_width=1,
            corner_radius=18,
            font=(FONT, 18, "bold"),
            command=lambda: self._create_group(name_entry, dialog),
        ).pack(side="left")

        name_entry.bind("<Return>", lambda _event: self._create_group(name_entry, dialog))
        dialog.after(50, lambda: self._center_group_dialog(dialog, width, height, name_entry))

    def _center_group_dialog(self, dialog: Any, width: int, height: int, entry: Any = None) -> None:
        parent = self.frame.winfo_toplevel()
        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.deiconify()
        if entry is not None:
            entry.focus_force()

    def _start_dialog_drag(self, dialog: Any, event: Any) -> None:
        dialog._drag_offset = (event.x_root - dialog.winfo_x(), event.y_root - dialog.winfo_y())

    def _drag_dialog(self, dialog: Any, event: Any) -> None:
        offset_x, offset_y = getattr(dialog, "_drag_offset", (0, 0))
        dialog.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _draw_group_dialog_header(self, canvas: Any) -> None:
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.delete("all")
        radius = min(14, height // 2)
        canvas.create_rectangle(0, radius, width, height, fill=BLUE, outline=BLUE)
        canvas.create_rectangle(radius, 0, width - radius, height, fill=BLUE, outline=BLUE)
        canvas.create_arc(
            0, 0, radius * 2, radius * 2, start=90, extent=90, fill=BLUE, outline=BLUE
        )
        canvas.create_arc(
            width - radius * 2,
            0,
            width,
            radius * 2,
            start=0,
            extent=90,
            fill=BLUE,
            outline=BLUE,
        )
        canvas.create_line(0, height - 1, width, height - 1, fill=INK, width=2)
        canvas.create_text(
            24,
            height // 2,
            text="Create New Group",
            fill=INK,
            font=(FONT, 24, "bold"),
            anchor="w",
        )

    def _create_group(self, entry: Any, dialog: Any) -> None:
        name = entry.get().strip()
        if not name or not self.on_create_group(name):
            return
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    def set_groups(
        self, groups: list[dict[str, Any]], current_user: str | None, error: str | None = None
    ) -> None:
        """Render current member lists with an explicit Join or Open action (R9/R10)."""
        for child in self.groups.winfo_children():
            child.destroy()
        self.group_rows.clear()
        if not groups:
            self.group_placeholder = _require_ctk().CTkLabel(
                self.groups, text="No groups yet. Create one to get started!", text_color=INK
            )
            self.group_placeholder.pack(pady=(12, 0))
            return

        for group in groups:
            name = str(group["group_name"])
            members = list(group["members"])
            joined = current_user in members
            row = _require_ctk().CTkFrame(self.groups, fg_color=PINK_LIGHT, corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            _require_ctk().CTkLabel(
                row, text=name, font=(FONT, 15, "bold"), text_color=INK, anchor="w"
            ).pack(fill="x", padx=10, pady=(7, 0))
            _require_ctk().CTkLabel(
                row,
                text="Members: " + ", ".join(members),
                font=(FONT, 11),
                text_color=INK,
                anchor="w",
                wraplength=220,
            ).pack(fill="x", padx=10, pady=(1, 7))
            action = "Open" if joined else "Join"
            command = (
                (lambda group_name=name: self.on_open_group(group_name))
                if joined
                else (lambda group_name=name: self.on_join_group(group_name))
            )
            _require_ctk().CTkButton(
                row,
                text=action,
                width=66,
                height=27,
                corner_radius=13,
                fg_color=BLUE,
                hover_color="#91D5DF",
                text_color=INK,
                command=command,
            ).place(relx=1.0, x=-9, y=9, anchor="ne")
            self.group_rows[name] = row
        if error:
            _require_ctk().CTkLabel(
                self.groups, text=error, text_color="#B22222", wraplength=300
            ).pack(fill="x", padx=8, pady=(6, 2))

    def _draw_card_header(self, canvas: Any, fill: str, title: str, icon_name: str) -> None:
        """Draw a rounded top header without changing the card body."""
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.delete("all")

        def draw_top(color: str, inset: int, radius: int) -> None:
            right = width - inset
            bottom = height - inset
            canvas.create_rectangle(inset, inset + radius, right, bottom, fill=color, outline=color)
            canvas.create_rectangle(
                inset + radius, inset, right - radius, bottom, fill=color, outline=color
            )
            canvas.create_arc(
                inset,
                inset,
                inset + radius * 2,
                inset + radius * 2,
                start=90,
                extent=90,
                fill=color,
                outline=color,
            )
            canvas.create_arc(
                right - radius * 2,
                inset,
                right,
                inset + radius * 2,
                start=0,
                extent=90,
                fill=color,
                outline=color,
            )

        radius = min(14, height // 2)
        draw_top(INK, 0, radius)
        draw_top(fill, 2, max(radius - 2, 1))
        canvas.create_line(0, height - 1, width, height - 1, fill=INK, width=2)
        icon = self.icon_photos.get(icon_name)
        if icon:
            canvas.create_image(24, height // 2, image=icon)
        canvas.create_text(
            44,
            height // 2,
            text=title,
            fill=INK,
            font=(FONT, 18, "bold"),
            anchor="w",
        )

    def set_users(
        self,
        users: list[str],
        current_user: str | None,
        avatars: dict[str, str],
        active_chat: str | None,
    ) -> None:
        for child in self.users.winfo_children():
            child.destroy()
        for user in users:
            label = f"{user} (You)" if user == current_user else user
            avatar_name = avatars.get(user, "rabbit")
            avatar = self.avatars.get(avatar_name, self.avatar)
            button = _require_ctk().CTkButton(
                self.users,
                text=label,
                image=avatar,
                compound="left",
                anchor="w",
                border_spacing=14,
                height=52,
                corner_radius=0,
                font=(FONT, 16),
                fg_color=PINK_LIGHT if user == active_chat else "transparent",
                hover_color=PINK_LIGHT,
                text_color=INK,
                command=lambda name=user: self.on_chat(name),
            )
            button.pack(fill="x", padx=0, pady=(0, 1))
            _require_ctk().CTkFrame(self.users, height=2, fg_color=INK, corner_radius=0).pack(
                fill="x", padx=0, pady=0
            )


class MessageBubble:
    """Reusable message row for a ChatWindow."""

    def __init__(
        self,
        parent: Any,
        sender: str,
        message: str,
        own: bool = False,
        timestamp: str | None = None,
    ) -> None:
        ui = _require_ctk()
        self.own = own
        self.timestamp = timestamp
        self.status_label: Any | None = None
        self.frame = ui.CTkFrame(parent, fg_color="transparent")
        bubble_shell = ui.CTkFrame(
            self.frame,
            fg_color=PINK if own else PINK_LIGHT,
            border_color=INK,
            border_width=2,
            corner_radius=16,
        )
        if not own:
            sender_label = f"{sender} • {timestamp}" if timestamp else sender
            ui.CTkLabel(
                self.frame,
                text=sender_label,
                text_color=INK,
                font=(FONT, 12, "bold"),
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(2, 0))
        bubble = ui.CTkLabel(
            bubble_shell,
            text=message,
            justify="left",
            wraplength=310,
            text_color=INK,
            fg_color="transparent",
        )
        bubble.pack(padx=12, pady=7)
        bubble_shell.pack(anchor="e" if own else "w", padx=8, pady=4)
        if own:
            status_text = f"{timestamp} • Sent" if timestamp else "Sent"
            self.status_label = ui.CTkLabel(
                self.frame,
                text=status_text,
                text_color="#888888",
                font=(FONT, 10),
                anchor="e",
            )
            self.status_label.pack(anchor="e", padx=12, pady=(0, 2))

    def mark_read(self, text: str = "Read ✓✓") -> None:
        """Update the sender status indicator to Read (SP4)."""
        if self.status_label is not None:
            prefix = f"{self.timestamp} • " if self.timestamp else ""
            self.status_label.configure(text=f"{prefix}{text}", text_color="#107C41")


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
        self.on_send = on_send
        self.on_microphone = on_microphone
        self.sticker_icon = _load_icons().get("sticker")
        self.microphone_icon = _load_icons().get("microphone")
        self.send_icon = _load_icons().get("send")
        self.frame = ui.CTkFrame(parent, fg_color=PINK_LIGHT, corner_radius=0)
        self.entry = ui.CTkEntry(
            self.frame,
            placeholder_text="Type a message…",
            height=34,
            fg_color=PINK,
            border_width=0,
            corner_radius=17,
            text_color=INK,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=8)
        self.entry.bind("<Return>", lambda _event: self._submit())
        sticker_button = ui.CTkButton(
            self.frame,
            text="",
            image=self.sticker_icon,
            width=30,
            height=30,
            fg_color="transparent",
            hover_color=PINK,
            text_color=INK,
            command=on_sticker,
        )
        sticker_button.pack(side="left", padx=2)
        self.action_button = ui.CTkButton(
            self.frame,
            text="",
            image=self.microphone_icon,
            width=34,
            height=30,
            fg_color="transparent",
            hover_color=PINK,
            text_color=INK,
            command=self._handle_action,
        )
        self.action_button.pack(side="left", padx=(2, 8))
        self.entry.bind("<KeyRelease>", lambda _event: self._update_action_icon())

    def _submit(self) -> None:
        message = self.entry.get().strip()
        if message:
            self.on_send(message)

    def _handle_action(self) -> None:
        if self.entry.get().strip():
            self._submit()
        else:
            self.on_microphone()

    def _update_action_icon(self) -> None:
        self.action_button.configure(
            image=self.send_icon if self.entry.get().strip() else self.microphone_icon
        )


class ChatRoomTab:
    """Reusable R5/R6 tab containing a scrollable ChatWindow and ChatBoxBar."""

    def __init__(
        self,
        parent: Any,
        room_name: str,
        on_send: Callable[[str], None] | None = None,
        on_sticker: Callable[[], None] | None = None,
        on_microphone: Callable[[], None] | None = None,
    ) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(
            parent,
            fg_color=BACKGROUND,
            border_color=INK,
            border_width=2,
            corner_radius=14,
        )
        self.icon_photos = _load_icon_photos()
        self.header = tk.Canvas(self.frame, height=52, bg=BACKGROUND, highlightthickness=0, bd=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", lambda _event: self._draw_header(room_name))
        self.window = ui.CTkScrollableFrame(self.frame, fg_color=BACKGROUND, corner_radius=10)
        self.window.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.on_send = on_send or self._send_local
        self.box = ChatBoxBar(
            self.frame,
            self._send,
            on_sticker=on_sticker,
            on_microphone=on_microphone,
        )
        self.box.frame.pack(fill="x", padx=4, pady=(0, 4))
        self.room_name = room_name
        self.bubbles: dict[str, MessageBubble] = {}

    def _scroll_to_latest(self) -> None:
        canvas = getattr(self.window, "_parent_canvas", None)
        if canvas is not None:
            self.window.after_idle(lambda: canvas.yview_moveto(1.0))

    def _draw_header(self, title: str) -> None:
        """Draw the rounded chat header without replacing the main frame."""
        width = max(self.header.winfo_width(), 10)
        height = max(self.header.winfo_height(), 10)
        self.header.delete("all")

        def draw_top(color: str, inset: int, radius: int) -> None:
            right = width - inset
            bottom = height - inset
            self.header.create_rectangle(
                inset, inset + radius, right, bottom, fill=color, outline=color
            )
            self.header.create_rectangle(
                inset + radius, inset, right - radius, bottom, fill=color, outline=color
            )
            self.header.create_arc(
                inset,
                inset,
                inset + radius * 2,
                inset + radius * 2,
                start=90,
                extent=90,
                fill=color,
                outline=color,
            )
            self.header.create_arc(
                right - radius * 2,
                inset,
                right,
                inset + radius * 2,
                start=0,
                extent=90,
                fill=color,
                outline=color,
            )

        radius = min(14, height // 2)
        draw_top(INK, 0, radius)
        draw_top(PINK, 2, max(radius - 2, 1))
        self.header.create_line(0, height - 1, width, height - 1, fill=INK, width=2)
        icon = self.icon_photos.get("chat")
        if icon:
            self.header.create_image(25, height // 2, image=icon)
        self.header.create_text(
            46,
            height // 2,
            text=title,
            fill=INK,
            font=(FONT, 20, "bold"),
            anchor="w",
        )

    def _send(self, message: str) -> None:
        if message.strip():
            self.on_send(message)
            self.box.entry.delete(0, "end")
            self.box._update_action_icon()

    def _send_local(self, message: str) -> None:
        """Render a local demonstration message until private transport (R7) is wired."""
        self.add_message("You", message, own=True)

    def add_message(
        self,
        sender: str,
        message: str,
        own: bool = False,
        timestamp: str | None = None,
        message_id: str | None = None,
    ) -> MessageBubble:
        bubble = MessageBubble(self.window, sender, message, own, timestamp=timestamp)
        bubble.frame.pack(fill="x")
        if message_id:
            self.bubbles[message_id] = bubble
        self._scroll_to_latest()
        return bubble

    def mark_message_read(self, message_id: str) -> bool:
        """Mark the message bubble identified by message_id as Read (SP4)."""
        bubble = self.bubbles.get(message_id)
        if bubble is not None:
            bubble.mark_read()
            return True
        return False


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

    def __init__(
        self,
        parent: Any,
        controller: GuiController,
        on_logout: Callable[[], None] | None = None,
    ) -> None:
        ui = _require_ctk()
        self.frame = ui.CTkFrame(parent, fg_color=BACKGROUND)
        self.controller = controller
        self.content = ui.CTkFrame(self.frame, width=900, height=540, fg_color="transparent")
        self.content.place(relx=0.5, rely=0.5, anchor="center")
        self.content.grid_propagate(False)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_columnconfigure(0, weight=0, minsize=330)
        self.content.grid_columnconfigure(1, weight=1)
        self.content.grid_columnconfigure(2, weight=0, minsize=80)
        self.logout_area = ui.CTkFrame(self.content, fg_color="transparent")
        self.logout_area.grid(row=1, column=2, sticky="ne", padx=(18, 0), pady=3)
        self.logout = ui.CTkButton(
            self.logout_area,
            text="",
            image=_load_icons().get("logout"),
            width=54,
            height=54,
            bg_color=BACKGROUND,
            fg_color="#FFC8B7",
            hover_color="#FFC8B7EF",
            text_color=INK,
            corner_radius=8,
            command=on_logout or parent.destroy,
        )
        self.logout.pack()
        ui.CTkLabel(
            self.logout_area,
            text="Logout",
            font=(FONT, 20),
            text_color=INK,
        ).pack(pady=(4, 0))
        self.banner = AnnouncementBanner(self.content)
        self.banner.frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.banner.frame.grid_remove()
        self.sidebar = SidebarPanel(
            self.content,
            self.open_private_room,
            self.controller.create_group,
            self.controller.join_group,
            self.open_group_room,
        )
        self.sidebar.frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tabs = ui.CTkTabview(self.content, fg_color=BACKGROUND)
        self.tabs.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        segmented_button = getattr(self.tabs, "_segmented_button", None)
        if segmented_button is not None:
            segmented_button.grid_remove()
        self.rooms: dict[str, ChatRoomTab] = {}
        self._active_room: str | None = None
        self._rendered_messages: set[str] = set()
        self._unread_messages: dict[str, set[str]] = {}
        self._acknowledged_messages: set[str] = set()
        self.controller.on_private_message = self._handle_incoming_private_message
        self.controller.on_private_message_error = self._handle_private_message_error
        self.controller.on_read_ack = self._handle_read_ack
        self.controller.on_reconnect = self._retry_pending_acks
        self.frame.bind("<Configure>", self._resize_content)
        # Process any messages received prior to MainChatWindow construction
        for peer, msgs in list(self.controller.private_messages.items()):
            for msg in msgs:
                self._handle_incoming_private_message(peer, msg)

    def _resize_content(self, event: Any) -> None:
        """Keep the Figma proportions while still adapting to smaller windows."""
        width = min(920, max(720, int(event.width * 0.82)))
        height = min(560, max(460, int(event.height * 0.78)))
        self.content.configure(width=width, height=height)

    def refresh(self) -> None:
        self.sidebar.set_users(
            self.controller.online_users,
            self.controller.username,
            self.controller.online_avatars,
            self._active_room,
        )
        self.sidebar.set_groups(
            self.controller.groups, self.controller.username, self.controller.group_error
        )
        peers = [user for user in self.controller.online_users if user != self.controller.username]
        if peers and not self.rooms:
            self.open_private_room(peers[0])
        self._retry_pending_acks()

    def _retry_pending_acks(self) -> None:
        """Retry sending pending read receipts for the active room upon reconnect (SP4)."""
        if not getattr(self.controller.session, "is_registered", True):
            return
        if self._active_room and self._unread_messages.get(self._active_room):
            self._ack_unread_in_room(self._active_room)

    def open_private_room(self, user: str, select: bool = True) -> None:
        if user == self.controller.username:
            return
        if user not in self.rooms:
            tab = self.tabs.add(user)
            self.rooms[user] = ChatRoomTab(
                tab,
                user,
                on_send=lambda msg, peer=user: self._send_private_message(peer, msg),
            )
            self.rooms[user].frame.pack(fill="both", expand=True)
        if select:
            self.tabs.set(user)
            self._active_room = user
            self._ack_unread_in_room(user)
            self.sidebar.set_users(
                self.controller.online_users,
                self.controller.username,
                self.controller.online_avatars,
                self._active_room,
            )
        segmented_button = getattr(self.tabs, "_segmented_button", None)
        if segmented_button is not None:
            segmented_button.grid_forget()

    def open_group_room(self, group_name: str) -> None:
        """Open a joined Group Room; group message transport is introduced in R11."""
        if group_name not in self.rooms:
            tab = self.tabs.add(group_name)
            self.rooms[group_name] = ChatRoomTab(tab, group_name, on_send=lambda _message: None)
            self.rooms[group_name].frame.pack(fill="both", expand=True)
        self.tabs.set(group_name)
        self._active_room = group_name

    def _send_private_message(self, peer: str, message: str) -> None:
        msg_id = self.controller.send_private_message(peer, message)
        if msg_id is None:
            # Fallback for offline or mock sessions without live server echo
            current_time = datetime.now().strftime("%H:%M")
            self.rooms[peer].add_message("You", message, own=True, timestamp=current_time)

    def _handle_incoming_private_message(
        self, peer: str, payload: PrivateMessageServerPayload
    ) -> None:
        if payload.meta.message_id in self._rendered_messages:
            return
        self._rendered_messages.add(payload.meta.message_id)
        if peer not in self.rooms:
            self.open_private_room(peer, select=False)
        own = payload.sender == self.controller.username
        display_sender = "You" if own else payload.sender
        self.rooms[peer].add_message(
            sender=display_sender,
            message=payload.message,
            own=own,
            timestamp=payload.meta.timestamp,
            message_id=payload.meta.message_id,
        )
        if not own:
            # Check user's actively selected room rather than relying on tabs.get()
            if self._active_room == peer:
                self._send_read_ack(payload.meta.message_id, peer)
            else:
                self._unread_messages.setdefault(peer, set()).add(payload.meta.message_id)

    def _send_read_ack(self, message_id: str, target: str) -> None:
        if message_id in self._acknowledged_messages:
            if target in self._unread_messages:
                self._unread_messages[target].discard(message_id)
            return
        sent = self.controller.send_read_ack(message_id, target)
        if sent:
            self._acknowledged_messages.add(message_id)
            if target in self._unread_messages:
                self._unread_messages[target].discard(message_id)
        else:
            self._unread_messages.setdefault(target, set()).add(message_id)

    def _ack_unread_in_room(self, room_name: str) -> None:
        unreads = list(self._unread_messages.get(room_name, set()))
        for mid in unreads:
            self._send_read_ack(mid, room_name)

    def _handle_read_ack(self, message_id: str, reader: str) -> None:
        if reader in self.rooms:
            self.rooms[reader].mark_message_read(message_id)

    def _handle_private_message_error(self, target: str, error: str) -> None:
        if target and target in self.rooms:
            current_time = datetime.now().strftime("%H:%M")
            self.rooms[target].add_message(
                sender="System",
                message=f"⚠️ {error}",
                own=False,
                timestamp=current_time,
            )


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
        self.root.resizable(False, False)
        self.root.configure(fg_color=BACKGROUND)
        self.controller = GuiController(session or ClientSession(), self._refresh)
        self.login = LoginView(self.root, self._connect, host=host, port=port)
        self.login.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.main: MainChatWindow | None = None
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._poll()

    def _connect(self, host: str, port: str, username: str, avatar: str) -> None:
        self.controller.avatar = avatar
        self.controller.connect_and_register(host, port, username, avatar)
        self.login.show_error(self.controller.login_error)

    def _poll(self) -> None:
        self.controller.poll_events()
        self.root.after(75, self._poll)

    def _refresh(self) -> None:
        if self.controller.username and self.main is None:
            self.login.frame.place_forget()
            self.main = MainChatWindow(self.root, self.controller, on_logout=self._close)
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
