"""Tests for resolve_output_schema utility."""

import pytest

from yutori._schema import resolve_output_schema

# -- Fake classes to avoid a hard pydantic dependency --


class FakeV2Model:
    """Simulates a Pydantic v2 BaseModel with model_json_schema."""

    @classmethod
    def model_json_schema(cls) -> dict:
        return {"type": "object", "properties": {"name": {"type": "string"}}}


class FakeV1Model:
    """Simulates a Pydantic v1 BaseModel with schema."""

    @classmethod
    def schema(cls) -> dict:
        return {"type": "object", "properties": {"age": {"type": "integer"}}}


class FakeV2AndV1Model:
    """Has both v2 and v1 methods — v2 should be preferred."""

    @classmethod
    def model_json_schema(cls) -> dict:
        return {"source": "v2"}

    @classmethod
    def schema(cls) -> dict:
        return {"source": "v1"}


class FakeBadReturnModel:
    """model_json_schema returns a non-dict."""

    @classmethod
    def model_json_schema(cls) -> str:
        return "not a dict"


class FakeBadV1ReturnModel:
    """schema returns a non-dict."""

    @classmethod
    def schema(cls) -> list:
        return [1, 2, 3]


class FakeInstanceMethodSchema:
    """Has schema as an instance method (not classmethod) — calling on cls raises TypeError."""

    def model_json_schema(self) -> dict:
        return {"type": "object"}


class FakeNonCallableAttr:
    """Has model_json_schema and schema as non-callable attributes."""

    model_json_schema = "not callable"
    schema = 42


# -- Tests --


class TestResolveOutputSchema:
    def test_none_returns_none(self):
        assert resolve_output_schema(None) is None

    def test_dict_passthrough(self):
        schema = {"type": "object", "properties": {"x": {"type": "number"}}}
        assert resolve_output_schema(schema) is schema

    def test_v2_class(self):
        result = resolve_output_schema(FakeV2Model)
        assert result == {"type": "object", "properties": {"name": {"type": "string"}}}

    def test_v2_instance(self):
        result = resolve_output_schema(FakeV2Model())
        assert result == {"type": "object", "properties": {"name": {"type": "string"}}}

    def test_v1_class(self):
        result = resolve_output_schema(FakeV1Model)
        assert result == {"type": "object", "properties": {"age": {"type": "integer"}}}

    def test_v1_instance(self):
        result = resolve_output_schema(FakeV1Model())
        assert result == {"type": "object", "properties": {"age": {"type": "integer"}}}

    def test_v2_preferred_over_v1(self):
        result = resolve_output_schema(FakeV2AndV1Model)
        assert result == {"source": "v2"}

    def test_v2_preferred_over_v1_instance(self):
        result = resolve_output_schema(FakeV2AndV1Model())
        assert result == {"source": "v2"}

    def test_invalid_string_raises(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema("bad")

    def test_invalid_int_raises(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema(42)

    def test_invalid_list_raises(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema([1, 2])

    def test_bad_return_from_v2(self):
        with pytest.raises(TypeError, match="model_json_schema.*returned str"):
            resolve_output_schema(FakeBadReturnModel)

    def test_bad_return_from_v1(self):
        with pytest.raises(TypeError, match="schema.*returned list"):
            resolve_output_schema(FakeBadV1ReturnModel)

    def test_instance_method_on_class_raises_canonical_error(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema(FakeInstanceMethodSchema)

    def test_non_callable_attributes_ignored(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema(FakeNonCallableAttr)

    def test_non_callable_attributes_ignored_instance(self):
        with pytest.raises(TypeError, match="output_schema must be"):
            resolve_output_schema(FakeNonCallableAttr())


class TestRealPydantic:
    def test_pydantic_v2_basemodel(self):
        pydantic = pytest.importorskip("pydantic")

        class Employee(pydantic.BaseModel):
            name: str
            title: str

        result = resolve_output_schema(Employee)
        assert isinstance(result, dict)
        assert "properties" in result
        assert "name" in result["properties"]
        assert "title" in result["properties"]

    def test_pydantic_v2_basemodel_instance(self):
        pydantic = pytest.importorskip("pydantic")

        class Employee(pydantic.BaseModel):
            name: str
            title: str

        result = resolve_output_schema(Employee(name="Alice", title="Engineer"))
        assert isinstance(result, dict)
        assert "name" in result["properties"]
