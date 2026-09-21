"""
field_data.py — загрузка и разбор "сырых" таблиц лабораторных PVT-исследований
по месторождению (глубинные/поверхностные пробы, много скважин, горизонты,
блоки), а также построение зависимостей между свойствами (Rs vs Pb, Bo vs Pb,
вязкость vs Pb, свойство vs глубина и т.д.) с группировкой по горизонту/блоку
или без неё.

Реальные полевые PVT-отчёты не имеют единого стандарта: шапка таблицы может
занимать 1-3 строки со слитыми ячейками, единицы измерения встречаются то в
шапке, то в отдельной строке, колонка "Горизонт"/"блок" может вообще
отсутствовать, попадаются строки-агрегаты ("Среднее значение: горизонт ..."),
опечатки, смешение кириллицы/латиницы в одних и тех же кодах горизонтов
(например "PT-IV" и "РТ-IV" — визуально одинаково, но разные символы Unicode).

Поэтому этот модуль НЕ подразумевает жёсткий формат: он предлагает
автоматическое сопоставление колонок по ключевым словам, но пользователь
может поправить сопоставление руками в UI (см. app_pages/field_analysis.py).
"""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Нормализация "грязных" значений
# ---------------------------------------------------------------------------
def robust_float(value) -> float:
    """
    Пытается привести значение ячейки к float, устойчиво к типичному "мусору"
    в полевых таблицах: запятая как десятичный разделитель, пометки
    аномальных значений (*), пропуски ("-", "", None).

    Примеры: "1,7568*" -> 1.7568; "-" -> nan; None -> nan; 12.3 -> 12.3
    """
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value) if not (isinstance(value, float) and math.isnan(value)) else float("nan")
    text = str(value).strip()
    if text in ("", "-", "—", "н/д", "n/a"):
        return float("nan")
    text = text.replace(",", ".")
    text = re.sub(r"[^0-9.\-+eE]", "", text)  # убрать '*', пробелы, единицы и т.п.
    if text in ("", "-", "+", "."):
        return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


# Таблица визуально неотличимых кириллических букв, которые в кодах
# горизонтов/блоков смешиваются с латиницей (например "PT-IV" и "РТ-IV").
# Приводим код к единому виду (латиница + верхний регистр) только для
# ГРУППИРОВКИ/сравнения, чтобы такие дубли не считались разными горизонтами.
_CYRILLIC_TO_LATIN = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "A", "в": "B", "е": "E", "к": "K", "м": "M", "н": "H", "о": "O",
    "р": "P", "с": "C", "т": "T", "у": "Y", "х": "X",
})


