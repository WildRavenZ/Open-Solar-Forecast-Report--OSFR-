"""Estimación horaria de producción solar a partir del pronóstico Open-Meteo."""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import pvlib
import requests


API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = [
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
]
CURRENT_FIELDS = [
    "cloud_cover",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
]
DAILY_FIELDS = [
    "sunrise",
    "sunset",
    "daylight_duration",
]


def parse_args() -> argparse.Namespace:
    """Define y valida los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Estima la energía solar disponible a partir del pronóstico horario."
    )
    parser.add_argument(
        "--lat",
        type=float,
        help="Latitud (-90 a 90); necesaria para generar un pronóstico.",
    )
    parser.add_argument(
        "--lon",
        type=float,
        help="Longitud (-180 a 180); necesaria para generar un pronóstico.",
    )
    parser.add_argument("--tilt", type=float, default=20, help="Inclinación del panel en grados.")
    parser.add_argument(
        "--azimuth",
        type=float,
        default=0,
        help="Orientación del panel: 0=sur, negativo=este, positivo=oeste.",
    )
    parser.add_argument("--kwp", type=float, default=1.0, help="Potencia nominal en kWp.")
    parser.add_argument(
        "--performance-ratio",
        type=float,
        default=0.8,
        help="Rendimiento global del sistema (0 a 1).",
    )
    parser.add_argument("--days", type=int, choices=range(1, 4), default=1, help="Días (1 a 3).")
    parser.add_argument(
        "--save",
        action="store_true",
        help="Guarda la figura sin abrir una ventana gráfica.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reportes_solares"),
        help="Carpeta raíz para guardar los reportes (default: reportes_solares).",
    )
    parser.add_argument(
        "--finalize",
        type=date.fromisoformat,
        metavar="AAAA-MM-DD",
        help="Genera el resumen diario desde los JSON guardados para esa fecha.",
    )
    args = parser.parse_args()

    if args.finalize is None and (args.lat is None or args.lon is None):
        parser.error("--lat y --lon son necesarios para generar un pronóstico.")

    numeric_options = ["tilt", "azimuth", "kwp", "performance_ratio"]
    if args.lat is not None:
        numeric_options.append("lat")
    if args.lon is not None:
        numeric_options.append("lon")
    for name in numeric_options:
        if not math.isfinite(getattr(args, name)):
            parser.error(f"--{name.replace('_', '-')} debe ser un número finito.")
    if args.lat is not None and not -90 <= args.lat <= 90:
        parser.error("--lat debe estar entre -90 y 90.")
    if args.lon is not None and not -180 <= args.lon <= 180:
        parser.error("--lon debe estar entre -180 y 180.")
    if not 0 <= args.tilt <= 90:
        parser.error("--tilt debe estar entre 0 y 90.")
    if not -180 <= args.azimuth <= 180:
        parser.error("--azimuth debe estar entre -180 y 180.")
    if args.kwp <= 0:
        parser.error("--kwp debe ser mayor que cero.")
    if not 0 < args.performance_ratio <= 1:
        parser.error("--performance-ratio debe ser mayor que 0 y como máximo 1.")
    return args


def fetch_data(
    lat: float, lon: float, tilt: float, azimuth: float, days: int
) -> dict[str, Any]:
    """Obtiene valores horarios, condiciones actuales y eventos solares."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(CURRENT_FIELDS),
        "hourly": ",".join(HOURLY_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": days,
        "past_hours": 24,
        "tilt": tilt,
        "azimuth": azimuth,
    }
    try:
        response = requests.get(API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Open-Meteo devolvió una respuesta JSON inválida: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"No se pudo consultar Open-Meteo: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("La respuesta de Open-Meteo no tiene el formato esperado.")
    missing = [key for key in ("timezone", "current", "hourly", "daily") if key not in data]
    if missing:
        raise RuntimeError(f"Respuesta incompleta de Open-Meteo: falta {', '.join(missing)}.")
    if (
        not isinstance(data["current"], dict)
        or "time" not in data["current"]
        or any(key not in data["current"] for key in CURRENT_FIELDS)
    ):
        raise RuntimeError("Respuesta incompleta de Open-Meteo: faltan campos actuales requeridos.")
    for key in CURRENT_FIELDS:
        try:
            float(data["current"][key])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Respuesta incompleta de Open-Meteo: current.{key} no es válido."
            ) from exc

    hourly = data["hourly"]
    if not isinstance(hourly, dict):
        raise RuntimeError("Respuesta incompleta de Open-Meteo: hourly no es válido.")
    missing_hourly = [key for key in ("time", *HOURLY_FIELDS) if key not in hourly]
    if missing_hourly:
        raise RuntimeError(
            f"Respuesta incompleta de Open-Meteo: falta {', '.join(missing_hourly)}."
        )
    expected_length = len(hourly["time"])
    if not expected_length or any(
        not isinstance(hourly[key], list) or len(hourly[key]) != expected_length
        for key in HOURLY_FIELDS
    ):
        raise RuntimeError("Respuesta incompleta de Open-Meteo: las series horarias no coinciden.")
    daily = data["daily"]
    if not isinstance(daily, dict) or any(
        key not in daily or not isinstance(daily[key], list)
        for key in ("time", *DAILY_FIELDS)
    ):
        raise RuntimeError("Respuesta incompleta de Open-Meteo: los datos solares diarios no son válidos.")
    daily_length = len(daily["time"])
    if not daily_length or any(len(daily[key]) != daily_length for key in DAILY_FIELDS):
        raise RuntimeError("Respuesta incompleta de Open-Meteo: los datos solares diarios no coinciden.")
    return data


