"""Lossless, disposable reading pages for already captured Agent inputs."""
from __future__ import annotations

import json

from .agent_aci import READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION

PAGE_BYTES = 12000
INLINE_MEMORY_BYTES = 6 * 1024
_SUFFIXES = ("--verification-context", "--implementation-source", "--verified-engineering-memory")


def _pages(data):
    """Prefer original line boundaries; split long UTF-8 lines without byte loss."""
    start = 0
    while start < len(data):
        end = min(len(data), start + PAGE_BYTES)
        if end < len(data):
            newline = data.rfind(b"\n", start, end)
            if newline >= start:
                end = newline + 1
            else:
                while end > start and data[end] & 0xC0 == 0x80:
                    end -= 1
        yield data[start:end]
        start = end


def input_views(request):
    """Return flat safe filenames and input-index advertisements, without writes.

    Original EmbeddedInput bytes and references remain authoritative. Page names
    depend only on input ordinal and traversal order, never candidate filenames.
    """
    files, advertisements = {}, {}
    if request.schema_version < READABLE_ENGINEERING_INPUTS_ACI_SCHEMA_VERSION:
        return files, advertisements
    for index, item in enumerate(request.input_artifacts):
        if not item.ref.endswith(_SUFFIXES):
            continue
        try:
            value = json.loads(item.content_bytes())
        except (ValueError, UnicodeDecodeError):
            continue
        prefix, inventory = f"{index:04d}", []

        def text_pages(label, text):
            paths, line, continued = [], 1, False
            for part in _pages(text.encode("utf-8")):
                name = f"{prefix}.page-{len(inventory):04d}.txt"
                files[name] = part
                paths.append("/inputs/" + name)
                inventory.append({"field_or_source": label, "path": paths[-1],
                    "bytes": len(part), "first_line": line, "continues_line": continued})
                line += part.count(b"\n")
                continued = not part.endswith(b"\n")
            return {"reading_pages": paths, "bytes": len(text.encode("utf-8"))}

        def readable(obj, label):
            if isinstance(obj, str) and ("\n" in obj or len(obj.encode("utf-8")) > 1024):
                return text_pages(label, obj)
            if isinstance(obj, dict):
                return {k: readable(v, label + "/" + k.replace("~", "~0").replace("/", "~1"))
                        for k, v in obj.items()}
            if isinstance(obj, list):
                return [readable(v, label + "/" + str(i)) for i, v in enumerate(obj)]
            return obj

        def json_pages(label, obj):
            return text_pages(label, json.dumps(readable(obj, label), ensure_ascii=False, indent=2) + "\n")

        overview = {}
        if isinstance(value, dict) and item.ref.endswith("--implementation-source"):
            for path, source in sorted(value.items()):
                overview[path] = text_pages(path, source) if isinstance(source, str) else json_pages(path, source)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"prior_reports", "entries"} and isinstance(child, list):
                    overview[key] = [json_pages(f"/{key}/{n}", entry) for n, entry in enumerate(child)]
                elif key in {"probe_plan", "probe_evidence"} and child is not None:
                    overview[key] = json_pages("/" + key, child)
                else:
                    overview[key] = readable(child, "/" + key)
        else:
            overview = readable(value, "")
        overview_pages = text_pages("overview", json.dumps(overview, ensure_ascii=False, indent=2) + "\n")
        # The inventory is itself paged, so even a many-file submission has a
        # bounded first read. Its entries describe the pages preceding it only.
        inventory_pages = text_pages("page inventory", json.dumps(list(inventory), ensure_ascii=False, indent=2) + "\n")
        intro = {"original_input": f"/inputs/{prefix}.txt", "evidence_ref": item.ref,
            "instructions": "Display only. Start with overview, then named pages. Cite the original evidence ref or its alias. "
                "Pages for one field/source concatenate exactly; first_line is its original line offset. "
                "Full original JSON remains available for machine parsing.",
            "overview": overview_pages, "inventory": inventory_pages}
        name = f"{prefix}.view.txt"
        files[name] = (json.dumps(intro, ensure_ascii=False, indent=2) + "\n").encode()
        advertisements[index] = {"index_path": "/inputs/" + name, "page_bytes_limit": PAGE_BYTES,
            "instruction": "Read index_path first; use its overview and pages instead of searching escaped JSON."}
    return files, advertisements
