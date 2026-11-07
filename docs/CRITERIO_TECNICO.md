# Criterio técnico

> Respuestas a las preguntas de la sección 6.6 del enunciado. Las cifras salen de
> mediciones del proyecto ([EVALUACION.md](EVALUACION.md)); cuando algo es una estimación,
> lo digo.

## ¿Qué fue lo más difícil y cómo lo resolví?

**1. Que no invente cuando la búsqueda "casi" encuentra algo.** Con e5-small los puntajes de
similitud se concentran entre 0,75 y 0,92. Una pregunta de recursos humanos que no está en
los documentos ("¿cuántas semanas de licencia de maternidad da la empresa?") puntúa 0,830,
más que algunas que sí están (0,805). Ningún umbral las separa.

La solución fue no depender de una sola barrera (D-06, D-11):
- Un umbral que descarta lo obvio sin gastar una llamada.
- Una regla en el prompt que obliga al modelo a devolver `NO_ENCONTRADO` si los fragmentos
  no contienen la respuesta.
- Citas armadas por el código desde los metadatos, nunca por el modelo.

Lo difícil fue medirlo en lugar de suponerlo. Escribí un conjunto de evaluación con
preguntas fuera de alcance que suenan plausibles. En la ejecución final, las 4 que pasaron
el umbral terminaron en "no encontrado".

**2. Actualizar un documento sin que el bot cite una versión a medias.** Borrar los
fragmentos viejos e insertar los nuevos deja un instante en que el documento no existe, o
existe dos veces.

Lo resolví con versiones (D-09):
- Cada actualización agrega la versión nueva completa (`archivo@vN`).
- Después cambia, en una operación corta bajo un candado, qué versiones ven las búsquedas.
- Al final borra la anterior.

Hay una prueba que consulta en paralelo durante el cambio. Una reconstrucción completa
usa una colección nueva y cambia el puntero al final.

**3. Lo que solo apareció con usuarios reales.** Las pruebas automáticas pasaban, pero al
usar el bot en Telegram aparecieron tres problemas que no había previsto:
- **Copias:** el cliente de Telegram del autor envió 10 copias de un "hola" y el bot
  respondió 10 veces.
- **Saludos:** "hola" recibía un seco "No encontré esa información".
- **Reformulación:** cambiaba el sentido de preguntas cortas ("Cómo formatear un ocho" se
  buscó como "disco de ocho terabytes").

Ninguno era un error de código en sentido estricto; eran decisiones que faltaban. Las
registré en la especificación antes de tocar código (CA-R5.7, CA-06.5, plan 1.6 a 1.9) y,
en el caso de la reformulación, medí antes y después: de 13 a 0 de 40 reformulaciones que
cambiaban el sentido.

**4. La API del modelo no era la que yo recordaba.** Antes de escribir el adaptador probé
la API real y encontré dos sorpresas:
- El SDK de Anthropic 2.6 ya no acepta `temperature`.
- Haiku 5.5 razona por defecto, y esos tokens cuentan dentro del límite de la respuesta.

Si hubiera escrito el adaptador de memoria, el primer arranque habría fallado o habría
devuelto respuestas vacías. Desde entonces todo parámetro de un modelo es configuración
("vacío = no se envía") y existe un comando que verifica la configuración con una llamada
real.

## ¿Qué decisión tomaría distinto si empezara de nuevo?

- **Escribiría el conjunto de evaluación antes del motor de respuestas, no después.**
  Lo armé en S8, con el bot terminado. Varios ajustes (el doble punto al quitar marcadores,
  el tuteo, la reformulación conservadora) los habría encontrado en S5 con una evaluación
  pequeña corriendo desde el principio. Lo ideal sería empezar con preguntas reales de la
  mesa de ayuda, no escritas por mí.
- **Diseñaría la reformulación conservadora desde el inicio.** La primera versión pedía
  "reescribir como pregunta autónoma", y el modelo interpretó eso como permiso para
  completar con lo que creía que el usuario quería. Pedirle que devuelva la pregunta igual
  salvo que tenga una referencia explícita era lo correcto desde el día uno.
- **Pediría la respuesta del modelo en un formato estructurado** (por ejemplo, un JSON con
  `respuesta`, `fragmentos_usados` y `encontrado`), en lugar de marcadores `[n]` dentro del
  texto y la palabra `NO_ENCONTRADO`. Los marcadores funcionaron, pero obligaron a escribir
  un post-proceso con casos borde: el doble punto, el `NO_ENCONTRADO` seguido de una
  explicación y el respaldo cuando el modelo no marca nada. Con salida estructurada, la
  validación sería de esquema y no de texto. No lo cambié a mitad de camino porque el
  formato actual ya estaba probado y el cambio tocaba los tres adaptadores.

## ¿Qué cambiaría para miles de documentos y cientos de usuarios concurrentes?

El diseño actual es un proceso con todo embebido: correcto para decenas de documentos y
pocos usuarios, y fácil de ejecutar para el evaluador. Para producción lo separaría así:

| Hoy | En producción | Por qué |
|---|---|---|
| *Long polling*, un proceso | *Webhook* detrás de un balanceador, varias réplicas sin estado | Telegram entrega los mensajes a un solo lector por *polling*; con *webhook* se escala horizontalmente |
| SQLite (historial, cupos, invitados) y límite por minuto en memoria | PostgreSQL para el historial y los registros; Redis para el límite y los cupos | Varias réplicas tienen que ver el mismo estado |
| ChromaDB embebido | Servidor vectorial (Qdrant, o pgvector si ya hay PostgreSQL) | Miles de documentos y lecturas concurrentes desde varias réplicas |
| Indexación dentro del bot | Un servicio de ingesta aparte con una cola: cada documento nuevo es un trabajo; el bot solo lee | Indexar miles de documentos no debe competir con las respuestas |
| Carpeta local | Repositorio documental (SharePoint, Drive) con sincronización por eventos | Los documentos ya viven allá, con dueños y versiones |
| e5-small en CPU | Un modelo de embeddings más grande (e5-base, bge-m3) en GPU o por lotes, y búsqueda híbrida (vectores + BM25) con *re-ranking* | Con miles de documentos, los temas se cruzan y la precisión de la recuperación pesa más que la latencia |
| Todos ven todos los documentos | Permisos por documento como metadato, filtrados en la búsqueda según el usuario (identidad ligada al directorio de la empresa) | Hay documentos de un área que otra no debe ver |
| Evaluación a mano | Evaluación en CI con cada cambio de prompt, modelo o parámetro, con un conjunto de preguntas reales | Un cambio de prompt puede mejorar un caso y empeorar diez |
| Logs JSON en consola | Trazas y métricas (OpenTelemetry), tablero de resultados, alertas de costo y de error | Diagnosticar con cientos de usuarios exige agregar, no leer líneas |
| `.env` | Gestor de secretos con rotación | — |

La lógica central (fragmentación, prompt, citas, umbral, reformulación) se conserva: está
separada de Telegram y de Chroma por interfaces pequeñas.

## Principales riesgos y cómo los mitigaría

| Riesgo | Hoy | Siguiente paso |
|---|---|---|
| **Respuesta incorrecta con apariencia de correcta** (calidad) | Cuatro barreras contra lo inventado, cita obligatoria, evaluación con 30 casos | Botón de "¿te sirvió?" en cada respuesta; revisión periódica de las preguntas sin respuesta; evaluación con preguntas reales en CI |
| **Documentos desactualizados o contradictorios** (calidad) | El prompt pide mostrar ambas versiones si dos fragmentos se contradicen; los borrados dejan de citarse | Fecha de vigencia como metadato; preferir la versión más reciente; aviso al dueño del documento |
| **Información interna enviada a un tercero** (privacidad) | Solo salen al LLM los fragmentos relevantes y la pregunta; el texto de las preguntas no se registra salvo que se active | Acuerdo de tratamiento de datos con el proveedor y sin retención para entrenamiento; para documentos sensibles, un modelo alojado por la empresa (el adaptador compatible con OpenAI ya lo permite) |
| **Historial guardado sin vencimiento** (privacidad) | Se guarda en SQLite local; `/reset` lo borra | Política de retención (por ejemplo, borrar después de 30 días) y no guardar datos personales en el registro de sin respuesta |
| **Acceso indebido** (seguridad) | Falla cerrada, solo chats privados, invitación revocable con huella del código | Identidad ligada al directorio de la empresa en lugar de listas de IDs |
| **Prompt injection** (seguridad) | Sin herramientas; delimitación y escape; reglas del prompt; revisión de documentos al indexar | Carpeta con permisos de escritura restringidos; cuarentena por defecto para documentos de fuentes no confiables |
| **Fuga del token o de la API key** (seguridad) | Solo en `.env`; redactados en los logs; escaneo del historial de git antes de entregar | Gestor de secretos y rotación |
| **Costo descontrolado** | Topes por usuario y totales por día, largo máximo, límite por minuto | Tope de gasto en la consola del proveedor (lo recomiendo en el README), alertas por consumo |
| **Proveedor caído** (disponibilidad) | Reintentos con tiempo total y un mensaje claro | Proveedor de respaldo: los tres adaptadores ya existen, faltaría la lógica de conmutación |

## ¿Cuánto cuesta cada consulta con la configuración por defecto?

Configuración por defecto: Claude Haiku 5.5, a USD 0,10 por millón de tokens de entrada y
USD 0,50 por millón de salida (precio confirmado en T-21). Hasta 5 fragmentos de 800
caracteres, `LLM_MAX_TOKENS = 600`. Los embeddings se calculan en local, así que no cuestan.

Tokens medidos en la evaluación final (promedio por caso):

