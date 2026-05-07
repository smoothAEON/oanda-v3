# Stage 17 - Test, CI, Deployment, And Core Release Readiness

> Active release-readiness stage. This is the first stage allowed to claim a core release.

- Tracker: [tracker.md](../tracker.md)

## Purpose

Turn the shipped core runtime into a releasable system with CI, deployment, backup, restore, runbooks, and tracker-backed release evidence.

## Scope

- broad unit and integration inventory ownership
- CI definition and repository workflows
- deployment topology and startup order
- health checks, structured log capture, and failure alerting
- backup and restore procedures for TinyDB and cache artifacts
- incident runbooks for stale-state, scheduler, stream, and persistence failures
- release checklist and evidence trail

## Dependencies

- Stages 01-16
- Stage 14 is non-blocking
- Stage 18 is non-blocking

## Implementation Checklist

- [ ] `S17-F01` define and automate the CI test inventory that aggregates the shipped Stage 16 feature-local suites
- [ ] `S17-F02` define deployment and startup order for the runtime services
- [ ] `S17-F03` define backup, restore, and incident runbooks
- [ ] `S17-F04` define the release checklist and tracker-backed release evidence rule

## Exit Criteria

- CI, deployment, backup, restore, and runbooks are documented and automated to the agreed bar
- the tracker can be used as real release evidence
- the core runtime can be released without depending on Stage 14 or Stage 18
