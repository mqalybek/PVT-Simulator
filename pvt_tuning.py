"""
pvt_tuning.py — калибровка (tuning) классических корреляций Black Oil
(pvt_correlations.py) под фактические лабораторные PVT-данные конкретного
месторождения/горизонта.

Идея (стандартный промышленный подход, тот же, что в PVTi/MBAL "correlation
match"): корреляция Standing/Beggs-Robinson даёт некое предсказание Pb, Rs,
Bo, μo по базовым параметрам (API, γg, T). Сравниваем это предсказание с
тем, что реально измерено в лаборатории для ТЕХ ЖЕ проб, и находим
мультипликативный коэффициент (factor), который минимизирует расхождение:

    lab_value ≈ factor * model_value

Коэффициент ищется методом наименьших квадратов через начало координат
(без свободного члена — это стандартная практика для PVT-тюнинга, т.к.
модель должна быть точной в нуле по определению свойства).

Найденные коэффициенты можно передать в калькулятор (app.py) и применить
к расчётной кривой — это и есть "калибровка корреляции под факт".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import pvt_correlations as pvt
import units as u


def fit_multiplier(lab: np.ndarray, model: np.ndarray) -> dict | None:
    """
    Находит мультипликативный коэффициент factor: lab ≈ factor * model
    методом наименьших квадратов через начало координат, и считает метрики
    качества как ДО, так и ПОСЛЕ калибровки.

    Параметры
    ---------
    lab, model : фактические (лабораторные) и модельные (по корреляции)
                 значения одного и того же свойства, поэлементно совпадающие

    Возвращает
    ----------
    dict с ключами: factor, n, rmse_before, rmse_after, mape_before,
    mape_after (в %), r2_before, r2_after — или None, если точек < 3
    """
    mask = np.isfinite(lab) & np.isfinite(model) & (model != 0)
    lab, model = lab[mask], model[mask]
    if len(lab) < 3:
        return None

    factor = float(np.sum(lab * model) / np.sum(model ** 2))
    model_tuned = factor * model

    def _rmse(a, b):
        return float(np.sqrt(np.mean((a - b) ** 2)))

    def _mape(a, b):
        return float(np.mean(np.abs((a - b) / a)) * 100)

    def _r2(a, b):
        ss_res = np.sum((a - b) ** 2)
        ss_tot = np.sum((a - np.mean(a)) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")

    return dict(
        factor=factor, n=len(lab),
        rmse_before=_rmse(lab, model), rmse_after=_rmse(lab, model_tuned),
        mape_before=_mape(lab, model), mape_after=_mape(lab, model_tuned),
        r2_before=_r2(lab, model), r2_after=_r2(lab, model_tuned),
    )


def compute_standing_predictions(df: pd.DataFrame, api: float, gamma_g: float,
                                  gamma_o: float) -> pd.DataFrame:
    """
    Для каждой строки таблицы (со стандартными полями pb [МПа], rs_m3m3,
    bo, mu_oil [мПа*с=сПз], t_sample_c, и опционально p_sample [МПа])
    считает, что предсказали бы корреляции Standing/Beggs-Robinson для ЭТОЙ
    ЖЕ пробы (используя её собственные Rsb и T как вход), — чтобы можно было
    сравнить "модель против факта" на реальных данных месторождения.

    Давление берётся в МПа (внутренний формат таблицы field_data), внутри
    функции конвертируется в field units для расчёта, результат
    конвертируется обратно в МПа/метрику для сравнения с фактом.

    Параметры
    ---------
    df      : датафрейм со стандартными полями (см. field_data.py), давление
              pb/p_sample должно быть в МПа
    api     : плотность нефти для корреляции, °API (одна на весь расчёт —
              обычно средняя по горизонту/месторождению)
    gamma_g : относительная плотность газа по воздуху
    gamma_o : относительная плотность нефти (SG), для формулы Bo Standing

    Возвращает
    ----------
    df с добавленными колонками: pb_model_mpa, rs_model_m3m3 (=rsb, т.к.
    сравнение идёт в точке насыщения), bo_model, mu_model_cp
    """
    out = df.copy()
    pb_model_mpa, bo_model, mu_model_cp = [], [], []

    for _, row in df.iterrows():
        rsb_m3m3 = row.get("rs_m3m3")
        t_c = row.get("t_sample_c")
        pb_mpa_lab = row.get("pb")

        if pd.isna(rsb_m3m3) or pd.isna(t_c) or rsb_m3m3 <= 0:
            pb_model_mpa.append(np.nan)
            bo_model.append(np.nan)
            mu_model_cp.append(np.nan)
            continue

        t_f = u.c_to_f(t_c)
        rsb_scf_stb = u.rs_m3m3_to_scf_stb(rsb_m3m3)

        pb_psi_model = pvt.pb_standing(rsb_scf_stb, gamma_g, api, t_f)
        bo_bbl_stb = pvt.bo_standing(pb_psi_model, pb_psi_model, rsb_scf_stb,
                                      rsb_scf_stb, gamma_g, gamma_o, t_f)

        # Вязкость меряют при фактическом давлении отбора (p_sample), если
        # оно есть; иначе считаем при модельном Pb (насыщенная нефть).
        p_sample_mpa = row.get("p_sample")
        pb_psi_for_visc = pb_psi_lab_psi = (
            u.mpa_to_psi(pb_mpa_lab) if pd.notna(pb_mpa_lab) else pb_psi_model
        )
        if pd.notna(p_sample_mpa):
            p_psi = u.mpa_to_psi(p_sample_mpa)
        else:
            p_psi = pb_psi_for_visc

        rs_at_p = (rsb_scf_stb if p_psi >= pb_psi_for_visc else
                   pvt.rs_standing(p_psi, pb_psi_for_visc, rsb_scf_stb, gamma_g, api, t_f))
        mu_cp = pvt.mu_oil(p_psi, pb_psi_for_visc, rs_at_p, rsb_scf_stb, api, t_f)

        pb_model_mpa.append(u.psi_to_mpa(pb_psi_model))
        bo_model.append(bo_bbl_stb)  # bbl/stb = м3/м3, конвертация 1:1
        mu_model_cp.append(mu_cp)

    out["pb_model_mpa"] = pb_model_mpa
    out["bo_model"] = bo_model
    out["mu_model_cp"] = mu_model_cp
    return out


def tune_group(df: pd.DataFrame, api: float, gamma_g: float, gamma_o: float) -> dict | None:
    """
    Считает предсказания Standing и подгоняет коэффициенты калибровки
    (Pb, Rs≡Bo-точка насыщения не тюнится отдельно — Rsb берётся из факта
    напрямую как вход модели, поэтому тюнится сам Pb; плюс Bo и μo) для
    одной группы данных (один горизонт, один блок, или весь массив).

    Возвращает
    ----------
    dict с under-ключами "pb", "bo", "mu" (каждый — результат fit_multiplier
    или None, если данных недостаточно), плюс "n_total" и "api"/"gamma_g"/
    "gamma_o", использованные для расчёта — или None, если совсем нет данных
    """
    if df.empty:
        return None

    df_pred = compute_standing_predictions(df, api, gamma_g, gamma_o)

    pb_fit = fit_multiplier(df_pred["pb"].to_numpy(dtype=float),
                             df_pred["pb_model_mpa"].to_numpy(dtype=float))
    bo_fit = fit_multiplier(df_pred["bo"].to_numpy(dtype=float),
                             df_pred["bo_model"].to_numpy(dtype=float))
    mu_fit = fit_multiplier(df_pred["mu_oil"].to_numpy(dtype=float),
                             df_pred["mu_model_cp"].to_numpy(dtype=float))

    if pb_fit is None and bo_fit is None and mu_fit is None:
        return None

    return dict(pb=pb_fit, bo=bo_fit, mu=mu_fit, n_total=len(df_pred),
                api=api, gamma_g=gamma_g, gamma_o=gamma_o, df_pred=df_pred)
