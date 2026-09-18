"""A small JSON Schema checker covering only the keywords task schemas may use.
Anything else fails when the task is loaded, so the limit is visible."""
import re

SUPPORTED_KEYWORDS = frozenset({
    "$schema", "$comment", "title", "description", "type", "required", "properties",
    "additionalProperties", "items", "enum", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "pattern"})
_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")


def unsupported_keywords(schema, path="$"):
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    problems = []
    for key, value in schema.items():
        if key not in SUPPORTED_KEYWORDS:
            problems.append(f"{path}: keyword '{key}' is not supported")
        elif key == "additionalProperties" and not isinstance(value, bool):
            problems.append(f"{path}: additionalProperties must be true or false")
        elif key == "type":
            for name in (value if isinstance(value, list) else [value]):
                if name not in _TYPES:
                    problems.append(f"{path}: unknown type '{name}'")
        elif key == "properties" and not isinstance(value, dict):
            problems.append(f"{path}: properties must be an object")
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            problems.extend(unsupported_keywords(sub, f"{path}.{name}"))
    if "items" in schema:
        if isinstance(schema["items"], dict):
            problems.extend(unsupported_keywords(schema["items"], f"{path}[]"))
        else:
            problems.append(f"{path}: items must be a single schema object")
    return problems


def _type_name(value):
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", int: "integer",
            float: "number"}.get(type(value), type(value).__name__)


def _type_ok(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if isinstance(value, bool):
        return False
    if name == "integer":
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float))
    return False


def validate(instance, schema, path="$"):
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(instance, t) for t in types):
            return [(path, f"expected {' or '.join(types)}, got {_type_name(instance)}")]
    errors = []
    if "enum" in schema and instance not in schema["enum"]:
        errors.append((path, f"must be one of {schema['enum']}"))
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append((path, f"must be at least {schema['minLength']} characters"))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append((path, f"must be at most {schema['maxLength']} characters"))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append((path, f"does not match the pattern {schema['pattern']}"))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append((path, f"must be at least {schema['minimum']}"))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append((path, f"must be at most {schema['maximum']}"))
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append((path, f"needs at least {schema['minItems']} items"))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append((path, f"allows at most {schema['maxItems']} items"))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append((f"{path}.{name}", "is required"))
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(validate(value, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append((f"{path}.{name}", "is not allowed"))
    return errors
