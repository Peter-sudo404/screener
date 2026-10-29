# Screener fundamental + insiders + 13F

Herramienta en Python que aplica cinco filtros fundamentales a una lista de tickers, y a cada uno le agrega dos señales de contexto (compras de insiders con dinero propio y posiciones de gestores institucionales) más una sección de texto libre con los cuellos de botella que la propia empresa reporta a la SEC.

El resultado es una tabla PASS / WARN / FAIL / N/A por filtro con una nota que explica cada veredicto. **No es una recomendación de compra.** Cinco PASS significa que la empresa pasó cinco reglas mecánicas con datos gratuitos y a veces sucios; la decisión de entrar o no la toma el usuario.

Para instrucciones de uso paso a paso, ver `USO.md`. Este documento es la referencia técnica.

> **Aviso legal.** Este proyecto es solo educativo e informativo. **No constituye asesoría financiera, de inversión, fiscal ni legal, ni una recomendación de comprar o vender ningún valor.** Los resultados (incluidos los de `ejemplo.md` y los archivos `ejemplo_*.csv`) son ilustrativos, se generan con datos gratuitos que pueden ser incompletos, erróneos o desactualizados, y no garantizan rendimientos futuros. Invertir implica riesgo de pérdida de capital. Usa el código bajo tu propio riesgo y consulta a un asesor profesional antes de tomar decisiones de inversión. El software se ofrece "tal cual", sin garantías (ver `LICENSE`).

---

## 1. Arquitectura

```
tickers  ──►  screener.py  ──►  5 filtros (yfinance + EDGAR 10-K)
                  │
                  ├──►  insiders.py      (EDGAR Form 4)          ──►  columna "Insiders 6m"
                  ├──►  institutions.py  (EDGAR 13F-HR × 39 gestores) ──►  columna "13F activos/hedge"
                  │                                                        + sección convergencia sectorial
                  └──►  bottlenecks.py   (EDGAR 8-K Ex.99.1 + 10-Q/10-K) ──►  texto libre por empresa
                                 │
                        todos usan  edgar.py  (throttle 5 req/s, reintentos, caché en disco)
                                 │
                  salida:  <out>_resumen.csv  ·  <out>_detalle.csv  ·  <out>.md  ·  consola
```

Orden de ejecución en `screener.main()`:

1. Para cada ticker: `screen()` corre los 5 filtros y guarda un `CompanyResult`.
2. Si no se pasó `--no-insiders`: `insiders.analyze()` por ticker.
3. Si no se pasó `--no-13f`: `institutions.load_managers()` una sola vez (descarga o lee de caché los 78 archivos 13F), luego `institutions.analyze()` por ticker y `institutions.convergence()` sobre todo el universo.
4. Si no se pasó `--no-bottlenecks`: `bottlenecks.analyze()` por ticker.
5. `to_frames()` arma las dos tablas (resumen y detalle) y se escriben CSV, Markdown y consola.

Cada módulo es independiente: se puede importar y usar solo (`import insiders; insiders.analyze("NKE")`).

---

## 2. Módulos

### `edgar.py` — cliente EDGAR compartido

Todo acceso a la SEC pasa por aquí.

| Función | Qué hace |
|---|---|
| `get(url, retries, cache, ttl_days)` | GET con límite de 5 peticiones/segundo (la SEC permite 10), hasta 4 reintentos con espera creciente ante 429/503, y caché en disco en `.cache_edgar/` con nombre SHA-1 de la URL. `ttl_days=None` = caché permanente (los filings no cambian); `ttl_days=1` para índices que sí cambian. |
| `submissions(cik)` | Índice JSON de filings de una entidad (`data.sec.gov/submissions/CIK##########.json`). Se refresca cada día. Contiene solo los últimos 1000 filings. |
| `recent_filings(cik, forms)` | Filtra ese índice por tipo de formulario. |
| `filing_dir()` / `filing_index()` | URL base de un filing y su `index.json` con la lista de archivos. |
| `ticker_to_cik(ticker)` | Mapa ticker → CIK usando `sec.gov/files/company_tickers.json`. Prueba variantes con guion, punto y sin separador (BRK-B, BRK.B, BRKB). |
| `find_entity_cik(name)` | Búsqueda de entidad por nombre en `browse-edgar`. Poco fiable: cuando hay varias coincidencias EDGAR devuelve los CIK sin nombre. Por eso los CIK de gestores están fijos en `institutions.py`. |
| `html_to_text()` | Quita etiquetas HTML y normaliza espacios. |

