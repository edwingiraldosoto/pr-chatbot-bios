"""Prueba con el modelo real de embeddings (descarga ~465 MB la primera vez).

No corre por defecto (constitución, principio V: la suite no usa red). Para correrla:
``RUN_MODEL_TESTS=1 python -m pytest tests/integration/test_embeddings_model.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mesa_ayuda.embeddings import LocalEmbedder

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_TESTS") != "1", reason="usa el modelo real; RUN_MODEL_TESTS=1"
)

MODEL_CACHE = Path(__file__).resolve().parents[2] / "data" / "models"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_e5_small_ranks_the_relevant_passage_first() -> None:
    embedder = LocalEmbedder("intfloat/multilingual-e5-small", MODEL_CACHE)

    query = embedder.embed_query("¿Cuántos días de vacaciones tengo?")
    passages = embedder.embed_documents(
        [
            "Cada colaborador tiene derecho a 15 días hábiles de vacaciones por año.",
            "El auxilio de conectividad es de $85.000 mensuales.",
            "Para restablecer la contraseña ingresa al portal de claves.",
        ]
    )

    scores = [cosine(query, passage) for passage in passages]
    assert len(query) == 384
    assert scores[0] == max(scores)
