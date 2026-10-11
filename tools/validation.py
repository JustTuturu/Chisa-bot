"""Minimal JSON-schema subset validator for tool arguments."""

from __future__ import annotations


class ValidationError(ValueError):
    pass


def _matches(value, expected: str) -> bool:
    if expected == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer()
        )
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def validate_object(schema: dict, value, path: str = "input") -> None:
    """Validate value against a small JSON-schema subset; raises ValidationError."""
    if not isinstance(schema, dict):
        raise ValidationError(f"{path}: invalid schema")

    types = schema.get("type")
    if types is not None:
        type_list = [types] if isinstance(types, str) else list(types)
        if not any(_matches(value, t) for t in type_list):
            raise ValidationError(f"{path} must be of type {' or '.join(type_list)}")

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ValidationError(f"{path} must be one of {enum!r}")

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                raise ValidationError(f"{path}.{req} is required")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                validate_object(sub, value[key], f"{path}.{key}")
        extra = schema.get("maxProperties")
        if extra is not None and len(value) > extra:
            raise ValidationError(f"{path} has too many properties")

    if isinstance(value, list):
        items = schema.get("items")
        if items:
            for i, item in enumerate(value):
                validate_object(items, item, f"{path}[{i}]")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValidationError(f"{path} needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValidationError(f"{path} allows at most {schema['maxItems']} item(s)")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValidationError(f"{path} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValidationError(f"{path} is too long (max {schema['maxLength']} chars)")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValidationError(f"{path} is below the minimum of {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValidationError(f"{path} is above the maximum of {schema['maximum']}")
