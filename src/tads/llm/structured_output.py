# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Provider-native structured-output schema derived from the model contract.

Local ``parse_findings`` validation remains the acceptance boundary. This
schema is an upstream reliability aid only, not a security boundary.

OpenAI Chat Completions and Gemini generateContent still return the model JSON
as text (``message.content`` / ``parts[].text``). Duplicate-key detection on
that raw string is unchanged. HTTP ``response.json()`` parses only the provider
envelope, not as a substitute for the model payload.

Capability is endpoint-level for the supported OpenAI Chat Completions host and
the Gemini ``generateContent`` path. The ``model`` argument is reserved for
later refinement and is not used to claim that every model is known capable.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Optional
from urllib.parse import urlsplit

from tads.jsonutil import loads
from tads.schemas.model_output import ModelFindingsResponse

OPENAI_SCHEMA_NAME = "tads_findings"
OFFICIAL_OPENAI_HOST = "api.openai.com"
UNSUPPORTED_FEATURE_BODY_CHARS = 8192
_CLASSIFY_SCALAR_CHARS = 512

OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS = (
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
)

_GEMINI_TYPE_MAP = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "null": "NULL",
}

_GEMINI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "minProperties",
        "maxProperties",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "anyOf",
        "items",
    }
)

_OPENAI_FEATURE_PARAMS = frozenset(
    {
        "response_format",
        "response_format.type",
        "response_format.json_schema",
        "json_schema",
    }
)

_GEMINI_FEATURE_PARAMS = frozenset(
    {
        "responseschema",
        "response_schema",
        "responsemimetype",
        "response_mime_type",
        "generationconfig.responseschema",
        "generationconfig.responsemimetype",
    }
)

_SCHEMA_DEFECT_KEYWORDS = OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS + (
    "additionalProperties",
)

_SCHEMA_KEYWORD_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(keyword) for keyword in _SCHEMA_DEFECT_KEYWORDS)
    + r")\b",
    re.IGNORECASE,
)

