# How the kit measures

The kit repeats, on one server, the measurement behind the PowerEdge R770 reference results. The R770 results came from three 30-minute series at each load. The kit schedules load steps within a two-hour budget, usually allowing three loads. Startup, draining and analysis can extend elapsed time. On the R770 itself, this analysis reproduces the reference figures from the same records (see "Checks" below).

## The workload

Five agent types arrive on a fixed schedule in the mix set by `calibration.yaml`. The Enterprise mix has six task, one research, one ingestion, two analyst and two code workflows in every twelve arrivals.

- **Planners and workers.** Each workflow runs a planner and its workers through the agent runtime. Model calls go to a local stand-in that answers each call with a recorded reply time and token counts for that call position, recorded against gpt-oss-20b. No GPU or model runs on the server.
- **Real work on the CPU.** Every other step does its real work on the CPU:
  - retrieval with query embedding and reranking on their own cores
  - document OCR and embedding
  - Lua and SQLite builds with tests and static analysis
  - NumPy analysis over 100 million generated payment events
  - parsing of fetched pages

  Each job runs in its own network namespace under resource limits.
- **Instances.** The server runs several agent runtime instances. Each has its own executors, model stand-in and database, pinned to the application cores.

## Core pools

Each socket gives its highest core ids to the services, in proportion to the reference server's 86-core sockets:

| Service | Cores per 86-core socket |
|---|---|
| Reranker | 6, one thread per core |
| Query embedder | 1 |
| Document embedder | 2 |

The rest run the agents and their jobs. The kit runs one instance per 19 or so application cores, as the reference server did with eight instances on 154 cores.

## Load steps and the keeps-up rule

Each step holds one arrival rate for 1,800 seconds. A step passes when two things hold:

- **Completions keep pace with arrivals.** Across the judged cohort, the backlog grows by no more than 5% of the cohort's arrivals, with a minimum allowance of 5 workflows.
- **The load generator delivered its rate.** At least 95% of the offered rate arrived.

Capacity is the highest passing rate. Each step must have an evidence ledger and capacity report from every planned instance. Missing records fail the step. The first step runs at the rate predicted from application cores. Subsequent steps move 8% higher if all tested rates passed, or 8% lower if all failed. Once a passing and failing rate bracket capacity, the next step tests their midpoint.

When every step passes, the result reads "at least" that rate. Holds shorter than 1,800 seconds read capacity low, so the kit doesn't use them. On the reference server, a 1,500-second plateau at the capacity rate failed the rule, while three 1,800-second plateaus at the same rate passed it.

## Each number

| Number | How it is measured |
|---|---|
| Workflows per second | Sum of the instances' achieved arrival rates in the capacity step |
| Busy cores | Per-core samples every 10 s, from 600 s into the step to 100 s before its end, each physical core counted as its busier thread, averaged over time |
| Resident agents | The instances' in-flight counts over the judged span, summed per two-second bucket, median |
| Memory | Whole-host samples over the same window, mean and maximum |
| Output tokens per second | The mix at the achieved rate, multiplied by each agent type's output tokens per workflow. Those are the tokens served in this run plus its model judgments, from the recorded profile. |
| GPUs per server | Output tokens per second divided by `gpu.output_tokens_per_s_per_gpu` |
| CPU breakdown | Described below |
| CPU work per agent type | Each agent's own job records, plus its share of each service by its calls to it, plus the runtime by model calls, converted to physical cores. These are measured in the mix, while the reference costs were measured with each agent alone. |

The CPU breakdown combines three records:

- **Service pools.** The reranker and embedders are measured on their own cores.
- **Runtime and supporting processes.** The agent runtime and supporting processes come from per-process samples (pidstat).
- **Jobs.** The application pool's remaining time is split across job steps in proportion to each job's own CPU record.

## Checks

On the R770, the kit's analysis was run on the raw records of a confirming series at the published capacity rate. It reproduced:

| Figure | Kit | Published |
|---|---|---|
| Workflows per second | 1.84 | 1.84 |
| Output tokens per second | 12,340 | 12,350 |
| GPUs per R770 | 3.07 | 3.07 |
| CPU-to-GPU ratio | 2:3 | 2:3 |
| Servers per XE7740 | 2.6 | 2.6 |
| CPU breakdown | every row within 0.1 point | |

The kit's core planner reproduces the R770's core pools exactly.

## Interpretation limits

This is a demonstration of a fixed workload with simulated model responses. It does not measure GPU performance, model answer quality or production application capacity. GPU counts depend on the serving rate supplied in `calibration.yaml`.

A single series has less evidence than the repeated reference measurements. Capacity precision depends on the rates actually tested; an unbracketed result is a lower bound. The memory warning is triggered by less than 5 GB of installed memory per physical core and does not establish that memory caused a failure.
