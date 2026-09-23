"""
app.py — точка входа Streamlit-приложения (`streamlit run app.py`).

Сам код страниц лежит в views/ — этот файл только регистрирует их через
st.navigation(), чтобы в боковом меню были осмысленные названия ("PVT-
калькулятор", "Анализ месторождения"), а не имена файлов по умолчанию.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="PVT-Simulator", layout="wide")

calculator_page = st.Page(
    "views/calculator.py", title="PVT-калькулятор", icon="🧮", default=True,
)
field_analysis_page = st.Page(
    "views/field_analysis.py", title="Анализ месторождения", icon="🛢️",
)

nav = st.navigation([calculator_page, field_analysis_page])
nav.run()