def normalize_code(value) -> str | None:
    """
    Нормализует код горизонта/блока/скважины для устойчивой группировки:
    убирает лишние пробелы, приводит к верхнему регистру, заменяет визуально
    неотличимые кириллические буквы на латинские аналоги.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in ("nan", "none"):
        return None
    text = " ".join(text.split())  # схлопнуть повторные пробелы
    text = text.upper().translate(_CYRILLIC_TO_LATIN)
    return text


# ---------------------------------------------------------------------------
# Разбор многострочной шапки таблицы
# ---------------------------------------------------------------------------
def merge_header_rows(header_rows: list[tuple]) -> list[str]:
    """
    Склеивает несколько строк шапки Excel-таблицы в один заголовок на колонку.
    Например, если для колонки "Давление" в первой строке шапки написано
    "Давление", а во второй строке — "насыщения | Мпа", результат —
    "Давление | насыщения | Мпа".

    Параметры
    ---------
    header_rows : список строк шапки (каждая — кортеж значений ячеек,
                  как возвращает openpyxl ws.iter_rows(..., values_only=True))

    Возвращает
    ----------
    headers : список заголовков колонок (по одному на каждую колонку)
    """
    if not header_rows:
        return []
    n_cols = max(len(r) for r in header_rows)
    headers = []
    for col_idx in range(n_cols):
        parts = []
        for row in header_rows:
            if col_idx < len(row) and row[col_idx] is not None:
                text = str(row[col_idx]).strip()
                if text and text not in parts:
                    parts.append(text)
        headers.append(" | ".join(parts) if parts else f"Колонка {col_idx + 1}")
    return headers


def guess_header_and_data_rows(all_rows: list[tuple], max_header_rows: int = 3) -> tuple[int, int]:
    """
    Пытается автоматически определить, с какой строки начинаются данные
    (первая строка, где большинство ячеек в первых нескольких колонках —
    числа), и сколько строк выше неё составляют шапку.

    Эвристика рассчитана на типичные полевые PVT-таблицы, но это только
    ПОДСКАЗКА по умолчанию — пользователь должен иметь возможность
    скорректировать её в UI, т.к. единого стандарта оформления таких
    таблиц не существует.

    Параметры
    ---------
    all_rows : все строки листа (кортежи значений ячеек)
    max_header_rows : максимум строк, которые могут составлять шапку

    Возвращает
    ----------
    (header_start_idx, data_start_idx) : индексы (с 0) первой строки шапки
        и первой строки данных в all_rows
    """
    def _numeric_fraction(row: tuple) -> float:
        cells = [c for c in row[:8] if c is not None]
        if not cells:
            return 0.0
        numeric = sum(1 for c in cells if isinstance(c, (int, float)) or
                      (isinstance(c, str) and not math.isnan(robust_float(c))))
        return numeric / len(cells)

    data_start_idx = None
    for i, row in enumerate(all_rows):
        if _numeric_fraction(row) >= 0.5 and any(c is not None for c in row):
            data_start_idx = i
            break

    if data_start_idx is None:
        return 0, min(1, len(all_rows) - 1)

    header_start_idx = data_start_idx
    for back in range(1, max_header_rows + 1):
        idx = data_start_idx - back
        if idx < 0 or all(c is None for c in all_rows[idx]):
            break
        header_start_idx = idx

    return header_start_idx, data_start_idx


# ---------------------------------------------------------------------------
# Сопоставление колонок со стандартными полями (алиасы, гибкое распознавание)
# ---------------------------------------------------------------------------
def _normalize_header_text(header: str) -> str:
    # "№" — не буквенно-цифровой символ и обычным фильтром вырезался бы
    # целиком, из-за чего колонка "№№" (номер пробы) переставала распознаваться
    text = str(header).lower().replace("№", "num")
    return "".join(ch for ch in text if ch.isalnum())


# Порядок важен: более специфичные алиасы проверяются раньше общих,
# чтобы, например, "давление насыщения" не попало в общее поле "давление".
FIELD_ALIASES: list[tuple[str, str, list[str]]] = [
    # (ключ, человекочитаемое имя, список нормализованных алиасов-подстрок)
    ("well", "Скважина", ["скв", "well", "номерскважины"]),
    ("horizon", "Горизонт", ["горизонт", "horizon"]),
    ("block", "Блок", ["блок", "block"]),
    ("depth", "Глубина отбора, м", ["глубинаотбора", "depth", "глубина"]),
    ("date", "Дата отбора", ["датаотбора", "date", "дата"]),
    ("t_sample_c", "Температура пробы, °C", ["температура", "temperature"]),
    ("pb", "Давление насыщения", ["давлениенасыщения", "pb", "bubblepoint", "рнас"]),
    ("p_sample", "Давление отбора/исследования", ["давлениеисследования", "давлениеотбора",
                                                   "pressuresample", "pпласт"]),
    ("bo", "Объёмный коэффициент Bo", ["объемныйкоэффициент", "объёмныйкоэффициент", "bo"]),
    ("recalc_factor", "Пересчётный коэффициент (θ)", ["пересчетныйкоэффициент",
                                                        "пересчётныйкоэффициент"]),
    ("rho_oil", "Плотность пластовой нефти", ["плотностьпластовойнефти", "oildensity"]),
    ("rs_m3t", "Газосодержание, м³/т", ["газосодержаниеm3t", "газосодержаниeм3т",
                                         "газосодержанием3т"]),
    ("rs_m3m3", "Газосодержание, м³/м³", ["газосодержанием3м3", "газосодержаниеm3m3",
                                           "rsm3m3", "gor"]),
    ("mu_oil", "Вязкость пластовой нефти", ["вязкостьпластовой", "вязкостьнефтимпас"]),
    ("mu_sep", "Вязкость сепарированной нефти", ["вязкостьсепарированной"]),
    ("co", "Сжимаемость нефти Co", ["сжимаемости", "изотермическойсжимаемости", "compressibility"]),
    ("gas_solubility_coef", "Коэффициент растворимости газа",
     ["растворимостигаза", "коэффициентрастворимости"]),
    ("gas_density", "Плотность выделившегося газа", ["плотностьвыделевшегосягаза",
                                                       "плотностьвыделившегосягаза"]),
    ("gas_rel_density", "Отн. плотность газа по воздуху",
     ["плотностьгазапослеоднократного", "поводуху", "повоздуху"]),
    ("shrinkage_pct", "Усадка нефти, %", ["усадканефти", "shrinkage"]),
    ("sample_type", "Вид пробы", ["видпроб", "sampletype"]),
    ("sample_no", "№ пробы", ["num", "номерпробы"]),
]


def suggest_column_mapping(headers: list[str]) -> dict[str, str]:
    """
    Предлагает сопоставление "стандартное поле -> заголовок колонки" по
    ключевым словам. Это только предложение по умолчанию — окончательное
    решение остаётся за пользователем в UI (т.к. таблицы бывают разные).

    Возвращает
    ----------
    mapping : {field_key: header} только для полей, для которых нашлось
              совпадение
    """
    mapping: dict[str, str] = {}
    used_headers: set[str] = set()
    for key, _label, aliases in FIELD_ALIASES:
        for header in headers:
            if header in used_headers:
                continue
            norm = _normalize_header_text(header)
            if any(alias in norm for alias in aliases):
                mapping[key] = header
                used_headers.add(header)
                break
    return mapping


FIELD_LABELS = {key: label for key, label, _ in FIELD_ALIASES}

# Единицы измерения по умолчанию для каждого поля (для подписи осей графиков).
# Давление (pb, p_sample) — особый случай: единица зависит от исходного файла
# и выбирается пользователем в UI отдельно (см. app_pages/field_analysis.py).
FIELD_UNITS = {
    "depth": "м", "t_sample_c": "°C", "pb": None, "p_sample": None,
    "bo": "д.ед", "recalc_factor": "д.ед", "rho_oil": "г/см³",
    "rs_m3t": "м³/т", "rs_m3m3": "м³/м³", "mu_oil": "мПа·с", "mu_sep": "мм²/с",
    "co": "10⁻⁴ 1/МПа", "gas_solubility_coef": "м³/(м³·МПа)",
    "gas_density": "кг/м³", "gas_rel_density": "д.ед", "shrinkage_pct": "%",
}
NUMERIC_FIELDS = [
    "depth", "t_sample_c", "pb", "p_sample", "bo", "recalc_factor", "rho_oil",
    "rs_m3t", "rs_m3m3", "mu_oil", "mu_sep", "co", "gas_solubility_coef",
    "gas_density", "gas_rel_density", "shrinkage_pct",
]
GROUP_FIELDS = ["horizon", "block", "well", "sample_type"]


# ---------------------------------------------------------------------------
# Построение "чистого" датафрейма из сырых строк по сопоставлению колонок
# ---------------------------------------------------------------------------
def build_clean_dataframe(df_raw: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """
    Строит стандартизированный датафрейм по сырым данным и сопоставлению
    колонок: числовые поля приводятся к float (устойчиво к "грязным"
    значениям), горизонт/блок/скважина нормализуются для группировки,
    дата — к datetime.

    Также помечает вероятные строки-агрегаты (например "Среднее значение:
    горизонт ...") флагом is_aggregate=True — определяется по тому, что
    поле "№ пробы"/"скважина" не парсится как число, а сам текст этой ячейки
    непустой (обычная числовая пропущенная ячейка от агрегата не отличается,
    поэтому отдельно проверяем текстовое содержимое первой колонки).

    Параметры
    ---------
    df_raw  : сырой датафрейм (как есть, с исходными заголовками колонок)
    mapping : {field_key: header_name} — сопоставление, обычно из
              suggest_column_mapping() с ручными правками пользователя

    Возвращает
    ----------
    df_clean : датафрейм со стандартными колонками field_key + is_aggregate
    """
    out = pd.DataFrame(index=df_raw.index)

    for key in NUMERIC_FIELDS:
        header = mapping.get(key)
        out[key] = df_raw[header].apply(robust_float) if header else np.nan

    for key in GROUP_FIELDS:
        header = mapping.get(key)
        out[key] = df_raw[header].apply(normalize_code) if header else None

    date_header = mapping.get("date")
    if date_header:
        out["date"] = pd.to_datetime(df_raw[date_header], errors="coerce")
    else:
        out["date"] = pd.NaT

    # Эвристика для агрегатных строк ("Среднее значение: горизонт ...");
    # берём первую доступную "идентифицирующую" колонку — № пробы или скважину
    id_header = mapping.get("sample_no") or mapping.get("well")
    if id_header:
        id_raw = df_raw[id_header]
        is_text_non_numeric = id_raw.apply(
            lambda v: v is not None and str(v).strip() != ""
            and math.isnan(robust_float(v))
        )
        out["is_aggregate"] = is_text_non_numeric
    else:
        out["is_aggregate"] = False

    # Полностью пустые строки (все стандартные поля NaN/None) отбрасываем сразу
    all_nan = out[NUMERIC_FIELDS].isna().all(axis=1)
    out = out[~all_nan].reset_index(drop=True)

    return out


# ---------------------------------------------------------------------------
# Простая регрессия для линии тренда зависимости Y(X)
# ---------------------------------------------------------------------------
def fit_trend(x: np.ndarray, y: np.ndarray, kind: str = "linear"):
    """
    Строит простую линию тренда через облако точек (x, y) — линейную
    (y = a*x + b) или степенную (y = a * x^b, через линеаризацию в
    логарифмических координатах).

    Параметры
    ---------
    x, y : массивы данных (NaN уже должны быть удалены)
    kind : "linear" или "power"

    Возвращает
    ----------
    dict с ключами:
        x_fit, y_fit : точки для отрисовки линии тренда (отсортированы по x)
        equation     : строка с уравнением тренда
        r2           : коэффициент детерминации R²
        None, если данных недостаточно (< 3 точек) или тренд не строится
    """
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 3:
        return None

    x_sorted = np.linspace(x.min(), x.max(), 50)

    if kind == "power":
        if np.any(x <= 0) or np.any(y <= 0):
            return None
        log_x, log_y = np.log(x), np.log(y)
        b, log_a = np.polyfit(log_x, log_y, 1)
        a = np.exp(log_a)
        y_pred = a * x ** b
        y_fit = a * x_sorted ** b
        equation = f"y = {a:.4g} · x^{b:.4g}"
    else:
        a, b = np.polyfit(x, y, 1)
        y_pred = a * x + b
        y_fit = a * x_sorted + b
        sign = "+" if b >= 0 else "-"
        equation = f"y = {a:.4g}·x {sign} {abs(b):.4g}"

    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return dict(x_fit=x_sorted, y_fit=y_fit, equation=equation, r2=r2)
