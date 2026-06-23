# Documentation

Guides for operating and developing hdd-forensics. New here? Start with the
[project README](../README.md), then come back for the deep dives.

## Operator
Day-to-day imaging and acquisition.
- [Acquisition checklist](operator/acquisition-checklist.md)
- [ddrescue workflow](operator/ddrescue-workflow.md)
- [Next-disk checklist](operator/future-disk-checklist.md)

## Analysis
Running the recovery pipeline over an image.
- [Image-analysis workflow](analysis/image-analysis-workflow.md) — the core fast→heavy path
- [Bulk-discovery runbook](analysis/bulk-discovery-runbook.md) — the heavy deleted/free-space stages

## Recovery goals
- [Wallets](recovery/wallets.md) — Bitcoin/crypto wallet artifacts
- [Pictures](recovery/pictures.md) — photo recovery

## Reference
- [Tool selection](reference/tool-selection.md) — which tool for which job

## Internal
Design notes and project history (not needed to operate the tool).
- [`internal/`](internal/) — decisions, roadmap, progress logs

## Contributing
See [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md).
Design specs for in-flight work live under [`superpowers/specs/`](superpowers/specs/).
