Create or update the devlog entry for today's work session.

## Usage
`/devlog` — creates or appends to `devlogs/YYYY-MM-DD.md`

## Steps

1. Use today's date from the environment.

2. If `devlogs/<YYYY-MM-DD>.md` exists, read it and append a new section; never rewrite earlier sections. Otherwise create it.

3. Fill the template from the session (ask the user if the changes are unclear):

```markdown
# <YYYY-MM-DD>

## <Feature/Fix/Optimization Name>

### Problem/Goal
-

### Solution/Implementation
-

### Results
-

### Files Modified
-

## Lessons Learned

## Next Steps
```

4. **This repository is public.** Write no real IP addresses, usernames, hostnames, home paths, key aliases or tokens; use the placeholders from `AGENTS.md` (`mac-mini.local`, `192.0.2.x`, `user-a`). Run `scripts/check_public.py` with your private pattern list before staging the devlog.

5. Confirm the path of the devlog that was written.
