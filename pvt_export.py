"""
pvt_export.py — экспорт PVT-модели Black Oil в формат Eclipse/tNavigator
GRDECL (ключевые слова PVTW, PVTO, PVDG, DENSITY, FILEUNIT).

Отличие от обычной таблицы калькулятора: PVTO должен содержать НЕСКОЛЬКО
"веток" по разным значениям Rs (не одну траекторию по давлению), каждая —
от своей точки насыщения Pb(Rs) до общего максимального давления модели.
Так симулятор может интерполировать PVT-свойства для ЛЮБОЙ комбинации
давления и газосодержания ячейки в ходе расчёта, а не только вдоль
"исторической" кривой Rs(P).

Все входные величины здесь — в МЕТРИЧЕСКИХ единицах (бар, м3/м3, °C, сПз),
т.к. Eclipse/tNavigator с FILEUNIT METRIC ожидает именно их. Внутри модуль
переводит в field units для вызова pvt_correlations.py (которая всегда
работает в psi/°F/scf-stb) и обратно.
"""

from __future__ import annotations

import numpy as np

import pvt_correlations as pvt
import units as u


def _fmt(x: float, width: int = 15, decimals: int = 6) -> str:
    """Форматирует число под колонку GRDECL: до `decimals` значащих цифр,
    без хвостовых нулей, выровнено по правому краю в `width` символов."""
    s = f"{x:.{decimals}g}"
    return s.rjust(width)


# ---------------------------------------------------------------------------
# Обратная операция: разбор уже существующего .GRDECL — например, файла,
# выгруженного из Fluid Model в Petrel/tNavigator, чтобы наложить ЕГО кривую
# на фактические лабораторные точки того же горизонта (проверить, насколько
# то, что заложено в модели, совпадает с лабораторией).
# ---------------------------------------------------------------------------
def _grdecl_section(lines: list[str], keyword: str) -> list[str]:
    """Возвращает "сырые" строки данных одного ключевого слова GRDECL
    (между строкой с keyword и завершающим "/"), пропуская комментарии."""
    out = []
    capture = False
    for raw in lines:
        s = raw.strip()
        if s.startswith(keyword):
            capture = True
            continue
        if not capture:
            continue
        if s.startswith("--"):
            continue
        if s == "/":
            break
        out.append(s)
    return out


def parse_fileunit(text: str) -> str:
    """Возвращает единицы файла: 'METRIC', 'FIELD' или 'LAB' (как в Eclipse),
    'METRIC' по умолчанию, если ключевое слово FILEUNIT не найдено."""
    lines = text.splitlines()
    section = _grdecl_section(lines, "FILEUNIT")
    if not section:
        return "METRIC"
    return section[0].replace("/", "").strip().upper()


def parse_pvto_saturation_curve(text: str) -> list[tuple[float, float, float, float]]:
    """
    Разбирает PVTO из текста .GRDECL и возвращает кривую точек насыщения —
    ПЕРВУЮ строку каждой ветки Rs (это и есть Pb(Rs), т.к. первая точка любой
    ветки PVTO всегда находится на давлении насыщения для этого Rs). Вместе
    эти точки образуют ту самую кривую "Rs/Bo/μo от Pb", которую заложили
    в модель через Fluid Model — её можно напрямую сравнивать с фактом.

    Параметры
    ---------
    text : содержимое .GRDECL-файла целиком

    Возвращает
    ----------
    points : список (Rs [м3/м3], Pb [бар], Bo [м3/м3], mu_o [сПз]),
             отсортированный по возрастанию Pb; пустой список, если PVTO
             не найден или не распознан
    """
    lines = text.splitlines()
    capture = False
    raw_rows = []
    for raw in lines:
        s = raw.strip()
        if s.startswith("PVTO"):
            capture = True
            continue
        if not capture:
            continue
        if s.startswith("--"):
            continue
        if s == "/":
            break
        raw_rows.append(s)

    points = []
    is_first_row_of_branch = True
    for row in raw_rows:
        nums_str = row.replace("/", "").split()
        try:
            nums = [float(x) for x in nums_str]
        except ValueError:
            continue
        if len(nums) == 4:
            rs, pb, bo, mu = nums
            points.append((rs, pb, bo, mu))
            is_first_row_of_branch = False
        elif len(nums) == 3 and not is_first_row_of_branch:
            # продолжение текущей ветки (уже выше Pb) — не точка насыщения,
            # пропускаем
            pass
        if row.rstrip().endswith("/"):
            is_first_row_of_branch = True

    points.sort(key=lambda p: p[1])
    return points