**User-Agent.** La SEC exige un encabezado identificable con formato `Nombre correo@dominio`. Se lee de la variable de entorno `SEC_USER_AGENT`. Si no existe, se usa el placeholder `screener-personal contacto@example.com`. Ver sección 7.

### `screener.py` — los 5 filtros y la orquestación

Fuentes: **yfinance** (Yahoo Finance) para estados financieros anuales (hasta 4 años), múltiplos, crecimiento y descripción del negocio; **EDGAR 10-K / 20-F / 40-F** para el filtro de clientes.

Umbrales en el diccionario `T` al inicio del archivo.

| # | Filtro | Fuente | Regla |
|---|---|---|---|
| 1 | Rentabilidad o camino claro | yfinance `financials`: Total Revenue, Operating Income, Gross Profit | PASS si margen operativo ≥ 5% y la utilidad operativa no cae todos los años. WARN si es positivo pero < 5%, o si hay pérdidas pero se reducen cada año con margen bruto ≥ 30% ("camino claro"). FAIL en el resto. |
| 2 | Diversificación de clientes | EDGAR: documento principal del último 10-K, 20-F o 40-F | Expresión regular que busca frases tipo "one customer accounted for 22% of total revenue". Sin mención → PASS (la SEC obliga a revelar clientes ≥ 10% bajo ASC 280). Un cliente 10–25% → WARN; ≥ 25% → FAIL. Si la frase dice "three largest customers … 43%" se divide entre N para evaluar y se etiqueta como grupo. Frases de cuentas por cobrar solo se usan si no hay frases de ingresos. Se descartan frases geográficas ("customers outside the United States") y de encuestas. Negaciones ("No customer accounted for more than 10%") → PASS. |
| 3 | Ventaja competitiva durable (moat) | yfinance márgenes + `longBusinessSummary` + `returnOnEquity` | Cuantitativo: margen operativo medio ≥ 10%, coeficiente de variación ≤ 0.25, margen bruto ≥ 30%. Cualitativo: palabras clave en la descripción (efectos de red, costos de cambio, recurrencia, patentes, marca, escala, barreras tecnológicas, regulación). PASS solo si ambos. Márgenes estables sin palabras clave → WARN. Márgenes bajos pero estables con ROE ≥ 20% → WARN "posible moat de costo". Márgenes altos pero inestables → WARN "posible ciclicidad". |
| 4 | Flujo de caja libre | yfinance `cashflow`: Free Cash Flow (o Operating Cash Flow + Capital Expenditure) | PASS si FCF > 0 en todos los años disponibles (mínimo 3) y FCF/ingresos ≥ 5%. WARN si un año negativo, o margen FCF bajo. FAIL en el resto. |
| 5 | Valuación vs. crecimiento | yfinance `trailingPE`, `forwardPE`, `enterpriseToEbitda`, `earningsGrowth`, Diluted EPS histórico | PEG = P/E forward ÷ crecimiento. Crecimiento = mediana de tres estimaciones (implícito en consenso = PE trailing/PE forward − 1; CAGR de EPS histórico; crecimiento yoy del último trimestre), **capada a 40%**. PEG ≤ 2 PASS, ≤ 3 WARN, si no FAIL. Sin utilidades → FAIL (WARN si EV/EBITDA ≤ 12). EV/EBITDA > 200 se descarta como dato corrupto. |

