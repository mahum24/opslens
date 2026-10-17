"""Ask OpsLens from the terminal.

    python -m opslens.assistant.cli "How has MRR changed over the last 6 months?"
    python -m opslens.assistant.cli --role finance --provider ollama "Who owes us money?"
"""
import argparse

from opslens.assistant import sql_guard
from opslens.assistant.assistant import Assistant
from opslens.assistant.llm import get_llm
from opslens.settings import WAREHOUSE_PATH


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions", nargs="+")
    ap.add_argument("--role", default="leadership", choices=list(sql_guard.load_policy()["roles"]))
    ap.add_argument("--provider", default=None, choices=["gemini", "ollama"])
    args = ap.parse_args(argv)

    assistant = Assistant(sql_guard.connect_read_only(WAREHOUSE_PATH), get_llm(args.provider), role=args.role)
    for q in args.questions:
        a = assistant.ask(q)
        print(f"\n{'=' * 80}\nQ ({a.role}): {q}\n")
        print(a.answer)
        print(f"\nConfidence: {a.confidence} · {a.provider} · {a.latency_ms / 1000:.1f}s")
        for c in a.caveats:
            print(f"Caveat: {c}")
        if a.sql:
            print(f"\nSQL ({', '.join(a.tables) or 'not run'}):\n{a.sql}")
        if a.data is not None:
            print(f"\n{a.data.head(10).to_string(index=False)}")
        for i, s in enumerate(a.sources):
            print(f"[D{i + 1}] {s}")
        print("Steps: " + " | ".join(a.steps))


if __name__ == "__main__":
    main()
