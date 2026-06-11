"""Flask UI: compile bankers.c, run with stdin, show output and charts."""
from __future__ import annotations

import datetime
import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

from flask import Flask, redirect, render_template_string, request

try:
    from sklearn.linear_model import LinearRegression
except ImportError:
    LinearRegression = None

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
SRC = BASE_DIR / "bankers.c"
EXE = BASE_DIR / ("bankers.exe" if os.name == "nt" else "bankers")
ELF = BASE_DIR / "bankers"
HISTORY_FILE = BASE_DIR / "history.json"

_P_CAP = 64
_R_CAP = 64
_S_CAP = 8
_Q_CAP = 40


def apply_random_seed(seed_raw: str) -> None:
    t = (seed_raw or "").strip()
    if not t:
        random.seed()
        return
    try:
        random.seed(int(t))
    except ValueError:
        random.seed(t)


def generate_random_stdin(processes: int, resources: int, scenarios: int, requests_per_scenario: int) -> str:
    """Build valid stdin: per resource type, total = Available + sum(Allocation). Max >= Allocation."""
    p = max(1, min(int(processes), _P_CAP))
    r = max(1, min(int(resources), _R_CAP))
    s = max(1, min(int(scenarios), _S_CAP))
    q = max(0, min(int(requests_per_scenario), _Q_CAP))
    lines = [str(s)]

    for _ in range(s):
        alloc = [[0] * r for _ in range(p)]
        maxm = [[0] * r for _ in range(p)]
        available = [0] * r

        for j in range(r):
            total_j = random.randint(max(2, p), max(8, p + r + 2))
            if total_j <= 1:
                available[j] = 0
                alloc[random.randrange(p)][j] = total_j
            else:
                available[j] = random.randrange(0, total_j)
                placed = total_j - available[j]
                for _ in range(placed):
                    alloc[random.randrange(p)][j] += 1

        for i in range(p):
            for j in range(r):
                extra = random.randint(0, min(18, 4 + p + r))
                maxm[i][j] = alloc[i][j] + extra

        need = [[maxm[i][j] - alloc[i][j] for j in range(r)] for i in range(p)]

        lines.append(f"{p} {r}")
        lines.append(" ".join(str(available[j]) for j in range(r)))
        for i in range(p):
            lines.append(" ".join(str(alloc[i][j]) for j in range(r)))
        for i in range(p):
            lines.append(" ".join(str(maxm[i][j]) for j in range(r)))

        lines.append(str(q))
        for _ in range(q):
            pid = random.randrange(p)
            row = []
            for j in range(r):
                nd = need[pid][j]
                if nd <= 0:
                    row.append(0)
                elif random.random() < 0.72:
                    row.append(random.randint(0, nd))
                else:
                    row.append(min(nd, available[j] + random.randint(0, 2)))
            lines.append(" ".join([str(pid)] + [str(x) for x in row]))

    return "\n".join(lines) + "\n"


def _wsl_path(win_path: Path) -> str:
    r = subprocess.run(
        ["wsl", "wslpath", "-a", str(win_path.resolve())],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "wslpath failed").strip())
    return r.stdout.strip()


def _wsl_has_gcc() -> bool:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc", "command -v gcc"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _src_mtime() -> float:
    return SRC.stat().st_mtime if SRC.exists() else 0.0


def _resolve_bankers_argv() -> tuple[list[str], str]:
    """Argv to run bankers; second item is an error string if missing."""
    st = _src_mtime()
    if not st:
        return [], "Missing bankers.c"

    if os.name == "nt":
        if EXE.exists() and EXE.stat().st_mtime >= st:
            return [str(EXE)], ""
        if ELF.exists() and ELF.stat().st_mtime >= st:
            try:
                return ["wsl", "-e", _wsl_path(ELF)], ""
            except (RuntimeError, OSError) as e:
                return [], str(e)
    else:
        if EXE.exists() and EXE.stat().st_mtime >= st:
            return [str(EXE)], ""

    return [], "No current bankers binary. Compile failed or bankers.c is newer."


def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-100000:], f, indent=2)


def compile_bankers() -> tuple[bool, str]:
    if not SRC.exists():
        return False, f"Missing {SRC.name}"
    argv, _ = _resolve_bankers_argv()
    if argv:
        return True, ""

    st = SRC.stat().st_mtime
    cc = shutil.which("gcc")
    if cc:
        cmd = [cc, "-std=c11", "-Wall", "-O2", "-pthread", str(SRC), "-o", str(EXE)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "compile failed").strip()
        return True, ""

    if os.name == "nt" and _wsl_has_gcc():
        try:
            wsrc = _wsl_path(SRC)
            wout = _wsl_path(ELF)
        except (RuntimeError, OSError) as e:
            return False, f"wslpath failed: {e}"
        r = subprocess.run(
            ["wsl", "-e", "gcc", "-std=c11", "-Wall", "-O2", "-pthread", wsrc, "-o", wout],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "wsl gcc failed").strip()
        return True, ""

    return (False, "No gcc in PATH. Install MinGW/MSYS2 gcc, or WSL with gcc.")


