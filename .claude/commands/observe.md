---
description: Prepare the corpus, observe packs, price opportunities, draft artifacts, and serve the local dashboard.
---

Run this sequence in the workspace. Do not read raw mail, pack JSON, or insight JSON. Do not read API keys. Do not use the network. Subagents return JSON. The parent writes that JSON to disk.

1. Run `python -m main prepare --corpus corpus --db output/observe.sqlite --output output`. Stop if the status is not 0. A non-zero status means the file count does not match or the projected run, including one reserved retry wave, is over 25 minutes.
2. Run `python -m main jobs status --agent observer --db output/observe.sqlite --output output`. For each pending job from `python -m main jobs pending --agent observer --db output/observe.sqlite --output output`, launch one observer subagent on that pack path only. Run at most 8 observers at a time. Give each subagent only its pack path. It returns JSON and does not write a file.
3. Write each returned JSON document to `output/logs/responses/<job_id>-<attempt>.json`. Record it with `python -m main jobs submit --job-id JOB --response PATH --db output/observe.sqlite --output output`. If the command returns non-zero, launch the same observer again with the error. Allow 3 attempts. If an observer has not returned after 120 seconds, stop it and run `python -m main jobs timeout --job-id JOB --elapsed-seconds ELAPSED --db output/observe.sqlite --output output`.
4. Run `python -m main jobs status --agent observer --require-ready --db output/observe.sqlite --output output`. Stop if the status is not 0. Do not consolidate a partial observer set.
5. Run `python -m main consolidate --corpus corpus --db output/observe.sqlite --output output`. Stop if the status is not 0.
6. Run `python -m main jobs pending --agent drafter --db output/observe.sqlite --output output`. Launch one drafter for each pending insight, at most 2 at a time. The drafter returns JSON. Write it to `output/logs/responses/<job_id>-<attempt>.json` and record it with `jobs submit`. Use the same 3-attempt and 120-second timeout rules.
7. Run `python -m main jobs status --agent drafter --require-ready --db output/observe.sqlite --output output`. Stop if the status is not 0.
8. Run `python -m main finalize --corpus corpus --db output/observe.sqlite --output output`. Stop if the status is not 0.
9. Run `python -m main serve --corpus corpus --report output/report.json`. Leave it running. The analysis budget is steps 1 through 8, not the time the dashboard stays up. If the remaining budget cannot fit another retry wave, do not start that wave; record the timeout or stop and leave the failed status in place.

A warm rerun repeats the pending steps. Succeeded jobs are omitted, so unchanged packs and insights launch nothing. `prepare` also skips parsing and pack generation when the corpus fingerprint, prompt, and schema are unchanged. Failed jobs are listed again unless `--no-retry-failed` is set. After an empty pending list, run the ready check, finalize again, and serve.

Example observer launch: "Read `output/packs/<pack_id>.json`. Return only the JSON object described in your agent prompt. Cite `span_id` values from that file."

Example drafter launch: "Read `output/insights/<input_sha256>.json`. Return only the JSON object described in your agent prompt. Copy factual-claim quotes from that file."

The parent may read command output, `output/logs/prepare.json`, `output/logs/consolidate.json`, `output/logs/finalize.json`, and `output/draft_targets.json` ids. It does not open pack or insight files.
