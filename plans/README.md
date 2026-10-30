# Plans

| Location | Use |
|---|---|
| [active](active/README.md) | Work being implemented now |
| [backlog](backlog/README.md) | Proposed or deferred work with an explicit next decision |
| [archive](archive/README.md) | Completed implementations and closed investigation records |

The [admin website consistency implementation](archive/platform/20261030_admin-web-consistency-and-usability.md)
records all 28 cross-backend UI findings, delivered changes and local validation.

New plans use `active/YYYYMMDD_<name>.md`. Keep one active plan for a coherent change;
update its progress instead of creating another file for every checkpoint. Move it into
`archive/<component>/` when the change is complete. Put a concise follow-up in backlog
when a proposal needs a future decision rather than leaving old work apparently active.

An archived investigation can have a failed or inconclusive outcome. Its original task
progress and evidence remain historical; archiving does not turn missing qualification
into a pass. Use the current [design](../docs/DESIGN.md), [operating guides](../ops/README.md)
and [report index](../docs/reports/splash/INDEX.md) before consulting historical plans.
All plans are public-safe; deployment details remain outside git (see `AGENTS.md`).
