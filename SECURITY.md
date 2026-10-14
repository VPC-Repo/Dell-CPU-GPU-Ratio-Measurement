# Security

The kit measures the whole server, so it needs broad access to it. Run it on a server set aside for the measurement, not on one serving other workloads.

## What the container can do

The `bench` container is **privileged**. It shares the host's network and process namespaces and mounts the Docker socket. It needs each of these:

| Access | Why |
|---|---|
| Privileged mode | Pin processes to cores, and run each sandboxed job in its own network namespace (`unshare -n`) with resource limits |
| Host network | Reach the sibling containers it starts on 127.0.0.1 |
| Host process namespace | Sample every process on the host for the CPU breakdown |
| Docker socket | Start and remove its own databases (`ratio-pg-f*`) and embedders (`ratio-tei-*`) |

The Docker socket gives the container control of the host's Docker daemon. Treat the kit as having root on the server.

## What the kit does not do

- **The page listens on 127.0.0.1 only**, on port 8766 by default or the configured `UI_PORT`. Reach it through an SSH tunnel. It has no sign-in, so don't expose the port.
- **A run sends nothing off the server.** It downloads nothing either. The model is a local stand-in replaying recorded timings, so no API keys are used.
- **The kit only touches its own containers.** It starts, stops and removes only containers named `ratio-*` and volumes named `ratio_*`.
- **The kit only ends its own processes.** Its processes run as user `bench` (UID 61000), which no host account shares.
- **It checks its ports before it starts.** It refuses to run if another program is listening on one of them.
- **Sandboxed jobs have no network.** They run without network access, as user `bench`, under CPU, memory and file-size limits.

## What the source includes

The `bench/backend` directory contains the agent runtime used by the benchmark, including optional integrations that the measurement does not enable: tracing, cloud model endpoints and encrypted connector storage. The kit configures none of those features or their API keys. Measurement model calls go to the local stand-in.

## Removing the kit

See "To remove everything" in the [README](README.md).

## Reporting a problem

Open an issue in this repository. Leave out server names, addresses and credentials.
