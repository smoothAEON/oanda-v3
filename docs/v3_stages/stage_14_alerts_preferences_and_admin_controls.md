# Stage 14 - Historical Security And Admin Backlog

> Historical Stage 14 is now the deferred security and admin backlog only. The non-security runtime work that used to be discussed here now lives in Stage 16.

- Tracker: [tracker.md](../tracker.md)
- Active runtime feature stage: [Stage 16](./stage_16_operator_and_trader_workflow_completion.md)

## Purpose

Keep the unfinished security, operator-control, and auditability scope isolated from the core runtime release path.

## Scope

Stage 14 now owns only the deferred admin and security surface:

- `/security`
- `/sessions`
- `/ban`
- `/unban`
- `/mute`
- `/unmute`
- `/override`
- failed-auth backoff, rate limiting, and audit logging for sensitive actions

## Explicitly Out Of Scope For Stage 14

These items are no longer tracked here:

- price-alert CRUD and evaluation
- indicator-alert CRUD and evaluation
- time alerts and session reminders
- trade-open and trade-close Telegram pushes
- `/dayrange`, `/pdh`, `/pdl`, `/fib`, `/tradeplan`
- `/price --live`
- chart modes and the Stage 16 runtime-config keys

Those items belong to [Stage 16](./stage_16_operator_and_trader_workflow_completion.md).

## Dependencies

- Stage 10 TinyDB persistence
- Stage 13 Telegram runtime and auth layer
- Stage 16 notifier and runtime-config plumbing, when mute and override behavior is eventually added

## Implementation Checklist

- [ ] `S14-F01` add authenticated admin command handlers for `/security`, `/sessions`, `/ban`, and `/unban`
- [ ] `S14-F02` add background push suppression controls for `/mute`, `/unmute`, and `/override` without affecting normal command replies
- [ ] `S14-F03` persist and expose security telemetry: failed-auth counts, cooldowns, active sessions, bans, and audit events

## Tests And Approval Evidence

- admin-command tests must prove only authorized users can invoke the Stage 14 surface
- security tests must prove failed-auth backoff, chat scoping, and audit persistence
- mute tests must prove background pushes can be suppressed without breaking normal command replies

## Exit Criteria

- the admin and security controls are implemented, authenticated, bounded, and auditable
- the Stage 14 surface layers on top of the shipped Stage 16 notifier path without changing the Stage 16 storage model

## Release Note

Stage 14 is explicitly non-blocking for the Stage 17 core release gate.
