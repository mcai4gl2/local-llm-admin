Run tests for a file inside the dev container.

## Usage
`/test [file]` — e.g. `/test src/local_llm_admin/tts.py`, `/test tests/test_tts.py`, or `/test` for the whole suite

## Steps

1. Find the running dev container ID:
```bash
docker ps --format '{{.ID}}\t{{.Image}}\t{{.Names}}' | grep -m1 local-llm-admin
```
If nothing matches, ask the user to open the repository in its dev container.

2. Pick the test files:
   - a test file (`tests/test_*.py`): use it;
   - a source file `src/local_llm_admin/<name>.py`: use every `tests/test_<name>*.py` (for example `tts.py` → `tests/test_tts.py`, `tests/test_tts_api.py`, …); if none exist, say so and run the whole suite;
   - a host or ops script: use the `tests/test_host_*.py` file that runs it (`grep -l <script name> tests/`);
   - no argument: the whole suite.

3. Run them with coverage:
```bash
docker exec -u vscode -w /workspaces/local-llm-admin <container-id> uv run pytest <files> --cov --cov-report=term-missing
```

4. Report the results, noting failures, errors and uncovered lines in the file under test.

**Always use `-u vscode`** to prevent file permission issues.
