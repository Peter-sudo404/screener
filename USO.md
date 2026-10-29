# Guía de uso del screener

Esta guía es para usar la herramienta, no para modificarla. Para saber cómo funciona por dentro, ver `README.md`.

---

## 1. Preparar el entorno (una sola vez)

Abrir PowerShell y entrar a la carpeta del screener:

```powershell
cd "<ruta a la carpeta>\screener"
```

Si la carpeta `.venv` ya existe (es el caso en esta máquina), no hay que hacer nada más. Si no existe, o si se copió el proyecto a otra computadora:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

Si no está instalado `uv`, la alternativa con Python normal:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

No hace falta "activar" el entorno. Todos los comandos de abajo llaman directamente al Python de la carpeta `.venv`.

Opcional pero recomendado: definir tu identificación para la SEC (ver sección 6).

---

## 2. Comandos

### Analizar unos tickers escritos a mano

```powershell
.venv\Scripts\python.exe screener.py MSFT AAPL NVDA
```

### Analizar una lista desde archivo

Crear un archivo de texto con un ticker por línea. Las líneas que empiezan con `#` se ignoran:

```
# mi universo
MSFT
NVDA
COST
BRK-B
```

Y correr:

```powershell
.venv\Scripts\python.exe screener.py --file mi_lista.txt --out mi_corrida
```

`--out` es el prefijo de los archivos de salida. Con `--out mi_corrida` se generan `mi_corrida_resumen.csv`, `mi_corrida_detalle.csv` y `mi_corrida.md`. Si no se indica, el prefijo es `resultados`.

Se pueden combinar tickers sueltos y archivo en el mismo comando.

### Tickers con guion o punto

Escribir el formato de Yahoo Finance: `BRK-B`, `BF-B`. El programa prueba las variantes que usa la SEC por su cuenta.

---

## 3. Banderas

Por defecto se ejecuta todo. Cada bandera apaga una parte:

| Bandera | Qué apaga | Cuándo usarla |
|---|---|---|
| `--no-sec` | La lectura del 10-K para el filtro 2 (clientes). El filtro sale N/A. | Solo si EDGAR está caído o quieres una corrida muy rápida de los otros 4 filtros. |
| `--no-insiders` | El módulo de compras de insiders (Form 4). Desaparece la columna "Insiders 6m". | Para ahorrar 30–60 s por ticker cuando solo interesan los fundamentales. |
| `--no-13f` | El módulo de gestores institucionales. Desaparecen la columna "13F activos/hedge" y la sección de convergencia. | Para evitar los ~4 minutos de descarga inicial en una máquina nueva. |
| `--no-bottlenecks` | La sección de texto libre de cuellos de botella. | Si solo se quiere la tabla. |

Ejemplo de corrida rápida solo con los 5 filtros:

```powershell
.venv\Scripts\python.exe screener.py --file mi_lista.txt --no-insiders --no-13f --no-bottlenecks
```

Ejemplo de corrida completa (lo normal):

```powershell
.venv\Scripts\python.exe screener.py --file mi_lista.txt --out resultados
```

---

## 4. Cuánto tarda

| Situación | Tiempo aproximado |
|---|---|
| Primera corrida completa en la máquina | ~4 minutos de descarga de 13F, más 30–60 segundos por ticker |
| Segunda corrida con los mismos tickers | Segundos. Todo queda guardado en `.cache_edgar/` |
| Ticker nuevo en una máquina ya "calentada" | 30–60 segundos por ticker (Form 4 y 10-K) |

Mientras corre, la consola muestra líneas como `... MSFT (Form 4)` para saber en qué va. No es que se haya colgado.

---

## 5. Cómo leer la salida

### 5.1 La tabla resumen (consola y `<out>_resumen.csv`)

Una fila por empresa. Columnas:

| Columna | Significado |
|---|---|
| `1 Rentabilidad` … `5 Valuación` | Resultado de cada filtro: `PASS`, `WARN`, `FAIL` o `N/A` (sin datos suficientes). |
| `Pasa` | Cuántos de los 5 salieron PASS. Es un conteo, no una calificación. |
| `Insiders 6m` | Etiqueta de compras de insiders en los últimos 6 meses. Ver 5.3. |
| `13F activos/hedge` | Cuántos gestores activos y de convicción tienen posición, y cómo cambió. Ver 5.4. |

Debajo de la tabla aparece siempre el **aviso 13F** con el periodo usado (por ejemplo 2026-06-30). Ese dato tiene meses de antigüedad; no leerlo como actual.

### 5.2 El detalle (consola y `<out>_detalle.csv`)

Una fila por filtro y empresa, con la **nota que explica el veredicto**: los márgenes reales, la cita del 10-K sobre clientes, el PEG y con qué crecimiento se calculó. Las filas adicionales son:

- `Insiders (Form 4)`: etiqueta y explicación.
- `Insider compra P`: una fila por compra, con fecha, nombre, cargo, acciones, precio y USD.
- `13F institucionales`: resumen.
- `13F activo` / `13F hedge` / `13F pasivo`: una fila por gestor con posición, con acciones, valor, % de su cartera y cambio trimestral.
- `Cuello de botella (texto)`: una fila por frase extraída.

Lo más útil de este archivo es abrirlo en Excel y filtrar por ticker.

### 5.3 Cómo leer "Insiders 6m"

Solo cuenta **compras en mercado abierto con dinero propio** (código P del Form 4). No cuenta acciones recibidas como compensación ni ejercicios de opciones.

