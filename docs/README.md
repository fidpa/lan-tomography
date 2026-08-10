# Documentation

## I want to…

| … | Read |
|---|---|
| understand what this is for before installing anything | [The method](explanation/target-matrix.md) |
| set up a first measurement | [Getting started](tutorial/getting-started.md) |
| add a second probe | [Deploy a probe node](how-to/deploy-a-probe-node.md) |
| feed in my own data | [Log formats](reference/log-formats.md) |
| know how these measurements mislead | **[Pitfalls](explanation/pitfalls.md)** |
| prove a layer-2 loop | [Proving a loop](explanation/proving-a-loop.md) |
| configure something | [Configuration](reference/configuration.md) |
| add a probe's data to the analysis | [Deploy a probe node](how-to/deploy-a-probe-node.md), then `src/ops/sync-node.sh` |
| take the measurement down again | [Tear down a campaign](how-to/tear-down.md) |
| check a Windows event log without fetching it | `src/contrib/evtx-peek.py --help`, and [pitfalls G](explanation/pitfalls.md#g-windows-event-logs) |
| see it applied to a real fault, corrections and all | [Case study](explanation/case-study.md) |

## Recommended reading order

If you are here because something is intermittently broken and the usual tools
have not narrowed it down:

1. **[The method](explanation/target-matrix.md).** How to choose probe
   locations and target roles so the sections you care about become
   individually determinable. Read this before installing anything; the
   installation is the easy part and the matrix is where the thinking is.
2. **[Pitfalls](explanation/pitfalls.md).** The core of this repository.
   Around forty ways these measurements produce confident wrong answers.
   Skimming it now saves more time than reading it later.
3. **[Getting started](tutorial/getting-started.md).** A first measurement on
   one probe.
4. **[Log formats](reference/log-formats.md).** Needed as soon as you want to
   check a result by hand, which you should.

## The shape of this documentation

Following [Diátaxis](https://diataxis.fr/):

- **tutorial/**: learning by doing, start to finish, one path
- **how-to/**: a specific task, assuming you know why
- **reference/**: formats, options, defaults; look things up
- **explanation/**: why the tools are shaped this way, and what goes wrong

The explanation section is unusually large for a toolkit this size. That is
deliberate: the code here is not hard, and reproducing it is not the obstacle.
Knowing which measurement to distrust is.