def run_bankers(stdin_text: str) -> tuple[int, str, str]:
    ok, err = compile_bankers()
    if not ok:
        return 1, "", err
    argv, err2 = _resolve_bankers_argv()
    if not argv:
        return 1, "", err2
    r = subprocess.run(
        argv,
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=120,
    )
    out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    return r.returncode, out.strip(), ""


# Lines like: "1          5          3          0.000012        1234" (Scenario P R Safety Memory)
_SUMMARY_RE = re.compile(r"^(\d+)\s+(\d+)\s+(\d+)\s+([\d.eE+-]+)\s+(\d+)\s*$")


def parse_summary_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _SUMMARY_RE.match(line.strip())
        if not m:
            continue
        _, p, r, cpu, mem = m.groups()
        rows.append(
            {
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
                "p": int(p),
                "r": int(r),
                "cpu": max(float(cpu), 1e-9),
                "memory": int(mem),
            }
        )
    return rows


def build_run_help(program_output: str) -> dict:
    """Short notes + parsed facts from bankers.c stdout for the web UI."""
    algo = (
        "Safety check (same idea as RAG completion test with Need as remaining claim): "
        "start Work = Available; if some unfinished process has Need <= Work, simulate it finishing and add "
        "its Allocation to Work; repeat. If every process finishes, SAFE. If no process fits and some remain, "
        "UNSAFE — deadlock detected for this snapshot (not all processes can complete)."
    )
    crit = (
        "Critical section: bankers.c uses pthread_mutex_lock/unlock around the report block so output lines are "
        "not interleaved if multiple threads printed. Here analysis runs in one thread; the mutex is the usual "
        "pattern for protecting shared banker tables in concurrent code."
    )
    bullets: list[str] = []
    if not program_output.strip():
        return {"bullets": bullets, "algorithm": algo, "critical": crit}

    if re.search(r"INVALID", program_output):
        bullets.append("Output contains INVALID: fix Allocation, Max, or Available so constraints hold.")

    n_safe = len(re.findall(r"Result: SAFE", program_output))
    n_unsafe = len(re.findall(r"Result: UNSAFE", program_output))
    if n_safe:
        bullets.append(f"SAFE appears {n_safe} time(s): a completion order exists (see trace / safe sequence in Output).")
    if n_unsafe:
        bullets.append(
            f"UNSAFE appears {n_unsafe} time(s): deadlock detected by the completion test — "
            "no way to finish all processes from that state."
        )

    seqs = re.findall(r"Safe sequence:\s*(.+)", program_output)
    if seqs:
        bullets.append(f"Last safe sequence: {seqs[-1].strip()}")

    unf = re.findall(r"Unfinished processes:\s*(.+)", program_output)
    if unf:
        bullets.append(f"Last stuck set: {unf[-1].strip()}")

    cycles = re.findall(r"Wait-for cycle:\s*(YES|NO)", program_output)
    if cycles:
        c = cycles[-1]
        bullets.append(
            "Wait-for cycle: "
            + (
                "YES (cycle in wait-for graph; matches deadlock when each resource type has one instance)."
                if c == "YES"
                else "NO."
            )
        )
    if "Wait-for graph: N/A" in program_output:
        bullets.append("Wait-for graph skipped: only defined when every resource type has exactly one instance.")

    g = len(re.findall(r"GRANTED", program_output))
    d = len(re.findall(r"DENIED", program_output))
    if g or d:
        bullets.append(f"Resource requests in log: {g} granted, {d} denied (unsafe trial grants are rolled back).")

    return {"bullets": bullets, "algorithm": algo, "critical": crit}


