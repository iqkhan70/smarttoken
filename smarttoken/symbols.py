"""Symbol extraction with line ranges. Tree-sitter when available, regex heuristic otherwise."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from smarttoken.rank import content_terms, term_in


@dataclass
class SymbolHit:
    name: str
    kind: str
    line: int
    end_line: int

    @property
    def span(self) -> int:
        return self.end_line - self.line + 1


# node type -> kind, per tree-sitter grammar
_NODE_KINDS: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "csharp": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "record_declaration": "record",
        "enum_declaration": "enum",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "typescript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "method_definition": "method",
        "variable_declarator": "function",
    },
}
_NODE_KINDS["javascript"] = _NODE_KINDS["typescript"]
_NODE_KINDS["tsx"] = _NODE_KINDS["typescript"]

_FUNCTION_VALUES = {"arrow_function", "function_expression", "function"}


def _grammar(lang: str, rel_path: str | None) -> str | None:
    if lang == "typescript" and rel_path and rel_path.lower().endswith(".tsx"):
        return "tsx"
    if lang == "javascript" and rel_path and rel_path.lower().endswith(".jsx"):
        return "tsx"
    return lang if lang in _NODE_KINDS else None


@lru_cache(maxsize=None)
def _parser(grammar: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(grammar)
    except Exception:
        return None


def _node_name(node) -> str | None:
    name = node.child_by_field_name("name")
    if name is None:
        return None
    return name.text.decode("utf-8", errors="replace")


def _tree_sitter_symbols(content: str, grammar: str) -> list[SymbolHit] | None:
    parser = _parser(grammar)
    if parser is None:
        return None
    try:
        tree = parser.parse(content.encode("utf-8"))
    except Exception:
        return None
    kinds = _NODE_KINDS[grammar]
    hits: list[SymbolHit] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind:
            if node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                if value is None or value.type not in _FUNCTION_VALUES:
                    kind = None
            name = _node_name(node) if kind else None
            if name:
                hits.append(SymbolHit(name, kind, node.start_point[0] + 1, node.end_point[0] + 1))
        stack.extend(reversed(node.children))
    hits.sort(key=lambda hit: (hit.line, -hit.end_line))
    return hits


_PY_DEF = re.compile(r"^(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(")
_PY_CLASS = re.compile(r"^class\s+([A-Za-z_][\w]*)\s*[\(:]")
_FN_GENERIC = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w]*)\s*\("
)
_CLASS_GENERIC = re.compile(r"^(?:export\s+)?class\s+([A-Za-z_][\w]*)\b")


def _regex_symbols(content: str, lang: str) -> list[SymbolHit]:
    """Start lines only; end_line equals line so callers never treat these as ranges."""
    hits: list[SymbolHit] = []
    for idx, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        patterns = (_PY_DEF, _PY_CLASS) if lang == "python" else (_FN_GENERIC, _CLASS_GENERIC)
        for pattern, kind in zip(patterns, ("function", "class")):
            if match := pattern.match(line):
                hits.append(SymbolHit(match.group(1), kind, idx, idx))
                break
    return hits


def extract_symbols(content: str, lang: str, rel_path: str | None = None) -> list[SymbolHit]:
    grammar = _grammar(lang, rel_path)
    if grammar:
        hits = _tree_sitter_symbols(content, grammar)
        if hits is not None:
            return hits
    return _regex_symbols(content, lang)


def symbols_matching(
    content: str, lang: str, query: str, rel_path: str | None = None
) -> list[SymbolHit]:
    """Symbols named after task words; a one-line declaration is dropped when its body is in the same file."""
    terms = content_terms(query)
    if not terms:
        return []
    named = [
        hit
        for hit in extract_symbols(content, lang, rel_path)
        if any(term_in(term, hit.name.lower()) for term in terms)
    ]
    with_body = {hit.name for hit in named if hit.span > 1}
    return [hit for hit in named if hit.span > 1 or hit.name not in with_body]
