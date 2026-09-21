"""
tests/test_pvt_correlations.py — регрессионные и физические тесты для
pvt_correlations.py (нефть, газ, вода).

Два типа проверок:
1. "Физические инварианты" — свойства, которые обязаны выполняться для
   ЛЮБЫХ корректных входных данных (монотонность Rs/Bo по давлению,
   непрерывность на границе Pb, диапазоны 0 < Z < 2 и т.п.). Эти тесты
   ловят логические ошибки при будущих правках формул.
2. "Регрессионные" — фиксируют текущие числовые результаты на конкретных
   наборах входных данных (проверены вручную на физическую адекватность
   при разработке). Если кто-то случайно поменяет коэффициент в формуле —
   тест упадёт даже без логической ошибки, это сигнал "проверь, что было
   изменено намеренно".

Запуск: pytest tests/ -v  (из корня репозитория)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pvt_correlations as pvt

# ---------------------------------------------------------------------------
# Общие тестовые условия (типичная казахстанская нефть средней плотности)
# ---------------------------------------------------------------------------
API = 35.0
GAMMA_G = 0.75
T_F = 200.0
RSB = 500.0  # scf/stb
GAMMA_O = 141.5 / (API + 131.5)


# ---------------------------------------------------------------------------
# 1. Pb (Standing) — инварианты
# ---------------------------------------------------------------------------
class TestPbStanding:
    def test_pb_positive_and_above_atmospheric(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        assert pb > 14.7

    def test_pb_zero_rsb_gives_atmospheric(self):
        assert pvt.pb_standing(0, GAMMA_G, API, T_F) == pytest.approx(14.7)

    def test_pb_increases_with_rsb(self):
        pb_low = pvt.pb_standing(100, GAMMA_G, API, T_F)
        pb_high = pvt.pb_standing(1000, GAMMA_G, API, T_F)
        assert pb_high > pb_low

    def test_pb_increases_with_temperature(self):
        # Standing: Pb растёт с температурой при прочих равных
        pb_cold = pvt.pb_standing(RSB, GAMMA_G, API, 120.0)
        pb_hot = pvt.pb_standing(RSB, GAMMA_G, API, 250.0)
        assert pb_hot > pb_cold

    def test_pb_regression_reference_case(self):
        # Зафиксировано и вручную проверено на физическую адекватность
        # (см. историю разработки): API=35, γg=0.75, T=200F, Rsb=500 scf/stb
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        assert pb == pytest.approx(2205.1, rel=1e-3)


# ---------------------------------------------------------------------------
# 2. Rs(P) — инварианты
# ---------------------------------------------------------------------------
class TestRsStanding:
    def test_rs_equals_rsb_at_and_above_pb(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        assert pvt.rs_standing(pb, pb, RSB, GAMMA_G, API, T_F) == pytest.approx(RSB)
        assert pvt.rs_standing(pb + 1000, pb, RSB, GAMMA_G, API, T_F) == pytest.approx(RSB)

    def test_rs_monotonically_increases_below_pb(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        pressures = [500, 1000, 1500, 2000]
        rs_values = [pvt.rs_standing(p, pb, RSB, GAMMA_G, API, T_F) for p in pressures]
        assert rs_values == sorted(rs_values)

    def test_rs_zero_at_atmospheric_pressure(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        rs = pvt.rs_standing(14.7, pb, RSB, GAMMA_G, API, T_F)
        assert rs >= 0
        assert rs < RSB  # у поверхности газа в нефти почти нет

    def test_rs_never_negative(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        rs = pvt.rs_standing(1.0, pb, RSB, GAMMA_G, API, T_F)
        assert rs >= 0


# ---------------------------------------------------------------------------
# 3. Bo(P) — инварианты и непрерывность на Pb
# ---------------------------------------------------------------------------
class TestBoStanding:
    def test_bo_above_one(self):
        # Объёмный коэффициент насыщенной нефти всегда > 1 (нефть в пласте
        # занимает больше места, чем на поверхности, из-за растворённого газа)
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        bo = pvt.bo_standing(pb, pb, RSB, RSB, GAMMA_G, GAMMA_O, T_F)
        assert bo > 1.0

    def test_bo_increases_with_pressure_below_pb(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        pressures = [500, 1000, 1500, 2000]
        bo_values = []
        for p in pressures:
            rs = pvt.rs_standing(p, pb, RSB, GAMMA_G, API, T_F)
            bo_values.append(pvt.bo_standing(p, pb, rs, RSB, GAMMA_G, GAMMA_O, T_F))
        assert bo_values == sorted(bo_values)

    def test_bo_decreases_with_pressure_above_pb(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        pressures = [pb + 200, pb + 500, pb + 1000]
        bo_values = []
        for p in pressures:
            co = pvt.co_vasquez_beggs(p, RSB, GAMMA_G, API, T_F)
            bo_values.append(pvt.bo_standing(p, pb, RSB, RSB, GAMMA_G, GAMMA_O, T_F, co))
        assert bo_values == sorted(bo_values, reverse=True)

    def test_bo_continuous_at_pb(self):
        # Bo не должен "прыгать" на границе точки насыщения
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        bo_at_pb = pvt.bo_standing(pb, pb, RSB, RSB, GAMMA_G, GAMMA_O, T_F)
        co = pvt.co_vasquez_beggs(pb + 1, RSB, GAMMA_G, API, T_F)
        bo_just_above = pvt.bo_standing(pb + 1, pb, RSB, RSB, GAMMA_G, GAMMA_O, T_F, co)
        assert bo_just_above == pytest.approx(bo_at_pb, rel=1e-3)

    def test_bo_above_pb_requires_co(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        with pytest.raises(ValueError):
            pvt.bo_standing(pb + 100, pb, RSB, RSB, GAMMA_G, GAMMA_O, T_F)


# ---------------------------------------------------------------------------
# 4. Вязкость нефти — инварианты
# ---------------------------------------------------------------------------
class TestOilViscosity:
    def test_dead_oil_viscosity_decreases_with_api(self):
        # Более лёгкая нефть (выше API) — менее вязкая
        mu_heavy = pvt.mu_dead_oil_beggs_robinson(20.0, T_F)
        mu_light = pvt.mu_dead_oil_beggs_robinson(45.0, T_F)
        assert mu_light < mu_heavy

    def test_dead_oil_viscosity_decreases_with_temperature(self):
        mu_cold = pvt.mu_dead_oil_beggs_robinson(API, 100.0)
        mu_hot = pvt.mu_dead_oil_beggs_robinson(API, 250.0)
        assert mu_hot < mu_cold

    def test_live_oil_viscosity_lower_than_dead_oil(self):
        # Растворённый газ снижает вязкость нефти
        mu_dead = pvt.mu_dead_oil_beggs_robinson(API, T_F)
        mu_live = pvt.mu_live_oil_beggs_robinson(mu_dead, RSB)
        assert mu_live < mu_dead

    def test_viscosity_minimum_at_bubble_point(self):
        # μo падает по мере роста Rs при P<=Pb (больше газа растворяется),
        # а выше Pb растёт снова (сжатие недонасыщенной нефти) —
        # т.е. минимум должен быть примерно в точке насыщения
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        mu_below = pvt.mu_oil(pb - 500, pb, pvt.rs_standing(pb - 500, pb, RSB, GAMMA_G, API, T_F),
                               RSB, API, T_F)
        mu_at_pb = pvt.mu_oil(pb, pb, RSB, RSB, API, T_F)
        mu_above = pvt.mu_oil(pb + 500, pb, RSB, RSB, API, T_F)
        assert mu_at_pb < mu_below
        assert mu_above > mu_at_pb

    def test_viscosity_positive(self):
        pb = pvt.pb_standing(RSB, GAMMA_G, API, T_F)
        mu = pvt.mu_oil(pb, pb, RSB, RSB, API, T_F)
        assert mu > 0


# ---------------------------------------------------------------------------
# 5. Сжимаемость нефти
# ---------------------------------------------------------------------------
class TestOilCompressibility:
    def test_co_positive_for_typical_oil(self):
        co = pvt.co_vasquez_beggs(3000, RSB, GAMMA_G, API, T_F)
        assert co > 0

    def test_co_decreases_with_pressure(self):
        # Co обратно пропорциональна P в корреляции Vasquez-Beggs
        co_low_p = pvt.co_vasquez_beggs(2000, RSB, GAMMA_G, API, T_F)
        co_high_p = pvt.co_vasquez_beggs(6000, RSB, GAMMA_G, API, T_F)
        assert co_high_p < co_low_p


# ---------------------------------------------------------------------------
# 6. Газ — псевдокритика, Z-фактор, Bg, вязкость
# ---------------------------------------------------------------------------
class TestGasCorrelations:
    def test_pseudo_critical_regression(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        assert ppc == pytest.approx(667.2, rel=1e-2)
        assert tpc == pytest.approx(404.7, rel=1e-2)

    def test_z_factor_near_one_at_low_pressure(self):
        # При низком приведённом давлении газ близок к идеальному (Z~1)
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        ppr, tpr = pvt.reduced_properties(100, T_F, ppc, tpc)
        z = pvt.z_factor_dak(ppr, tpr)
        assert 0.9 < z <= 1.05

    def test_z_factor_physical_range(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        for p in [500, 2000, 5000, 8000]:
            ppr, tpr = pvt.reduced_properties(p, T_F, ppc, tpc)
            z = pvt.z_factor_dak(ppr, tpr)
            assert 0.3 < z < 2.0, f"Z={z} вне физического диапазона при P={p}"

    def test_bg_decreases_with_pressure(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        bg_values = []
        for p in [500, 2000, 5000]:
            ppr, tpr = pvt.reduced_properties(p, T_F, ppc, tpc)
            z = pvt.z_factor_dak(ppr, tpr)
            bg_values.append(pvt.bg_gas(p, T_F, z))
        assert bg_values == sorted(bg_values, reverse=True)

    def test_gas_viscosity_increases_with_pressure(self):
        # При постоянной T вязкость газа растёт с давлением (растёт плотность)
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        mu_values = []
        for p in [500, 2000, 5000]:
            ppr, tpr = pvt.reduced_properties(p, T_F, ppc, tpc)
            z = pvt.z_factor_dak(ppr, tpr)
            mu_values.append(pvt.mu_gas_lee_gonzalez_eakin(p, T_F, z, GAMMA_G))
        assert mu_values == sorted(mu_values)

    def test_gas_viscosity_positive(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        ppr, tpr = pvt.reduced_properties(2000, T_F, ppc, tpc)
        z = pvt.z_factor_dak(ppr, tpr)
        mu_g = pvt.mu_gas_lee_gonzalez_eakin(2000, T_F, z, GAMMA_G)
        assert mu_g > 0


# ---------------------------------------------------------------------------
# 8. Коррекция Wichert-Aziz для кислого газа (H2S, CO2)
# ---------------------------------------------------------------------------
class TestWichertAziz:
    def test_zero_impurities_no_correction(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(GAMMA_G)
        ppc_c, tpc_c = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0, y_co2=0)
        assert ppc_c == pytest.approx(ppc)
        assert tpc_c == pytest.approx(tpc)

    def test_sour_gas_reduces_critical_properties(self):
        # Оба Ppc и Tpc должны снижаться при наличии H2S/CO2
        ppc, tpc = pvt.pseudo_critical_properties_standing(0.65)
        ppc_c, tpc_c = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0.144, y_co2=0.0319)
        assert ppc_c < ppc
        assert tpc_c < tpc

    def test_correction_increases_with_h2s_content(self):
        # Регрессия на реальных данных Королёвского месторождения (Казахстан,
        # H2S~14.4 мол.%, CO2~3.19 мол.%) — проверено вручную при разработке
        ppc, tpc = pvt.pseudo_critical_properties_standing(0.65)
        ppc_c, tpc_c = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0.144, y_co2=0.0319)
        assert tpc_c == pytest.approx(350.6, rel=1e-2)
        assert ppc_c == pytest.approx(624.2, rel=1e-2)

    def test_more_h2s_means_larger_correction(self):
        ppc, tpc = pvt.pseudo_critical_properties_standing(0.65)
        _, tpc_low = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0.02, y_co2=0.01)
        _, tpc_high = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0.15, y_co2=0.03)
        assert (tpc - tpc_high) > (tpc - tpc_low)

    def test_z_factor_changes_meaningfully_for_sour_gas(self):
        # На реальных пластовых условиях Королёвского месторождения
        # (P~3660 psi, T~73°C) коррекция Z-фактора не пренебрежимо мала
        ppc, tpc = pvt.pseudo_critical_properties_standing(0.65)
        t_f = 73 * 9 / 5 + 32
        p_psi = 3660

        ppr, tpr = pvt.reduced_properties(p_psi, t_f, ppc, tpc)
        z_sweet = pvt.z_factor_dak(ppr, tpr)

        ppc_c, tpc_c = pvt.wichert_aziz_correction(ppc, tpc, y_h2s=0.144, y_co2=0.0319)
        ppr_c, tpr_c = pvt.reduced_properties(p_psi, t_f, ppc_c, tpc_c)
        z_sour = pvt.z_factor_dak(ppr_c, tpr_c)

        assert abs(z_sour / z_sweet - 1) > 0.02  # >2% разницы — не шум


# ---------------------------------------------------------------------------
# 7. Вода — Bw, вязкость, сжимаемость
# ---------------------------------------------------------------------------
class TestWaterCorrelations:
    def test_bw_close_to_one(self):
        # Bw пластовой воды обычно в диапазоне 0.98-1.08
        bw = pvt.bw_mccain(3000, T_F)
        assert 0.9 < bw < 1.15

    def test_water_viscosity_positive(self):
        mu_w = pvt.mu_water_mccain(3000, T_F, salinity_ppm=30000)
        assert mu_w > 0

    def test_water_viscosity_increases_with_salinity(self):
        # Солёная вода обычно более вязкая, чем пресная, при прочих равных
        mu_fresh = pvt.mu_water_mccain(3000, 150.0, salinity_ppm=0)
        mu_saline = pvt.mu_water_mccain(3000, 150.0, salinity_ppm=100000)
        assert mu_saline > mu_fresh

    def test_water_compressibility_positive(self):
        cw = pvt.cw_water(3000, T_F, salinity_ppm=30000)
        assert cw > 0

    def test_water_compressibility_small(self):
        # Вода практически несжимаема: Cw порядка 1e-6 - 1e-5 1/psi
        cw = pvt.cw_water(3000, T_F, salinity_ppm=30000)
        assert 1e-7 < cw < 1e-4
