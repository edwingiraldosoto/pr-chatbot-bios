# Imagen del bot de mesa de ayuda (CA-R2.4).
#
#   docker compose up -d --build      # construir y arrancar
#   docker compose logs -f bot        # ver los logs JSON
#
# Los secretos llegan por variables de entorno (env_file: .env en compose.yaml); el .env no
# se copia a la imagen. El índice, la base SQLite y el modelo de embeddings viven en el
# volumen /app/data, así que sobreviven a reconstrucciones de la imagen.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Primero las dependencias: esta capa se reutiliza mientras pyproject.toml no cambie.
COPY pyproject.toml README.md ./
RUN mkdir -p src/mesa_ayuda && touch src/mesa_ayuda/__init__.py \
    && pip install . \
    && pip uninstall -y mesa-ayuda-rag \
    && rm -rf src

# Después el código y los archivos de configuración versionados.
COPY src ./src
RUN pip install --no-deps .
COPY config.yaml ./
COPY prompts ./prompts
COPY scripts ./scripts
COPY knowledge_base ./knowledge_base

# Usuario sin privilegios; solo /app/data es escribible.
RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data
USER bot
VOLUME ["/app/data"]

CMD ["python", "-m", "mesa_ayuda"]
