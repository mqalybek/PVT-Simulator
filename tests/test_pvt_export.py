"""
tests/test_pvt_export.py — тесты генератора Eclipse/tNavigator .GRDECL
(pvt_export.py): физическая корректность веток PVTO, монотонность,
корректность формата вывода.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pvt_correlations as pvt
import pvt_export as pe
import units as u

API = u.rho_kgm3_to_api(798.0)
GAMMA_G = 0.94
T_C = 43.1
GAMMA_O = 0.798


class TestBuildPvtoBranches:
    def _branches(self, **overrides):
        kwargs = dict(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=5, p_max_bar=500.0, n_points_per_branch=5,
        )
        kwargs.update(overrides)
        return pe.build_pvto_branches(**kwargs)

    def test_returns_requested_number_of_branches(self):
        branches = self._branches(n_branches=6)
        assert len(branches) == 6

    def test_rs_values_increasing(self):
        branches = self._branches()
        rs_values = [rs for rs, _ in branches]
        assert rs_values == sorted(rs_values)

    def test_each_branch_reaches_p_max(self):
        branches = self._branches(p_max_bar=500.0)
        for _rs, rows in branches:
            assert rows[-1][0] == pytest.approx(500.0)

    def test_first_point_is_at_bubble_point(self):
        # первая точка ветки должна быть примерно в её собственной Pb
        branches = self._branches()
        for rs_m3m3, rows in branches:
            pb_bar_expected = u.psi_to_bar(
                pvt.pb_standing(u.rs_m3m3_to_scf_stb(rs_m3m3), GAMMA_G, API, T_C * 9 / 5 + 32)
            )
            assert rows[0][0] == pytest.approx(pb_bar_expected, rel=1e-6)

    def test_bo_decreases_above_bubble_point(self):
        branches = self._branches()
        for _rs, rows in branches:
            bo_values = [r[1] for r in rows]
            assert bo_values == sorted(bo_values, reverse=True)

    def test_viscosity_increases_above_bubble_point(self):
        branches = self._branches()
        for _rs, rows in branches:
            mu_values = [r[2] for r in rows]
            assert mu_values == sorted(mu_values)

    def test_higher_rs_branch_has_higher_bubble_point(self):
        branches = self._branches()
        pb_values = [rows[0][0] for _rs, rows in branches]
        assert pb_values == sorted(pb_values)

    def test_calibration_factors_applied(self):
        base = self._branches(factor_pb=1.0, factor_bo=1.0, factor_mu=1.0)
        tuned = self._branches(factor_pb=0.8, factor_bo=1.05, factor_mu=0.5)
        pb_base = base[2][1][0][0]
        pb_tuned = tuned[2][1][0][0]
        assert pb_tuned == pytest.approx(pb_base * 0.8, rel=1e-6)


class TestBuildPvdgTable:
    def test_bg_decreases_with_pressure(self):
        rows = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=6)
        bg_values = [r[1] for r in rows]
        assert bg_values == sorted(bg_values, reverse=True)

    def test_viscosity_increases_with_pressure(self):
        rows = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=6)
        mu_values = [r[2] for r in rows]
        assert mu_values == sorted(mu_values)

    def test_sour_gas_correction_changes_result(self):
        sweet = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3)
        sour = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3,
                                    y_h2s=0.144, y_co2=0.0319)
        assert sweet[1][1] != pytest.approx(sour[1][1])


class TestBuildPvtwRecord:
    def test_returns_five_values(self):
        record = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        assert len(record) == 5

    def test_pref_matches_input(self):
        record = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        assert record[0] == 142.0

    def test_last_value_is_zero(self):
        record = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        assert record[4] == 0.0

    def test_bw_close_to_one(self):
        record = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        assert 0.9 < record[1] < 1.15


class TestFormatGrdecl:
    def test_contains_all_keywords(self):
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=3, p_max_bar=500.0, n_points_per_branch=3,
        )
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3)
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)
        for kw in ("PVTW", "PVTO", "PVDG", "DENSITY", "FILEUNIT", "METRIC"):
            assert kw in text

    def test_roundtrip_saturation_curve_matches_branch_start_points(self):
        # То, что сами построили (build_*), должны сами же и распарсить
        # обратно (parse_*) — иначе наложение "своей же" модели на факт
        # покажет не то, что реально в файле
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=5, p_max_bar=500.0, n_points_per_branch=4,
        )
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3)
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)

        parsed = pe.parse_pvto_saturation_curve(text)
        assert len(parsed) == len(branches)
        for (rs_expected, rows), (rs_parsed, pb_parsed, bo_parsed, mu_parsed) in zip(
            branches, parsed
        ):
            pb_expected, bo_expected, mu_expected = rows[0]
            assert rs_parsed == pytest.approx(rs_expected, rel=1e-3)
            assert pb_parsed == pytest.approx(pb_expected, rel=1e-3)
            assert bo_parsed == pytest.approx(bo_expected, rel=1e-3)
            assert mu_parsed == pytest.approx(mu_expected, rel=1e-3)

    def test_saturation_curve_sorted_by_pb(self):
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=6, p_max_bar=500.0, n_points_per_branch=3,
        )
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3)
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)
        parsed = pe.parse_pvto_saturation_curve(text)
        pb_values = [p[1] for p in parsed]
        assert pb_values == sorted(pb_values)

    def test_parse_fileunit_metric(self):
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=2, p_max_bar=500.0, n_points_per_branch=2,
        )
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=2)
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)
        assert pe.parse_fileunit(text) == "METRIC"

    def test_parse_pvdg_table_roundtrip(self):
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=5)
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=2, p_max_bar=500.0, n_points_per_branch=2,
        )
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)
        parsed = pe.parse_pvdg_table(text)
        assert len(parsed) == len(pvdg)
        for (p1, bg1, mu1), (p2, bg2, mu2) in zip(pvdg, parsed):
            assert p2 == pytest.approx(p1, rel=1e-4)
            assert bg2 == pytest.approx(bg1, rel=1e-4)
            assert mu2 == pytest.approx(mu1, rel=1e-4)

    def test_pvto_branch_count_matches_slash_terminators(self):
        branches = pe.build_pvto_branches(
            rsb_field_m3m3=114.0, api=API, gamma_g=GAMMA_G, t_c=T_C, gamma_o=GAMMA_O,
            pb_fn=pvt.pb_standing, rs_fn=pvt.rs_standing, bo_fn=pvt.bo_standing,
            rs_max_m3m3=120.0, n_branches=4, p_max_bar=500.0, n_points_per_branch=3,
        )
        pvdg = pe.build_pvdg_table(GAMMA_G, T_C, p_max_bar=500.0, n_points=3)
        pvtw = pe.build_pvtw_record(142.0, T_C, salinity_ppm=250000)
        text = pe.format_grdecl(pvtw, branches, pvdg, 798.0, 1157.9, 1.15)
        pvto_section = text.split("PVTO")[1].split("PVDG")[0]
        # 4 ветки -> 4 терминатора "/" в конце строк данных + 1 закрывающий "  /"
        assert pvto_section.count("/") == 5
