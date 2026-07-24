"""Security contract shared by every CSV export."""

from __future__ import annotations

import csv
import io

from app.csv_export import render_csv


def test_render_csv_neutralizes_formula_cells_after_whitespace_and_controls() -> None:
    hostile_cells = [
        "=SUM(1,1)",
        "+SUM(1,1)",
        "-2+3",
        "@cmd",
        "\t=SUM(1,1)",
        "\r+SUM(1,1)",
        "   -2+3",
        "\x00@cmd",
    ]

    rendered = render_csv([["value"], *[[cell] for cell in hostile_cells]])
    parsed = list(csv.reader(io.StringIO(rendered)))

    assert parsed[0] == ["value"]
    assert [row[0] for row in parsed[1:]] == ["'" + cell for cell in hostile_cells]


def test_render_csv_quotes_commas_quotes_and_newlines_without_changing_content() -> None:
    values = ["comma, value", 'quote " value', "line one\nline two"]

    rendered = render_csv([["a", "b", "c"], values])

    assert list(csv.reader(io.StringIO(rendered))) == [["a", "b", "c"], values]
