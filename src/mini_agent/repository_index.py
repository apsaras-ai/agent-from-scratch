"""Chapter 14: safe, read-only Python repository map and search.

This code never imports or executes indexed modules. It skips symlinks, hidden
folders, and oversized/non-UTF8 files. AST resolution is syntactic and cannot
substitute for a semantic Language Server or complete call graph.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Symbol:
    path: str
    qualname: str
    kind: str
    line: int


@dataclass(frozen=True)
class FileInfo:
    path: str
    imports: tuple[str, ...]
    symbols: tuple[Symbol, ...]


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    excerpt: str


class RepositoryIndex:
    def __init__(self, root: str | Path, *, max_bytes: int = 100_000,
                 max_files: int = 500):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Root must be a directory")
        if max_bytes < 1 or max_files < 1:
            raise ValueError("Limits must be positive")
        self.max_bytes, self.max_files = max_bytes, max_files
        self.files: dict[str, FileInfo] = {}
        self._text: dict[str, str] = {}

    def build(self) -> None:
        self.files.clear()
        self._text.clear()
        count = 0
        for path in sorted(self.root.rglob("*.py")):
            rel = path.relative_to(self.root)
            if any(part.startswith(".") or part in {
                "__pycache__", "node_modules", ".venv", "venv",
            } for part in rel.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if count >= self.max_files:
                break
            try:
                if path.stat().st_size > self.max_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=str(rel))
            except (OSError, UnicodeError, SyntaxError):
                continue
            imports: set[str] = set()
            symbols: list[Symbol] = []

            class Visitor(ast.NodeVisitor):
                def __init__(self):
                    self.scope: list[str] = []
                def visit_Import(self, node):
                    imports.update(alias.name for alias in node.names)
                def visit_ImportFrom(self, node):
                    imports.add("." * node.level + (node.module or ""))
                def _definition(self, node, kind):
                    name = ".".join(self.scope + [node.name])
                    symbols.append(Symbol(rel.as_posix(), name, kind, node.lineno))
                    self.scope.append(node.name)
                    for child in node.body:
                        self.visit(child)
                    self.scope.pop()
                def visit_ClassDef(self, node):
                    self._definition(node, "class")
                def visit_FunctionDef(self, node):
                    self._definition(node, "function")
                def visit_AsyncFunctionDef(self, node):
                    self._definition(node, "async function")
            Visitor().visit(tree)
            key = rel.as_posix()
            self.files[key] = FileInfo(key, tuple(sorted(imports)), tuple(symbols))
            self._text[key] = text
            count += 1

    def search(self, literal: str, *, limit: int = 10) -> list[SearchHit]:
        if not literal or limit < 1:
            raise ValueError("Nonempty query and positive limit required")
        hits = []
        for path, text in sorted(self._text.items()):
            for line_no, line in enumerate(text.splitlines(), 1):
                if literal.casefold() in line.casefold():
                    hits.append(SearchHit(path, line_no, line.strip()[:180]))
                    if len(hits) >= limit:
                        return hits
        return hits

    def repository_map(self, *, max_lines: int = 80) -> str:
        if max_lines < 1:
            raise ValueError("max_lines must be positive")
        output = []
        for filename, info in sorted(self.files.items()):
            output.append(filename)
            for sym in info.symbols:
                output.append(f"  {sym.line}: {sym.kind} {sym.qualname}")
            if len(output) >= max_lines:
                break
        return "\n".join(output[:max_lines])