def make_hourly_frame(data: dict[str, Any]) -> pd.DataFrame:
    """Convierte las series de la API a un DataFrame horario y localizado."""
    timezone = data["timezone"]
    try:
        times = pd.DatetimeIndex(pd.to_datetime(data["hourly"]["time"]))
        times = times.tz_localize(timezone, ambiguous="infer", nonexistent="shift_forward")
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError(f"No se pudo interpretar la zona horaria del pronóstico: {exc}") from exc

    frame = pd.DataFrame(
        {name: data["hourly"][name] for name in HOURLY_FIELDS},
        index=times,
    )
    frame.index.name = "time"
    frame = frame.apply(pd.to_numeric, errors="coerce")
    return frame


def compute_sun_position(
    frame: pd.DataFrame, lat: float, lon: float
) -> pd.DataFrame:
    """Calcula elevación y azimut solar localmente con pvlib."""
    solar_position = pvlib.solarposition.get_solarposition(
        time=frame.index,
        latitude=lat,
        longitude=lon,
    )
    result = frame.copy()
    result["solar_elevation"] = solar_position["apparent_elevation"]
    result["solar_azimuth"] = solar_position["azimuth"]
    return result


def estimate_power(frame: pd.DataFrame, kwp: float, performance_ratio: float) -> pd.DataFrame:
    """Calcula potencia instantánea estimada a partir de la GTI horaria."""
    result = frame.copy()
    result["power_kw"] = (
        result["global_tilted_irradiance"] / 1000 * kwp * performance_ratio
    )
    return result


def integrate_daylight_energy(
    hourly_frame: pd.DataFrame,
    sunrise: pd.Timestamp,
    sunset: pd.Timestamp,
    kwp: float,
    performance_ratio: float,
) -> tuple[float, float]:
    """Integra GTI entre amanecer y anochecer con interpolación lineal horaria."""
    gti = hourly_frame["global_tilted_irradiance"].dropna()
    if len(gti) < 2:
        raise RuntimeError("No hay suficientes muestras para integrar la energía diaria.")

    daylight_index = gti.index.union(pd.DatetimeIndex([sunrise, sunset])).sort_values()
    daylight_gti = gti.reindex(daylight_index).interpolate(method="time")
    daylight_gti = daylight_gti.loc[
        (daylight_gti.index >= sunrise) & (daylight_gti.index <= sunset)
    ]
    if len(daylight_gti) < 2 or daylight_gti.isna().any():
        raise RuntimeError("No se pudo interpolar la irradiancia entre amanecer y anochecer.")

    elapsed_hours = (
        daylight_gti.index.to_series().diff().dt.total_seconds().iloc[1:] / 3600
    )
    average_gti = (
        daylight_gti.iloc[:-1].to_numpy() + daylight_gti.iloc[1:].to_numpy()
    ) / 2
    gti_wh_m2 = float((average_gti * elapsed_hours.to_numpy()).sum())
    estimated_energy_kwh = gti_wh_m2 * kwp * performance_ratio / 1000
    return estimated_energy_kwh, gti_wh_m2 / 1000


def best_production_window(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, float] | None:
    """Busca la ventana de tres horas consecutivas con mayor irradiación."""
    daylight = frame.loc[frame["solar_elevation"] > 0]
    if daylight.empty:
        return None

    best: tuple[pd.Timestamp, pd.Timestamp, float] | None = None
    for day in pd.Index(daylight.index.date).unique():
        daily = daylight.loc[[timestamp.date() == day for timestamp in daylight.index]]
        for start in range(len(daily)):
            window = daily.iloc[start : start + 3]
            if len(window) == 3 and all(
                window.index[i + 1] - window.index[i] == pd.Timedelta(hours=1)
                for i in range(2)
            ):
                total_gti = float(window["global_tilted_irradiance"].sum())
                candidate = (window.index[0], window.index[-1], total_gti)
                if best is None or total_gti > best[2]:
                    best = candidate
    if best is not None:
        return best
    peak_time = daylight["global_tilted_irradiance"].idxmax()
    return peak_time, peak_time, float(daylight.loc[peak_time, "global_tilted_irradiance"])


