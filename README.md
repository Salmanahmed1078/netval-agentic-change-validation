# NetVal: agentic network change validation with a two-tier digital twin

Phase 3 implementation for *Agentic AI for network change validation in CI/CD pipelines using network digital twins*
(Comp Networks, FAST NUCES, Ahmed Tanweer 23i-3019 and Salman Ahmad 23i-6029).

## What is here
| Path | Contents |
|---|---|
| `netval/` | config model/parser, two-tier twin, topologies, change-case generator, oracle, impact analyzer, typed test DSL, agent, baselines, LLM back-ends, runner, statistics |
| `gui/app.py` | NetVal Studio web GUI (`http://127.0.0.1:5050`) |
| `ci/validate_pr.py`, `ci/.github/workflows/netval.yml` | CI/CD gate and GitHub Actions job |
| `tests/` | twin verification tests |
| `data/dataset.json` | 120 labelled change cases (+ base snapshots) |
| `data/results/*.jsonl` | every experiment run with trace, tests and evidence |
| `data/llm_cache/` | cached LLM calls (lets the evaluation replay offline) |
| `figures/`, `results/`, `screenshots/` | report figures, tables (JSON), screenshots |
| `23i-3019_23i-6029_Phase3_Implementation_Evaluation.pdf/.docx` | the report |

## Setup
```bash
python3.12 -m venv .venv && .venv/bin/pip install numpy scipy pandas matplotlib networkx requests flask python-docx statsmodels anthropic python-dotenv pytest pymupdf
ollama pull qwen2.5:7b-instruct            # local model (optional)
cp .env.example .env                        # put ANTHROPIC_API_KEY=... for the hosted model (never commit .env)
```

## Reproduce
```bash
.venv/bin/python -m pytest -q tests                       # twin verification
.venv/bin/python gen_dataset.py 7                         # dataset (oracle-labelled)
./run_hosted.sh && ./run_local.sh                         # experiments (resumable; cached calls replay offline)
.venv/bin/python analyze.py                               # tables + figures
.venv/bin/python report/build.py                          # PDF + DOCX report
.venv/bin/python demo.py D-MTU-1-F clear hosted           # console demo of one case
.venv/bin/python gui/app.py                               # GUI
./ci/demo/run_demo.sh                                     # CI gate on two example PRs (exit 0/1/2)
```