### `insiders.py` — Módulo 1: compras de insiders (Form 4)

Fuente: **EDGAR Form 4 y 4/A** del emisor, últimos 6 meses (`MONTHS_BACK`). Se descarga el XML de cada filing (`form4.xml`) y se parsea con ElementTree.

- Solo cuenta transacciones de la tabla no derivada con código de transacción **P** (open-market purchase) y código adquirido/dispuesto **A**. Quedan fuera M (ejercicio de opciones), A (otorgamiento), F (retención fiscal por vesting), S (venta), G (regalo), C (conversión), J (otro).
- Por compra: nombre del insider, cargo clasificado (CEO / CFO / Chair / Officer / Director / 10%-owner / Other) a partir de `reportingOwnerRelationship` y `officerTitle`, fecha, acciones, precio, USD = acciones × precio, propiedad directa o indirecta (`directOrIndirectOwnership`), y si fue bajo plan 10b5-1 (campo `aff10b5One` o nota al pie que diga "pursuant to / under … 10b5-1").
- Deduplicación por (insider, fecha, acciones redondeadas), porque un 4/A suele repetir la compra del 4 original con precio corregido.
- **Patrón de grupo**: compras de ≥ 2 insiders distintos dentro de una ventana de 30 días (`CLUSTER_WINDOW_DAYS`). Los accionistas ≥ 10% no cuentan para el grupo.
- Etiqueta:
  - `FUERTE`: grupo que incluye CEO o CFO, o grupo de ≥ 3 insiders.
  - `MODERADA`: grupo sin CEO/CFO, o compra individual de CEO/CFO ≥ USD 100,000 (`CLEVEL_SINGLE_MIN_USD`).
  - `DEBIL`: compras aisladas de directores u otros cargos.
  - `10%-OWNER`: solo compras de accionistas ≥ 10% (empresas, fondos, familias).
  - `NINGUNA`: Form 4 revisados pero sin código P.
  - `N/A`: emisor sin Form 4 (extranjeros con 20-F/40-F) o sin CIK.

### `institutions.py` — Módulo 2: gestores institucionales (13F-HR)

Fuente: **EDGAR 13F-HR** (tabla de posiciones XML) de una lista fija de 39 gestores, dos trimestres cada uno.

- La lista `MANAGERS` tiene (nombre, CIK, grupo). Los CIK fueron verificados uno por uno contra EDGAR el 2026-09-14 (nombre de la entidad y fecha del último 13F), porque varios CIK "conocidos" resultaron ser otra entidad o una entidad abandonada (BlackRock cambió de CIK en 2024).
- Grupos:
  - `activo` (14): T. Rowe Price, Capital Research Global, Capital World, Wellington, Fidelity (FMR), Dodge & Cox, Primecap, Harris Associates, Baillie Gifford, Davis Selected, Akre, Fundsmith, AllianceBernstein, Franklin Templeton.
  - `hedge` (16): Berkshire Hathaway, Pershing Square, Third Point, Appaloosa, Tiger Global, Coatue, Lone Pine, Viking, Baupost, Elliott, Icahn, ValueAct, Soros, Duquesne, D1, Altimeter.
  - `pasivo` (9): Vanguard, BlackRock, State Street, Geode, Northern Trust, JPMorgan, Morgan Stanley, Goldman Sachs, Invesco. Se muestran en el detalle pero **no cuentan en la columna resumen ni en la convergencia**.
  - Excluidos a propósito: Renaissance, Citadel, Millennium, Two Sigma, Bridgewater (cuantitativos y multiestrategia).
