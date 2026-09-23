"""
app.py — Streamlit-интерфейс PVT-симулятора Black Oil модели.

Все расчёты выполняются в pvt_correlations.py во внутренних field units,
а этот модуль отвечает за:
    - ввод параметров в выбранной пользователем системе единиц (метрика/field)
    - конвертацию вход/выход через units.py
    - таблицы, графики (Plotly), валидацию диапазонов применимости, экспорт
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import pvt_correlations as pvt
import units as u

# ---------------------------------------------------------------------------
# Sidebar: система единиц измерения (первое, что видит пользователь)
# ---------------------------------------------------------------------------
st.sidebar.title("PVT-симулятор Black Oil")

unit_system = st.sidebar.radio(
    "Система единиц измерения",
    options=["Метрическая (Казахстан)", "Field units (США)"],
    index=0,
)
is_metric = unit_system.startswith("Метрическая")

st.sidebar.markdown("---")
st.sidebar.header("Входные параметры")

# ---------------------------------------------------------------------------
# Плотность нефти: кг/м3 <-> API, синхронизированные поля
# ---------------------------------------------------------------------------
st.sidebar.subheader("Плотность нефти")

if "rho_kgm3" not in st.session_state:
    st.session_state.rho_kgm3 = 850.0

if is_metric:
    rho_kgm3 = st.sidebar.number_input(
        "Плотность нефти, кг/м³", min_value=600.0, max_value=1050.0,
        value=st.session_state.rho_kgm3, step=1.0, key="rho_input_metric",
    )
    api = u.rho_kgm3_to_api(rho_kgm3)
    st.sidebar.caption(f"Справочно: {api:.1f} °API")
else:
    api_default = u.rho_kgm3_to_api(st.session_state.rho_kgm3)
    api = st.sidebar.number_input(
        "Плотность нефти, °API", min_value=5.0, max_value=70.0,
        value=float(api_default), step=0.5, key="api_input_field",
    )
    rho_kgm3 = u.api_to_rho_kgm3(api)
    st.sidebar.caption(f"Справочно: {rho_kgm3:.1f} кг/м³")

st.session_state.rho_kgm3 = rho_kgm3
gamma_o = u.rho_kgm3_to_sg(rho_kgm3)  # относительная плотность нефти (SG, вода=1)

# ---------------------------------------------------------------------------
# Остальные входные параметры
# ---------------------------------------------------------------------------
gamma_g = st.sidebar.number_input(
    "Относительная плотность газа γg (по воздуху)",
    min_value=0.55, max_value=1.70, value=0.75, step=0.01,
)

with st.sidebar.expander("Кислый газ H2S/CO2 (коррекция Wichert-Aziz)", expanded=False):
    st.caption("Для месторождений с сероводородом/CO2 в газе (например, Тенгиз, "
               "Королёвское) — по умолчанию 0% (обычный «сладкий» газ, поправка "
               "не применяется).")
    h2s_mol_pct = st.number_input("H2S, мол.%", min_value=0.0, max_value=80.0,
                                   value=0.0, step=0.1)
    co2_mol_pct = st.number_input("CO2, мол.%", min_value=0.0, max_value=80.0,
                                   value=0.0, step=0.1)

st.sidebar.subheader("Температура")
if is_metric:
    t_c = st.sidebar.number_input("Пластовая температура, °C", min_value=10.0,
                                   max_value=200.0, value=95.0, step=1.0)
    t_f = u.c_to_f(t_c)
else:
    t_f = st.sidebar.number_input("Пластовая температура, °F", min_value=50.0,
                                   max_value=390.0, value=u.c_to_f(95.0), step=1.0)
    t_c = u.f_to_c(t_f)

st.sidebar.subheader("Давление насыщения / газосодержание")
st.sidebar.subheader("Корреляция Pb/Rs/Bo")
correlation_choice = st.sidebar.selectbox(
    "Какую корреляцию использовать",
    options=["Standing (1947)", "Glaso (1980)", "Petrosky-Farshad (1993)"],
    index=0,
    help="Standing — универсальная (California), Glaso — North Sea (API 22-48), "
         "Petrosky-Farshad — Мексиканский залив (API 16-45). Вязкость и сжимаемость "
         "нефти всегда считаются по Beggs-Robinson/Vasquez-Beggs независимо от выбора.",
)
CORRELATION_FUNCS = {
    "Standing (1947)": (pvt.pb_standing, pvt.rs_standing, pvt.bo_standing),
    "Glaso (1980)": (pvt.pb_glaso, pvt.rs_glaso, pvt.bo_glaso),
    "Petrosky-Farshad (1993)": (pvt.pb_petrosky_farshad, pvt.rs_petrosky_farshad, pvt.bo_petrosky_farshad),
}
pb_fn, rs_fn, bo_fn = CORRELATION_FUNCS[correlation_choice]

pb_mode = st.sidebar.radio(
    "Способ определения Pb",
    options=[f"Рассчитать по корреляции {correlation_choice.split(' (')[0]}",
             "Задать из лабораторных данных"],
    index=0,
)

if is_metric:
    rsb_m3m3 = st.sidebar.number_input(
        "Газосодержание при Pb (Rsb), м³/м³", min_value=1.0, max_value=400.0,
        value=100.0, step=1.0,
    )
    rsb_scf_stb = u.rs_m3m3_to_scf_stb(rsb_m3m3)
else:
    rsb_scf_stb = st.sidebar.number_input(
        "Газосодержание при Pb (Rsb), scf/stb", min_value=5.0, max_value=2250.0,
        value=560.0, step=5.0,
    )
    rsb_m3m3 = u.rs_scf_stb_to_m3m3(rsb_scf_stb)

pb_calc_psi = pb_fn(rsb_scf_stb, gamma_g, api, t_f)

if pb_mode.startswith("Рассчитать"):
    pb_psi = pb_calc_psi
    if is_metric:
        st.sidebar.info(f"Pb ({correlation_choice}) = {u.psi_to_bar(pb_psi):.1f} бар")
    else:
        st.sidebar.info(f"Pb ({correlation_choice}) = {pb_psi:.1f} psi")
else:
    if is_metric:
        pb_bar = st.sidebar.number_input(
            "Давление насыщения Pb (лаб.), бар", min_value=1.0, max_value=700.0,
            value=round(u.psi_to_bar(pb_calc_psi), 1), step=1.0,
        )
        pb_psi = u.bar_to_psi(pb_bar)
    else:
        pb_psi = st.sidebar.number_input(
            "Давление насыщения Pb (лаб.), psi", min_value=15.0, max_value=10000.0,
            value=round(pb_calc_psi, 1), step=10.0,
        )

st.sidebar.subheader("Диапазон давлений расчёта")
if is_metric:
    p_min_bar = st.sidebar.number_input("P min, бар", min_value=1.0, max_value=690.0, value=1.0)
    p_max_bar = st.sidebar.number_input("P max, бар", min_value=2.0, max_value=700.0, value=400.0)
    p_step_bar = st.sidebar.number_input("Шаг P, бар", min_value=0.5, max_value=50.0, value=5.0)
    p_min_psi, p_max_psi, p_step_psi = (u.bar_to_psi(p_min_bar), u.bar_to_psi(p_max_bar),
                                         u.bar_to_psi(p_step_bar))
else:
    p_min_psi = st.sidebar.number_input("P min, psi", min_value=15.0, max_value=10000.0, value=15.0)
    p_max_psi = st.sidebar.number_input("P max, psi", min_value=30.0, max_value=10500.0, value=5800.0)
    p_step_psi = st.sidebar.number_input("Шаг P, psi", min_value=5.0, max_value=500.0, value=75.0)

st.sidebar.subheader("Пластовая вода")
if is_metric:
    salinity_gpl = st.sidebar.number_input(
        "Солёность пластовой воды, г/л NaCl-экв", min_value=0.0, max_value=350.0,
        value=30.0, step=1.0,
    )
    salinity_ppm = u.salinity_gpl_to_ppm(salinity_gpl)
else:
    salinity_ppm = st.sidebar.number_input(
        "Солёность пластовой воды, ppm", min_value=0.0, max_value=350000.0,
        value=30000.0, step=1000.0,
    )

# ---------------------------------------------------------------------------
# Валидация диапазонов применимости корреляций
# ---------------------------------------------------------------------------
warnings = []

def _check(cond: bool, msg_metric: str, msg_field: str) -> None:
    if not cond:
        warnings.append(msg_metric if is_metric else msg_field)

_check(16.5 <= api <= 63.8,
       f"API={api:.1f} вне диапазона Standing/Beggs-Robinson "
       f"(плотность {u.api_to_rho_kgm3(16.5):.0f}-{u.api_to_rho_kgm3(63.8):.0f} кг/м³).",
       f"API={api:.1f} вне диапазона Standing/Beggs-Robinson (16.5-63.8 °API).")

_check(100 <= t_f <= 258,
       f"T={t_c:.1f} °C вне диапазона Standing "
       f"({u.f_to_c(100):.0f}-{u.f_to_c(258):.0f} °C).",
       f"T={t_f:.1f} °F вне диапазона Standing (100-258 °F).")

_check(20 <= rsb_scf_stb <= 1425,
       f"Rsb={rsb_m3m3:.1f} м³/м³ вне диапазона Standing "
       f"({u.rs_scf_stb_to_m3m3(20):.1f}-{u.rs_scf_stb_to_m3m3(1425):.1f} м³/м³).",
       f"Rsb={rsb_scf_stb:.0f} scf/stb вне диапазона Standing (20-1425 scf/stb).")

_check(0.59 <= gamma_g <= 1.40,
       f"γg={gamma_g:.2f} вне диапазона Standing/DAK (0.59-1.40).",
       f"γg={gamma_g:.2f} вне диапазона Standing/DAK (0.59-1.40).")

if warnings:
    for w in warnings:
        st.sidebar.warning(w)

# ---------------------------------------------------------------------------
# Расчёт таблицы PVT-свойств по диапазону давлений
# ---------------------------------------------------------------------------
p_values_psi = np.arange(p_min_psi, p_max_psi + p_step_psi / 2, p_step_psi)
p_values_psi = p_values_psi[p_values_psi > 0]

ppc_psi, tpc_r = pvt.pseudo_critical_properties_standing(gamma_g)
tpc_r_sweet = tpc_r  # до коррекции на кислый газ, для отображения в UI
if h2s_mol_pct > 0 or co2_mol_pct > 0:
    ppc_psi, tpc_r = pvt.wichert_aziz_correction(
        ppc_psi, tpc_r, y_h2s=h2s_mol_pct / 100.0, y_co2=co2_mol_pct / 100.0
    )

rows = []
for p_psi in p_values_psi:
    rs_scf_stb = rs_fn(p_psi, pb_psi, rsb_scf_stb, gamma_g, api, t_f)

    co_per_psi = None
    if p_psi > pb_psi:
        co_per_psi = pvt.co_vasquez_beggs(p_psi, rsb_scf_stb, gamma_g, api, t_f)

    bo_bbl_stb = bo_fn(p_psi, pb_psi, rs_scf_stb, rsb_scf_stb,
                        gamma_g, gamma_o, t_f, co_per_psi)
    mu_o_cp = pvt.mu_oil(p_psi, pb_psi, rs_scf_stb, rsb_scf_stb, api, t_f)

    ppr, tpr = pvt.reduced_properties(p_psi, t_f, ppc_psi, tpc_r)
    z = pvt.z_factor_dak(ppr, tpr)
    bg_rcf_scf = pvt.bg_gas(p_psi, t_f, z)
    mu_g_cp = pvt.mu_gas_lee_gonzalez_eakin(p_psi, t_f, z, gamma_g)

    bw_bbl_stb = pvt.bw_mccain(p_psi, t_f)
    mu_w_cp = pvt.mu_water_mccain(p_psi, t_f, salinity_ppm)
    cw_per_psi = pvt.cw_water(p_psi, t_f, salinity_ppm=salinity_ppm)

    rows.append(dict(
        p_psi=p_psi, rs_scf_stb=rs_scf_stb, bo_bbl_stb=bo_bbl_stb, mu_o_cp=mu_o_cp,
        co_per_psi=co_per_psi if co_per_psi is not None else np.nan,
        z=z, bg_rcf_scf=bg_rcf_scf, mu_g_cp=mu_g_cp,
        bw_bbl_stb=bw_bbl_stb, mu_w_cp=mu_w_cp, cw_per_psi=cw_per_psi,
    ))

df_field = pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# Конвертация таблицы в выбранную систему единиц для отображения
# ---------------------------------------------------------------------------
if is_metric:
    df_display = pd.DataFrame({
        "P, бар": df_field["p_psi"].apply(u.psi_to_bar),
        "Rs, м³/м³": df_field["rs_scf_stb"].apply(u.rs_scf_stb_to_m3m3),
        "Bo, м³/м³": df_field["bo_bbl_stb"].apply(u.bo_bbl_stb_to_m3m3),
        "μo, сПз": df_field["mu_o_cp"],
        "Co, 1/бар": df_field["co_per_psi"].apply(
            lambda x: u.compressibility_per_psi_to_per_bar(x) if pd.notna(x) else np.nan),
        "Z, -": df_field["z"],
        "Bg, м³/м³": df_field["bg_rcf_scf"].apply(u.bg_rcf_scf_to_m3m3),
        "μg, сПз": df_field["mu_g_cp"],
        "Bw, м³/м³": df_field["bw_bbl_stb"].apply(u.bw_bbl_stb_to_m3m3),
        "μw, сПз": df_field["mu_w_cp"],
        "Cw, 1/бар": df_field["cw_per_psi"].apply(u.compressibility_per_psi_to_per_bar),
    })
    pb_display = u.psi_to_bar(pb_psi)
    p_axis_title = "Давление, бар"
    rs_axis_title = "Rs, м³/м³"
    bo_axis_title = "Bo, м³/м³"
    bg_axis_title = "Bg, м³/м³"
    mu_axis_title = "Вязкость, сПз"
else:
    df_display = pd.DataFrame({
        "P, psi": df_field["p_psi"],
        "Rs, scf/stb": df_field["rs_scf_stb"],
        "Bo, bbl/stb": df_field["bo_bbl_stb"],
        "μo, cP": df_field["mu_o_cp"],
        "Co, 1/psi": df_field["co_per_psi"],
        "Z, -": df_field["z"],
        "Bg, rcf/scf": df_field["bg_rcf_scf"],
        "μg, cP": df_field["mu_g_cp"],
        "Bw, bbl/stb": df_field["bw_bbl_stb"],
        "μw, cP": df_field["mu_w_cp"],
        "Cw, 1/psi": df_field["cw_per_psi"],
    })
    pb_display = pb_psi
    p_axis_title = "Давление, psi"
    rs_axis_title = "Rs, scf/stb"
    bo_axis_title = "Bo, bbl/stb"
    bg_axis_title = "Bg, rcf/scf"
    mu_axis_title = "Вязкость, cP"

p_col = df_display.columns[0]

# ---------------------------------------------------------------------------
# Основная область: таблица и графики
# ---------------------------------------------------------------------------
st.title("PVT-симулятор Black Oil модели")
st.caption(f"Pb/Rs/Bo: {correlation_choice} · вязкость/сжимаемость: Beggs-Robinson / "
           f"Vasquez-Beggs · газ: Dranchuk-Abou-Kassem, Lee-Gonzalez-Eakin · "
           f"вода: McCain")

col1, col2 = st.columns([1, 1])
with col1:
    st.metric("Давление насыщения Pb",
              f"{pb_display:.1f} {'бар' if is_metric else 'psi'}")
with col2:
    st.metric("Плотность нефти",
              f"{rho_kgm3:.1f} кг/м³ ({api:.1f} °API)")

if h2s_mol_pct > 0 or co2_mol_pct > 0:
    delta_tpc_c = (tpc_r - tpc_r_sweet) * 5.0 / 9.0  # градус R = градус F по величине
    st.info(
        f"Применена коррекция Wichert-Aziz на кислый газ (H2S={h2s_mol_pct:.1f}%, "
        f"CO2={co2_mol_pct:.1f}%): псевдокритическая температура Tpc "
        f"скорректирована на {delta_tpc_c:.1f} °C — это влияет на Z-фактор, "
        f"Bg и вязкость газа."
    )

st.subheader("Таблица PVT-свойств")
st.dataframe(df_display.style.format(precision=4), use_container_width=True)


def _add_pb_line(fig: go.Figure) -> None:
    fig.add_vline(x=pb_display, line_dash="dash", line_color="red",
                  annotation_text="Pb", annotation_position="top right")


st.subheader("Графики")

g1, g2 = st.columns(2)
with g1:
    fig_rs = go.Figure()
    fig_rs.add_trace(go.Scatter(x=df_display[p_col], y=df_display.iloc[:, 1],
                                 mode="lines+markers", name="Rs"))
    _add_pb_line(fig_rs)
    fig_rs.update_layout(title="Газосодержание Rs(P)", xaxis_title=p_axis_title,
                          yaxis_title=rs_axis_title, hovermode="x unified")
    st.plotly_chart(fig_rs, use_container_width=True)

    fig_bg = go.Figure()
    fig_bg.add_trace(go.Scatter(x=df_display[p_col], y=df_display["Bg, м³/м³" if is_metric else "Bg, rcf/scf"],
                                 mode="lines+markers", name="Bg", line=dict(color="orange")))
    _add_pb_line(fig_bg)
    fig_bg.update_layout(title="Объёмный коэффициент газа Bg(P)", xaxis_title=p_axis_title,
                          yaxis_title=bg_axis_title, hovermode="x unified")
    st.plotly_chart(fig_bg, use_container_width=True)

with g2:
    fig_bo = go.Figure()
    fig_bo.add_trace(go.Scatter(x=df_display[p_col], y=df_display.iloc[:, 2],
                                 mode="lines+markers", name="Bo", line=dict(color="green")))
    _add_pb_line(fig_bo)
    fig_bo.update_layout(title="Объёмный коэффициент нефти Bo(P)", xaxis_title=p_axis_title,
                          yaxis_title=bo_axis_title, hovermode="x unified")
    st.plotly_chart(fig_bo, use_container_width=True)

    fig_mu = go.Figure()
    fig_mu.add_trace(go.Scatter(x=df_display[p_col], y=df_display.iloc[:, 3],
                                 mode="lines+markers", name="μo (нефть)"))
    fig_mu.add_trace(go.Scatter(x=df_display[p_col], y=df_display["μg, сПз" if is_metric else "μg, cP"],
                                 mode="lines+markers", name="μg (газ)", yaxis="y2"))
    _add_pb_line(fig_mu)
    fig_mu.update_layout(
        title="Вязкость нефти и газа vs P", xaxis_title=p_axis_title,
        yaxis_title=f"{mu_axis_title} (нефть)",
        yaxis2=dict(title=f"{mu_axis_title} (газ)", overlaying="y", side="right"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_mu, use_container_width=True)

fig_z = go.Figure()
fig_z.add_trace(go.Scatter(x=df_display[p_col], y=df_display["Z, -"],
                            mode="lines+markers", name="Z", line=dict(color="purple")))
_add_pb_line(fig_z)
fig_z.update_layout(title="Коэффициент сверхсжимаемости газа Z(P)", xaxis_title=p_axis_title,
                     yaxis_title="Z, -", hovermode="x unified")
st.plotly_chart(fig_z, use_container_width=True)

# ---------------------------------------------------------------------------
# Калибровка (tuning) по факту — если сохранена на странице "Анализ месторождения"
# ---------------------------------------------------------------------------
tuning = st.session_state.get("pvt_tuning")
if tuning:
    st.markdown("---")
    st.subheader("Калибровка по фактическим данным")
    st.info(
        f"Применена калибровка Standing/Beggs-Robinson по данным: **{tuning['source']}** "
        f"(рассчитана на странице «Анализ месторождения»). Коэффициенты: "
        f"Pb ×{tuning['factor_pb']:.3f}, Bo ×{tuning['factor_bo']:.3f}, "
        f"μo ×{tuning['factor_mu']:.3f}."
        + (f" Обратите внимание: калибровка всегда считается относительно Standing, "
           f"даже если выше выбрана корреляция «{correlation_choice}»."
           if correlation_choice != "Standing (1947)" else "")
    )
    tune_col1, tune_col2 = st.columns([3, 1])
    with tune_col1:
        show_tuned = st.checkbox("Показать откалиброванную кривую на графиках выше", value=True)
    with tune_col2:
        if st.button("Сбросить калибровку"):
            del st.session_state["pvt_tuning"]
            st.rerun()

    if show_tuned:
        pb_psi_tuned = pb_psi * tuning["factor_pb"]
        rows_tuned = []
        for p_psi_t in p_values_psi:
            rs_t = pvt.rs_standing(p_psi_t, pb_psi_tuned, rsb_scf_stb, gamma_g, api, t_f)
            co_t = (pvt.co_vasquez_beggs(p_psi_t, rsb_scf_stb, gamma_g, api, t_f)
                    if p_psi_t > pb_psi_tuned else None)
            bo_t = pvt.bo_standing(p_psi_t, pb_psi_tuned, rs_t, rsb_scf_stb,
                                    gamma_g, gamma_o, t_f, co_t) * tuning["factor_bo"]
            mu_t = pvt.mu_oil(p_psi_t, pb_psi_tuned, rs_t, rsb_scf_stb, api, t_f) * tuning["factor_mu"]
            rows_tuned.append(dict(p_psi=p_psi_t, rs=rs_t, bo=bo_t, mu=mu_t))
        df_tuned = pd.DataFrame(rows_tuned)

        p_tuned_display = df_tuned["p_psi"].apply(u.psi_to_bar) if is_metric else df_tuned["p_psi"]
        rs_tuned_display = df_tuned["rs"].apply(u.rs_scf_stb_to_m3m3) if is_metric else df_tuned["rs"]
        bo_tuned_display = df_tuned["bo"].apply(u.bo_bbl_stb_to_m3m3) if is_metric else df_tuned["bo"]
        mu_tuned_display = df_tuned["mu"]  # сПз = cP, конвертация не нужна

        fig_rs.add_trace(go.Scatter(x=p_tuned_display, y=rs_tuned_display, mode="lines",
                                     name="Rs (с калибровкой)", line=dict(color="darkred", dash="dot")))
        fig_bo.add_trace(go.Scatter(x=p_tuned_display, y=bo_tuned_display, mode="lines",
                                     name="Bo (с калибровкой)", line=dict(color="darkgreen", dash="dot")))
        fig_mu.add_trace(go.Scatter(x=p_tuned_display, y=mu_tuned_display, mode="lines",
                                     name="μo (с калибровкой)", line=dict(color="black", dash="dot")))

        tc1, tc2, tc3 = st.columns(3)
        with tc1:
            st.plotly_chart(fig_rs, use_container_width=True, key="rs_with_tuning")
        with tc2:
            st.plotly_chart(fig_bo, use_container_width=True, key="bo_with_tuning")
        with tc3:
            st.plotly_chart(fig_mu, use_container_width=True, key="mu_with_tuning")

# ---------------------------------------------------------------------------
# Этап 5: загрузка лабораторного PVT-отчёта и сравнение
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Сравнение с лабораторным PVT-отчётом (опционально)")
st.caption(
    "Поддерживаются два типа лабораторных данных: дифференциальное разгазирование "
    "(Differential Liberation, DL) — таблица P/Rs/Bo/Bg/μo/μg/Z по ступеням давления "
    "при пластовой температуре, и сепараторный (flash) тест — GOR/Bo по ступеням сепарации "
    "с итоговыми свойствами товарной нефти."
)

# Распознавание колонок отчёта по названию (гибко: рус/англ, любой порядок колонок)
_DL_COLUMN_ALIASES = {
    "p": ["p", "давление", "pressure"],
    "rs": ["rs", "gor", "газосодержание", "растворенныйгаз", "растворённыйгаз"],
    "bo": ["bo", "boi", "bod", "объемныйкоэффициентнефти", "объёмныйкоэффициентнефти"],
    "bg": ["bg", "объемныйкоэффициентгаза", "объёмныйкоэффициентгаза"],
    "bt": ["bt", "boi2", "двухфазныйкоэффициент", "totalfvf"],
    "muo": ["muo", "mo", "oilviscosity", "вязкостьнефти"],
    "mug": ["mug", "gasviscosity", "вязкостьгаза"],
    "z": ["z", "zfactor", "zфактор", "коэффициентсверхсжимаемости"],
}


def _normalize_header(col: str) -> str:
    """Нормализует заголовок колонки: берёт часть до запятой/скобки, убирает
    все символы кроме букв и цифр, приводит к нижнему регистру."""
    head = str(col).split(",")[0].split("(")[0]
    return "".join(ch for ch in head.lower() if ch.isalnum())


def _match_dl_columns(df: pd.DataFrame) -> dict[str, str]:
    """Сопоставляет колонки лабораторного DL-отчёта с внутренними ключами
    (p, rs, bo, bg, bt, muo, mug, z) по названию, а не по порядковому номеру."""
    matched: dict[str, str] = {}
    for col in df.columns:
        norm = _normalize_header(col)
        for key, aliases in _DL_COLUMN_ALIASES.items():
            if key in matched:
                continue
            if norm in aliases:
                matched[key] = col
                break
    return matched


st.markdown("**1. Дифференциальное разгазирование (Differential Liberation)**")
dl_units = st.radio("Единицы измерения в файле DL-отчёта",
                     options=["Метрическая (бар, м³/м³)", "Field units (psi, scf/stb, bbl/stb)"],
                     index=0, horizontal=True, key="dl_units")
dl_file = st.file_uploader(
    "Загрузить CSV с DL-отчётом (колонки узнаются по названию: P, Rs, Bo, Bg, "
    "mu_o, mu_g, Z — можно в любом порядке и не все сразу)",
    type=["csv"], key="dl_uploader",
)

if dl_file is not None:
    try:
        df_dl = pd.read_csv(dl_file)
        dl_is_metric = dl_units.startswith("Метрическая")
        cols = _match_dl_columns(df_dl)

        if "p" not in cols:
            st.error("В файле не найдена колонка давления (P). Проверьте заголовки.")
        else:
            p_raw = df_dl[cols["p"]].astype(float)
            p_disp = (p_raw if dl_is_metric == is_metric else
                      (p_raw.apply(u.bar_to_psi) if dl_is_metric else p_raw.apply(u.psi_to_bar)))

            def _conv(raw: pd.Series, m2f, f2m):
                if dl_is_metric == is_metric:
                    return raw
                return raw.apply(m2f) if dl_is_metric else raw.apply(f2m)

            overlays = []  # (fig, column_key, label, converted_series)
            if "rs" in cols:
                rs_disp = _conv(df_dl[cols["rs"]].astype(float),
                                 u.rs_m3m3_to_scf_stb, u.rs_scf_stb_to_m3m3)
                overlays.append((fig_rs, "Rs (лаб. DL)", rs_disp))
            if "bo" in cols:
                bo_disp = _conv(df_dl[cols["bo"]].astype(float),
                                 u.bo_m3m3_to_bbl_stb, u.bo_bbl_stb_to_m3m3)
                overlays.append((fig_bo, "Bo (лаб. DL)", bo_disp))
            if "bg" in cols:
                bg_disp = _conv(df_dl[cols["bg"]].astype(float),
                                 u.bg_m3m3_to_rcf_scf, u.bg_rcf_scf_to_m3m3)
                overlays.append((fig_bg, "Bg (лаб. DL)", bg_disp))
            if "muo" in cols:
                muo_disp = df_dl[cols["muo"]].astype(float)  # сПз = сПз, конвертация не нужна
                overlays.append((fig_mu, "μo (лаб. DL)", muo_disp))
            if "mug" in cols:
                mug_disp = df_dl[cols["mug"]].astype(float)
                fig_mu.add_trace(go.Scatter(x=p_disp, y=mug_disp, mode="markers", yaxis="y2",
                                             name="μg (лаб. DL)", marker=dict(size=9, symbol="x")))
            if "z" in cols:
                z_disp = df_dl[cols["z"]].astype(float)
                overlays.append((fig_z, "Z (лаб. DL)", z_disp))

            for fig, label, series in overlays:
                fig.add_trace(go.Scatter(x=p_disp, y=series, mode="markers", name=label,
                                          marker=dict(size=10, symbol="diamond")))

            for fig, key in [(fig_rs, "rs"), (fig_bo, "bo"), (fig_bg, "bg"),
                              (fig_mu, "mu"), (fig_z, "z")]:
                st.plotly_chart(fig, use_container_width=True, key=f"{key}_with_dl")

            found_props = ", ".join(k.upper() for k in cols if k != "p")
            st.success(f"Лабораторные точки DL наложены на графики: {found_props or '—'}.")
            if not found_props:
                st.warning("Кроме давления, ни одна распознанная величина (Rs, Bo, Bg, mu_o, "
                           "mu_g, Z) не найдена — проверьте заголовки колонок.")
    except Exception as e:
        st.error(f"Не удалось прочитать DL-файл: {e}")

st.markdown("**2. Сепараторный (flash) тест**")
st.caption(
    "Данные по ступеням сепарации (давление/температура сепаратора, GOR и Bo каждой "
    "ступени) и итоговые свойства товарной нефти. Пока отображается как таблица для "
    "справки — используется, например, чтобы привести Rsb/Bob дифф. разгазирования "
    "к условиям промысловой сепарации (flash-коррекция)."
)
sep_units = st.radio("Единицы измерения в файле сепараторного теста",
                      options=["Метрическая (бар, м³/м³)", "Field units (psi, scf/stb, bbl/stb)"],
                      index=0, horizontal=True, key="sep_units")
sep_file = st.file_uploader(
    "Загрузить CSV сепараторного теста (колонки, например: Stage, P_sep, T_sep, GOR, Bo)",
    type=["csv"], key="sep_uploader",
)

if sep_file is not None:
    try:
        df_sep = pd.read_csv(sep_file)
        sep_is_metric = sep_units.startswith("Метрическая")
        st.dataframe(df_sep, use_container_width=True)

        gor_col = next((c for c in df_sep.columns
                        if _normalize_header(c) in ("gor", "rs", "газосодержание")), None)
        if gor_col is not None:
            gor_total_raw = df_sep[gor_col].astype(float).sum()
            if sep_is_metric == is_metric:
                gor_total = gor_total_raw
            elif sep_is_metric:
                gor_total = u.rs_m3m3_to_scf_stb(gor_total_raw)
            else:
                gor_total = u.rs_scf_stb_to_m3m3(gor_total_raw)
            unit_label = "м³/м³" if is_metric else "scf/stb"
            st.metric("Суммарный GOR по ступеням сепарации", f"{gor_total:.1f} {unit_label}")
        else:
            st.info("Колонка GOR/Rs по ступеням не найдена — показана только таблица как есть.")
    except Exception as e:
        st.error(f"Не удалось прочитать файл сепараторного теста: {e}")

# ---------------------------------------------------------------------------
# Экспорт таблицы
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Экспорт таблицы")

csv_bytes = df_display.to_csv(index=False).encode("utf-8-sig")
st.download_button("Скачать CSV", data=csv_bytes, file_name="pvt_table.csv", mime="text/csv")

excel_buffer = io.BytesIO()
df_display.to_excel(excel_buffer, index=False, sheet_name="PVT")
st.download_button("Скачать Excel", data=excel_buffer.getvalue(),
                    file_name="pvt_table.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
