# Polymarket AI Trading Bot 🚀

Bot de trading algorítmico y auto-sostenible diseñado para operar en mercados de predicción en **Polymarket**. Combina ingesta de datos en tiempo real, filtrado por **Machine Learning** (Scikit-Learn), gestión conservadora de PnL y sincronización continua de memoria con **SQLite**.

---

## 🛠️ Características Principales

* **Filtrado Predictivo de Machine Learning:** Evalúa la probabilidad de éxito de cada candidato usando un modelo probabilístico (`model_filter.pkl`) con un umbral de aprobación del **50%**.
* **Gestión de Riesgo y PnL Ajustado:** 
  * **Take-Profit (TP):** +30%
  * **Stop-Loss (SL):** -15%
* **Sincronización de Memoria en Vivo:** Purgado automático de órdenes en memoria local (`PaperExecutor`) tras ser liquidadas por el resolutor de mercados (`check_and_settle_orders`), evitando bloqueos por estado duplicado (`CONDITION_ALREADY_ACTIVE`).
* **Auto-Entrenamiento Continuo:** Re-entrena el modelo ML automáticamente cada ciertos ciclos utilizando los resultados históricos reales almacenados en la base de datos.
* **Control de Cooldown y Duplicados:** Filtros en SQLite y en memoria para evitar reoperar el mismo mercado o procesar señales contradictorias (YES/NO) en la misma ráfaga.

---

## 🏗️ Arquitectura del Sistema

polymarket-ai-bot/
├── analytics/
│   └── performance.py               # Cálculo de métricas de rendimiento y esperanza matemática (Ev)
├── connectors/
│   └── polymarket_ingest.py        # Ingesta de snapshot de mercados activos desde la API de Polymarket
├── data/
│   └── trading_bot.db               # Base de datos SQLite para persistencia de órdenes e historial
├── database/
│   └── db_manager.py                # CRUD y actualización de PnL para órdenes
├── executors/
│   └── paper_executor.py            # Motor de simulación (Paper Trading), manejo de cooldown y purga
├── models/
│   ├── model_filter.pkl             # Binario del modelo ML entrenado
│   ├── predictive_filter.py         # Filtro predictivo de candidatos por umbral de probabilidad (50%)
│   └── train_filter.py              # Pipeline de re-entrenamiento automático del modelo ML
├── services/
│   └── order_settlement_resolver.py # Verificación de resolución de mercados/precios en tiempo real
├── tools/
│   └── directional_limit_hunter_executor.py # Ejecutor global de candidatos aprobados
└── main.py                          # Orquestador principal del ciclo continuo

---

## 🔄 Flujo de Ejecución en Cada Ciclo

1. **Resolución y Sincronización:** `check_and_settle_orders()` verifica si las órdenes activas alcanzaron el TP (+30%), SL (-15%) o cierre del mercado, y purga sus claves (`match_key`) de la memoria de `PaperExecutor`.
2. **Ingesta de Mercado:** `get_live_snapshot()` recupera las oportunidades de mercado actuales.
3. **Filtro ML:** `filter_snapshot()` evalúa cada candidato con el modelo predictivo y descarta aquellos con probabilidad menor al 50%.
4. **Ejecución de Órdenes:** `process_snapshot()` aplica validaciones de unicidad, cooldown y registra el evento `PENDING_CREATED` en SQLite.
5. **Auto-Entrenamiento & Analítica:** Cada 5 ciclos, el sistema entrena el modelo con el historial más reciente e imprime el **Reporte de Rendimiento (Ev)**.

---

## 🚀 Instalación y Uso

### 1. Clonar el repositorio
```bash
git clone [https://github.com/H3cm4n/Ia-bot-traiding.git](https://github.com/H3cm4n/Ia-bot-traiding.git)
cd Ia-bot-traiding

### 2. Crear entorno virtual e instalar dependencias
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

### 3. Ejecutar el orquestador principal
python3 main.py

### 📊 Métricas y Reportes

El bot genera reportes de desempeño automáticos al finalizar ejecuciones o durante la rutina de entrenamiento:


=== REPORTE DE RENDIMIENTO (Ev) ===
Total Trades Cerrados: 10
Tasa de Acierto (Win Rate): 100.00%
Ganancia Promedio: $3.60
Pérdida Promedio: $0.00
Esperanza Matemática (Ev): $3.6000 por operación
===================================