- Para cada gestor: los dos últimos `13F-HR` con periodo distinto. Se lee el XML de posiciones (`infoTable`), se agrupan filas por CUSIP (un gestor reporta varias filas del mismo valor por tipo de discreción), se ignoran filas con `putCall` y filas cuyo tipo no sea `SH` (acciones). Un gestor cuyo último 13F tiene más de 200 días se marca `stale` y se excluye de la señal.
- **Unidades**: la regla vigente pide valor en USD, pero al menos un gestor (T. Rowe) sigue reportando en miles. Se detecta con el precio implícito mediano (valor / acciones): si es < USD 1, se multiplica por 1000.
- **Ticker → CUSIP**: el 13F no trae tickers. Se usa el archivo público de fails-to-deliver de la SEC (`sec.gov/files/data/fails-deliver-data/cnsfailsYYYYMMa|b.zip`), que lista CUSIP y símbolo. Se prueba el mes actual y hasta 6 meses hacia atrás hasta encontrar uno publicado.
- Por posición: acciones, valor, **% del 13F del gestor** (proxy de convicción), y cambio vs. trimestre anterior: `NUEVA`, `SALIO`, `+x%`, `-x%`, `=` (|Δ| < 1%). Las acciones del trimestre anterior se ajustan por splits ocurridos entre ambas fechas (yfinance `splits`).
- Columna resumen: `19/30 activos/hedge (+7 -12 salio 1)` = 19 de los 30 gestores activo+hedge con datos válidos tienen posición; 7 abrieron o aumentaron; 12 redujeron; 1 salió.
- **Convergencia sectorial** (`convergence()`): para cada sector presente en el universo con ≥ 2 empresas, lista los gestores activo/hedge con posición en ≥ 2 de esas empresas y los que abrieron o aumentaron en ≥ 2 a la vez. El sector viene de yfinance.
- `lag_disclaimer()` genera el aviso de rezago que se imprime siempre en consola y en el Markdown, con el periodo más reciente usado y los gestores excluidos.

### `bottlenecks.py` — texto libre: cuellos de botella

Fuente: **EDGAR 8-K** (busca entre los últimos 10 un Exhibit 99.1 que parezca press release de resultados) y **el último 10-Q / 10-K / 20-F** (desde la segunda mención de "Management's Discussion and Analysis", que suele ser el encabezado real y no el índice).

- Se separan frases y se conservan las que contienen vocabulario de restricción real: supply/capacity constraints, shortage, bottleneck, backlog, lead times, demand exceeded supply, tariffs paid/imposed, export controls, labor shortage, escasez de componentes específicos (memoria, GPU, wafers, helio, energía), construction/production delays.
- Se descartan hipotéticos y boilerplate: frases con "may", "could", "might", "no assurance", "forward-looking", "from time to time", "if we fail", y frases con más de 25% de palabras en mayúsculas (índices y encabezados).
- Deduplicación entre el 8-K y el 10-Q por los primeros 60 caracteres alfabéticos (la misma frase suele aparecer en ambos con comillas distintas).
- Máximo 8 frases por fuente. Es texto para leer, no un filtro.

---

## 3. Fuentes de datos, por dato

| Dato | Fuente exacta |
|---|---|
| Ingresos, utilidad operativa, utilidad bruta, EPS, utilidad neta | yfinance `Ticker.financials` (anual, hasta 4 años) |
| Flujo de caja operativo, capex, FCF | yfinance `Ticker.cashflow` |
| P/E trailing y forward, EV/EBITDA, crecimiento yoy, ROE, sector, nombre, descripción | yfinance `Ticker.info` |
| Splits | yfinance `Ticker.splits` |
| Ticker → CIK | `sec.gov/files/company_tickers.json` |
| Índice de filings por entidad | `data.sec.gov/submissions/CIK##########.json` |
| Concentración de clientes | Documento principal del último 10-K / 20-F / 40-F |
| Compras de insiders | Form 4 y 4/A (XML `ownershipDocument`) |
| Posiciones institucionales | 13F-HR (XML `informationTable`), dos últimos por gestor |
| Ticker → CUSIP | Archivo fails-to-deliver de la SEC |
| Cuellos de botella | 8-K Exhibit 99.1 y 10-Q / 10-K / 20-F |