def parse_pvdg_table(text: str) -> list[tuple[float, float, float]]:
    """Разбирает PVDG из текста .GRDECL: список (P [бар], Bg [м3/м3], μg [сПз])."""
    lines = text.splitlines()
    section = _grdecl_section(lines, "PVDG")
    points = []
    for row in section:
        nums_str = row.replace("/", "").split()
        try:
            nums = [float(x) for x in nums_str]
        except ValueError:
            continue
        if len(nums) == 3:
            points.append(tuple(nums))
    return points


def build_pvto_branches(
    rsb_field_m3m3: float, api: float, gamma_g: float, t_c: float, gamma_o: float,
    pb_fn, rs_fn, bo_fn,
    rs_max_m3m3: float, n_branches: int, p_max_bar: float, n_points_per_branch: int,
    factor_pb: float = 1.0, factor_bo: float = 1.0, factor_mu: float = 1.0,
) -> list[tuple[float, list[tuple[float, float, float]]]]:
    """
    Строит ветки PVTO: список (Rs [м3/м3], [(P [бар], Bo [м3/м3], mu [сПз]), ...]).

    Для каждого Rs из сетки (от малого значения до rs_max_m3m3) считает точку
    насыщения Pb(Rs) выбранной корреляцией (с учётом коэффициента калибровки
    factor_pb, если передан), а затем — Bo и вязкость нефти от этой Pb до
    p_max_bar с шагом, дающим n_points_per_branch точек на ветку.

    Параметры
    ---------
    rsb_field_m3m3 : фактическое Rsb месторождения/горизонта, м3/м3 — не
                     используется в расчёте напрямую, только для справки/лога
    api, gamma_g, t_c, gamma_o : параметры модели (плотность нефти в °API,
                     относительная плотность газа, температура в °C, SG нефти)
    pb_fn, rs_fn, bo_fn : функции корреляции из pvt_correlations.py
                     (например, pvt.pb_standing/rs_standing/bo_standing)
    rs_max_m3m3    : верхняя граница сетки Rs, м3/м3 (обычно с запасом выше
                     фактического Rsb — чтобы симулятор мог интерполировать
                     и при повышении давления/довышении газонасыщенности)
    n_branches     : количество веток Rs
    p_max_bar      : максимальное давление модели, бар (общее для всех веток)
    n_points_per_branch : количество точек давления на каждой ветке
    factor_pb/bo/mu : мультипликативные коэффициенты калибровки (из tuning),
                     1.0 — без калибровки

    Возвращает
    ----------
    branches : список веток, все величины уже в метрических единицах
    """
    t_f = u.c_to_f(t_c)
    rs_grid_m3m3 = np.linspace(max(rs_max_m3m3 / n_branches * 0.1, 0.3), rs_max_m3m3, n_branches)

    branches = []
    for rs_m3m3 in rs_grid_m3m3:
        rsb_scf_stb = u.rs_m3m3_to_scf_stb(rs_m3m3)
        pb_psi = pb_fn(rsb_scf_stb, gamma_g, api, t_f) * factor_pb
        pb_bar = u.psi_to_bar(pb_psi)

        p_points_bar = np.linspace(pb_bar, p_max_bar, n_points_per_branch)
        rows = []
        for p_bar in p_points_bar:
            p_psi = u.bar_to_psi(p_bar)
            co_per_psi = None
            if p_psi > pb_psi:
                co_per_psi = pvt.co_vasquez_beggs(p_psi, rsb_scf_stb, gamma_g, api, t_f)
                # Vasquez-Beggs на краю своего диапазона (очень лёгкая нефть +
                # почти нулевой Rsb) иногда даёт физически невозможную
                # отрицательную сжимаемость — из-за этого Bo начал бы расти
                # с давлением вместо падения. Подстраховываемся минимальным
                # положительным порогом, заметно ниже любых реальных Co
                # (~1e-5..3e-5 1/psi), чтобы не портить нормальные ветки.
                co_per_psi = max(co_per_psi, 1e-6)
            bo = bo_fn(p_psi, pb_psi, rsb_scf_stb, rsb_scf_stb, gamma_g, gamma_o, t_f,
                       co_per_psi) * factor_bo
            mu = pvt.mu_oil(p_psi, pb_psi, rsb_scf_stb, rsb_scf_stb, api, t_f) * factor_mu
            rows.append((p_bar, bo, mu))
        branches.append((rs_m3m3, rows))

    return branches