def parse_run_insights(text: str) -> dict | None:
    if not text.strip():
        return None
    n_safe = len(re.findall(r"Result: SAFE", text))
    n_unsafe = len(re.findall(r"Result: UNSAFE", text))
    if n_safe and n_unsafe:
        verdict = "MIXED"
    elif n_unsafe:
        verdict = "UNSAFE"
    elif n_safe:
        verdict = "SAFE"
    else:
        verdict = "—"

    last_mem, last_st = None, None
    for m in re.finditer(r"Memory\(bytes\):\s*(\d+)\s*\|\s*Safety time\(s\):\s*([\d.eE+-]+)", text):
        last_mem = int(m.group(1))
        last_st = float(m.group(2))

    wall_m = re.search(r"Wall time\(s\):\s*([\d.eE+-]+)", text)
    wall_s = float(wall_m.group(1)) if wall_m else None

    seqs = re.findall(r"Safe sequence:\s*(.+)", text)
    last_seq = seqs[-1].strip() if seqs else None
    unf = re.findall(r"Unfinished processes:\s*(.+)", text)
    last_unf = unf[-1].strip() if unf else None
    cycles = re.findall(r"Wait-for cycle:\s*(YES|NO)", text)
    wfc = cycles[-1] if cycles else None
    wfn = "Wait-for graph: N/A" in text
    works = re.findall(r"Final Work =\s*(\[[^\n]+\])", text)
    last_work = works[-1].strip() if works else None
    steps = [int(x) for x in re.findall(r"Step\s+(\d+):", text)]
    last_step = max(steps) if steps else None

    parts = []
    if n_unsafe:
        parts.append(
            "At least one scenario is UNSAFE: the completion test cannot finish all processes. "
            "See the safety trace in the output for Work and Need at the stuck step."
        )
    if wfc == "YES":
        parts.append(
            "Wait-for graph reports a cycle (only meaningful when each resource type has one instance). "
            "That matches the classic circular-wait deadlock picture."
        )
    if wfn and n_unsafe == 0 and n_safe:
        parts.append("Wait-for graph was not built (multi-instance resources); rely on the safety trace for deadlock detection.")

    return {
        "verdict": verdict,
        "n_safe": n_safe,
        "n_unsafe": n_unsafe,
        "last_memory": last_mem,
        "last_safety_s": last_st,
        "wall_s": wall_s,
        "last_safe_seq": last_seq,
        "last_unfinished": last_unf,
        "wait_for_cycle": wfc,
        "wait_for_na": wfn,
        "last_work": last_work,
        "last_step": last_step,
        "deadlock_note": " ".join(parts) if parts else "",
    }


