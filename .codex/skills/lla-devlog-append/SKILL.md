---
name: lla-devlog-append
description: Append a structured, public-safe entry to devlogs/YYYY-MM-DD.md at the repository root after code, docs, test or configuration changes. Append-only.
---

# local-llm-admin devlog append

## When to use
At the end of a work session, or after any meaningful change.

## Workflow
1. Resolve today's date and the file `devlogs/<YYYY-MM-DD>.md` at the repository root.
2. If the file exists, append a new `## <title>` section; never rewrite or delete earlier sections. Otherwise create it with a `# <YYYY-MM-DD>` heading.
3. Use the sections: Problem/Goal, Solution/Implementation, Results (with test counts and coverage), Files Modified, then Lessons Learned and Next Steps.
4. Check the entry is public-safe: no real IP addresses, usernames, hostnames, home paths, key aliases or tokens (placeholders: `mac-mini.local`, `192.0.2.x`, `user-a`). Run `scripts/check_public.py` with the private pattern list if one is configured.
5. Stage it with `git add`; never commit unless the user explicitly asks.
