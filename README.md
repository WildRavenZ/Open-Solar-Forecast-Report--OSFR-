# Open Solar Forecast Report (OSFR)

Herramienta de línea de comandos en Python para consultar el pronóstico solar
horario de [Open-Meteo](https://open-meteo.com/) y estimar la energía diaria
disponible para un sistema fotovoltaico configurado.

> **Alcance:** OSFR es una herramienta de pronóstico. Los datos actuales y
> horarios son resultados de modelos meteorológicos; no son mediciones de un
> panel, inversor ni medidor, y no deben usarse como verificación de producción
> o rendimiento real.

## Requisitos

- Python 3.10 o posterior
- Conexión a Internet para consultar Open-Meteo

## Instalación

### Desde una copia local

```powershell
git clone https://github.com/<usuario>/open-solar-forecast-report.git
cd open-solar-forecast-report
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Si prefieres no instalar el proyecto como paquete:

```powershell
python -m pip install -r requirements.txt
```

## Uso rápido

Especifica las coordenadas del lugar para cada pronóstico. Reemplaza
`LATITUD` y `LONGITUD` por valores numéricos; no se incluyen coordenadas
personales como valores predeterminados.

```powershell
python solar_report.py --lat LATITUD --lon LONGITUD
```

Para consultar hasta tres días y guardar la gráfica sin abrir una ventana:

```powershell
python solar_report.py --lat LATITUD --lon LONGITUD --days 3 --save
```

Configura el sistema fotovoltaico con `--kwp`, `--tilt`, `--azimuth` y
`--performance-ratio`:

```powershell
python solar_report.py --lat LATITUD --lon LONGITUD `
  --kwp 5 --tilt 20 --azimuth 0 --performance-ratio 0.8 --save
```

También se puede usar el comando instalado:

```powershell
solar-report --lat LATITUD --lon LONGITUD --save
```

### Parámetros principales

| Opción | Descripción | Valor predeterminado |
|---|---|---:|
| `--lat` | Latitud, entre -90 y 90. Obligatoria para pronosticar. | — |
| `--lon` | Longitud, entre -180 y 180. Obligatoria para pronosticar. | — |
| `--kwp` | Potencia nominal del sistema, en kWp. | `1.0` |
| `--tilt` | Inclinación de los paneles, en grados. | `20` |
| `--azimuth` | Orientación del panel: `0` sur, negativo este, positivo oeste. | `0` |
| `--performance-ratio` | Factor de rendimiento supuesto entre 0 y 1. | `0.8` |
| `--days` | Horizonte del pronóstico: 1, 2 o 3 días. | `1` |
| `--save` | Guarda la gráfica sin abrir una ventana. | desactivado |
| `--output-dir` | Carpeta raíz para guardar los reportes. | `reportes_solares` |
| `--finalize AAAA-MM-DD` | Genera el resumen de una fecha a partir de sus archivos guardados. | — |

## Archivos generados

Cada ejecución guarda un JSON y una gráfica en
`reportes_solares/AAAA-MM-DD/HH/`. Ejecutar de nuevo en la misma hora reemplaza
esos dos archivos. Una ejecución a las 19:00 genera también el resumen diario:

```text
reportes_solares/
└── AAAA-MM-DD/
    ├── 06/
    │   ├── reporte.json
    │   └── grafica.png
    ├── ...
    ├── 19/
    │   ├── reporte.json
    │   └── grafica.png
    ├── resumen_diario.json
    ├── resumen_diario.png
    └── index.html
```

Para crear o actualizar manualmente el resumen:

```powershell
python solar_report.py --finalize 2026-10-27
```

Los archivos de salida contienen coordenadas y resultados de cada consulta. Se
excluyen del control de versiones mediante `.gitignore`; no publiques estos
datos salvo que quieras compartirlos explícitamente.

## Métricas y metodología

- **GTI**: irradiancia global en el plano inclinado del panel, en W/m².
- **GHI, DNI y DHI**: irradiancia global horizontal, directa normal y difusa
  horizontal, en W/m².
- **Potencia estimada**: `GTI / 1000 × kWp × PR`, en kW.
- **Energía diaria pronosticada**: integra la GTI horaria entre amanecer y
  anochecer mediante interpolación lineal, incluidos ambos límites solares.
- **Horas pico de sol (HPS)**: GTI diaria integrada dividida entre 1000 W/m².
- **Mejor ventana**: tres muestras horarias consecutivas con la mayor GTI
  acumulada.

La integración es una estimación a partir del pronóstico horario. El resultado
depende del modelo meteorológico, de la resolución temporal y de los parámetros
del sistema; no es energía observada ni una garantía de producción.

## Datos y atribución

Los datos meteorológicos se obtienen de
[Open-Meteo](https://open-meteo.com/). Consulta sus
[términos y atribución](https://open-meteo.com/en/terms) antes de redistribuir
datos o usar el servicio en otro contexto. El cálculo solar local utiliza
[pvlib-python](https://pvlib-python.readthedocs.io/).

## Licencia

Este proyecto se distribuye bajo la licencia MIT. Consulta [LICENSE](LICENSE).
