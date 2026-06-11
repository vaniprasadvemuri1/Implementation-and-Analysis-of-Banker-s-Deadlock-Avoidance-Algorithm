# Implementation and Analysis of Banker’s Deadlock Avoidance Algorithm

An Operating Systems project that implements **Banker’s Algorithm** in C and wraps it with a small **Flask web UI** so you can run scenarios, see SAFE/UNSAFE results, visualize the Resource Allocation Graph (RAG), and compare safety time and memory across different process/resource sizes.

The core idea is straightforward: before granting resources, the system checks whether the resulting state is still **safe**. If not, the request is denied and rolled back. If the safety test cannot finish all processes, the state is reported as **UNSAFE** (deadlock detected in this model).

---

## What this project does

- Runs the classic **safety algorithm** (Work / Need / Finish)
- Handles **resource requests** with trial allocation → safety check → grant or deny
- Prints a step-by-step **safety trace** in the terminal output
- Builds **RAG** edges and (when each resource has one instance) a **wait-for graph** with cycle detection
- Uses **pthread mutex** around the report block as a **critical section**
- Records **safety time** and **estimated memory** per scenario
- Provides a **web dashboard** with history charts and parsed summaries

---

## Project structure

```
.
├── bankers.c          # Banker's algorithm, requests, RAG, metrics, pthread mutex
├── server.py          # Flask UI: compile/run, parse output, charts, random stdin
├── requirements.txt   # Python dependencies
└── history.json       # Created at runtime (run history for charts)
```

---

## Requirements

**C toolchain**

- `gcc` with pthread support (`gcc -std=c11 -pthread`)
- On Windows: MinGW/MSYS2 gcc, or WSL with gcc if native gcc is not available

**Python**

- Python 3.10+ recommended
- Flask (required)
- scikit-learn (optional — only for the **Predict** button)

Install Python packages:

```bash
pip install -r requirements.txt
```

---

## How to run

1. Open a terminal in this folder.
2. Start the server:

```bash
python server.py
```

3. Open in your browser:

```
http://127.0.0.1:8080
```

The server compiles `bankers.c` automatically on first run (or when the source is newer than the binary).

---

## Using the web UI

| Control | Meaning |
|---------|---------|
| **P** | Number of processes (max 64) |
| **R** | Number of resource types (max 64) |
| **Scenarios** | How many test cases in one run (max 8) |
| **Req / scenario** | Number of resource request lines per scenario (max 40) |
| **Seed** | Optional — same seed + same P,R,Scenarios,Req gives the same random input |
| **Run** | Uses textarea if non-empty; otherwise builds random stdin |
| **Run from controls** | Always builds fresh random stdin from the fields (ignores textarea) |
| **Generate** | Fills textarea with random valid input without running |
| **Reset history** | Clears `history.json` and chart data |

**Panels you’ll see**

- Execution terminal (full `bankers.c` output)
- Run summary (SAFE / UNSAFE / MIXED, time, memory)
- Deadlock location (when UNSAFE)
- Critical section note (pthread mutex)
- RAG graph (Mermaid)
- Safety time and memory history charts

---

## Stdin format (manual input)

```
S
For each scenario:
  P R
  Available[R]
  Allocation[P][R]    (P lines)
  Max[P][R]           (P lines)
  Q
  Q lines: pid r0 r1 ... r(R-1)
```

**Constraints:** `Max >= Allocation`, `Available >= 0`, and total instances per resource type are implied by `Available + sum(Allocation)`.

---

## Quick test examples

**SAFE (2 processes, 1 resource)** — paste into textarea, click **Run**:

```text
1
2 1
0
1
0
1
1
0
0
```

**UNSAFE (2 processes, 1 resource)**:

```text
1
2 1
0
1
0
2
2
0
0
```

**Wait-for cycle (3 processes, 3 single-instance resources)**:

```text
1
3 3
0 0 0
1 0 0
0 1 0
0 0 1
2 2 2
2 2 2
2 2 2
0
```

Expect `Wait-for cycle: YES` in the output.

**Random SAFE without paste:** P=16, R=4, Scenarios=1, Req=0, Seed=**9**, empty textarea → **Run from controls**.

---

## How scenarios work

- **Scenarios** = separate complete test cases in one execution (each with its own matrices and requests).
- **Req/scenario** = how many request lines are generated/tested inside each scenario.
- The terminal shows **all** scenario output; history stores one summary row per scenario.

---

## Notes

- **Wait-for graph** is only built when every resource type has exactly **one** instance. For multi-instance resources, rely on the safety trace.
- **Memory (bytes)** in output is an **estimate** from matrix sizes, not live heap profiling.
- If results look stale after code changes, restart `server.py` and hard-refresh the browser (`Ctrl+F5`).

---

## Course context

Built for an **Operating Systems** module covering deadlock, Banker’s algorithm, safe/unsafe states, resource allocation graphs, and critical sections. The web layer is there to make demos and performance comparison easier — the algorithm itself lives in `bankers.c`.

---
