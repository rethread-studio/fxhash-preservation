#!/usr/bin/env python3
"""Render a random sample of artworks through the art gateway in headless Chrome.

    python3 scripts/art_gateway_render_check.py INDEX.json.gz EXAMPLES.json.gz N OUTDIR \
        [--base https://{label}.fxhash.gakoy.com] [--chrome PATH] [--seed S] [--wait MS]

EXAMPLES: {code hash: a real token artifactUri} (one minted token per generator); a project
without one gets a random hash in its chain's format (Tezos `oo…` base58, EVM `0x…`).
Per artwork: a screenshot, the console messages Chrome logs (CSP refusals, errors), and a
verdict: "drawn" (the screenshot has more than one colour), "blank" or "timeout". Writes
OUTDIR/report.jsonl and prints the counts. Needs Pillow.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import subprocess
from pathlib import Path

from PIL import Image

B58 = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"


def query_for(entry: dict[str, str], examples: dict[str, str], rng: random.Random) -> str:
    h = re.split(r"[/?#]", entry["uri"].split("://", 1)[1])[0]
    if h in examples and "?" in examples[h]:
        return examples[h].split("?", 1)[1]
    if "fxhash-2.0-evm" in entry["repo"]:
        return "fxhash=0x" + "".join(rng.choice("0123456789abcdef") for _ in range(64))
    return "fxhash=oo" + "".join(rng.choice(B58) for _ in range(49))


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("index")
    ap.add_argument("examples")
    ap.add_argument("n", type=int)
    ap.add_argument("outdir")
    ap.add_argument("--base", default="https://{label}.fxhash.gakoy.com")
    ap.add_argument("--chrome", default=str(Path.home() / "bin/google-chrome"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--wait", type=int, default=8000)
    a = ap.parse_args()
    with gzip.open(a.index, "rt") as f:
        index = json.load(f)
    with gzip.open(a.examples, "rt") as f:
        examples = json.load(f)
    rng = random.Random(a.seed)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with open(out / "report.jsonl", "w") as rep:
        for label in rng.sample(sorted(index), a.n):
            e = index[label]
            url = a.base.format(label=label) + "/?" + query_for(e, examples, rng)
            shot = out / f"{label}.png"
            shot.unlink(missing_ok=True)
            try:
                p = subprocess.run(
                    [a.chrome, "--headless=new", "--no-sandbox", "--use-angle=swiftshader",
                     "--enable-unsafe-swiftshader", "--window-size=800,800",
                     f"--timeout={a.wait}", "--enable-logging=stderr", "--v=0",
                     f"--screenshot={shot}", url],
                    capture_output=True, text=True, timeout=a.wait / 1000 + 60, check=False)
                console = [ln.split("] ", 1)[-1][:300] for ln in p.stderr.splitlines()
                           if "CONSOLE" in ln or "Refused" in ln]
            except subprocess.TimeoutExpired:
                console = ["chrome did not exit"]
            if not shot.exists():
                verdict = "timeout"
            else:
                colours = Image.open(shot).convert("RGB").getcolors(maxcolors=2)
                verdict = "blank" if colours is not None and len(colours) <= 1 else "drawn"
            counts[verdict] = counts.get(verdict, 0) + 1
            rep.write(json.dumps({"label": label, "repo": e["repo"], "url": url,
                                  "verified": e["verified"], "verdict": verdict,
                                  "csp_refusals": sum("Refused" in c for c in console),
                                  "console": console[:20]}) + "\n")
            rep.flush()
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
