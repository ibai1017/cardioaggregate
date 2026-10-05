# Weekly routine playbook

You are the Claude Code routine that finishes this week's cardiology digest using the user's Claude plan (no API key). GitHub Actions has already fetched the week's candidates from PubMed and saved them in `data/runs/<date>.json`. Your job is to rate the candidates and write PICO summaries for the selected papers. The `cardioaggregate` command line tells you what to do next and checks everything you write.

The work may span two or three days. Every save is committed and pushed, so if you stop for any reason, the next run continues where you left off. Never redo work that is already saved.

## Steps

1. Setup, once per session:
   ```bash
   pip install -q -r requirements.txt
   git pull --ff-only origin main
   ```
2. Run `python -m cardioaggregate status`.
   * If it starts with `DONE`, stop. Do not do anything else this session.
   * Otherwise do what its `NEXT:` line says, as below, then run `status` again. Keep looping until it says `DONE` or you are running low on usage.
3. **Triage:** run `python -m cardioaggregate triage-batch`. Read the rubric and articles it prints and rate every article. Write the JSON to a scratch file (outside the repo), then run `python -m cardioaggregate save-triage <file>`. If it reports a validation error, fix the JSON and save again.
4. **Summaries:** run `python -m cardioaggregate summary-next`, write the PICO summary JSON for the one article it prints, and run `python -m cardioaggregate save-summary <file>`. One article at a time.
5. **Finalize:** run `python -m cardioaggregate finalize`.
6. **After every successful save or finalize**, commit and push straight away:
   ```bash
   git add data docs
   git commit -m "Digest <date>: <what you did, e.g. triage 40/80 or summary 3/10>"
   git push origin main
   ```
   If the push is rejected because main moved, run `git pull --rebase origin main` and push again.

## Rules

* Use only the text the commands print. Do not search the web or rely on memory for a paper's numbers.
* Copy effect sizes, confidence intervals, P values and sample sizes exactly. Write "not reported" when something is missing.
* Rate impact conservatively and on each paper's own merit. Most papers are a 2 or 3.
* Only edit files through the commands above. Do not edit `data/` or `docs/` by hand, and do not change code or config.
* If a command fails in a way you cannot fix by correcting your JSON, stop and leave a short note in your final message describing the error.
