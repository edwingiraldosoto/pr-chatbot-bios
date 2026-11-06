"""Embeddings locales con fastembed (ONNX en CPU, sin torch) y un sustituto para pruebas.

Por qué fastembed y no sentence-transformers: con los mismos vectores, la instalación pasa
de 14 min y 1,6 GB a 3 min y 456 MB (medición en ``specs/research.md``, R-04).

El modelo por defecto, ``intfloat/multilingual-e5-small``, exige prefijos: ``passage: ``
para los fragmentos y ``query: `` para la pregunta. Sin ellos la calidad de la búsqueda cae
de forma notable y sin ningún error visible, por eso los aplica este módulo y no quien lo
llama.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from mesa_ayuda.logging_setup import get_logger

if TYPE_CHECKING:
    from fastembed import TextEmbedding

log = get_logger(__name__)


class EmbeddingModelError(Exception):
    """El modelo configurado no existe o no se pudo descargar/cargar."""


class Embedder(Protocol):
    """Convierte textos en vectores normalizados (contrato en ``specs/contracts``)."""

    @property
    def model_id(self) -> str:
        """Identificador del modelo; si cambia, el índice se reconstruye."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectores de fragmentos de documentos."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Vector de una pregunta."""
        ...


@dataclass(frozen=True)
class _CustomModel:
    dim: int
    model_file: str = "onnx/model.onnx"


#: Modelos que fastembed no trae en su catálogo y el proyecto registra. El archivo ONNX
#: sale del repositorio oficial del modelo en Hugging Face.
CUSTOM_MODELS: dict[str, _CustomModel] = {
    "intfloat/multilingual-e5-small": _CustomModel(dim=384),
}

_E5_FAMILY = re.compile(r"(^|/)(multilingual-)?e5-", re.IGNORECASE)


def _prefixes(model_name: str) -> tuple[str, str]:
    """(prefijo de documento, prefijo de pregunta) que exige la familia del modelo."""
    if _E5_FAMILY.search(model_name):
        return "passage: ", "query: "
    return "", ""


class LocalEmbedder:
    """Embeddings con fastembed. El modelo se carga en el primer uso (descarga única)."""

    def __init__(self, model_name: str, cache_dir: Path, *, batch_size: int = 32) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._document_prefix, self._query_prefix = _prefixes(model_name)
        self._model: TextEmbedding | None = None
        self._load_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        """Nombre del modelo, tal como se configuró."""
        return self._model_name

    def load(self) -> None:
        """Carga (y si hace falta descarga) el modelo; se puede llamar al arrancar."""
        self._get_model()

    def _get_model(self) -> TextEmbedding:
        with self._load_lock:
            if self._model is None:
                self._model = self._create_model()
            return self._model

    def _create_model(self) -> TextEmbedding:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        supported = {item["model"] for item in TextEmbedding.list_supported_models()}
        custom = CUSTOM_MODELS.get(self._model_name)
        if custom is not None and self._model_name not in supported:
            TextEmbedding.add_custom_model(
                model=self._model_name,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=self._model_name),
                dim=custom.dim,
                model_file=custom.model_file,
            )
        elif self._model_name not in supported:
            options = ", ".join(sorted(supported | set(CUSTOM_MODELS)))
            raise EmbeddingModelError(
                f"EMBEDDING_MODEL (config.yaml: embeddings.model): el modelo "
                f"'{self._model_name}' no está disponible. Opciones: {options}."
            )

        log.info("embedding_model_loading", model=self._model_name, cache=str(self._cache_dir))
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            model = TextEmbedding(self._model_name, cache_dir=str(self._cache_dir))
        except Exception as error:  # red caída, disco lleno, archivo corrupto, ...
            raise EmbeddingModelError(
                f"No se pudo cargar el modelo de embeddings '{self._model_name}' "
                f"({type(error).__name__}: {error}). La primera ejecución necesita internet "
                f"para descargarlo a {self._cache_dir}."
            ) from error
        log.info("embedding_model_ready", model=self._model_name)
        return model

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._get_model().embed(list(texts), batch_size=self._batch_size)
        return [[float(value) for value in vector] for vector in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectores de fragmentos, con el prefijo de documento del modelo."""
        return self._embed([self._document_prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        """Vector de la pregunta, con el prefijo de consulta del modelo."""
        return self._embed([self._query_prefix + text])[0]


_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


class FakeEmbedder:
    """Embeddings deterministas por bolsa de palabras, para pruebas sin red ni modelo.

    Cada palabra (en minúsculas y sin tildes) suma 1 en una posición derivada de su hash, y
    el vector se normaliza. Textos que comparten palabras quedan cerca, lo suficiente para
    probar recuperación, umbral y citas sin descargar 465 MB.
    """

    def __init__(self, dimension: int = 256, model_id: str = "fake-bow-256") -> None:
        self.dimension = dimension
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        """Identificador fijo; cambiarlo simula un cambio de modelo."""
        return self._model_id

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in _WORD.findall(_fold(text)):
            digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
            vector[int.from_bytes(digest, "little") % self.dimension] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0], norm = 1.0, 1.0  # texto sin palabras: vector válido, lejos de todo
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Un vector por texto."""
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Vector de la pregunta (sin prefijos: no hay modelo que los necesite)."""
        return self._vector(text)