| Etiqueta | Qué significa |
|---|---|
| `FUERTE` | El CEO o el CFO compraron dentro de un grupo de varios insiders en la misma ventana de 30 días, o ≥ 3 insiders distintos compraron en esa ventana. |
| `MODERADA` | Varios insiders en 30 días pero sin CEO/CFO, o una sola compra grande (≥ USD 100,000) del CEO o CFO. |
| `DEBIL` | Una o más compras aisladas de directores u otros cargos, sin patrón de grupo. |
| `10%-OWNER` | Solo compró un accionista mayoritario (una empresa, un fondo, una familia). Es otra señal, no "la gerencia comprando". |
| `NINGUNA` | Se revisaron los Form 4 y no hubo compras P. |
| `N/A` | La empresa es extranjera (presenta 20-F o 40-F) y sus insiders no reportan Form 4. No significa que no compraron; significa que no hay dato. |

En el detalle, cada compra dice si fue **indirecta** (vía trust o familiar) y si fue **bajo plan 10b5-1** (programada con antelación, menos informativa que una compra discrecional).

### 5.4 Cómo leer "13F activos/hedge"

Formato: `19/30 activos/hedge (+7 -12 salio 1)`

- `19/30`: 19 de los 30 gestores activos y de convicción con datos tienen posición en la empresa.
- `+7`: 7 abrieron posición nueva o la aumentaron respecto al trimestre anterior.
- `-12`: 12 la redujeron.
- `salio 1`: 1 la vendió por completo.

Los gestores pasivos (Vanguard, BlackRock, etc.) aparecen en el detalle pero no en este conteo, porque tienen de todo y sus cambios no dicen nada.

En el detalle, la columna **% de su 13F** es la más informativa: 15% de la cartera de Pershing Square en Microsoft dice mucho más que 0.5% de la de Fidelity.

### 5.5 La sección "Convergencia sectorial 13F"

Al final del reporte. Para cada sector con dos o más empresas **en tu lista**, dice cuántos gestores tienen posición en varias de ellas a la vez y cuántos aumentaron en varias a la vez. Es una lectura de tu universo, no del mercado entero: si solo pusiste una empresa de un sector, ese sector no aparece.

### 5.6 El Markdown (`<out>.md`)

Todo lo anterior en un solo documento legible: tabla resumen, avisos, un bloque por empresa con sus filtros, tabla de compras de insiders, tabla de gestores y frases de cuellos de botella, y al final la convergencia. Se abre bien en Obsidian o en cualquier visor de Markdown.

### 5.7 "Cuellos de botella"

Frases textuales del press release de resultados y del último informe trimestral donde la empresa habla de restricciones de oferta, capacidad, aranceles, controles de exportación o escasez de componentes. Es texto para leer, sin puntuación ni veredicto. Si no aparece nada, o la empresa no tiene restricciones o las describió con otras palabras.

---

## 6. Identificación para la SEC

La SEC pide que los programas que descargan de EDGAR se identifiquen con un nombre y un correo. El programa usa un valor genérico si no se le indica otro. Para poner uno propio, antes de correr:

```powershell
$env:SEC_USER_AGENT = "Nombre Apellido correo@dominio.com"
```

Esto dura solo en esa ventana de PowerShell. El correo se envía únicamente a la SEC en el encabezado de la petición; no se guarda en ningún archivo.

---

## 7. Si algo falla

**"EDGAR tarda muchísimo la primera vez."** Es normal. La descarga inicial de 13F son 78 archivos, algunos de 8 MB, a 5 peticiones por segundo. Dejar que termine una vez; después queda en caché.

**Error `503 Service Unavailable` o `429`.** La SEC está limitando. El programa reintenta 4 veces con esperas crecientes. Si sigue fallando, esperar unos minutos y volver a correr; lo ya descargado no se vuelve a pedir.

**Un ticker sale con `ERR` o "ticker no encontrado en Yahoo Finance".** Revisar que el ticker sea el de Yahoo (con guion, no con punto). Empresas muy pequeñas o recién listadas a veces no tienen estados financieros en Yahoo.

**Filtro 2 sale N/A con "no reporta a la SEC".** La empresa no cotiza en EE.UU. o no tiene CIK. No hay solución gratuita; revisar el informe anual a mano.

**"Insiders 6m" sale N/A.** La empresa es extranjera (20-F). Ver 5.3.

**"13F" sale N/A con "no se encontró CUSIP".** El ticker no apareció en el archivo de la SEC que mapea tickers a CUSIP. Suele pasar con valores muy poco negociados.

**Sospecho que un dato está viejo.** La caché guarda los filings para siempre y los índices por un día. Para forzar descarga limpia de todo, borrar la carpeta `.cache_edgar` (se regenera sola en la siguiente corrida, con los 4 minutos iniciales).

**La consola muestra caracteres raros (acentos rotos).** El programa fuerza UTF-8 al arrancar; si aun así pasa, los archivos `.csv` y `.md` sí están bien codificados; abrirlos desde ahí.

**Quiero cambiar un umbral** (por ejemplo, que el moat exija 15% de margen en vez de 10%). Abrir `screener.py`, buscar el diccionario `T` al inicio y cambiar el número. No hay que tocar nada más.

---

## 8. Qué no hace

- No recomienda comprar ni vender.
- No mira precios diarios ni gráficos.
- No lee redes sociales ni foros (excluido a propósito).
- No tiene transcripciones de earnings calls (no hay fuente gratuita estable); usa el press release y el 10-Q en su lugar.
- No cubre insiders de empresas extranjeras ni posiciones cortas de ningún fondo.
