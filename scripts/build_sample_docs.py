"""Genera los documentos de ejemplo binarios de ``knowledge_base/`` (PDF y DOCX).

El ``.md`` y el ``.txt`` se editan a mano. Este script deja reproducibles los otros dos:
``python -m scripts.build_sample_docs``. El contenido es ficticio (empresa NubeVerde S.A.S.)
y se redactó con ayuda de IA (ver ``docs/USO_IA.md``).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import docx

from scripts.simple_pdf import write_pdf

KNOWLEDGE_BASE = Path(__file__).resolve().parents[1] / "knowledge_base"

REIMBURSEMENT_PAGES = [
    """POLÍTICA DE REEMBOLSO DE GASTOS
NubeVerde S.A.S. (empresa ficticia) - Código FIN-POL-002 - Vigente desde el 1 de enero de 2026
Responsable: Dirección Financiera

1. OBJETIVO
Definir qué gastos de viaje y de representación pagados por el colaborador con su propio dinero se le devuelven, con qué topes y cómo se solicitan.

2. GASTOS REEMBOLSABLES
Se reembolsan los gastos de alimentación, hospedaje, transporte local (taxi o aplicaciones de transporte), peajes, parqueaderos y combustible de vehículo propio, siempre que estén relacionados con una actividad de trabajo aprobada por el jefe inmediato.

3. GASTOS NO REEMBOLSABLES
No se reembolsan bebidas alcohólicas, multas de tránsito, propinas superiores al 10 %, gastos de acompañantes, minibar, lavandería en viajes de menos de 5 días ni compras personales.""",
    """4. TOPES POR CONCEPTO
Alimentación: hasta $60.000 por día en viajes nacionales.
Hospedaje: hasta $280.000 por noche en ciudades principales y hasta $200.000 por noche en otros municipios.
Transporte local: hasta $90.000 por día. Se prefiere el transporte por aplicación con factura electrónica.
Combustible de vehículo propio: $1.100 por kilómetro recorrido, según la distancia que reporte el mapa del recorrido.
Los gastos que superen el tope solo se reembolsan con aprobación escrita de la Dirección Financiera.

5. ANTICIPOS
Para viajes de más de 2 días se puede pedir un anticipo de hasta el 70 % del presupuesto estimado. El anticipo se legaliza con el mismo formulario de reembolso y el saldo a favor de la empresa se descuenta de la nómina siguiente.""",
    """6. CÓMO SOLICITAR EL REEMBOLSO
Paso 1: Reúne las facturas electrónicas a nombre de NubeVerde S.A.S., NIT 900.000.000-1. No se aceptan tiquetes de máquina registradora ni cuentas de cobro sin factura.
Paso 2: Diligencia el formulario de reembolso en el portal Mi Nube, menú "Finanzas" > "Reembolsos", y adjunta las facturas en PDF.
Paso 3: Envía la solicitud a más tardar 30 días calendario después de la fecha del gasto. Las solicitudes posteriores se rechazan.
Paso 4: Tu jefe inmediato aprueba la solicitud y luego la revisa Contabilidad.

7. PAGO
El reembolso aprobado se paga por transferencia a la cuenta de nómina en un plazo de 10 días hábiles después de la aprobación de Contabilidad.

8. CONTACTO
Dudas sobre reembolsos: reembolsos@nubeverde.example o extensión 2210.""",
]


def build_reimbursement_pdf(target: Path) -> None:
    write_pdf(target, REIMBURSEMENT_PAGES)


def build_it_support_docx(target: Path) -> None:
    document = docx.Document()
    props = document.core_properties
    props.author = "NubeVerde S.A.S."
    props.title = "Guía de soporte de TI"
    props.created = props.modified = dt.datetime(2026, 1, 15, tzinfo=dt.UTC)

    document.add_heading("Guía de soporte de TI", level=0)
    document.add_paragraph(
        "NubeVerde S.A.S. (empresa ficticia). Código TI-GUI-001. Versión 2, vigente desde el "
        "15 de enero de 2026. Responsable: Coordinación de Tecnología."
    )

    document.add_heading("1. Canales de la mesa de servicio", level=1)
    document.add_paragraph(
        "La mesa de servicio de TI atiende de lunes a viernes de 7:00 a. m. a 7:00 p. m. y los "
        "sábados de 8:00 a. m. a 12:00 m. Los canales son el portal Mi Nube (menú "
        '"Tecnología" > "Nuevo caso"), el correo soporte@nubeverde.example y la extensión '
        "4357. Fuera de horario, solo los incidentes de prioridad P1 se atienden por la línea "
        "de guardia 601 555 0199."
    )

    document.add_heading("2. Prioridades y tiempos de respuesta", level=1)
    document.add_paragraph("Cada caso recibe una prioridad según su impacto:")
    rows = [
        ("Prioridad", "Ejemplo", "Primera respuesta", "Solución objetivo"),
        ("P1 Crítica", "Caída del ERP o de la red de una sede", "15 minutos", "4 horas"),
        ("P2 Alta", "Un área completa sin correo", "1 hora", "8 horas"),
        ("P3 Media", "Falla de un equipo individual", "4 horas", "2 días hábiles"),
        ("P4 Baja", "Solicitud de software o de un accesorio", "1 día hábil", "5 días hábiles"),
    ]
    table = document.add_table(rows=len(rows), cols=4)
    table.style = "Table Grid"
    for row, values in zip(table.rows, rows, strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value

    document.add_heading("3. Restablecer la contraseña", level=1)
    document.add_paragraph(
        "La contraseña de la cuenta corporativa vence cada 90 días y debe tener al menos 12 "
        "caracteres, con mayúsculas, minúsculas y números."
    )
    for step in (
        "Paso 1: Abre https://clave.nubeverde.example desde cualquier navegador.",
        "Paso 2: Escribe tu usuario corporativo y elige la opción Olvidé mi contraseña.",
        "Paso 3: Ingresa el código de 6 dígitos que llega a tu celular registrado.",
        "Paso 4: Define la nueva contraseña. No puede repetir ninguna de las últimas 5.",
    ):
        document.add_paragraph(step)
    document.add_paragraph(
        "Si tu cuenta queda bloqueada tras 5 intentos fallidos, se desbloquea sola a los 30 "
        "minutos o puedes pedir el desbloqueo a la mesa de servicio."
    )

    document.add_heading("4. Equipos y accesorios", level=1)
    document.add_paragraph(
        "Los portátiles se renuevan cada 4 años. Para pedir un accesorio (mouse, teclado, "
        "diadema o segundo monitor) crea un caso P4 con la aprobación de tu jefe. Un equipo "
        "perdido o robado se reporta de inmediato como P1 para bloquearlo de forma remota."
    )

    document.add_heading("5. Software permitido", level=1)
    document.add_paragraph(
        "Solo se puede instalar software del catálogo publicado en Mi Nube. Cualquier otro "
        "programa requiere un caso P4 y la aprobación de Seguridad de la Información, que "
        "responde en máximo 3 días hábiles."
    )
    document.save(str(target))


def main() -> None:
    KNOWLEDGE_BASE.mkdir(exist_ok=True)
    build_reimbursement_pdf(KNOWLEDGE_BASE / "reembolso_gastos.pdf")
    build_it_support_docx(KNOWLEDGE_BASE / "soporte_ti.docx")
    print(f"Documentos generados en {KNOWLEDGE_BASE}")


if __name__ == "__main__":
    main()
