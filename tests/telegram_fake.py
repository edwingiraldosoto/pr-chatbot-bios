"""Telegram falso para probar los manejadores sin red.

Arma una ``Application`` real de python-telegram-bot con un transporte que no sale a
internet: responde ``getMe``, ``sendMessage`` y ``sendChatAction`` y guarda cada llamada.
Así las pruebas pasan por el mismo camino que en producción (filtros, grupos,
``ApplicationHandlerStop``) y verifican lo que el bot envía.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest, RequestData

from mesa_ayuda.bot.handlers import AnyApplication, BotHandlers

BOT_USER = {"id": 42, "is_bot": True, "first_name": "Mesa de ayuda", "username": "mesa_bot"}


class FakeRequest(BaseRequest):
    """Transporte que contesta como la API de Telegram y registra las llamadas."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls
        self._message_ids = itertools.count(1000)

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def read_timeout(self) -> float | None:
        return None

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: Any = None,
        write_timeout: Any = None,
        connect_timeout: Any = None,
        pool_timeout: Any = None,
    ) -> tuple[int, bytes]:
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((name, params))
        result: Any = True
        if name == "getMe":
            result = BOT_USER
        elif name == "sendMessage":
            result = {
                "message_id": next(self._message_ids),
                "date": 0,
                "chat": {"id": int(params["chat_id"]), "type": "private"},
                "from": BOT_USER,
                "text": params["text"],
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()


class TelegramHarness:
    """Envía mensajes simulados a los manejadores y devuelve lo que el bot contestó."""

    def __init__(self, handlers: BotHandlers) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.app: AnyApplication = (
            Application.builder()
            .token("123456:TEST")
            .request(FakeRequest(self.calls))
            .get_updates_request(FakeRequest(self.calls))
            .concurrent_updates(True)
            .build()
        )
        handlers.register(self.app)
        self._ids = itertools.count(1)

    async def __aenter__(self) -> TelegramHarness:
        await self.app.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.app.shutdown()

    def sent(self, chat_id: int | None = None) -> list[str]:
        """Textos enviados (a un chat, si se indica), en orden."""
        return [
            params["text"]
            for name, params in self.calls
            if name == "sendMessage" and (chat_id is None or int(params["chat_id"]) == chat_id)
        ]

    def actions(self) -> list[str]:
        """Acciones de chat enviadas (``typing``)."""
        return [params["action"] for name, params in self.calls if name == "sendChatAction"]

    async def send(
        self,
        user_id: int,
        text: str | None = None,
        *,
        chat_type: str = "private",
        edited: bool = False,
        **content: Any,
    ) -> list[str]:
        """Procesa un mensaje y devuelve los textos que el bot respondió a ese chat."""
        chat_id = user_id if chat_type == "private" else -100
        before = len(self.sent(chat_id))
        message: dict[str, Any] = {
            "message_id": next(self._ids),
            "date": 0,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": "Usuario"},
            **content,
        }
        if text is not None:
            message["text"] = text
            if text.startswith("/"):
                command = text.split()[0]
                message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
        if edited:
            message["edit_date"] = 1
        key = "edited_message" if edited else "message"
        update = Update.de_json({"update_id": next(self._ids), key: message}, self.app.bot)
        await self.app.process_update(update)
        return self.sent(chat_id)[before:]
