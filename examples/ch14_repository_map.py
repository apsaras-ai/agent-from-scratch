"""Read-only repository inspection demo. Run from repository root."""
from pathlib import Path
from mini_agent.repository_index import RepositoryIndex

def main():
 root=Path(__file__).resolve().parents[1] / "src" / "mini_agent"
 index=RepositoryIndex(root)
 index.build()
 print("Indexed files:",len(index.files))
 print("Repository map:")
 print(index.repository_map(max_lines=14))
 print("ToolRegistry hits:",len(index.search("ToolRegistry")))
 print("AST parsing only; no source module is imported or executed.")

if __name__=="__main__":
 main()