_UNKNOWN_NAMED_PARAM = re.compile(
    r"unknown\s+(?:parameter|field|name)\s*:?\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

_GEMINI_UNKNOWN_AT_GENCONFIG = re.compile(
    r"unknown name\s+[\"']?(responseschema|response_schema|responsemimetype|"
    r"response_mime_type)[\"']?\s+at\s+[\"']generation[_]?config[\"'](?!\.)",
    re.IGNORECASE,
)


def model_findings_json_schema() -> dict[str, Any]:
    """JSON Schema for the model-facing findings envelope."""
    return ModelFindingsResponse.model_json_schema()


def openai_response_format() -> dict[str, Any]:
    """Chat Completions ``response_format`` for strict json_schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": OPENAI_SCHEMA_NAME,
            "strict": True,
            "schema": _openai_strict_schema(model_findings_json_schema()),
        },
    }


def gemini_response_schema() -> dict[str, Any]:
    """Gemini ``generationConfig.responseSchema`` (OpenAPI-like, inlined)."""
    return _gemini_schema(model_findings_json_schema())


def official_openai_chat_completions_base(base_url: Optional[str]) -> bool:
    """True only for the official ``api.openai.com`` Chat Completions host."""
    raw = (base_url or "").strip()
    if not raw:
        return True
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except Exception:
        return False
    return host == OFFICIAL_OPENAI_HOST


def classify_native_structured_output_error(
    snippet: str, *, family: str
) -> bool:
    """True only for an unambiguous unsupported/unknown native-feature error.

    ``snippet`` is a bounded body used only for classification. It must never be
    logged, stored, or copied onto an exception. Invalid or ambiguous schema
    errors return False (fail closed).
    """
    if not isinstance(snippet, str) or not snippet:
        return False
    fields = _allowlisted_error_fields(snippet)
    if family == "openai":
        return _openai_feature_unsupported(fields)
    if family == "gemini":
        return _gemini_feature_unsupported(fields)
    return False


def _allowlisted_error_fields(snippet: str) -> dict[str, str]:
    empty = {"code": "", "type": "", "status": "", "param": "", "message": ""}
    try:
        data = loads(snippet)
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    err = data.get("error")
    if isinstance(err, list) and err and isinstance(err[0], dict):
        err = err[0]
    if not isinstance(err, dict):
        return empty
    fields = dict(empty)
    for key in fields:
        value = err.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int)):
            fields[key] = str(value)[:_CLASSIFY_SCALAR_CHARS]
    return fields


def _invalid_supplied_schema(message: str, code: str) -> bool:
    if code == "invalid_json_schema":
        return True
    if "invalid_json_schema" in message:
        return True
    if "invalid schema" in message or "invalid json schema" in message:
        return True
    if "invalid value" in message and (
        "json_schema" in message or "responseschema" in message
        or "response_schema" in message
    ):
        return True
    return False


def _schema_or_supplied_defect(message: str, code: str) -> bool:
    """True when the error is about a supplied schema, not missing feature support."""
    if _invalid_supplied_schema(message, code):
        return True
    if "unsupported keyword" in message or "unknown keyword" in message:
        return True
    if "invalid property" in message and (
        "json_schema" in message
        or "response_schema" in message
        or "responseschema" in message.replace("_", "")
    ):
        return True
    return _SCHEMA_KEYWORD_RE.search(message) is not None


def _named_unknown_field(message: str) -> str:
    match = _UNKNOWN_NAMED_PARAM.search(message)
    if match is None:
        return ""
    return match.group(1).casefold().strip()


def _unrelated_param(param: str, *, feature_params: frozenset[str]) -> bool:
    return bool(param) and param not in feature_params


def _openai_feature_unsupported(fields: dict[str, str]) -> bool:
    code = fields["code"].casefold().strip()
    param = fields["param"].casefold().strip()
    message = fields["message"].casefold()
    if _schema_or_supplied_defect(message, code):
        return False
    if _unrelated_param(param, feature_params=_OPENAI_FEATURE_PARAMS):
        return False
    unknown = "unknown parameter" in message or "unknown field" in message
    unsupported = "does not support" in message or "not supported" in message
    if not unknown and not unsupported:
        return False
    if param in _OPENAI_FEATURE_PARAMS:
        return True
    named = _named_unknown_field(message)
    if named:
        return named in _OPENAI_FEATURE_PARAMS
    if unsupported and (
        "json_schema" in message
        or "structured output" in message
        or "structured outputs" in message
    ):
        return True
    return False


def _gemini_feature_unsupported(fields: dict[str, str]) -> bool:
    message = fields["message"].casefold()
    param = fields["param"].casefold().strip()
    if _schema_or_supplied_defect(message, fields["code"].casefold().strip()):
        return False
    if "generation_config.response_schema" in message:
        return False
    if "generation_config.response_mime" in message:
        return False
    if _unrelated_param(param, feature_params=_GEMINI_FEATURE_PARAMS):
        return False
    if _GEMINI_UNKNOWN_AT_GENCONFIG.search(message):
        return True
    unsupported = "does not support" in message or "not supported" in message
    if unsupported and (
        "responseschema" in message.replace("_", "")
        or "responsemimetype" in message.replace("_", "")
    ):
        return True
    return False


def _openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(schema)
    out.pop("$schema", None)
    defs = out.get("$defs")
    if isinstance(defs, dict):
        for value in defs.values():
            if isinstance(value, dict):
                _strictify_object(value)
    _strictify_object(out)
    return out


def _strictify_object(node: dict[str, Any]) -> None:
    _strip_openai_unsupported_keywords(node)
    node.pop("default", None)
    node.pop("title", None)
    if "$ref" in node:
        return
    for key in ("anyOf", "oneOf", "allOf"):
        variants = node.get(key)
        if isinstance(variants, list):
            for item in variants:
                if isinstance(item, dict):
                    _strictify_object(item)
    items = node.get("items")
    if isinstance(items, dict):
        _strictify_object(items)
    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        _strictify_object(additional)
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return
    node["additionalProperties"] = False
    required = [name for name in node.get("required", []) if name in properties]
    for name, sub in list(properties.items()):
        if not isinstance(sub, dict):
            continue
        _strictify_object(sub)
        if name not in required:
            properties[name] = _nullable_schema(sub)
    node["required"] = list(properties)


def _strip_openai_unsupported_keywords(node: dict[str, Any]) -> None:
    for key in OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS:
        node.pop(key, None)


def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "null":
        return schema
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any(
        isinstance(part, dict) and part.get("type") == "null" for part in any_of
    ):
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
    inlined = _inline_refs(deepcopy(schema), defs)
    inlined.pop("$defs", None)
    inlined.pop("$schema", None)
    inlined.pop("title", None)
    converted = _gemini_types(inlined)
    if not isinstance(converted, dict):
        return {"type": "OBJECT"}
    return converted


def _inline_refs(node: Any, defs: dict[str, Any], *, stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, list):
        return [_inline_refs(item, defs, stack=stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.rsplit("/", 1)[-1]
        if name in stack:
            return {"type": "object"}
        target = defs.get(name)
        if isinstance(target, dict):
            return _inline_refs(deepcopy(target), defs, stack=stack + (name,))
        return node
    return {
        key: _inline_refs(value, defs, stack=stack) for key, value in node.items()
    }


def _gemini_types(node: Any) -> Any:
    if isinstance(node, list):
        return [_gemini_types(item) for item in node]
    if not isinstance(node, dict):
        return node
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            part
            for part in any_of
            if not (isinstance(part, dict) and part.get("type") == "null")
        ]
        has_null = len(non_null) != len(any_of)
        if has_null and len(non_null) == 1 and isinstance(non_null[0], dict):
            converted = _gemini_types(non_null[0])
            if isinstance(converted, dict):
                converted = dict(converted)
                converted["nullable"] = True
            return converted
        out_any: dict[str, Any] = {"anyOf": _gemini_types(any_of)}
        return out_any
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = _GEMINI_TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out[key] = {
                name: _gemini_types(sub) for name, sub in value.items()
            }
        else:
            out[key] = _gemini_types(value)
    return out
