"""Pruebas de las guardas de acceso, límite y validación (T-27; RF-07, CA-R5.3, CA-R5.4)."""

from __future__ import annotations

import pytest

from mesa_ayuda.guards import (
    AccessControl,
    DuplicateFilter,
    RateDecision,
    RateLimiter,
    clean_question,
    is_private_chat,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [("private", True), ("group", False), ("supergroup", False), ("channel", False), (None, False)],
)
def test_only_private_chats(chat_type: str | None, expected: bool) -> None:
    # CA-07.4
    assert is_private_chat(chat_type) is expected


def test_allowed_and_admins_are_authorized_and_only_admins_administer() -> None:
    # CA-07.1
    access = AccessControl(allowed=[1, 2], admins=[9])

    assert [access.is_authorized(u) for u in (1, 2, 9, 3)] == [True, True, True, False]
    assert [access.is_admin(u) for u in (1, 9)] == [False, True]


def test_empty_lists_fail_closed() -> None:
    # CA-07.3
    access = AccessControl(allowed=[], admins=[])

    assert not access.is_authorized(1572098300)


def test_rate_limit_allows_up_to_the_limit_then_warns_once() -> None:
    # CA-R5.4 y escenario 10 del quickstart: aviso a partir del mensaje 11.
    limiter = RateLimiter(10, clock=Clock())

    decisions = [limiter.check(1) for _ in range(13)]

    assert decisions[:10] == [RateDecision.ALLOW] * 10
    assert decisions[10:] == [RateDecision.WARN, RateDecision.DROP, RateDecision.DROP]


def test_window_slides_and_frees_one_message_at_a_time() -> None:
    clock = Clock()
    limiter = RateLimiter(2, clock=clock)
    limiter.check(1)  # t=0
    clock.now += 30
    limiter.check(1)  # t=30
    assert limiter.check(1) is RateDecision.WARN

    clock.now += 30  # t=60: el primero sale de la ventana
    assert limiter.check(1) is RateDecision.ALLOW
    assert limiter.check(1) is RateDecision.WARN  # ventana nueva, aviso nuevo


def test_dropped_messages_do_not_extend_the_wait() -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock=clock)
    limiter.check(1)
    limiter.check(1)  # aviso
    for _ in range(5):
        clock.now += 10
        assert limiter.check(1) is RateDecision.DROP  # no cuentan

    clock.now += 10  # 60 s desde el único aceptado
    assert limiter.check(1) is RateDecision.ALLOW


def test_users_have_independent_limits() -> None:
    limiter = RateLimiter(1, clock=Clock())

    assert limiter.check(1) is RateDecision.ALLOW
    assert limiter.check(1) is RateDecision.WARN
    assert limiter.check(2) is RateDecision.ALLOW


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="al menos 1"):
        RateLimiter(0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  ¿Cómo pido vacaciones?  ", "¿Cómo pido vacaciones?"),
        ("", None),
        ("   \n\t ", None),
        (None, None),
    ],
)
def test_clean_question(text: str | None, expected: str | None) -> None:
    # CA-R5.3: texto vacío se trata igual que un mensaje sin texto.
    assert clean_question(text) == expected


# --- Copias repetidas (T-49; CA-R5.7) -------------------------------------------------------


def test_same_text_within_the_window_is_a_duplicate() -> None:
    clock = Clock()
    duplicates = DuplicateFilter(5, clock=clock)

    assert duplicates.is_duplicate(1, "hola") is False
    clock.now += 0.1
    assert duplicates.is_duplicate(1, "  HOLA ") is True  # sin espacios ni mayúsculas
    assert duplicates.is_duplicate(1, "Hola\n") is True


def test_window_counts_from_the_first_copy() -> None:
    # Una ráfaga larga no alarga la ventana: a los 5 s de la primera, se acepta de nuevo.
    clock = Clock()
    duplicates = DuplicateFilter(5, clock=clock)
    duplicates.is_duplicate(1, "hola")
    for _ in range(4):
        clock.now += 1
        assert duplicates.is_duplicate(1, "hola") is True

    clock.now += 1
    assert duplicates.is_duplicate(1, "hola") is False


def test_different_text_or_user_is_not_a_duplicate() -> None:
    duplicates = DuplicateFilter(5, clock=Clock())
    duplicates.is_duplicate(1, "¿Cuántos días de vacaciones?")

    assert duplicates.is_duplicate(1, "¿Y se acumulan?") is False
    assert duplicates.is_duplicate(2, "¿Y se acumulan?") is False
    # Solo se compara con el último: volver a la primera pregunta ya no es una copia.
    assert duplicates.is_duplicate(1, "¿Cuántos días de vacaciones?") is False


@pytest.mark.parametrize("text", [None, "", "   "])
def test_messages_without_text_are_never_duplicates(text: str | None) -> None:
    duplicates = DuplicateFilter(5, clock=Clock())

    assert duplicates.is_duplicate(1, text) is False
    assert duplicates.is_duplicate(1, text) is False


def test_zero_window_disables_the_filter() -> None:
    duplicates = DuplicateFilter(0, clock=Clock())

    assert [duplicates.is_duplicate(1, "hola") for _ in range(3)] == [False, False, False]


def test_negative_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="negativo"):
        DuplicateFilter(-1)
