"""
Run all three demos in order. This is what `docker compose run --rm app` does.

Each demo is also a standalone script you can run on its own, e.g.
    docker compose run --rm app python search_demo.py
"""
import importlib
import sys

from common import console

DEMOS = [
    ("kv_and_queries", "Demo 1 of 3 · Key-Value & SQL++"),
    ("search_demo", "Demo 2 of 3 · Full Text Search"),
    ("vector_demo", "Demo 3 of 3 · Semantic (Vector) Search"),
]


def main():
    for module_name, title in DEMOS:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # Lets us run the demos that exist while the others are still being built.
            if exc.name != module_name:
                raise
            console.print(f"[dim]Skipping {title}: {module_name}.py not found[/dim]")
            continue
        module.main()
    console.print("\n[bold green]🎉 All demos finished.[/bold green] "
                  "Open http://localhost:8091 to explore the data in the Query and Search tabs.")


if __name__ == "__main__":
    sys.exit(main())
