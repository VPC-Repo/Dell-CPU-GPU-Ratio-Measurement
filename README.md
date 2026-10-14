# Dell CPU-GPU Ratio Measurement

Measure how many GPUs one server's CPUs can keep busy when they run AI agents.

The kit runs a mix of five agent types on the server until it finds the highest load the server sustains. It then measures the CPU work behind that load and the model demand it creates. From those numbers, a GPU serving rate in `calibration.yaml` gives the CPU-to-GPU ratio for this server. The page shows the result next to the published PowerEdge R770 results.

This is a demonstration benchmark, not a production sizing guarantee. A typical measurement takes about 1 hour 50 minutes after installation. Model responses are simulated from recorded timings; GPU demand is calculated from the configured serving rate. The results stay on the server.

## Requirements

| | Needed | Notes |
|---|---|---|
| Server | Linux x86_64 with Docker Engine and Compose 2.24 or newer | `./install.sh` installs Docker on Ubuntu 22.04, 24.04 or 26.04 |
| Cores | 32 physical cores or more | 64 or more recommended |
| Memory | 5 GB per physical core recommended | Less triggers a memory warning; jobs may fail or time out |
| Disk | 80 GB free in the kit folder | The run's databases grow with each load step |
| Load | An idle server | The run refuses to start while more than 3% of the CPU is busy |
| Network | Internet access to install | The install builds the images here, 15 to 35 minutes. A run downloads nothing |
| GPUs | None | The GPU side comes from `calibration.yaml` |

## Install and run

On the server:

```bash
git clone https://github.com/VPC-Repo/Dell-CPU-GPU-Ratio-Measurement.git
cd Dell-CPU-GPU-Ratio-Measurement
./install.sh
./measure
```

`./install.sh` builds the images on the server, starts the page and checks that the server is ready. Allow another 15 to 35 minutes for the image builds. `./measure` starts the run and follows it. The run continues if your SSH session closes. `./measure follow` picks it up again.

On your laptop:

```bash
ssh -L 8766:127.0.0.1:8766 <server>
```

Then open http://127.0.0.1:8766. Before the first run it shows the readiness check and the run as it happens. After a successful run it opens on the replay with this server's measurements. The run's details stay at http://127.0.0.1:8766/status. If a later run fails, the replay still shows the last successful result; check `/status` for the latest attempt.

### Optional configuration

The defaults work without a `.env` file. To override the page port or image names:

```bash
cp .env.example .env
# Edit .env, then run ./install.sh.
```

When changing `UI_PORT`, use the same port in the SSH tunnel and browser URL. `./install.sh --pull` requires published `BENCH_IMAGE` and `EMBED_IMAGE` names that your server can access; the default local image names are intended for builds on the server.

## What the run does

1. **Checks the server.** It reads cores, memory, free disk and idle CPU, and confirms the privileges it needs.
2. **Plans the cores.** The reranker and embedders get their own cores on each socket, sized as on the reference server. The rest run the agents and their jobs.
3. **Holds load steps of 30 minutes each.** The two-hour scheduling budget usually allows three steps. The first runs at the load predicted from the server's cores. Further steps move 8% up or down until a passing and failing rate bracket capacity, then split that bracket. Service startup, draining and analysis can extend elapsed time beyond the scheduling budget.
4. **Judges each step.** A step passes when completed workflows keep pace with arrivals and the load generator delivered its rate. This is the rule used for the published results.
5. **Analyses the highest step that passed.** It reports capacity, busy cores, resident agents, memory, model demand and the CPU breakdown.

## Reading the results

- **Workflows per second** is the highest arrival rate the server sustained on the agent mix.
- **Resident agents** are the workflows in progress at that load, counted in flight.
- **Busy cores** count each physical core as busy when either of its threads is.
- **Output tokens per second** is the model demand those agents create.
- **GPUs per server** divides that demand by the GPU serving rate in `calibration.yaml`. The CPU-to-GPU ratio is per processor socket.
- **CPU breakdown** shows where the busy CPU time went: sandboxed jobs, retrieval and embedding services, the agent runtime and supporting processes.
- **CPU work per agent type** is measured inside the mix. The reference figures were measured with each agent type running alone, so expect differences.
- **Memory-bound** is a warning based on installed memory per physical core, not a measured diagnosis of the limiting resource.

## Calibration

`calibration.yaml` has three blocks.

- **`gpu`** sets the GPU serving rate and the GPUs per GPU server. Change it and reload the page. No new run is needed.
- **`model`** sets the recorded model calls the run replays. A change needs a new run.
- **`mix`** sets the arrival mix of the five agent types. A change needs a new run.

The page flags a result measured under different `model` or `mix` settings than the file now holds.

## Commands

| Command | What it does |
|---|---|
| `./install.sh` | Build the images, start the page, check the server |
| `./install.sh --pull` | Use prebuilt images named in `.env` instead of building |
| `./measure` | Start a run and follow it |
| `./measure follow` | Follow a run that is already going |
| `./measure status` | Show where the run is |
| `./measure stop` | Stop the run |
| `./measure --force` | Run even when the readiness check fails |
| `docker compose down` | Stop the page |

To remove everything:

```bash
docker compose down
docker ps -aq --filter name=^ratio- | xargs -r docker rm -f
docker volume ls -q --filter name=^ratio_ | xargs -r docker volume rm
```

Then delete the kit folder. Results are in its `data` folder.

## More

- [docs/SELLER-GUIDE.md](docs/SELLER-GUIDE.md): how to present the result and what not to claim
- [docs/METHOD.md](docs/METHOD.md): how each number is measured
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): what to check when something goes wrong
- [SECURITY.md](SECURITY.md): what the kit can do on the server
- [CONTRIBUTING.md](CONTRIBUTING.md): development setup and validation

## License

The source code is MIT licensed. See [LICENSE](LICENSE). Trademarks, logos, product images and bundled fonts are not covered by it. See [NOTICE](NOTICE).
