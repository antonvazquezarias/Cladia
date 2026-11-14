# Documentation

## Using Cladia

- [Project overview](../README.md)
- [Installation on Windows](INSTALLATION.md)
- [User guide](USER_GUIDE.md): library, viewers, editing, tools, figures and sharing

## Developing and publishing

- [Contributing and running checks](../CONTRIBUTING.md)
- [Current priorities and limitations](PROJECT_REVIEW.md)
- [Folder interaction audit](FOLDER_AUDIT.md): original findings and implemented fixes
- [Tool and viewer integration guide](FEATURE_GUIDE.md)
- [Optional tools from bioconda](PLAN_BIOCONDA_TOOLS.md): how they are installed, adding one
- [Tool version audit](TOOL_VERSION_AUDIT.md)

## Design and verification records

These documents retain the original designs and delivery evidence. The plans
below are implemented; they are not a list of features waiting to be built.

| Record | Scope |
| --- | --- |
| [Tool boundaries](PLAN_TOOL_BOUNDARIES.md) | Generated dialogs, input/output contracts and host boundaries |
| [Engine ownership](PLAN_ENGINE_OWNERSHIP.md) | Tool-owned scientific workflows and host responsibility split |
| [Genome-map figures](PLAN_GENOME_MAP_FIGURE.md) | Single-genome publication figures |
| [GC tracks](PLAN_GC_TRACKS.md) | GC-content and GC-skew tracks |
| [Long-read mapping](PLAN_LONG_READS.md) | Minimap2 long-read presets and result display |
| [Project portability](PORTABILITY_PLAN.md) | Metadata import and project ZIP implementation |
| [Project risk checks](PROJECT_RISK_TESTS.md) | Fault injection, recovery and resource-limit evidence |

The user guide and the current project review are the starting points for
current behavior and priorities.
