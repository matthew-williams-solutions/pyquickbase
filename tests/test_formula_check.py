"""Reading what a formula uses, and refusing one that reads more than a client may."""

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import ReadRefusedError
from pyquickbase._formula import check_formula, formula_parts


@pytest.mark.parametrize(
    ("formula", "fields", "functions"),
    [
        ("[Hours] * 2", {"Hours"}, set()),
        ('If([Status] = "Done", [Hours], 0)', {"Status", "Hours"}, {"If"}),
        ('"[Not a field]" & [Title]', {"Title"}, set()),
        ('"He said \\"[x]\\"" & [Title]', {"Title"}, set()),
        ("// [Hidden]\n[Title]", {"Title"}, set()),
        ("/* [Hidden] */ [Title]", {"Title"}, set()),
        ("ToText ([Hours])", {"Hours"}, {"ToText"}),
        ("var number x = [Hours]; $x", {"Hours"}, set()),
        ("[Hours] and ([Billable])", {"Hours", "Billable"}, {"and"}),
        ('GetRecords("{3.GT.0}", [_DBID_TASKS])', {"_DBID_TASKS"}, {"GetRecords"}),
        ("'[Quoted]'", {"Quoted"}, set()),
    ],
)
def test_it_finds_the_fields_and_functions_a_formula_uses(
    formula: str, fields: set[str], functions: set[str]
) -> None:
    parts = formula_parts(formula)
    assert (parts.fields, parts.functions) == (fields, functions)


@pytest.mark.parametrize("formula", ['"open', "[Hours", "/* never closed", '"\\"'])
def test_a_formula_it_cant_read_is_refused(formula: str) -> None:
    with pytest.raises(ReadRefusedError, match="can't be checked"):
        formula_parts(formula)


def test_only_readable_fields_may_be_used_ignoring_case() -> None:
    assert check_formula("[hours] * 2", ["Hours"]).fields == {"hours"}
    with pytest.raises(ReadRefusedError, match=r"uses \[Salary\]"):
        check_formula("[Hours] + [Salary]", ["Hours"])


@pytest.mark.parametrize(
    "function", ["GetRecords", "GetRecord", "GetFieldValues", "SumValues", "UserToEmail", "Bogus"]
)
def test_functions_that_may_read_other_records_are_refused(function: str) -> None:
    with pytest.raises(ReadRefusedError, match=rf"calls {function}\(\)"):
        check_formula(f"{function}([Hours])", ["Hours"])


def test_known_functions_are_allowed_in_any_case() -> None:
    check_formula("IF(IsNull([Hours]), 0, Round([Hours], 0.5)) & ToText(Today())", ["Hours"])


@given(st.text(max_size=60))
def test_any_text_is_read_or_refused_never_crashes(formula: str) -> None:
    with contextlib.suppress(ReadRefusedError):
        formula_parts(formula)


def test_a_field_may_be_named_by_its_id() -> None:
    assert check_formula("[_FID_10] * 2", ["Hours"], [10]).fields == {"_FID_10"}
    with pytest.raises(ReadRefusedError, match=r"uses \[_fid_16\]"):
        check_formula("[_fid_16] * 2", ["Hours"], [10])


def test_each_unsafe_function_is_named_once_as_written() -> None:
    with pytest.raises(ReadRefusedError, match=r"calls GetRecords\(\), which") as caught:
        check_formula('Size(GetRecords("{3.GT.0}")) + Size(getrecords("x"))', [])
    assert "Size" not in str(caught.value)
