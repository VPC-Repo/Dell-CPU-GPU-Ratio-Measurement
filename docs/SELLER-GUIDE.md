# Presenting the result

## Before the meeting

1. Run `./install.sh` and `./measure` on the customer's server, or on a lab server of the same configuration. Allow 15 to 35 minutes for installation, then about 2 hours for measurement.
2. Confirm the server was idle before the run. The readiness check on the page records this.
3. Open the page through the tunnel and check that the run finished with the capacity step bracketed. If it reads "at least", the server can do more than the highest load tried.

## The story in three numbers

- **Agents.** "This server ran *N* agents at once, in a realistic enterprise mix."
- **CPU work.** "The agents kept *X*% of its cores busy, almost all of it on the work agents ask for: builds, data analysis, document processing and retrieval."
- **GPUs.** "That load needs *G* GPUs of model serving, so the ratio is *C:G* CPU sockets to GPUs for this model and GPU."

## What you can claim

- **This server's own numbers**, measured on it, under this workload mix and this recorded model profile.
- **The ratio for the GPU serving rate in `calibration.yaml`.** If the customer serves a different model or GPU, edit the `gpu` block and reload the page.
- **A comparison with the published PowerEdge R770 results**, shown as the reference row.

## What not to claim

- **That the numbers hold for another workload.** A mix with more builds or data analysis fills the CPU sooner and supports fewer GPUs per server.
- **That this is a GPU measurement.** No GPU runs here. The GPU side is the serving rate in `calibration.yaml`.
- **That the memory warning proves a bottleneck.** The label is based on installed memory per core. Check job failures, timeouts and measured memory use before attributing a limit to memory.
- **That the per-agent costs equal the reference figures.** The kit measures them inside the mix, and the reference measured each agent type alone.
- **Precision beyond the tested rates.** A midpoint test within an 8% bracket can narrow it to about 4%. Three steps do not always produce a bracket; an "at least" result is a lower bound.

## If the customer asks

**Why are there no GPUs?** The CPUs decide how many agents can run and so how much model demand they create. The GPU serving rate turns that demand into GPUs. Keeping the two apart lets you change the GPU without re-running the server.

**Is this a synthetic benchmark?** The agent steps are real: real builds, real OCR, real analysis and real retrieval. Only the model replies are replayed, from calls recorded against gpt-oss-20b.

**Can we try our own mix?** Yes. Edit the `mix` block in `calibration.yaml` and run `./measure` again.