Ninguna fuente requiere cuenta ni API key. Yahoo Finance no es una API oficial: yfinance hace scraping y puede romperse con cambios del sitio.

---

## 4. Limitaciones y heurísticas (lista completa)

Esta sección recoge todo lo identificado durante la construcción. No es una versión optimista.

### Generales
- **No es una recomendación.** Cinco PASS es el resultado de reglas mecánicas. No incluye contexto macro, gobierno corporativo, calidad de la gerencia ni riesgo de balance.
- **yfinance es gratis y a veces sucio.** Campos que faltan, ADRs con estados en una moneda y precio en otra (por eso ASML devuelve EV/EBITDA absurdo y se descarta), solo 4 años de historia anual. Cuando un campo falta, el filtro sale N/A en vez de inventar.
- **Umbrales arbitrarios.** Los valores de `T` (5% de margen, PEG 2, ROE 20%, etc.) son puntos de partida razonables, no óptimos calibrados.
- **Velocidad.** EDGAR limita a 10 peticiones/s (usamos 5). Primera corrida: ~4 minutos para 13F más 30–60 s por ticker. Todo queda en caché; reruns sobre los mismos tickers son casi instantáneos, pero la caché ocupa 350+ MB.

### Filtro 1 (rentabilidad)
- "Camino claro a la rentabilidad" se reduce a "la pérdida operativa se achica cada año y el margen bruto es ≥ 30%". No evalúa runway de caja ni dilución.

### Filtro 2 (clientes)
- **Es una heurística de texto, no una garantía.** Una expresión regular sobre el 10-K. Puede fallar si la empresa usa redacción inusual, tablas en vez de prosa, o si el % aparece lejos de la palabra "customer".
- "Distributors accounted for 48%" (Broadcom) es concentración de canal, no de cliente final. Se reporta igual porque es un riesgo real, pero se etiqueta "cliente/canal".
- Cuando el % es de un grupo ("three largest customers, 43%") se divide entre N. Eso asume reparto uniforme; el mayor podría ser 30% y los otros dos 6–7%.
- Empresas que no reportan a la SEC (listadas solo en Europa o Asia) → N/A.
- La ausencia de mención se toma como PASS. Eso depende de que la empresa cumpla ASC 280 y de que la frase no se haya escapado a la regex.

### Filtro 3 (moat)
- **Es un proxy, no una prueba.** Márgenes estables pueden venir de un oligopolio regulado, de un ciclo favorable o de contabilidad, no de una ventaja durable.
- Las palabras clave provienen de la descripción que la propia empresa escribe: es marketing. "Leading" y "brand" aparecen en casi cualquier descripción.
- Negocios de bajo margen con moat de costo (Costco) salen WARN, nunca PASS.
- Con 4 años de datos el coeficiente de variación es estadísticamente pobre.

### Filtro 4 (FCF)
- FCF de yfinance ya viene calculado; si falta se reconstruye como flujo operativo más capex (capex negativo). No ajusta por compensación en acciones, arrendamientos ni adquisiciones.
- Un solo año negativo baja a WARN aunque sea por una adquisición puntual.

### Filtro 5 (valuación)
- El crecimiento se capa a 40% para evitar PEG ridículos, pero **una empresa cíclica en pico de utilidades puede pasar con un múltiplo que parece barato** solo porque el denominador está inflado (chips, materias primas).
- El "crecimiento implícito en consenso" (PE trailing / PE forward) hereda cualquier distorsión de un año con cargos extraordinarios (Broadcom tras VMware).
- P/E forward de Yahoo depende de estimaciones de analistas que Yahoo no siempre actualiza.

