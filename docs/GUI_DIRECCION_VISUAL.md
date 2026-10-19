# Dirección visual de la GUI — tres opciones

**Qué es el producto:** consola interna de Machinery Hunters (compraventa,
subasta y consignación de maquinaria pesada). **Quién la usa:** Dirección y
líderes, a veces desde el celular. **Su trabajo principal:** hacer una
pregunta y ver una respuesta **con evidencia**: de qué fuentes salió, cuáles
no respondieron y qué no se pudo afirmar. La confianza es el producto.

El esqueleto actual usa grises neutros provisionales (`web/src/app/globals.css`,
tokens `--mh-*`). Elegir una dirección = reemplazar esos valores y las
tipografías; los componentes solo usan nombres semánticos.

Se evitaron a propósito: fondo crema con serif y terracota, negro con un solo
acento ácido, rejilla de tarjetas idénticas con sombra y degradados,
etiquetas en mayúsculas sobre cada título y numeración decorativa.

---

## A. Ficha de equipo (recomendada)

Idea: la respuesta se lee como la ficha técnica de un equipo de catálogo:
datos con estructura, cada afirmación con su referencia.

| | |
|---|---|
| Papel | `#E9ECE8` (gris-verde frío), panel `#FFFFFF` |
| Tinta | `#17242C` |
| Acero (secundario) | `#66757D`, líneas `#BCC5C9` |
| Amarillo maquinaria | `#E3AE00` **solo** como marca de evidencia (cita) |
| Estados | ok `#2A6A4E`, aviso `#8A5A00`, error `#A8261C` |
| Tipografía | Barlow Condensed (títulos, números) + Public Sans (texto) |
| Esquinas | 2 px; la cita es un chip con borde, como placa troquelada |

```
┌ fuentes ─────┐ ┌ Respuesta ────────────────────────────┐
│ Drive    ok  │ │ Quedan 4 grúas en subasta [1][2].       │
│ ERP   sin    │ │ No tengo datos del ERP: no está         │
│  configurar  │ │ configurado.                            │
│ CRM      ok  │ │ [1] Inventario_Subasta · Drive          │
│ Firestore ok │ │ [2] Portafolio aprobado · Firestore     │
└──────────────┘ └────────────────────────────────────────┘
```

Lo memorable: el **riel de fuentes** con el estado en vivo de cada sistema. Es
la única parte con carácter propio; el resto es sobrio. Alineado a la
izquierda, líneas de texto de menos de 70 caracteres. En celular el riel pasa
arriba como una franja horizontal.

## B. Tablero de despacho

Idea: sala de control; cada consulta es un "despacho" que muestra los
sub-agentes trabajando.

| | |
|---|---|
| Fondo | `#1E2A33` (pizarra azulada, no casi negro), panel `#26343F` |
| Texto | `#E7EDF0`, secundario `#9FB0BA` |
| Fuente consultada | cian `#53B6D8` |
| Aviso | ámbar `#F0A23A` |
| Tipografía | Archivo (ancho variable) para títulos; Source Sans 3 para texto |

Estructura: columna central con la pregunta arriba y una línea de tiempo
vertical de sub-agentes (consultando → respondió / agotó tiempo / sin
permiso) y la respuesta debajo. Alineación a la izquierda. **Riesgo:** la
combinación oscuro + acento es un lugar común; solo se justifica si se usa
mucho en pantallas grandes de oficina.

## C. Carta topográfica

Idea: "Hunters": explorar territorio y marcar lo hallado. Curvas de nivel muy
tenues como textura de fondo; las citas son marcadores en el mapa.

| | |
|---|---|
| Fondo | `#E4EADF` (salvia pálido), panel `#F8FAF6` |
| Tinta | `#1E3A2E` (verde bosque) |
| Marcador de hallazgo | naranja señal `#E4571B` |
| Líneas de nivel | `#B9C7B5` |
| Tipografía | IBM Plex Serif (respuesta) + IBM Plex Sans (interfaz) |

Estructura: respuesta a una columna ancha, citas como marcadores numerados al
margen (aquí la numeración sí es una secuencia real de referencias). Más
cálida y editorial; menos "herramienta".

---

## Revisión contra el encargo

- Se quitaron etiquetas en mayúsculas sobre los títulos y los puntos medios como separadores en las tres propuestas.
- A: se descartó el degradado de acento y se limitó el amarillo a un solo uso funcional; el amarillo puro sobre blanco no cumple contraste para texto, por eso es marca (borde/relleno), nunca color de letra.
- B: se movió el fondo de casi negro a pizarra para alejarlo del lugar común, y se dejó dos acentos con función (consulta y aviso), no decorativos.
- C: se evitó el beige cálido y el terracota; el naranja se usa solo como marcador.
- Estados siempre con texto además de color (accesibilidad).

**Recomendación:** A. Convierte el valor del sistema (evidencia y fuentes
visibles) en la forma de la interfaz, funciona en celular y no depende de un
adorno. Con la elección, se cargan las fuentes con `next/font` (hoy se usan las
del sistema para no depender de la red en el build) y se reemplazan los tokens.
