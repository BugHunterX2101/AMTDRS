"""Grammar registry. One entry per language, one query file per entry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tree_sitter as ts

HERE = Path(__file__).parent

PY_SUFFIXES = {".py"}
TS_SUFFIXES = {".ts", ".tsx"}


@dataclass(frozen=True, slots=True)
class Grammar:
    name: str
    language: ts.Language
    query: ts.Query
    suffixes: frozenset[str]


def _python() -> ts.Language:
    import tree_sitter_python

    return ts.Language(tree_sitter_python.language())


def _typescript() -> ts.Language:
    import tree_sitter_typescript

    return ts.Language(tree_sitter_typescript.language_typescript())


def _tsx() -> ts.Language:
    import tree_sitter_typescript

    return ts.Language(tree_sitter_typescript.language_tsx())


@cache
def grammar(lang: str) -> Grammar:
    if lang == "python":
        language = _python()
        source = (HERE / "python.scm").read_text(encoding="utf-8")
        suffixes = frozenset(PY_SUFFIXES)
    elif lang == "typescript":
        language = _typescript()
        source = (HERE / "typescript.scm").read_text(encoding="utf-8")
        suffixes = frozenset(TS_SUFFIXES)
    elif lang == "tsx":
        language = _tsx()
        source = (HERE / "typescript.scm").read_text(encoding="utf-8")
        suffixes = frozenset({".tsx"})
    else:
        raise ValueError(f"no grammar for {lang}")
    return Grammar(name=lang, language=language, query=ts.Query(language, source), suffixes=suffixes)


def lang_of(path: Path | str) -> str | None:
    suffix = Path(path).suffix
    if suffix in PY_SUFFIXES:
        return "python"
    if suffix in TS_SUFFIXES:
        return "typescript"
    return None


def parser_for(lang: str, path: Path | str | None = None) -> ts.Parser:
    if lang == "typescript" and path is not None and Path(path).suffix == ".tsx":
        return ts.Parser(grammar("tsx").language)
    return ts.Parser(grammar(lang).language)


def query_for(lang: str, path: Path | str | None = None) -> ts.Query:
    if lang == "typescript" and path is not None and Path(path).suffix == ".tsx":
        return grammar("tsx").query
    return grammar(lang).query


SUPPORTED_SUFFIXES = PY_SUFFIXES | TS_SUFFIXES