def parse_rag_to_mermaid(output_text: str):
    graph = ["%% Resource allocation graph", "graph LR"]
    graph.append("classDef process fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#0f172a;")
    graph.append("classDef resource fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#0f172a;")
    edges = []
    processes = set()
    resources = set()
    for line in output_text.split("\n"):
        m1 = re.search(r"R(\d+)\s+--\((\d+)\)-->\s+P(\d+)", line)
        if m1:
            r_id, weight, p_id = m1.groups()
            edges.append(f"    R{r_id}(R{r_id}) -->|{weight}| P{p_id}[P{p_id}]")
            processes.add(p_id)
            resources.add(r_id)
        m2 = re.search(r"P(\d+)\s+--\((\d+)\)-->\s+R(\d+)", line)
        if m2:
            p_id, weight, r_id = m2.groups()
            edges.append(f"    P{p_id}[P{p_id}] -.->|{weight}| R{r_id}(R{r_id})")
            processes.add(p_id)
            resources.add(r_id)
    if not edges:
        return None
    graph.extend(edges)
    for p in processes:
        graph.append(f"    class P{p} process")
    for r in resources:
        graph.append(f"    class R{r} resource")
    return "\n".join(graph)


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Banker’s algorithm — analysis</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/9.4.3/mermaid.min.js"></script>
    <style>
        :root {
            --bg: #f4f6f9;
            --surface: #ffffff;
            --border: #e2e8f0;
            --text: #1e293b;
            --muted: #64748b;
            --accent: #1e40af;
            --accent-soft: #eff6ff;
            --ok: #15803d;
            --warn: #b45309;
            --bad: #b91c1c;
            --radius: 10px;
            --shadow: 0 1px 3px rgba(15, 23, 42, 0.08), 0 4px 14px rgba(15, 23, 42, 0.06);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.55;
            font-size: 15px;
            position: relative;
        }
        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background:
                radial-gradient(circle at 10% 15%, rgba(30, 64, 175, 0.10), transparent 24%),
                radial-gradient(circle at 90% 5%, rgba(109, 40, 217, 0.08), transparent 22%),
                radial-gradient(circle at 80% 85%, rgba(22, 101, 52, 0.08), transparent 22%);
        }
        .wrap { max-width: 1200px; margin: 0 auto; padding: 1.25rem 1.5rem 3rem; }
        .hero {
            background: linear-gradient(135deg, #1e3a8a 0%, #1e40af 45%, #312e81 100%);
            color: #f8fafc;
            border-radius: var(--radius);
            padding: 1.5rem 1.75rem;
            margin-bottom: 1.25rem;
            box-shadow: var(--shadow);
        }
        .hero h1 { margin: 0 0 0.35rem 0; font-size: 1.5rem; font-weight: 650; letter-spacing: -0.02em; }
        .hero p { margin: 0; opacity: 0.92; font-size: 0.95rem; max-width: 62ch; }
        .hero-meta { margin-top: 0.9rem; display: flex; flex-wrap: wrap; gap: 0.45rem; }
        .chip {
            display: inline-flex;
            align-items: center;
            background: rgba(255, 255, 255, 0.16);
            border: 1px solid rgba(255, 255, 255, 0.28);
            color: #e2e8f0;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 600;
            padding: 0.2rem 0.6rem;
        }
        .split-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.9rem; margin-bottom: 1.25rem; }
        @media (max-width: 980px) { .split-3 { grid-template-columns: 1fr; } }
        .mini-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            box-shadow: var(--shadow);
            padding: 0.8rem 0.95rem;
        }
        .mini-card .title { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700; }
        .mini-card .body { margin-top: 0.25rem; font-size: 0.9rem; color: var(--text); }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; }
        @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 1.25rem 1.35rem;
            margin-bottom: 1.25rem;
            box-shadow: var(--shadow);
        }
        .card h2, .card h3 { margin: 0 0 0.75rem 0; font-size: 1.05rem; color: var(--text); letter-spacing: -0.01em; }
        .card h2 { font-size: 1.12rem; border-bottom: 1px solid var(--border); padding-bottom: 0.6rem; margin-bottom: 1rem; }
        details.doc { margin-bottom: 0.65rem; border: 1px solid var(--border); border-radius: 8px; padding: 0.35rem 0.75rem; background: #fafbfc; }
        details.doc summary { cursor: pointer; font-weight: 600; color: var(--accent); }
        details.doc p, details.doc ul { margin: 0.5rem 0 0.65rem 1rem; color: var(--muted); font-size: 0.92rem; }
        .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.65rem 1rem; margin-bottom: 0.85rem; }
        .toolbar span {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            background: #f8fafc;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 0.24rem 0.45rem;
        }
        .toolbar label { font-weight: 700; color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }
        input[type="number"], input[type="text"], select {
            padding: 0.45rem 0.55rem;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 0.9rem;
            background: #ffffff;
        }
        input[type="number"] { width: 76px; }
        textarea {
            width: 100%;
            min-height: 160px;
            font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
            font-size: 0.82rem;
            padding: 0.85rem;
            border: 1px solid var(--border);
            border-radius: 8px;
            resize: vertical;
            background: #fbfdff;
        }
        button, .btn-link {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 7px;
            font-weight: 600;
            font-size: 0.88rem;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            transition: transform 0.12s ease, filter 0.15s ease, box-shadow 0.15s ease;
        }
        button:hover { filter: brightness(1.05); transform: translateY(-1px); box-shadow: 0 6px 14px rgba(15, 23, 42, 0.12); }
        button:disabled { opacity: 0.6; cursor: not-allowed; transform: none; box-shadow: none; }
        .btn-primary { background: var(--accent); color: #fff; }
        .btn-gen { background: #166534; color: #fff; }
        .btn-predict { background: #6d28d9; color: #fff; }
        .btn-secondary { background: #e2e8f0; color: #334155; }
        .btn-danger { background: #fef2f2; color: var(--bad); border: 1px solid #fecaca; }
        .hint { font-size: 0.82rem; color: var(--muted); margin: 0.5rem 0 0.75rem; }
        pre.out {
            background: #0f172a;
            color: #e2e8f0;
            padding: 1rem 1.1rem;
            border-radius: 8px;
            overflow: auto;
            max-height: 420px;
            font-family: ui-monospace, monospace;
            font-size: 0.8rem;
            white-space: pre-wrap;
            word-break: break-word;
            margin: 0;
        }
        pre.err { background: #7f1d1d; color: #fecaca; }
        table.data { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
        table.data th { text-align: left; border-bottom: 2px solid var(--border); padding: 0.65rem; color: var(--muted); background: #f8fafc; }
        table.data td { border-bottom: 1px solid var(--border); padding: 0.65rem; }
        table.data tbody tr:nth-child(odd) { background: #fcfdff; }
        table.data tbody tr:hover { background: #f1f5f9; }
        .metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 0.75rem; margin-bottom: 1rem; }
        .metric {
            background: var(--accent-soft);
            border: 1px solid #bfdbfe;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }
        .metric .k { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 700; }
        .metric .v { font-size: 1.15rem; font-weight: 700; color: var(--text); margin-top: 0.2rem; }
        .badge {
            display: inline-block;
            padding: 0.2rem 0.55rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.02em;
        }
        .badge-safe { background: #dcfce7; color: var(--ok); }
        .badge-unsafe { background: #fee2e2; color: var(--bad); }
        .badge-mixed { background: #ffedd5; color: var(--warn); }
        .badge-neutral { background: #e2e8f0; color: var(--muted); }
        .callout {
            border-left: 4px solid var(--accent);
            background: #f8fafc;
            padding: 0.75rem 1rem;
            border-radius: 0 8px 8px 0;
            margin-top: 0.75rem;
            font-size: 0.9rem;
            color: var(--text);
        }
        .callout.deadlock { border-color: var(--bad); background: #fef2f2; }
        .deadlock-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 0.75rem; }
        .deadlock-box {
            background: #fff7ed;
            border: 1px solid #fed7aa;
            border-radius: 8px;
            padding: 0.7rem 0.85rem;
        }
        .deadlock-box .k { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.05em; color: #9a3412; font-weight: 700; }
        .deadlock-box .v { font-size: 0.92rem; margin-top: 0.25rem; color: #7c2d12; font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }
        .critical-strip {
            background: #ecfeff;
            border: 1px solid #bae6fd;
            border-radius: 8px;
            padding: 0.8rem 0.95rem;
            margin-top: 0.8rem;
            color: #0f172a;
            font-size: 0.9rem;
        }
        .critical-strip.placeholder { background: #f8fafc; border-color: var(--border); color: var(--muted); }
        .deadlock-panel {
            margin-top: 0.9rem;
            border: 1px solid #fed7aa;
            background: #fff7ed;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }
        .deadlock-panel h3 { margin: 0 0 0.45rem; font-size: 0.95rem; color: #7c2d12; }
        .deadlock-panel p { margin: 0; font-size: 0.88rem; color: #7c2d12; }
        .deadlock-panel.placeholder { border-color: var(--border); background: #f8fafc; }
        .deadlock-panel.placeholder h3, .deadlock-panel.placeholder p { color: var(--muted); }
        .mermaid-wrap { text-align: center; overflow-x: auto; padding: 0.5rem 0; }
        .rag-legend { font-size: 0.85rem; color: var(--muted); margin-bottom: 0.75rem; }
        .empty-charts { color: var(--muted); font-size: 0.9rem; padding: 1rem 0; text-align: center; min-height: 170px; display: flex; align-items: center; justify-content: center; }
        canvas { min-height: 170px; }
        footer { margin-top: 2rem; font-size: 0.8rem; color: var(--muted); text-align: center; }
        .explain .box { background: #f8fafc; border-left: 3px solid var(--accent); padding: 0.65rem 0.9rem; margin-top: 0.6rem; font-size: 0.9rem; }
        .explain ul { margin: 0.4rem 0 0 1.1rem; padding: 0; }
        .explain li { margin: 0.3rem 0; }
    </style>
    <script>mermaid.initialize({ startOnLoad: true, securityLevel: 'loose', theme: 'neutral' });</script>
</head>
<body>
<div class="wrap">
    <header class="hero">
        <h1>Banker’s algorithm — safety, requests, and RAG</h1>
        <p>Configure processes (P) and resource types (R), paste stdin or generate a valid instance, then run the native simulator. The UI summarizes verdicts, timing and memory from the program output, renders the resource allocation graph (RAG), and ties UNSAFE states to deadlock detection.</p>
        <div class="hero-meta">
            <span class="chip">P ≤ 64</span>
            <span class="chip">R ≤ 64</span>
            <span class="chip">Scenarios ≤ 8</span>
            <span class="chip">Requests/scenario ≤ 40</span>
        </div>
    </header>
    <section class="split-3">
        <div class="mini-card">
            <div class="title">Safety flow</div>
            <div class="body">Need ≤ Work → finish process → release Allocation → repeat.</div>
        </div>
        <div class="mini-card">
            <div class="title">Deadlock signal</div>
            <div class="body">If no unfinished process can proceed, completion test reports UNSAFE.</div>
        </div>
        <div class="mini-card">
            <div class="title">Avoidance policy</div>
            <div class="body">Grant request only if trial state stays SAFE; otherwise deny and rollback.</div>
        </div>
    </section>

    <div class="card">
        <h2>Analysis controls</h2>
        <form method="post" action="/">
            <div class="toolbar">
                <span><label for="p">P</label><input id="p" type="number" name="p" value="{{ p }}" min="1" max="64" required title="Processes (max 64)"></span>
                <span><label for="r">R</label><input id="r" type="number" name="r" value="{{ r }}" min="1" max="64" required title="Resource types (max 64)"></span>
                <button type="submit" name="action" value="run" class="btn-primary">Run</button>
                <button type="submit" name="action" value="run_controls" class="btn-secondary">Run from controls</button>
                <span><label for="rs">Scenarios</label><input id="rs" type="number" name="random_s" value="{{ random_s }}" min="1" max="8" style="width:64px;"></span>
                <span><label for="rq">Req / scenario</label><input id="rq" type="number" name="random_q" value="{{ random_q }}" min="0" max="40" style="width:64px;"></span>
                <span><label for="seed">Seed</label><input id="seed" type="text" name="rand_seed" value="{{ rand_seed }}" placeholder="optional" style="width:110px;" title="Repeatable random stdin"></span>
                <button type="submit" name="action" value="random_fill" class="btn-gen">Generate</button>
                <button type="submit" name="action" value="predict" class="btn-predict" {% if not has_sklearn %}disabled title="pip install scikit-learn"{% endif %}>Predict</button>
                <a href="/"><button type="button" class="btn-secondary">Clear</button></a>
                <a href="/reset"><button type="button" class="btn-danger">Reset history</button></a>
            </div>
            <p class="hint">Run uses textarea input when it is non-empty. Use <strong>Run from controls</strong> to ignore textarea and always build fresh random stdin from P, R, Scenarios, and Req/scenario. Per resource j: total instances = Available[j] + sum_i Allocation[i][j]; Max must be at least Allocation.</p>
            <textarea name="stdin" placeholder="stdin (first line S, then per scenario: P R, Available, Allocation rows, Max rows, Q, request lines)…">{{ stdin_text }}</textarea>
        </form>
    </div>

    <div class="card">
        <h2>How it works</h2>
        <details class="doc" open>
            <summary>Implementation (safety sequence)</summary>
            <p>Work starts as Available. While unfinished processes remain, pick any process whose Need is component-wise ≤ Work; simulate it finishing (release its Allocation into Work). If all finish, the state is <strong>SAFE</strong>. If no such process exists while some remain, the state is <strong>UNSAFE</strong> for that snapshot: the completion test fails (deadlock detected in this model).</p>
        </details>
        <details class="doc">
            <summary>Avoidance (resource requests)</summary>
            <p>Each request is tentatively applied if it respects Need and available units. The same safety check runs on the trial state. If the trial is unsafe, the request is <strong>denied</strong> and rolled back; if safe, it is <strong>granted</strong>. That is avoidance: never enter a state that cannot be completed by all processes.</p>
        </details>
        <details class="doc">
            <summary>Deadlock and the RAG</summary>
            <p>The RAG shows assigned units (resource → process, solid) and remaining claims (process → resource, dashed). When the safety algorithm gets stuck, the trace shows which processes still need resources and why Work cannot grow—circular hold is visible in the graph when instances are scarce. If every resource type has exactly one instance, the program also builds a wait-for graph and reports whether a cycle exists.</p>
        </details>
        {% if run_help %}
        <div class="explain" style="margin-top:1rem;">
            <div class="box"><strong>Safety / completion test</strong><br>{{ run_help.algorithm }}</div>
            <div class="box"><strong>Critical section (report)</strong><br>{{ run_help.critical }}</div>
            {% if run_help.bullets %}
            <p style="margin:0.85rem 0 0.25rem;font-weight:600;">Parsed from this run</p>
            <ul>{% for b in run_help.bullets %}<li>{{ b }}</li>{% endfor %}</ul>
            {% endif %}
        </div>
        {% endif %}
    </div>

    {% if err %}
    <div class="card"><pre class="out err">{{ err }}</pre></div>
    {% endif %}

    <div class="card">
        <h2>Run summary</h2>
        {% if insights %}
        <div class="metrics">
            <div class="metric">
                <div class="k">Verdict</div>
                <div class="v">
                    {% if insights.verdict == 'SAFE' %}<span class="badge badge-safe">SAFE</span>
                    {% elif insights.verdict == 'UNSAFE' %}<span class="badge badge-unsafe">UNSAFE</span>
                    {% elif insights.verdict == 'MIXED' %}<span class="badge badge-mixed">MIXED</span>
                    {% else %}<span class="badge badge-neutral">{{ insights.verdict }}</span>{% endif %}
                </div>
            </div>
            <div class="metric"><div class="k">SAFE / UNSAFE counts</div><div class="v">{{ insights.n_safe }} / {{ insights.n_unsafe }}</div></div>
            <div class="metric"><div class="k">Peak memory (last line)</div><div class="v">{% if insights.last_memory is not none %}{{ insights.last_memory }}{% else %}—{% endif %}</div></div>
            <div class="metric"><div class="k">Safety time (last)</div><div class="v">{% if insights.last_safety_s is not none %}{{ "%.6g"|format(insights.last_safety_s) }} s{% else %}—{% endif %}</div></div>
            <div class="metric"><div class="k">Wall time</div><div class="v">{% if insights.wall_s is not none %}{{ "%.6g"|format(insights.wall_s) }} s{% else %}—{% endif %}</div></div>
            <div class="metric"><div class="k">Wait-for cycle</div><div class="v">{% if insights.wait_for_cycle %}{{ insights.wait_for_cycle }}{% elif insights.wait_for_na %}N/A{% else %}—{% endif %}</div></div>
        </div>
        {% if insights.last_safe_seq %}<p style="margin:0;font-size:0.9rem;"><strong>Last safe sequence:</strong> <code style="background:#f1f5f9;padding:0.15rem 0.4rem;border-radius:4px;">{{ insights.last_safe_seq }}</code></p>{% endif %}
        {% if insights.last_unfinished %}<p style="margin:0.5rem 0 0;font-size:0.9rem;"><strong>Last unfinished set:</strong> <code style="background:#f1f5f9;padding:0.15rem 0.4rem;border-radius:4px;">{{ insights.last_unfinished }}</code></p>{% endif %}
        {% if insights.deadlock_note %}
        <div class="callout {% if insights.n_unsafe %}deadlock{% endif %}"><strong>Deadlock / UNSAFE interpretation:</strong> {{ insights.deadlock_note }}</div>
        {% endif %}
        {% if insights.n_unsafe %}
        <div class="deadlock-panel">
            <h3>Deadlock location (exact blocked state from trace)</h3>
            <div class="deadlock-grid">
                <div class="deadlock-box">
                    <div class="k">Blocked after step</div>
                    <div class="v">{% if insights.last_step is not none %}Step {{ insights.last_step }}{% else %}No completion step executed{% endif %}</div>
                </div>
                <div class="deadlock-box">
                    <div class="k">Unfinished processes</div>
                    <div class="v">{% if insights.last_unfinished %}{{ insights.last_unfinished }}{% else %}Not reported{% endif %}</div>
                </div>
                <div class="deadlock-box">
                    <div class="k">Final Work vector</div>
                    <div class="v">{% if insights.last_work %}{{ insights.last_work }}{% else %}Not reported{% endif %}</div>
                </div>
            </div>
        </div>
        {% else %}
        <div class="deadlock-panel placeholder">
            <h3>Deadlock location</h3>
            <p>No UNSAFE state in current run. When deadlock is detected, this section will show blocked step, unfinished processes, and final Work vector.</p>
        </div>
        {% endif %}
        {% if run_help %}
        <div class="critical-strip">
            <strong>Critical section:</strong> {{ run_help.critical }}
        </div>
        {% else %}
        <div class="critical-strip placeholder">
            <strong>Critical section:</strong> bankers.c wraps report generation with <code>pthread_mutex_lock</code>/<code>pthread_mutex_unlock</code> to avoid interleaved output.
        </div>
        {% endif %}
        {% else %}
        <div class="metrics">
            <div class="metric"><div class="k">Verdict</div><div class="v"><span class="badge badge-neutral">No run yet</span></div></div>
            <div class="metric"><div class="k">SAFE / UNSAFE counts</div><div class="v">— / —</div></div>
            <div class="metric"><div class="k">Peak memory (last line)</div><div class="v">—</div></div>
            <div class="metric"><div class="k">Safety time (last)</div><div class="v">—</div></div>
            <div class="metric"><div class="k">Wall time</div><div class="v">—</div></div>
            <div class="metric"><div class="k">Wait-for cycle</div><div class="v">—</div></div>
        </div>
        <div class="deadlock-panel placeholder">
            <h3>Deadlock location</h3>
            <p>Run a scenario to detect and pinpoint deadlock state from the safety trace.</p>
        </div>
        <div class="critical-strip placeholder">
            <strong>Critical section:</strong> bankers.c wraps report generation with <code>pthread_mutex_lock</code>/<code>pthread_mutex_unlock</code> to avoid interleaved output.
        </div>
        {% endif %}
    </div>

    <div class="grid-2">
        <div class="card">
            <h2>Execution terminal</h2>
            {% if output %}
            <pre class="out">{{ output }}</pre>
            {% elif err %}
            <pre class="out err">{{ err }}</pre>
            {% else %}
            <div class="empty-charts">No run output yet. Click <strong>Run</strong> to execute `bankers.c` and stream its terminal output here.</div>
            {% endif %}
        </div>
        <div class="card">
            <h2>Resource allocation graph (Mermaid)</h2>
            {% if mermaid_graph %}
            <p class="rag-legend">Solid arrow: instance assigned to process. Dashed arrow: claim (remaining Max − Allocation along that edge in the simulator output).</p>
            <div class="mermaid-wrap mermaid">{{ mermaid_graph | safe }}</div>
            {% elif output %}
            <p class="rag-legend">Run completed, but no RAG edges were found in this output (e.g. empty allocation and no claim lines in the parsed format).</p>
            {% else %}
            <p class="rag-legend">No graph yet. Run a scenario first; parsed RAG edges will be rendered here automatically.</p>
            {% endif %}
        </div>
    </div>

    <div class="grid-2">
        <div class="card">
            <h2>Safety time (history)</h2>
            {% if history %}<canvas id="cpuChart"></canvas>{% else %}<div class="empty-charts">Run at least once to populate charts from the summary table in the program output.</div>{% endif %}
        </div>
        <div class="card">
            <h2>Memory (history)</h2>
            {% if history %}<canvas id="memChart"></canvas>{% else %}<div class="empty-charts">No history rows yet.</div>{% endif %}
        </div>
    </div>

    <div class="card">
        <h2>History</h2>
        {% if history %}
        <table class="data">
            <thead><tr><th>#</th><th>Time</th><th>P</th><th>R</th><th>Safety (s)</th><th>Memory</th></tr></thead>
            <tbody>
                {% for item in history|reverse %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td>{{ item.timestamp }}</td>
                    <td>{{ item.p }}</td>
                    <td>{{ item.r }}</td>
                    <td>{{ "%.6f"|format(item.cpu) }}</td>
                    <td>{{ item.memory }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}<p class="hint" style="margin:0;">No runs recorded.</p>{% endif %}
    </div>

    <footer>bankers.c via Flask — compile with gcc -pthread (WSL gcc supported on Windows if no native toolchain).</footer>
</div>
<script>
    const history = {{ history | tojson | safe }};
    const labels = history.map((item, index) => 'Run ' + (index + 1) + ' (P=' + item.p + ', R=' + item.r + ')');
    const cpuData = history.map(item => item.cpu);
    const memData = history.map(item => item.memory);
    if (document.getElementById('cpuChart') && history.length) {
        new Chart(document.getElementById('cpuChart'), {
            type: 'bar',
            data: { labels, datasets: [{ label: 'Safety time (s)', data: cpuData, backgroundColor: 'rgba(30,64,175,0.45)', borderColor: 'rgba(30,64,175,1)', borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, title: { display: true, text: 'Seconds' } } } }
        });
    }
    if (document.getElementById('memChart') && history.length) {
        new Chart(document.getElementById('memChart'), {
            type: 'bar',
            data: { labels, datasets: [{ label: 'Memory (bytes)', data: memData, backgroundColor: 'rgba(22,101,52,0.4)', borderColor: 'rgba(22,101,52,1)', borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
    }
    document.addEventListener('DOMContentLoaded', function() { if (window.mermaid) mermaid.contentLoaded(); });
</script>
</body>
</html>
"""


@app.route("/reset")
def reset():
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()
    return redirect("/")


@app.route("/", methods=["GET", "POST"])
def home():
    p = max(1, min(request.values.get("p", default=5, type=int) or 5, _P_CAP))
    r = max(1, min(request.values.get("r", default=3, type=int) or 3, _R_CAP))
    random_s = max(1, min(request.values.get("random_s", default=2, type=int) or 2, _S_CAP))
    random_q = max(0, min(request.values.get("random_q", default=2, type=int) or 2, _Q_CAP))
    rand_seed = (request.values.get("rand_seed") or "").strip()
    stdin_text = request.values.get("stdin", "") or ""
    history = load_history()
    output = ""
    err = ""
    mermaid_graph = None
    run_help = None
    insights = None

    if request.method == "POST":
        action = request.values.get("action", "run")
        if action == "predict":
            if LinearRegression is None:
                err = "scikit-learn not installed (pip install scikit-learn)."
            elif len(history) > 1:
                X = [[h["p"], h["r"]] for h in history]
                Y = [[h["cpu"], h["memory"]] for h in history]
                pred = LinearRegression().fit(X, Y).predict([[p, r]])[0]
                output = f"Predict ({len(history)} rows):\nSafety (s): {pred[0]:.6f}\nMemory: {int(pred[1])}"
            else:
                err = "Need at least 2 history rows for prediction."
        elif action == "random_fill":
            apply_random_seed(rand_seed)
            stdin_text = generate_random_stdin(p, r, random_s, random_q).strip()
        elif action in ("run", "run_controls"):
            stdin_body: str | None = None
            if action == "run_controls":
                apply_random_seed(rand_seed)
                stdin_body = generate_random_stdin(p, r, random_s, random_q)
                stdin_text = stdin_body.strip()
            elif stdin_text.strip():
                stdin_body = stdin_text.strip() + "\n"
            else:
                apply_random_seed(rand_seed)
                stdin_body = generate_random_stdin(p, r, random_s, random_q)
                stdin_text = stdin_body.strip()

            code, out, cerr = run_bankers(stdin_body or "")
            if code != 0:
                err = cerr or out or "bankers exited non-zero"
            else:
                output = out
                run_help = build_run_help(output)
                mermaid_graph = parse_rag_to_mermaid(output)
                new_rows = parse_summary_rows(output)
                for row in new_rows:
                    history.append(row)
                if new_rows:
                    save_history(history)

    if output and not output.lstrip().startswith("Predict"):
        insights = parse_run_insights(output)

    return render_template_string(
        HTML_TEMPLATE,
        p=p,
        r=r,
        random_s=random_s,
        random_q=random_q,
        rand_seed=rand_seed,
        stdin_text=stdin_text,
        output=output,
        err=err,
        history=history,
        mermaid_graph=mermaid_graph,
        run_help=run_help,
        insights=insights,
        has_sklearn=LinearRegression is not None,
    )


if __name__ == "__main__":
    print(f"{BASE_DIR}")
    print("http://127.0.0.1:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
