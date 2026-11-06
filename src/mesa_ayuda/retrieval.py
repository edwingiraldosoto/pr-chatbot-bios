"""Recuperación de fragmentos: top-K por similitud coseno y umbral de relevancia (RF-03, RF-04).

El índice devuelve los ``top_k`` fragmentos más parecidos con su similitud
(``1 − distancia coseno``). Solo los que alcanzan ``SIMILARITY_THRESHOLD`` se consideran
relevantes; si ninguno lo alcanza, el servicio de respuesta contesta "no encontrado" sin
consultar al modelo de lenguaje (CA-04.1).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mesa_ayuda.embeddings import Embedder
from mesa_ayuda.vectorstore import SearchHit

SearchFn = Callable[[Sequence[float], int], list[SearchHit]]


@dataclass(frozen=True)
class Retrieval:
    """Resultado de buscar una pregunta en el índice.

    Attributes:
        question: Texto que se buscó (ya reformulado, si hubo reformulación).
        hits: Los ``top_k`` fragmentos recuperados, de mayor a menor similitud.
        relevant: Los de ``hits`` con similitud mayor o igual al umbral.
        threshold: Umbral aplicado.
    """

    question: str
    hits: list[SearchHit]
    relevant: list[SearchHit]
    threshold: float

    @property
    def best_score(self) -> float | None:
        """Mejor similitud obtenida, o ``None`` si el índice no devolvió nada."""
        return self.hits[0].score if self.hits else None

    @property
    def found(self) -> bool:
        """``True`` si al menos un fragmento alcanzó el umbral."""
        return bool(self.relevant)


class Retriever:
    """Convierte la pregunta en vector y busca los fragmentos más parecidos.

    Args:
        embedder: Modelo de embeddings (el mismo que se usó al indexar).
        search: Función de búsqueda del índice, normalmente ``Indexer.search``.
        top_k: Fragmentos a recuperar (``RETRIEVAL_TOP_K``).
        threshold: Similitud mínima para considerar relevante un fragmento
            (``SIMILARITY_THRESHOLD``).
    """

    def __init__(
        self, embedder: Embedder, search: SearchFn, *, top_k: int, threshold: float
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k debe ser al menos 1")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold debe estar entre 0 y 1")
        self._embedder = embedder
        self._search = search
        self.top_k = top_k
        self.threshold = threshold

    def retrieve(self, question: str) -> Retrieval:
        """Busca la pregunta y separa los fragmentos que superan el umbral.

        Es una función bloqueante (calcula el embedding en CPU); desde código asíncrono se
        llama con ``asyncio.to_thread``.
        """
        embedding = self._embedder.embed_query(question)
        hits = sorted(self._search(embedding, self.top_k), key=lambda hit: -hit.score)
        relevant = [hit for hit in hits if hit.score >= self.threshold]
        return Retrieval(question=question, hits=hits, relevant=relevant, threshold=self.threshold)