### Módulo 1 (insiders, Form 4)
- **Emisores extranjeros (20-F / 40-F) salen N/A.** Sus insiders no presentan Form 4 y no hay fuente gratuita equivalente. ASML es el ejemplo en el universo de prueba.
- **El código P incluye cosas que no son "insider operativo comprando"**: compras de accionistas ≥ 10% (Volkswagen comprando USD 1,000M de Rivian), compras bajo planes 10b5-1 preprogramados, compras indirectas vía trust o familiares, compras en programas de reinversión de dividendos. Las tres primeras se detectan y etiquetan; la cuarta no siempre.
- La detección de 10b5-1 depende de que la empresa marque el campo o lo diga en nota al pie con las palabras "pursuant to / under / in accordance with".
- Un Form 4 con varios declarantes (director más su trust, o dos cotitulares) se atribuye al primero.
- Cuando el precio no viene en el XML (a veces está en nota al pie), el monto en USD queda n/d y no suma al total.
- El índice `submissions` solo contiene los últimos 1000 filings; para emisores con muchísima actividad podrían faltar Form 4 antiguos dentro de la ventana de 6 meses. No ocurrió en las pruebas (Microsoft: 76 en 6 meses).
- Solo se miran compras. Las ventas no se reportan aunque serían contexto útil.
- La ventana de 30 días para "grupo" y los umbrales de USD 100,000 son arbitrarios.

### Módulo 2 (13F)
- **Rezago estructural de hasta 45 días.** Un 13F se presenta hasta 45 días después del cierre de trimestre. La foto tiene entre 1.5 y 4.5 meses. El reporte lo dice siempre; no debe leerse como dato en tiempo real.
- **Solo muestra posiciones largas en acciones de EE.UU.** No cortos, no coberturas, no bonos, no derivados (las opciones se descartan explícitamente), no posiciones en bolsas extranjeras. Una posición larga puede ser una pata de un arbitraje o una cobertura de un corto que no se ve.
- **Convergencia sectorial solo dentro del universo de tickers que se pasa**, no de todo el mercado. Medirla en todo el mercado exigiría clasificar por sector miles de CUSIPs y no hay fuente gratuita para eso. Si el universo tiene una sola empresa de un sector, ese sector no aparece.
- **Los gestores pasivos no son señal.** Vanguard, BlackRock y State Street tienen posición en casi todo; sus cambios reflejan flujos de índices. Se excluyen de la columna resumen y de la convergencia.
- Los cuantitativos y multiestrategia se excluyen a propósito porque sus 13F no reflejan convicción.
- La lista de gestores es fija y opinable. No incluye fondos soberanos, pensiones, ni gestores europeos o asiáticos sin obligación 13F.
- Vanguard aparece con último 13F de 2025-12-31 bajo su CIK habitual (0000102909) y se marca como desactualizado. No se investigó si cambió de entidad declarante.
- Las enmiendas 13F-HR/A no se procesan. Si un gestor corrigió una posición, se usa la versión original.
- La detección de "valores en miles" es una heurística por precio implícito; podría fallar en un gestor cuya cartera sea toda de acciones de centavos.
- El mapeo ticker → CUSIP depende de que el valor haya aparecido en el archivo de fails-to-deliver de algún mes reciente. Casi todo lo líquido aparece, pero no es un mapa completo. Sin CUSIP → N/A.
- El ajuste por splits usa yfinance; si yfinance no tiene el split, un split 10:1 aparecerá como "+900%".
- "% de su 13F" se calcula sobre el total de posiciones en acciones del 13F (sin opciones), no sobre los activos totales del gestor.
- `submissions` solo trae 1000 filings; en gestores que presentan miles de formularios al año (Vanguard, BlackRock) el 13F más reciente podría no estar en la lista. Es la explicación más probable del caso Vanguard.