def daily_solar_events(data: dict[str, Any]) -> dict[Any, tuple[pd.Timestamp, pd.Timestamp, float]]:
    """Interpreta amanecer, anochecer y duración diurna de la API."""
    timezone = data["timezone"]
    events: dict[Any, tuple[pd.Timestamp, pd.Timestamp, float]] = {}
    try:
        for day, sunrise, sunset, duration in zip(
            data["daily"]["time"],
            data["daily"]["sunrise"],
            data["daily"]["sunset"],
            data["daily"]["daylight_duration"],
        ):
            sunrise_time = pd.Timestamp(sunrise).tz_localize(timezone)
            sunset_time = pd.Timestamp(sunset).tz_localize(timezone)
            events[pd.Timestamp(day).date()] = (
                sunrise_time,
                sunset_time,
                float(duration) / 3600,
            )
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError(f"No se pudieron interpretar los eventos solares diarios: {exc}") from exc
    return events


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Escribe JSON completo antes de reemplazar el archivo de destino."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as exc:
        raise RuntimeError(f"No se pudo guardar el archivo {path}: {exc}") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_hourly_record(
    frame: pd.DataFrame,
    data: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Construye el registro persistente de una ejecución."""
    timezone = data["timezone"]
    current_time = pd.Timestamp(data["current"]["time"])
    if current_time.tzinfo is None:
        current_time = current_time.tz_localize(timezone)
    events = daily_solar_events(data)
    daylight = frame.loc[frame["solar_elevation"] > 0]
    hourly_data: list[dict[str, Any]] = []
    for timestamp, row in daylight.iterrows():
        if timestamp < current_time:
            data_type = "model_output_for_past_hour"
        elif timestamp == current_time:
            data_type = "hourly_model_at_query_time"
        else:
            data_type = "forecast"
        hourly_data.append(
            {
                "time": timestamp.isoformat(),
                "data_type": data_type,
                "cloud_cover_percent": float(row["cloud_cover"]),
                "solar_elevation_degrees": float(row["solar_elevation"]),
                "solar_azimuth_degrees": float(row["solar_azimuth"]),
                "gti_w_m2": float(row["global_tilted_irradiance"]),
                "estimated_power_kw": float(row["power_kw"]),
            }
        )

    daily_summaries: list[dict[str, Any]] = []
    for day in pd.Index(daylight.index.date).unique():
        daily = daylight.loc[[timestamp.date() == day for timestamp in daylight.index]]
        peak_time = daily["power_kw"].idxmax()
        solar_event = events.get(day)
        if solar_event is None:
            raise RuntimeError(f"No hay datos de amanecer y anochecer para {day}.")
        sunrise, sunset, daylight_hours = solar_event
        full_day = frame.loc[[timestamp.date() == day for timestamp in frame.index]]
        energy_kwh, peak_sun_hours = integrate_daylight_energy(
            full_day,
            sunrise,
            sunset,
            args.kwp,
            args.performance_ratio,
        )
        summary: dict[str, Any] = {
            "date": day.isoformat(),
            "estimated_energy_kwh": energy_kwh,
            "peak_sun_hours": peak_sun_hours,
            "maximum_estimated_power_time": peak_time.isoformat(),
            "sunrise": sunrise.isoformat(),
            "sunset": sunset.isoformat(),
            "daylight_duration_hours": daylight_hours,
            "energy_method": (
                "Piecewise-linear integration of hourly GTI from sunrise to sunset, "
                "including interpolated daylight boundaries."
            ),
        }
        window = best_production_window(daily)
        if window is not None:
            start, end, total_gti = window
            summary["best_window"] = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "gti_total_wh_m2": total_gti,
            }
        daily_summaries.append(summary)

    current = data["current"]
    current_position = pvlib.solarposition.get_solarposition(
        time=pd.DatetimeIndex([current_time]),
        latitude=args.lat,
        longitude=args.lon,
    )
    return {
        "schema_version": 2,
        "captured_at": current_time.isoformat(),
        "date": current_time.date().isoformat(),
        "hour": current_time.strftime("%H"),
        "timezone": timezone,
        "location": {"latitude": args.lat, "longitude": args.lon},
        "panel": {
            "capacity_kwp": args.kwp,
            "tilt_degrees": args.tilt,
            "azimuth_degrees": args.azimuth,
            "performance_ratio": args.performance_ratio,
        },
        "methodology": {
            "data_source": "Open-Meteo forecast/model output",
            "power_estimate": "GTI / 1000 * panel kWp * performance ratio",
            "daily_energy_estimate": (
                "Piecewise-linear integration of hourly GTI between sunrise and "
                "sunset, with interpolated boundary values."
            ),
            "measurements_included": False,
        },
        "current_conditions": {
            "source": "Open-Meteo current API model output; not a panel measurement",
            "cloud_cover_percent": float(current["cloud_cover"]),
            "ghi_w_m2": float(current["shortwave_radiation"]),
            "dni_w_m2": float(current["direct_normal_irradiance"]),
            "dhi_w_m2": float(current["diffuse_radiation"]),
            "gti_w_m2": float(current["global_tilted_irradiance"]),
            "solar_elevation_degrees": float(
                current_position["apparent_elevation"].iloc[0]
            ),
            "estimated_power_kw": (
                float(current["global_tilted_irradiance"])
                / 1000
                * args.kwp
                * args.performance_ratio
            ),
        },
        "daily_summaries": daily_summaries,
        "hourly_data": hourly_data,
        "files": {},
    }


def generate_daily_report(output_dir: Path, report_date: date) -> Path:
    """Combina los JSON y gráficas horarios en un resumen diario."""
    date_dir = output_dir / report_date.isoformat()
    record_paths = sorted(date_dir.glob("[0-2][0-9]/reporte.json"))
    if not record_paths:
        raise RuntimeError(
            f"No hay reportes horarios guardados para {report_date.isoformat()} en {date_dir}."
        )

    records_with_paths: list[tuple[Path, dict[str, Any]]] = []
    for record_path in record_paths:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo leer el registro {record_path}: {exc}") from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"El registro {record_path} no contiene un objeto JSON.")
        if record.get("date") != report_date.isoformat():
            raise RuntimeError(f"El registro {record_path} no corresponde a la fecha esperada.")
        if not all(
            key in record
            for key in (
                "captured_at",
                "timezone",
                "panel",
                "location",
                "current_conditions",
                "hourly_data",
                "daily_summaries",
            )
        ):
            raise RuntimeError(f"El registro {record_path} no contiene los campos requeridos.")
        conditions = record["current_conditions"]
        if not isinstance(conditions, dict) or any(
            key not in conditions
            for key in ("cloud_cover_percent", "gti_w_m2", "estimated_power_kw")
        ):
            raise RuntimeError(f"El registro {record_path} contiene condiciones actuales incompletas.")
        if not isinstance(record["hourly_data"], list) or not isinstance(
            record["daily_summaries"], list
        ):
            raise RuntimeError(f"El registro {record_path} contiene series diarias u horarias no válidas.")
        if not isinstance(record["captured_at"], str) or not isinstance(
            record["timezone"], str
        ):
            raise RuntimeError(f"El registro {record_path} contiene fecha u hora no válidas.")
        if any(
            not isinstance(item, dict)
            or any(key not in item for key in ("time", "data_type", "estimated_power_kw"))
            for item in record["hourly_data"]
        ):
            raise RuntimeError(f"El registro {record_path} contiene datos horarios incompletos.")
        if any(
            not isinstance(item, dict) or "date" not in item
            for item in record["daily_summaries"]
        ):
            raise RuntimeError(f"El registro {record_path} contiene resúmenes diarios incompletos.")
        if not (record_path.parent / "grafica.png").is_file():
            raise RuntimeError(f"Falta la gráfica horaria asociada a {record_path}.")
        records_with_paths.append((record_path, record))

    records_with_paths.sort(key=lambda item: item[1]["captured_at"])
    record_paths = [item[0] for item in records_with_paths]
    records = [item[1] for item in records_with_paths]
    reference = records[0]
    if any(
        record["timezone"] != reference["timezone"]
        or record["panel"] != reference["panel"]
        or record["location"] != reference["location"]
        for record in records[1:]
    ):
        raise RuntimeError(
            "Los registros del día usan zonas horarias, ubicaciones o parámetros de panel "
            "distintos; no se pueden combinar en un único resumen."
        )
    timezone = records[-1]["timezone"]
    panel = records[-1]["panel"]
    latest_daily_summary = next(
        (
            summary
            for summary in records[-1].get("daily_summaries", [])
            if summary["date"] == report_date.isoformat()
        ),
        None,
    )
    if latest_daily_summary is not None and (
        any(
            key not in latest_daily_summary
            for key in ("estimated_energy_kwh", "peak_sun_hours")
        )
        or not any(
            key in latest_daily_summary
            for key in ("maximum_estimated_power_time", "peak_production_time")
        )
    ):
        raise RuntimeError(
            f"La última estimación guardada para {report_date.isoformat()} está incompleta."
        )
    daily_report = {
        "schema_version": 2,
        "date": report_date.isoformat(),
        "timezone": timezone,
        "panel": panel,
        "hourly_snapshots": records,
        "hourly_report_count": len(records),
        "latest_model_daily_estimate": latest_daily_summary,
        "methodology": (
            "All values are Open-Meteo model estimates. Daily energy is obtained by "
            "integrating hourly GTI from sunrise to sunset with interpolated boundaries."
        ),
        "note": (
            "Los valores actuales y horarios son datos de Open-Meteo y estimaciones "
            "del modelo; no son mediciones de producción del panel."
        ),
    }

    figure_path = date_dir / "resumen_diario.png"
    figure, axes = plt.subplots(2, 1, figsize=(13, 9), constrained_layout=True)
    capture_times = [pd.Timestamp(record["captured_at"]) for record in records]
    current_gti = [
        record["current_conditions"]["gti_w_m2"] for record in records
    ]
    current_power = [
        record["current_conditions"]["estimated_power_kw"] for record in records
    ]
    axes[0].plot(
        capture_times,
        current_gti,
        marker="o",
        color="tab:red",
        label="GTI del modelo al consultar",
    )
    axes[0].set_title(f"GTI estimada por Open-Meteo — {report_date.isoformat()}")
    axes[0].set_ylabel("GTI (W/m²)")
    axes[0].legend()

    for index, record in enumerate(records):
        forecasts = [
            item
            for item in record.get("hourly_data", [])
            if item["time"][:10] == report_date.isoformat()
            and item["data_type"]
            in ("forecast", "hourly_model_at_query_time", "Pronóstico")
        ]
        if forecasts:
            forecast_times = [pd.Timestamp(item["time"]) for item in forecasts]
            forecast_power = [item["estimated_power_kw"] for item in forecasts]
            axes[1].plot(
                forecast_times,
                forecast_power,
                color="tab:blue",
                alpha=0.25 if index < len(records) - 1 else 0.9,
                linewidth=1 if index < len(records) - 1 else 2,
                label="Pronósticos guardados en cada consulta" if index == 0 else None,
            )
    axes[1].plot(
        capture_times,
        current_power,
        marker="o",
        color="tab:red",
        linewidth=2,
        label="Potencia estimada al consultar (API)",
    )
    axes[1].set_title("Evolución de los pronósticos de potencia")
    axes[1].set_ylabel("Potencia estimada (kW)")
    axes[1].set_xlabel(f"Hora local ({timezone})")
    axes[1].xaxis.set_major_formatter(
        mdates.DateFormatter("%H:%M", tz=records[-1]["timezone"])
    )
    axes[1].legend()
    for ax in axes:
        ax.grid(True, alpha=0.25)
    try:
        figure.savefig(figure_path, dpi=150)
    except OSError as exc:
        plt.close(figure)
        raise RuntimeError(f"No se pudo guardar la gráfica diaria {figure_path}: {exc}") from exc
    plt.close(figure)

    table_rows: list[str] = []
    gallery: list[str] = []
    for record_path, record in zip(record_paths, records):
        captured_at = pd.Timestamp(record["captured_at"]).strftime("%H:%M")
        conditions = record["current_conditions"]
        image_path = f"{record_path.parent.name}/grafica.png"
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(captured_at)}</td>"
            f"<td>{conditions['cloud_cover_percent']:.0f}% (API)</td>"
            f"<td>{conditions['gti_w_m2']:.1f} (API)</td>"
            f"<td>{conditions['estimated_power_kw']:.3f} (estimada)</td>"
            f"<td><a href=\"{html.escape(image_path)}\">Ver gráfica</a></td>"
            "</tr>"
        )
        gallery.append(
            f"<section><h2>Consulta de {html.escape(captured_at)}</h2>"
            f"<a href=\"{html.escape(image_path)}\">"
            f"<img src=\"{html.escape(image_path)}\" alt=\"Gráfica de {html.escape(captured_at)}\">"
            "</a></section>"
        )

    daily_summary_html = ""
    if latest_daily_summary is not None:
        peak_time_key = (
            "maximum_estimated_power_time"
            if "maximum_estimated_power_time" in latest_daily_summary
            else "peak_production_time"
        )
        daily_summary_html = (
            "<h2>Último pronóstico diario del modelo</h2><ul>"
            f"<li>Energía diaria pronosticada: "
            f"{latest_daily_summary['estimated_energy_kwh']:.2f} kWh</li>"
            f"<li>Horas pico de sol pronosticadas: "
            f"{latest_daily_summary['peak_sun_hours']:.2f} h</li>"
            f"<li>Máxima potencia estimada: "
            f"{html.escape(pd.Timestamp(latest_daily_summary[peak_time_key]).strftime('%H:%M'))}</li>"
            "</ul>"
        )
        if "energy_method" in latest_daily_summary:
            daily_summary_html += (
                "<p>Método de energía: integración lineal de la GTI horaria entre "
                "amanecer y anochecer, con interpolación en ambos límites.</p>"
            )

    html_content = (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        f"<title>Reporte solar {report_date.isoformat()}</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:2rem auto}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5rem}"
        "img{max-width:100%;height:auto}section{margin:2rem 0}"
        "</style></head><body>"
        f"<h1>Reporte solar diario — {report_date.isoformat()}</h1>"
        "<p>Este informe contiene pronósticos y estimaciones de Open-Meteo; "
        "no incluye mediciones ni producción observada del panel.</p>"
        + daily_summary_html
        + "<h2>Consultas guardadas</h2>"
        "<table><thead><tr><th>Consulta</th><th>Nubosidad del modelo</th>"
        "<th>GTI de Open-Meteo (W/m²)</th><th>Potencia estimada (kW)</th>"
        "<th>Detalle</th></tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table>"
        "<h2>Evolución de pronósticos</h2><img src=\"resumen_diario.png\" "
        "alt=\"Evolución de condiciones y pronósticos\">"
        "<h2>Gráficas por hora</h2>"
        + "".join(gallery)
        + "</body></html>"
    )

    html_path = date_dir / "index.html"
    try:
        html_path.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"No se pudo guardar el reporte HTML {html_path}: {exc}") from exc

    daily_report["files"] = {
        "summary_json": "resumen_diario.json",
        "summary_chart": "resumen_diario.png",
        "html_report": "index.html",
    }
    atomic_write_json(date_dir / "resumen_diario.json", daily_report)
    return html_path


def print_report(frame: pd.DataFrame, data: dict[str, Any], args: argparse.Namespace) -> None:
    """Imprime historial modelado, condiciones actuales y pronóstico diurno."""
    timezone = data["timezone"]
    try:
        current_time = pd.Timestamp(data["current"]["time"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Open-Meteo devolvió una hora actual no válida.") from exc
    if current_time.tzinfo is None:
        current_time = current_time.tz_localize(timezone)
    current = data["current"]
    current_power = (
        float(current["global_tilted_irradiance"]) / 1000
        * args.kwp
        * args.performance_ratio
    )
    current_position = pvlib.solarposition.get_solarposition(
        time=pd.DatetimeIndex([current_time]),
        latitude=args.lat,
        longitude=args.lon,
    )
    events = daily_solar_events(data)

    print("=" * 86)
    print(
        f"REPORTE SOLAR | Coordenadas: {args.lat:.4f}, {args.lon:.4f} | "
        f"Zona horaria: {timezone}"
    )
    print(
        f"Panel: {args.kwp:.2f} kWp | Inclinación: {args.tilt:.1f}° | "
        f"Azimut: {args.azimuth:.1f}° (0° = sur) | PR: {args.performance_ratio:.2f}"
    )
    print(f"Hora de consulta Open-Meteo: {current_time.strftime('%Y-%m-%d %H:%M')}")
    print(
        "Nota metodológica: todos los datos horarios y actuales son resultados de "
        "Open-Meteo; no son mediciones del panel."
    )
    print(
        "La energía diaria se estima integrando la GTI horaria entre amanecer y "
        "anochecer, con interpolación en ambos límites."
    )
    print(
        "\nValores de Open-Meteo para el momento de consulta "
        f"({float(current_position['apparent_elevation'].iloc[0]):.1f}° de elevación):"
    )
    print(
        f"Nubosidad {float(current['cloud_cover']):.0f}% | "
        f"GHI {float(current['shortwave_radiation']):.1f} W/m² | "
        f"DNI {float(current['direct_normal_irradiance']):.1f} W/m² | "
        f"DHI {float(current['diffuse_radiation']):.1f} W/m² | "
        f"GTI {float(current['global_tilted_irradiance']):.1f} W/m² | "
        f"potencia estimada {current_power:.3f} kW"
    )

    daylight = frame.loc[frame["solar_elevation"] > 0]
    print("\nPronóstico/modelo horario diurno (sin horas nocturnas)")
    print(
        f"{'Hora':<19} {'Tipo de dato':<24} {'Nubes %':>8} {'Elev. °':>9} "
        f"{'Azimut °':>10} {'GTI W/m²':>11} {'Potencia kW':>13}"
    )
    print("-" * 111)
    for timestamp, row in daylight.iterrows():
        if timestamp == current_time:
            continue
        if timestamp.date() == current_time.date() and timestamp < current_time:
            data_type = "Modelo hora pasada"
        else:
            data_type = "Pronóstico"
        print(
            f"{timestamp.strftime('%Y-%m-%d %H:%M'):<19} "
            f"{data_type:<24} "
            f"{row['cloud_cover']:>8.0f} {row['solar_elevation']:>9.1f} "
            f"{row['solar_azimuth']:>10.1f} "
            f"{row['global_tilted_irradiance']:>11.1f} {row['power_kw']:>13.3f}"
        )

    print("\nResumen solar diario")
    if daylight.empty:
        print("No hay horas de luz solar en el periodo consultado.")
    else:
        dates = pd.Index(daylight.index.date).unique()
        for day in dates:
            daily_daylight = daylight.loc[
                [timestamp.date() == day for timestamp in daylight.index]
            ]
            full_day = frame.loc[[timestamp.date() == day for timestamp in frame.index]]
            peak = daily_daylight["power_kw"].idxmax()
            solar_event = events.get(day)
            if solar_event is None:
                raise RuntimeError(f"No hay datos de amanecer y anochecer para {day}.")
            sunrise, sunset, daylight_hours = solar_event
            energy, sun_hours = integrate_daylight_energy(
                full_day,
                sunrise,
                sunset,
                args.kwp,
                args.performance_ratio,
            )
            print(
                f"{day}: amanecer {sunrise.strftime('%H:%M')} | "
                f"anochecer {sunset.strftime('%H:%M')} | "
                f"luz diurna {daylight_hours:.2f} h"
            )
            print(
                f"  energía diaria pronosticada {energy:.2f} kWh | "
                f"HPS pronosticadas {sun_hours:.2f} h | "
                f"máxima potencia estimada {peak.strftime('%H:%M')}"
            )

        window = best_production_window(daylight)
        if window:
            start, end, total_gti = window
            end_time = end.strftime("%H:%M")
            if start.date() == end.date() and start != end:
                end_time = f"{end_time} (última hora)"
            print(
                f"Mejor ventana de pronóstico (3 muestras horarias): "
                f"{start.strftime('%Y-%m-%d %H:%M')} "
                f"a {end_time}, GTI acumulada {total_gti:.1f} Wh/m²."
            )
            if start == end:
                print("Nota: no hay tres horas diurnas consecutivas disponibles; se muestra la hora pico.")


def plot_report(
    frame: pd.DataFrame,
    data: dict[str, Any],
    args: argparse.Namespace,
    figure_path: Path,
    show: bool,
) -> None:
    """Dibuja solo horas diurnas y etiqueta explícitamente la zona horaria."""
    daylight = frame.loc[frame["solar_elevation"] > 0]
    if daylight.empty:
        figure, axis = plt.subplots(figsize=(8, 4), constrained_layout=True)
        axis.text(
            0.5,
            0.5,
            "No hay horas de luz solar en el periodo consultado.",
            ha="center",
            va="center",
        )
        axis.set_axis_off()
        try:
            figure_path.parent.mkdir(parents=True, exist_ok=True)
            figure.savefig(figure_path, dpi=150)
        except OSError as exc:
            plt.close(figure)
            raise RuntimeError(
                f"No se pudo guardar la gráfica {figure_path}: {exc}"
            ) from exc
        plt.close(figure)
        print(f"\nGráfica guardada en {figure_path}")
        return

    timezone = data["timezone"]
    current_time = pd.Timestamp(data["current"]["time"])
    if current_time.tzinfo is None:
        current_time = current_time.tz_localize(timezone)
    current = data["current"]
    current_day = current_time.date()
    current_power = (
        float(current["global_tilted_irradiance"]) / 1000
        * args.kwp
        * args.performance_ratio
    )
    events = daily_solar_events(data)

    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharex=True, constrained_layout=True)
    cloud_ax, irradiance_ax, power_ax = axes

    cloud_ax.plot(daylight.index, daylight["cloud_cover"], label="Total", linewidth=2)
    cloud_ax.scatter(
        [current_time],
        [float(current["cloud_cover"])],
        color="tab:red",
        zorder=5,
        label="Consulta actual API",
    )
    for column, label in (
        ("cloud_cover_low", "Baja"),
        ("cloud_cover_mid", "Media"),
        ("cloud_cover_high", "Alta"),
    ):
        cloud_ax.plot(daylight.index, daylight[column], label=label, alpha=0.8)
    cloud_ax.set_title("Nubosidad por hora")
    cloud_ax.set_ylabel("Nubosidad (%)")
    cloud_ax.set_ylim(0, 100)
    cloud_ax.legend(ncol=4, loc="upper right")

    for column, label in (
        ("shortwave_radiation", "GHI"),
        ("direct_normal_irradiance", "DNI"),
        ("diffuse_radiation", "DHI"),
        ("global_tilted_irradiance", "GTI"),
    ):
        irradiance_ax.plot(daylight.index, daylight[column], label=label)
    irradiance_ax.scatter(
        [current_time],
        [float(current["global_tilted_irradiance"])],
        color="tab:red",
        zorder=5,
        label="GTI de Open-Meteo al consultar",
    )
    irradiance_ax.set_title("Irradiancia por hora")
    irradiance_ax.set_ylabel("Irradiancia (W/m²)")
    irradiance_ax.legend(ncol=4, loc="upper right")

    past = daylight.loc[
        [
            timestamp.date() == current_day and timestamp < current_time
            for timestamp in daylight.index
        ]
    ]
    forecast = daylight.loc[
        [
            timestamp.date() > current_day or timestamp > current_time
            for timestamp in daylight.index
        ]
    ]
    power_ax.bar(
        past.index,
        past["power_kw"],
        width=0.03,
        color="tab:gray",
        label="Modelo para horas pasadas",
    )
    power_ax.bar(
        forecast.index,
        forecast["power_kw"],
        width=0.03,
        color="tab:blue",
        label="Pronóstico",
    )
    power_ax.scatter(
        [current_time],
        [current_power],
        color="tab:red",
        zorder=5,
        label="Potencia estimada al consultar (API)",
    )
    power_ax.set_title("Potencia estimada y elevación solar")
    power_ax.set_ylabel("Potencia (kW)")
    elevation_ax = power_ax.twinx()
    elevation_ax.plot(
        daylight.index,
        daylight["solar_elevation"],
        color="tab:orange",
        label="Elevación solar",
    )
    elevation_ax.set_ylabel("Elevación solar (°)")
    elevation_ax.set_ylim(bottom=0)

    for ax in axes:
        ax.grid(True, alpha=0.25)
        ax.axvline(current_time, color="tab:red", linestyle="--", alpha=0.65)
    power_ax.legend(loc="upper left")
    elevation_ax.legend(loc="upper right")
    power_ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%H:%M\n%d-%b", tz=daylight.index.tz)
    )
    power_ax.set_xlabel(f"Hora local ({daylight.index.tz})")
    first_day = daylight.index[0].date()
    last_day = daylight.index[-1].date()
    if first_day in events and last_day in events:
        power_ax.set_xlim(events[first_day][0], events[last_day][1])

    try:
        figure_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(figure_path, dpi=150)
    except OSError as exc:
        plt.close(fig)
        raise RuntimeError(f"No se pudo guardar la gráfica {figure_path}: {exc}") from exc
    print(f"\nGráfica guardada en {figure_path}")
    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> None:
    """Ejecuta la consulta, los cálculos y la salida del reporte."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.finalize is not None:
        try:
            html_path = generate_daily_report(args.output_dir, args.finalize)
            print(f"Reporte diario generado: {html_path}")
        except RuntimeError as exc:
            raise SystemExit(f"Error: {exc}") from exc
        return

    try:
        data = fetch_data(args.lat, args.lon, args.tilt, args.azimuth, args.days)
        frame = make_hourly_frame(data)
        current_time = pd.Timestamp(data["current"]["time"])
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize(data["timezone"])
        first_day = current_time.date()
        last_day = first_day + pd.Timedelta(days=args.days - 1)
        frame = frame.loc[
            [
                first_day <= timestamp.date() <= last_day
                for timestamp in frame.index
            ]
        ]
        if frame.empty or frame.isna().any().any():
            raise RuntimeError(
                "Respuesta incompleta de Open-Meteo: faltan valores horarios "
                "para el periodo solicitado."
            )
        frame = compute_sun_position(frame, args.lat, args.lon)
        frame = estimate_power(frame, args.kwp, args.performance_ratio)
        print_report(frame, data, args)
        current_time = pd.Timestamp(data["current"]["time"])
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize(data["timezone"])
        run_dir = (
            args.output_dir
            / current_time.strftime("%Y-%m-%d")
            / current_time.strftime("%H")
        )
        figure_path = run_dir / "grafica.png"
        plot_report(frame, data, args, figure_path, show=not args.save)

        record = build_hourly_record(frame, data, args)
        record["files"] = {
            "hourly_json": "reporte.json",
            "hourly_chart": "grafica.png",
        }
        atomic_write_json(run_dir / "reporte.json", record)
        print(f"Registro JSON guardado en {run_dir / 'reporte.json'}")

        if current_time.hour == 19:
            html_path = generate_daily_report(args.output_dir, current_time.date())
            print(f"Reporte diario generado: {html_path}")
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()