def build_pvdg_table(gamma_g: float, t_c: float, p_max_bar: float, n_points: int,
                      y_h2s: float = 0.0, y_co2: float = 0.0) -> list[tuple[float, float, float]]:
    """
    Строит таблицу PVDG (сухой газ): (P [бар], Bg [м3/м3], mu_g [сПз]).

    Параметры
    ---------
    gamma_g, t_c   : относительная плотность газа, температура в °C
    p_max_bar      : максимальное давление, бар
    n_points       : число точек davления
    y_h2s, y_co2   : мольные доли H2S/CO2 (0..1) для поправки Wichert-Aziz;
                     по умолчанию 0 — коррекция не применяется
    """
    t_f = u.c_to_f(t_c)
    ppc_psi, tpc_r = pvt.pseudo_critical_properties_standing(gamma_g)
    if y_h2s > 0 or y_co2 > 0:
        ppc_psi, tpc_r = pvt.wichert_aziz_correction(ppc_psi, tpc_r, y_h2s, y_co2)

    p_points_bar = np.linspace(1.02, p_max_bar, n_points)
    rows = []
    for p_bar in p_points_bar:
        p_psi = u.bar_to_psi(p_bar)
        ppr, tpr = pvt.reduced_properties(p_psi, t_f, ppc_psi, tpc_r)
        z = pvt.z_factor_dak(ppr, tpr)
        bg_rcf_scf = pvt.bg_gas(p_psi, t_f, z)
        bg_m3m3 = u.bg_rcf_scf_to_m3m3(bg_rcf_scf)
        mu_g = pvt.mu_gas_lee_gonzalez_eakin(p_psi, t_f, z, gamma_g)
        rows.append((p_bar, bg_m3m3, mu_g))
    return rows


def build_pvtw_record(p_ref_bar: float, t_c: float, salinity_ppm: float) -> tuple[float, ...]:
    """Строит запись PVTW: (Pref [бар], Bw, Cw [1/бар], muw [сПз], dmuw/dp=0)."""
    t_f = u.c_to_f(t_c)
    p_ref_psi = u.bar_to_psi(p_ref_bar)
    bw = pvt.bw_mccain(p_ref_psi, t_f)
    cw_per_psi = pvt.cw_water(p_ref_psi, t_f, salinity_ppm=salinity_ppm)
    cw_per_bar = u.compressibility_per_psi_to_per_bar(cw_per_psi)
    muw = pvt.mu_water_mccain(p_ref_psi, t_f, salinity_ppm=salinity_ppm)
    return (p_ref_bar, bw, cw_per_bar, muw, 0.0)


def format_grdecl(
    pvtw_record: tuple[float, ...],
    pvto_branches: list[tuple[float, list[tuple[float, float, float]]]],
    pvdg_rows: list[tuple[float, float, float]],
    rho_oil_kgm3: float, rho_water_kgm3: float, rho_gas_kgm3: float,
    source_label: str = "PVT-Simulator",
) -> str:
    """Собирает итоговый текст .GRDECL из готовых PVTW/PVTO/PVDG/DENSITY данных."""
    lines = []

    lines.append(f"PVTW                                   -- Generated : {source_label}")
    lines.append("".join(_fmt(v, 15, 6) for v in pvtw_record) + " /")
    lines.append("")

    lines.append(f"PVTO                                   -- Generated : {source_label}")
    for rs, rows in pvto_branches:
        for i, (p, bo, mu) in enumerate(rows):
            terminator = " /" if i == len(rows) - 1 else ""
            if i == 0:
                lines.append(_fmt(rs, 15, 7) + _fmt(p, 15, 7) + _fmt(bo, 15, 7)
                              + _fmt(mu, 13, 6) + terminator)
            else:
                lines.append(" " * 15 + _fmt(p, 15, 7) + _fmt(bo, 15, 7)
                              + _fmt(mu, 13, 6) + terminator)
    lines.append("  /")
    lines.append("")

    lines.append(f"PVDG                                   -- Generated : {source_label}")
    for p, bg, mu_g in pvdg_rows:
        lines.append(_fmt(p, 15, 7) + _fmt(bg, 18, 7) + _fmt(mu_g, 18, 7))
    lines.append("  /")
    lines.append("")

    lines.append(f"DENSITY                                -- Generated : {source_label}")
    lines.append(_fmt(rho_oil_kgm3, 12, 5) + _fmt(rho_water_kgm3, 12, 6)
                  + _fmt(rho_gas_kgm3, 12, 3) + " /")
    lines.append("")

    lines.append("FILEUNIT                               -- Generated : "
                  f"{source_label}")
    lines.append("  METRIC /")
    lines.append("")

    return "\n".join(lines) + "\n"
