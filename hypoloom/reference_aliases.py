"""Request-local display names; durable responses keep their original references.

The existing request and output schema alone define the mapping. No mapping is
stored or guessed, and aliases never authorize a reference outside that schema.
"""
from __future__ import annotations

from copy import deepcopy


def reference_aliases(request, schema: dict) -> dict[str, str]:
    refs = list(request.immutable_context_refs)

    def collect(node):
        if isinstance(node, dict):
            supporting = node.get("properties", {}).get("supporting_refs", {})
            refs.extend(supporting.get("items", {}).get("enum", ()))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(schema)
    return {f"R{index}": ref for index, ref in enumerate(dict.fromkeys(refs), 1)}


def aliased_schema(schema: dict, aliases: dict[str, str]) -> dict:
    result = deepcopy(schema)
    inverse = {ref: alias for alias, ref in aliases.items()}

    def visit(node):
        if isinstance(node, dict):
            supporting = node.get("properties", {}).get("supporting_refs")
            if isinstance(supporting, dict) and "items" in supporting:
                items = supporting["items"]
                allowed = items.get("enum", list(inverse))
                supporting["items"] = {**items, "enum": [inverse[ref] for ref in allowed]}
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def canonical_output(output: dict, aliases: dict[str, str]) -> dict:
    result = deepcopy(output)

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "supporting_refs":
                    if not isinstance(value, list) or any(not isinstance(ref, str) or ref not in aliases for ref in value):
                        raise ValueError("supporting_refs must use this request's exact reference aliases")
                    node[key] = [aliases[ref] for ref in value]
                else:
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result.get("typed_candidate", {}))
    return result
