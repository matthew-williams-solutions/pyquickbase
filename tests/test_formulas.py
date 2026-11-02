"""Tests for reading formula results and checking formula text, which do no network access."""

from decimal import Decimal
from typing import Any

import pytest

from pyquickbase import Field, FormulaResult, InvalidValueError
from pyquickbase._writes import field_properties, require_formula


@pytest.mark.parametrize(
    ("result_type", "text", "value"),
    [
        ("bool", "1", True),
        ("bool", "0", False),
        ("bool", "", None),
        ("bool", "maybe", "maybe"),
        ("number", "25", Decimal(25)),
        ("number", "0", Decimal(0)),
        ("number", "0.3333333", Decimal("0.3333333")),
        ("number", "1000000000000000000", Decimal(10**18)),
        ("number", "", None),
        ("number", "not a number", "not a number"),
        ("text", "007", "007"),
        ("text", "1e5", "1e5"),
        ("text", "true", "true"),
        ("text", "", ""),
        ("null", "", None),
        ("date", "03-04-2026", "03-04-2026"),
        ("date", "", ""),
        ("date/time", "03-03-2026 09:06 PM", "03-03-2026 09:06 PM"),
        ("timeofday", "11:27 am", "11:27 am"),
        ("duration", "72:00", "72:00"),
        ("textlist", "a ; b", "a ; b"),
        ("user", "", ""),
        ("something new", "x", "x"),
    ],
)
def test_value_converts_only_where_its_safe(result_type: str, text: str, value: Any) -> None:
    result = FormulaResult.from_api({"result": text, "resultType": result_type})
    assert (result.text, result.type) == (text, result_type)
    assert result.value == value
    assert type(result.value) is type(value)


def test_result_values_stay_out_of_reprs() -> None:
    assert repr(FormulaResult(text="secret", type="text")) == "FormulaResult(type='text')"


@pytest.mark.parametrize("formula", ["", "   ", "\n\t", None, 123])
def test_require_formula_refuses_blanks_and_non_strings(formula: Any) -> None:
    with pytest.raises(InvalidValueError, match="formula"):
        require_formula(formula)


def test_field_properties_merges_the_formula() -> None:
    assert field_properties(None, None) == {}
    assert field_properties({"a": 1}, None) == {"a": 1}
    assert field_properties(None, "1 + 1") == {"formula": "1 + 1"}
    assert field_properties({"a": 1}, "1 + 1") == {"a": 1, "formula": "1 + 1"}


@pytest.mark.parametrize(
    ("properties", "formula"),
    [(None, ""), (None, "  "), ({"formula": ""}, None), ({"formula": " "}, None)],
)
def test_field_properties_refuses_a_blank_formula_however_its_sent(
    properties: Any, formula: Any
) -> None:
    with pytest.raises(InvalidValueError, match="formula"):
        field_properties(properties, formula)


def test_field_exposes_its_formula() -> None:
    plain = {"id": 6, "label": "A", "fieldType": "text", "properties": {"formula": ""}}
    formula = {"id": 7, "label": "B", "fieldType": "numeric", "properties": {"formula": "1 + 1"}}
    assert Field.from_api(plain).formula is None
    assert Field.from_api(formula).formula == "1 + 1"
    assert Field.from_api({"id": 8, "label": "C", "fieldType": "text"}).formula is None
