"""tests/test_units.py — pytest-обёртка над конвертацией единиц (units.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import units as u


class TestPressure:
    def test_bar_to_psi(self):
        assert u.bar_to_psi(1) == pytest.approx(14.5038, rel=1e-4)

    def test_psi_to_bar_roundtrip(self):
        assert u.psi_to_bar(u.bar_to_psi(37.5)) == pytest.approx(37.5)

    def test_mpa_to_bar_exact(self):
        assert u.mpa_to_bar(1) == 10.0

    def test_bar_to_mpa_exact(self):
        assert u.bar_to_mpa(10) == 1.0

    def test_mpa_psi_roundtrip(self):
        assert u.psi_to_mpa(u.mpa_to_psi(5.0)) == pytest.approx(5.0)

    def test_kgf_cm2_to_bar(self):
        assert u.kgf_cm2_to_bar(1) == pytest.approx(0.980665, rel=1e-6)

    def test_bar_to_kgf_cm2_roundtrip(self):
        assert u.bar_to_kgf_cm2(u.kgf_cm2_to_bar(20.0)) == pytest.approx(20.0)

    def test_kgf_cm2_to_psi(self):
        assert u.kgf_cm2_to_psi(1) == pytest.approx(14.2233, rel=1e-3)

    def test_kgf_cm2_mpa_roundtrip(self):
        assert u.mpa_to_kgf_cm2(u.kgf_cm2_to_mpa(15.0)) == pytest.approx(15.0)


class TestTemperature:
    def test_freezing_point(self):
        assert u.c_to_f(0) == pytest.approx(32.0)

    def test_boiling_point(self):
        assert u.c_to_f(100) == pytest.approx(212.0)

    def test_roundtrip(self):
        assert u.f_to_c(u.c_to_f(95.0)) == pytest.approx(95.0)


class TestGasSolutionRatio:
    def test_m3m3_to_scf_stb(self):
        assert u.rs_m3m3_to_scf_stb(1) == pytest.approx(5.614583, rel=1e-4)

    def test_roundtrip(self):
        assert u.rs_scf_stb_to_m3m3(u.rs_m3m3_to_scf_stb(120.0)) == pytest.approx(120.0)


class TestFormationVolumeFactor:
    def test_bo_is_one_to_one(self):
        assert u.bo_m3m3_to_bbl_stb(1.25) == 1.25
        assert u.bo_bbl_stb_to_m3m3(1.25) == 1.25

    def test_bg_is_one_to_one(self):
        assert u.bg_m3m3_to_rcf_scf(0.005) == 0.005

    def test_bg_to_bbl_mscf(self):
        assert u.bg_rcf_scf_to_bbl_mscf(1) == pytest.approx(178.108, rel=1e-2)


class TestOilDensity:
    def test_api_from_sg(self):
        # SG=0.85 -> API = 141.5/0.85 - 131.5
        assert u.sg_to_api(0.85) == pytest.approx(141.5 / 0.85 - 131.5)

    def test_known_density_api_pair(self):
        # 850 кг/м3 -> SG=0.85 -> API ~ 34.97
        api = u.rho_kgm3_to_api(850)
        assert api == pytest.approx(34.97, abs=0.1)

    def test_roundtrip(self):
        rho = 900.0
        assert u.api_to_rho_kgm3(u.rho_kgm3_to_api(rho)) == pytest.approx(rho, abs=1e-6)


class TestCompressibility:
    def test_roundtrip(self):
        c = 3.0e-6
        assert u.compressibility_per_bar_to_per_psi(
            u.compressibility_per_psi_to_per_bar(c)) == pytest.approx(c, rel=1e-6)


class TestSalinity:
    def test_gpl_to_ppm(self):
        assert u.salinity_gpl_to_ppm(30) == 30000

    def test_ppm_to_wt_pct(self):
        assert u.salinity_ppm_to_wt_pct(10000) == pytest.approx(1.0)