### Cuellos de botella (texto libre)
- **No hay transcripciones de earnings calls en fuentes gratuitas estables.** Motley Fool bloquea scrapers y cambia formato; Seeking Alpha es de pago. Se usan en su lugar el press release del 8-K y el MD&A del 10-Q/10-K, que son lo más cercano y sí son estables.
- La extracción depende de que la empresa use vocabulario estándar. Una restricción descrita con otras palabras no aparece.
- El filtro de hipotéticos (may/could/might) elimina también frases reales que usan esas palabras.
- Empresas con 20-F solo tienen informe anual, sin 8-K trimestral: la información puede tener hasta un año.
- La detección del 8-K de resultados es heurística (busca "quarter/fiscal year … results/revenue" en un Exhibit 99). Puede tomar un 8-K de otro tema o no encontrar ninguno.
- Sin puntuación ni severidad: es texto para leer.

### Caché
- `.cache_edgar/` guarda todo lo descargado de EDGAR con nombre hash. Los filings son permanentes; los índices se refrescan cada día y el mapa de tickers cada 7 días. Si un filing se corrige después de descargado, la caché no lo sabe. Para forzar descarga limpia, borrar la carpeta.

---

## 5. Estructura de archivos

```
screener/
├── screener.py          5 filtros + orquestación + salida
├── edgar.py             cliente EDGAR (throttle, reintentos, caché)
├── insiders.py          Módulo 1: Form 4
├── institutions.py      Módulo 2: 13F-HR
├── bottlenecks.py       texto libre: 8-K + 10-Q/10-K
├── server.py            UI web local (Flask): ejecuta el screener y sirve web/
├── web/                 index.html + app.js: las 4 pantallas de la UI
├── requirements.txt     yfinance, pandas, tabulate, flask
├── tickers_ejemplo.txt  universo de prueba (9 tickers)
├── ejemplo_*.csv/.md    salida de referencia generada con ese universo
├── README.md            este documento
├── USO.md               guía de uso
├── .gitignore
├── .venv/               entorno virtual (no se versiona)
└── .cache_edgar/        caché de descargas (no se versiona)
```

---

## 6. Umbrales configurables

En `screener.py`, diccionario `T`: márgenes mínimos, porcentajes de concentración de clientes, coeficiente de variación del moat, ROE mínimo, años mínimos de FCF, PEG, EV/EBITDA, cap de crecimiento.

En `insiders.py`: `MONTHS_BACK` (6), `CLUSTER_WINDOW_DAYS` (30), `CLEVEL_SINGLE_MIN_USD` (100,000).

En `institutions.py`: lista `MANAGERS`, `STALE_DAYS` (200).

En `bottlenecks.py`: `KEYWORDS`, `_NOISE_RE`, `MAX_SENTENCES` (8).

En `edgar.py`: `MIN_INTERVAL` (0.2 s entre peticiones), `CACHE_DIR`.

---

## Interfaz web (opcional)

Además de la línea de comandos hay una UI local con cuatro pantallas: Resumen, Detalle por ticker, Convergencia 13F y Nueva corrida (universo, módulos, umbrales editables y descarga de CSV/Markdown).

```powershell
pip install -r requirements.txt
python server.py        # abre http://127.0.0.1:8765
```

El servidor solo escucha en `127.0.0.1` y rechaza peticiones de otros hosts. El User-Agent que escribas en la UI se usa durante la sesión y no se guarda.

---

## 7. User-Agent para EDGAR

La política de acceso de la SEC pide que cada petición automatizada lleve un encabezado `User-Agent` con nombre y correo de contacto (`Nombre Apellido correo@dominio.com`). No verifican el correo, pero pueden bloquear tráfico sin identificación o con identificación claramente falsa.

El código lee la variable de entorno `SEC_USER_AGENT`. Si no está definida usa un placeholder con `example.com`. Para uso serio conviene definirla en la sesión (no en el código):

```powershell
$env:SEC_USER_AGENT = "Nombre Apellido correo@dominio.com"
```

Ese correo queda solo en el encabezado HTTP que recibe la SEC; no se escribe en ningún archivo del proyecto ni en la caché.
