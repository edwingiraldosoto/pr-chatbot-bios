"""Pruebas del fragmentador jerárquico (T-10; RF-02, CA-02.1 a CA-02.3)."""

from __future__ import annotations

import itertools
import random
import re

import pytest

from mesa_ayuda.ingest.chunker import HEADING_SEPARATOR, Chunk, chunk_sections
from mesa_ayuda.ingest.loaders import Section

WORDS = (  # noqa: SIM905 - una frase se lee mejor que una lista de 27 palabras
    "el colaborador debe solicitar vacaciones con quince días de anticipación en el portal "
    "y su jefe aprueba la solicitud en cinco días hábiles según la política vigente"
).split()


def random_text(seed: int, paragraphs: int) -> str:
    rng = random.Random(seed)
    result = []
    for _ in range(paragraphs):
        sentences = []
        for _ in range(rng.randint(1, 8)):
            sentence = " ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 30)))
            sentences.append(sentence.capitalize() + rng.choice([".", ".", "?", ":"]))
        result.append(" ".join(sentences))
    return "\n\n".join(result)


def body(chunk: Chunk, section: Section) -> str:
    header = HEADING_SEPARATOR.join(section.heading_path)
    return chunk.text.removeprefix(f"{header}\n") if header else chunk.text


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize(("size", "overlap"), [(800, 150), (300, 60), (200, 0), (1200, 400)])
def test_chunks_never_exceed_size(seed: int, size: int, overlap: int) -> None:
    # CA-02.1 sobre textos aleatorios, con encabezado incluido en el tamaño.
    section = Section(text=random_text(seed, 6), title="Días", heading_path=("Política", "Días"))

    chunks = chunk_sections([section], size, overlap)

    assert chunks
    assert all(len(chunk.text) <= size for chunk in chunks)
    assert all(chunk.text.startswith("Política > Días\n") for chunk in chunks)


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize(("size", "overlap"), [(300, 60), (500, 150), (250, 100)])
def test_overlap_is_a_shared_suffix_of_the_previous_chunk(
    seed: int, size: int, overlap: int
) -> None:
    # Palabras únicas: así lo compartido entre dos fragmentos se identifica sin ambigüedad.
    rng = random.Random(seed)
    sentences = [
        " ".join(f"w{seed}x{n}x{i}" for i in range(rng.randint(3, 25))) + "." for n in range(40)
    ]
    text = "\n\n".join(" ".join(sentences[i : i + 4]) for i in range(0, 40, 4))

    chunks = chunk_sections([Section(text=text)], size, overlap)

    overlapping_pairs = 0
    for previous, current in itertools.pairwise(chunks):
        first = words(current.text)[0]
        previous_words = words(previous.text)
        if first in previous_words:
            overlapping_pairs += 1
            shared = " ".join(previous_words[previous_words.index(first) :])
            assert len(shared) <= overlap
            assert " ".join(words(current.text)).startswith(shared)
    # La mayoría de los cortes debe llevar solapamiento (no todos: a veces la pieza
    # siguiente es tan larga que no queda espacio para repetir texto).
    assert overlapping_pairs >= (len(chunks) - 1) // 2 > 0

    rebuilt: list[str] = []
    for chunk in chunks:
        for word in words(chunk.text):
            if not rebuilt or word not in rebuilt[-60:]:
                rebuilt.append(word)
    assert rebuilt == words(text)  # nada se pierde ni se desordena


@pytest.mark.parametrize("seed", range(10))
def test_no_text_is_lost_or_reordered(seed: int) -> None:
    text = random_text(seed, 5)
    chunks = chunk_sections([Section(text=text)], 300, 0)

    assert [w for chunk in chunks for w in words(chunk.text)] == words(text)


def test_words_are_never_cut_in_the_middle() -> None:
    text = random_text(3, 6)
    vocabulary = {w.strip(".?:").lower() for w in words(text)}

    chunks = chunk_sections([Section(text=text)], 250, 50)

    for chunk in chunks:
        for word in words(chunk.text):
            assert word.strip(".?:").lower() in vocabulary


def test_short_section_stays_whole_with_header() -> None:
    section = Section(
        text="Cada colaborador tiene 15 días hábiles.",
        title="2. Días de vacaciones",
        heading_path=("Política de vacaciones", "2. Días de vacaciones"),
    )

    [chunk] = chunk_sections([section], 800, 150)

    assert chunk.text == (
        "Política de vacaciones > 2. Días de vacaciones\nCada colaborador tiene 15 días hábiles."
    )
    assert (chunk.section, chunk.page, chunk.index) == ("2. Días de vacaciones", 0, 0)


def test_prefers_paragraph_boundaries() -> None:
    # CA-02.2: dos párrafos que no caben juntos se separan por el párrafo, no por la mitad.
    first = "Primer párrafo sobre los topes de alimentación y hospedaje. " * 3
    second = "Segundo párrafo sobre los anticipos para viajes largos. " * 3
    section = Section(text=f"{first.strip()}\n\n{second.strip()}")

    chunks = chunk_sections([section], 250, 0)

    assert [chunk.text for chunk in chunks] == [first.strip(), second.strip()]


def test_paragraph_break_survives_after_the_overlap() -> None:
    # Regresión: el texto repetido se unía con un espacio al párrafo siguiente y dos temas
    # distintos quedaban pegados en la misma línea ("...equipos. HORARIO El horario...").
    first = "Los días remotos se registran en el portal. Los lunes son presenciales."
    second = "HORARIO\nEl horario núcleo es de 9:00 a 12:00 y de 2:00 a 4:00."

    chunks = chunk_sections([Section(text=f"{first}\n\n{second}")], 100, 40)

    assert chunks[1].text == f"Los lunes son presenciales.\n\n{second}"


def test_long_paragraph_is_split_by_lines_before_sentences() -> None:
    # Texto de PDF: líneas sin líneas en blanco entre ellas.
    lines = [f"Paso {n}: realiza la acción número {n} del procedimiento." for n in range(1, 13)]
    chunks = chunk_sections([Section(text="\n".join(lines), page=3)], 200, 0)

    for chunk in chunks:
        assert all(line in lines for line in chunk.text.split("\n"))
        assert chunk.page == 3


def test_sections_do_not_overlap_and_keep_metadata() -> None:
    sections = [
        Section(text="Texto de la página uno. " * 20, page=1),
        Section(text="Texto de la página dos. " * 20, page=2),
    ]

    chunks = chunk_sections(sections, 200, 80)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    first_of_page_two = next(chunk for chunk in chunks if chunk.page == 2)
    assert "uno" not in first_of_page_two.text
    assert {chunk.page for chunk in chunks} == {1, 2}


def test_overlap_starts_at_a_word_boundary() -> None:
    text = " ".join(f"palabra{n}" for n in range(200))

    chunks = chunk_sections([Section(text=text)], 200, 50)

    for chunk in chunks[1:]:
        assert chunk.text.split(" ")[0] in text.split(" ")


def test_word_longer_than_chunk_is_cut_as_last_resort() -> None:
    url = "https://intranet.example/" + "x" * 500

    chunks = chunk_sections([Section(text=f"Ver {url} para más detalles.")], 200, 20)

    assert all(len(chunk.text) <= 200 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks).replace(" ", "").count("x") >= 500


def test_very_long_heading_is_truncated() -> None:
    section = Section(text="Contenido.", title="t", heading_path=("Título " * 100,))

    [chunk] = chunk_sections([section], 400, 0)

    assert len(chunk.text) <= 400
    assert chunk.text.endswith("Contenido.")


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError, match="chunk_overlap"):
        chunk_sections([Section(text="x")], 100, 100)
