# Troubleshooting

**The page doesn't open.**
- Check the tunnel: `ssh -L 8766:127.0.0.1:8766 <server>` must stay open while you use the page.
- On the server, `docker compose ps` should show `bench` running. If it doesn't, start it with `docker compose up -d bench`.
- If `UI_PORT` is set in `.env`, use that port in both the tunnel and browser URL.

**The readiness check fails on "Idle server".**
- Something else is using the CPU. The check lists the busiest containers.
- Stop them, or run `./measure --force` and treat the result as a lower bound.

**The readiness check fails on "Ports".**
- Another program listens on a port the run needs, and the check names it.
- Stop that program, or measure on another server.

**The readiness check fails on "Job isolation" or "CPU pinning".**
- The container isn't privileged. Use the `compose.yaml` from this repository unchanged.

**"Memory-bound" appears on the result.**
- The server has less than 5 GB of memory per physical core.
- The label is a readiness warning, not a measured cause of failure. Analyst jobs can need up to 40 GB each; inspect the run's memory use and job errors.

**Every step kept up.**
- The server is stronger than the prediction from its core count, and the result reads "at least".
- Repeating the default run starts at the same prediction and may give the same outcome. If time allows, use `./measure --steps 4` to test an additional rate. An explicit step count overrides the scheduling budget and extends the run.

**No step kept up.**
- The server fell behind even at the lowest load tried.
- Check the readiness report for a busy server or low memory.
- Make sure nothing else started during the run.

**The run stopped.**
- `./measure status` shows the last state, and `data/capacity/measure-console.log` shows the error.
- Each run's logs are in `data/capacity/runs/<time>/measure.log`.
- An "incomplete records" error means at least one instance did not produce its evidence ledger or capacity report. Check the step's `rate-*.log` and fleet logs before retrying.

**The replay shows an older result.**
- The replay keeps the last successful measurement while a new run is in progress or has failed.
- Open `/status` or use `./measure status` to inspect the latest attempt.

**Start again from scratch.**

```bash
./measure stop
docker ps -aq --filter name=^ratio- | xargs -r docker rm -f
docker volume ls -q --filter name=^ratio_ | xargs -r docker volume rm
./measure
```
