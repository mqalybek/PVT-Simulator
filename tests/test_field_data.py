"""
tests/test_field_data.py — тесты парсера "сырых" лабораторных PVT-таблиц
(field_data.py): устойчивость к грязным значениям, склейка многострочной
шапки, нормализация кодов горизонтов, детектор строк-агрегатов.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import field_data as fd


class TestRobustFloat:
    def test_plain_number(self):
        assert fd.robust_float(12.3) == 12.3

    def test_comma_decimal(self):
        assert fd.robust_float("1,7568*") == pytest.approx(1.7568)

    def test_dash_is_nan(self):
        assert math.isnan(fd.robust_float("-"))

    def test_none_is_nan(self):
        assert math.isnan(fd.robust_float(None))

    def test_plain_int_string(self):
        assert fd.robust_float("42") == 42.0

    def test_asterisk_suffix(self):
        assert fd.robust_float("31*") == 31.0

    def test_text_is_nan(self):
        assert math.isnan(fd.robust_float("Среднее значение: горизонт PT-IV"))


class TestNormalizeCode:
    def test_mixed_cyrillic_latin_horizon_match(self):
        # "PT-IV" (латиница) и "РТ-IV" (кириллица Р,Т) визуально одинаковы,
        # но это разные юникод-символы — должны нормализоваться в один код
        assert fd.normalize_code("PT-IV") == fd.normalize_code("РТ-IV")

    def test_whitespace_and_case(self):
        assert fd.normalize_code("  pt-v  ") == fd.normalize_code("PT-V")

    def test_none_returns_none(self):
        assert fd.normalize_code(None) is None

    def test_empty_string_returns_none(self):
        assert fd.normalize_code("") is None


class TestMergeHeaderRows:
    def test_single_row(self):
        headers = fd.merge_header_rows([("A", "B", "C")])
        assert headers == ["A", "B", "C"]

    def test_multi_row_merge(self):
        rows = [
            ("Давление", "Давление", "Объемный"),
            (None, "насыщения", "коэффициент"),
            (None, "Мпа", "д.ед"),
        ]
        headers = fd.merge_header_rows(rows)
        assert headers[0] == "Давление"
        assert headers[1] == "Давление | насыщения | Мпа"
        assert headers[2] == "Объемный | коэффициент | д.ед"

    def test_empty_column_gets_placeholder(self):
        headers = fd.merge_header_rows([(None, "X")])
        assert headers[0] == "Колонка 1"
        assert headers[1] == "X"


class TestSuggestColumnMapping:
    def test_recognizes_kazakh_style_headers(self):
        headers = ["№№", "№ скв.", "Горизонт", "блок",
                   "Давление | насыщения | Мпа", "Газосодержание | м3/м3"]
        mapping = fd.suggest_column_mapping(headers)
        assert mapping["well"] == "№ скв."
        assert mapping["horizon"] == "Горизонт"
        assert mapping["block"] == "блок"
        assert mapping["pb"] == "Давление | насыщения | Мпа"
        assert mapping["rs_m3m3"] == "Газосодержание | м3/м3"

    def test_recognizes_english_headers(self):
        headers = ["Pressure (psi)", "GOR (scf/stb)", "Boi (bbl/stb)"]
        mapping = fd.suggest_column_mapping(headers)
        assert mapping.get("p_sample") == "Pressure (psi)"
        assert mapping.get("rs_m3m3") == "GOR (scf/stb)"
        assert mapping.get("bo") == "Boi (bbl/stb)"


class TestGuessHeaderAndDataRows:
    def test_typical_layout(self):
        rows = [
            ("№№", "Горизонт", "Pb"),
            (1, "PT-IV", 14.78),
            (2, "PT-V", 15.84),
        ]
        header_start, data_start = fd.guess_header_and_data_rows(rows)
        assert header_start == 0
        assert data_start == 1


class TestBuildCleanDataframe:
    def _sample_raw_df(self):
        return pd.DataFrame({
            "№№": [1, 2, "Среднее значение: горизонт PT-IV"],
            "Горизонт": ["PT-IV", "РТ-IV", None],
            "Давление | насыщения | Мпа": [14.78, 15.0, 14.9],
            "Газосодержание | м3/м3": [165.69, 170.0, 167.8],
        })

    def test_aggregate_row_detected(self):
        df_raw = self._sample_raw_df()
        mapping = {"sample_no": "№№", "horizon": "Горизонт",
                   "pb": "Давление | насыщения | Мпа", "rs_m3m3": "Газосодержание | м3/м3"}
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        assert df_clean["is_aggregate"].tolist() == [False, False, True]

    def test_horizon_normalized_across_rows(self):
        df_raw = self._sample_raw_df()
        mapping = {"sample_no": "№№", "horizon": "Горизонт",
                   "pb": "Давление | насыщения | Мпа", "rs_m3m3": "Газосодержание | м3/м3"}
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        real_rows = df_clean[~df_clean["is_aggregate"]]
        assert real_rows["horizon"].nunique() == 1  # PT-IV и РТ-IV — один горизонт

    def test_anomaly_marker_detected_on_well_column(self):
        df_raw = pd.DataFrame({
            "№ скв.": ["31*", "12", "5*"],
            "Давление | насыщения | Мпа": [14.78, 15.0, 14.9],
        })
        mapping = {"well": "№ скв.", "pb": "Давление | насыщения | Мпа"}
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        assert df_clean["is_anomalous"].tolist() == [True, False, True]

    def test_mass_balance_cross_check_flags_inconsistent_row(self):
        # Согласованная проба (как в реальном файле, ~0.2% расхождения)
        # и явно рассогласованная (Rs м3/т не бьётся с Rs м3/м3)
        df_raw = pd.DataFrame({
            "Плотность пластовой нефти | г/см3": [0.738, 0.738],
            "Объемный | коэффициент | д.ед": [1.299, 1.299],
            "Газосодержание | м3/м3": [165.69, 165.69],
            "Газосодержание | м3/т": [203.8, 400.0],  # вторая строка испорчена
            "Плотность газа после однократного разгазирования | д.ед (по воздуху)": [0.726, 0.726],
        })
        mapping = {
            "rho_oil": "Плотность пластовой нефти | г/см3",
            "bo": "Объемный | коэффициент | д.ед",
            "rs_m3m3": "Газосодержание | м3/м3",
            "rs_m3t": "Газосодержание | м3/т",
            "gas_rel_density": "Плотность газа после однократного разгазирования | д.ед (по воздуху)",
        }
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        assert df_clean.loc[0, "rs_cross_check_dev_pct"] < 1.0
        assert df_clean.loc[1, "rs_cross_check_dev_pct"] > 50.0

    def test_cross_check_nan_without_gas_density(self):
        df_raw = pd.DataFrame({
            "Давление | насыщения | Мпа": [14.78],
            "Газосодержание | м3/м3": [165.69],
        })
        mapping = {"pb": "Давление | насыщения | Мпа", "rs_m3m3": "Газосодержание | м3/м3"}
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        assert math.isnan(df_clean.loc[0, "rs_cross_check_dev_pct"])

    def test_p_below_pb_flag(self):
        df_raw = pd.DataFrame({
            "Давление | насыщения | Мпа": [14.78, 5.0],
            "Давление | исследования проб | Мпа": [19.2, 4.0],  # вторая ниже Pb
        })
        mapping = {"pb": "Давление | насыщения | Мпа",
                   "p_sample": "Давление | исследования проб | Мпа"}
        df_clean = fd.build_clean_dataframe(df_raw, mapping)
        assert df_clean["p_below_pb"].tolist() == [False, True]


class TestFitTrend:
    def test_linear_fit_recovers_known_line(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = 2.0 * x + 1.0
        res = fd.fit_trend(x, y, kind="linear")
        assert res is not None
        assert res["r2"] == pytest.approx(1.0, abs=1e-6)

    def test_returns_none_for_too_few_points(self):
        assert fd.fit_trend(np.array([1.0, 2.0]), np.array([1.0, 2.0]), kind="linear") is None

    def test_power_fit_rejects_non_positive(self):
        x = np.array([1.0, -2.0, 3.0])
        y = np.array([1.0, 2.0, 3.0])
        assert fd.fit_trend(x, y, kind="power") is None