| Tipo de consulta | Llamadas al LLM | Entrada | Salida | Costo |
|---|---|---|---|---|
| Pregunta sin historial | 1 | 2.181 | 99 | 2.181 × 0,10 / 10⁶ + 99 × 0,50 / 10⁶ = USD 0,000218 + 0,000050 = **USD 0,00027** |
| Seguimiento (reformulación + respuesta) | 2 | 2.675 | 243 | 0,000268 + 0,000122 = **USD 0,00039** |
| Fuera de alcance que pasa el umbral | 1 | 1.950 | 13 | 0,000195 + 0,000007 = **USD 0,00020** |
| Fuera de alcance por umbral, saludo, comando | 0 | 0 | 0 | **USD 0** |

**Peor caso** (estimado, no medido): 5 fragmentos completos, historial de 6 mensajes,
reformulación y una respuesta que agota los 600 tokens.
- Entrada: unos 3.500 tokens → USD 0,00035.
- Salida: unos 700 tokens → USD 0,00035.
- Total: unos **USD 0,0007**.

**En volumen** (estimación con los promedios de arriba, suponiendo 7 de cada 10 preguntas
sin historial y 3 seguimientos, y redondeando hacia arriba):

| Uso | Consultas | Costo del LLM |
|---|---|---|
| 1.000 consultas | 1.000 | unos USD 0,31 |
| 200 colaboradores × 5 preguntas al día × 22 días | 22.000 al mes | unos USD 7 al mes |
| Tope total del bot (300 al día, el peor caso) | 9.000 al mes | menos de USD 6,50 al mes |

Telegram no cobra por la API de bots. Faltaría sumar la máquina donde corre el proceso: el
bot usa unos 1,5 GB de disco y funciona en una máquina virtual pequeña, cuyo costo depende
del proveedor de nube. Cambiar a un modelo más grande multiplica el costo por el cociente
de precios. Como el modelo es configuración, el cálculo se rehace con la tabla de precios
del proveedor y los mismos tokens.

## ¿Qué dejé por fuera de manera consciente y por qué?

| Qué | Por qué no |
|---|---|
| OCR de PDF escaneados | Agrega una dependencia pesada (Tesseract) y errores de reconocimiento que se convertirían en respuestas incorrectas. El bot informa el PDF como ilegible para que alguien suba la versión con texto |
| Tablas estructuradas | Se leen como texto. Sirven para preguntas puntuales; extraerlas bien, sobre todo en PDF, es un proyecto en sí |
| *Webhook*, varias réplicas, servicio vectorial aparte | Le exigirían al evaluador un servidor público o Docker. Están en la sección de producción |
| Permisos por documento | El enunciado no los pide y requieren una identidad ligada al directorio de la empresa (supuesto S-05) |
| Búsqueda híbrida y *re-ranking* | Con 4 documentos la fuente esperada aparece en el top-5 en el 100 % de los casos; no había un problema que resolver |
| Panel web de administración | Los comandos de Telegram cubren lo necesario (`/reindex`, `/invitados`, `/revocar`) sin otra superficie que proteger |
| Respuestas por voz y audios del usuario | Los teléfonos ya traen dictado y lector de pantalla; el bot sugiere el dictado cuando recibe un audio |
| Respuesta por partes mientras se genera (*streaming*) | Las respuestas tardan alrededor de 1 s; editar mensajes en Telegram mientras se generan complica la división en partes de 4.096 caracteres |
| Retención con vencimiento del historial | Lo dejé anotado como riesgo de privacidad; para la prueba basta `/reset` |
| Conmutación automática a otro proveedor si uno falla | Se propuso y el autor prefirió un solo proveedor activo por configuración. Los adaptadores ya existen |

## ¿Cómo mediría la organización si el asistente genera valor?

El objetivo del reto es que las preguntas repetitivas dejen de llegar a la mesa de ayuda.
Mediría eso primero y lo demás después:

1. **Casos evitados en la mesa de ayuda:** número de casos sobre los temas cubiertos
   (vacaciones, reembolsos, contraseñas…) antes y después de lanzar el bot, en las mismas
   semanas del año. Es la métrica de negocio.
2. **Tasa de respuesta:** proporción de preguntas con respuesta frente a "no encontrado". Ya
   se puede calcular con los logs (`outcome` en `message_handled`) y la tabla `unanswered`.
3. **Utilidad percibida:** un botón "¿te sirvió? sí / no" después de cada respuesta.
   Distingue "respondió" de "respondió bien".
4. **Adopción:** usuarios activos por semana y cuántos vuelven a preguntar.
5. **Brechas de conocimiento cerradas:** cuántas preguntas sin respuesta se convirtieron en
   un documento nuevo o actualizado. El CSV de `scripts.unanswered` es la lista de trabajo.
6. **Costo por pregunta resuelta frente al costo de un caso atendido por una persona:** el
   primero ronda los USD 0,0003; el segundo lo conoce la organización.

Las métricas 2 y 5 ya salen de lo que el bot registra. Las demás necesitan el botón de
utilidad y los datos de la mesa de ayuda.
