"""Start, stop or show an unattended run. Used by /foreman:run."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import declaration
import doctor
import gitutil
import tunnels
from state import State


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start")
    start.add_argument("--plan", required=True, action="append", nargs="+", metavar="FILE UNIT",
                       help="a plan file (relative to the project root) and its units; repeat for a chain")
    start.add_argument("--plain", action="store_true", help="skip the simplify pass")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    root = declaration.find_root(Path.cwd())
    if root is None:
        print(f"no {declaration.NAME} found here or above", file=sys.stderr)
        return 1
    st = State(root)
    if args.cmd == "start":
        plans = [(p[0], p[1:]) for p in args.plan]
        for plan_file, units in plans:
            if not units:
                print(f"no units given for {plan_file}", file=sys.stderr)
                return 1
            if not (root / plan_file).is_file():
                print(f"plan file not found: {plan_file}", file=sys.stderr)
                return 1
        st.start_run(plans, gitutil.head(root), plain=args.plain)
        print("run started: " + doctor.run_line(st.run()).removeprefix("run active: "))
        held = tunnels.warning()
        if held:
            print(held + ". Pass this to every subagent you brief.")
        print("\nThe run contract below applies until the run ends.\n\n" + doctor.contract())
    elif args.cmd == "stop":
        st.end_run("stopped")
        print("run stopped")
    else:
        r = st.run()
        print(doctor.run_line(r) if r else "no run active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
