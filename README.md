# CardioAggregate

A weekly digest of the cardiology papers worth reading: primary results of randomized trials, meta-analyses and systematic reviews (including Cochrane), and very large observational studies from NEJM, The Lancet, JAMA, BMJ, Annals, JACC, European Heart Journal, Circulation and JAMA Cardiology. Each selected paper gets a PICO summary, an appraisal of strengths and limitations, and links to its accompanying editorials.

It runs once a week on GitHub Actions, publishes a static page with GitHub Pages, and can also email you the digest.

## How it works

```
PubMed (last 7 days)  ->  Claude triage  ->  selection rules  ->  editorials  ->  Claude PICO summaries  ->  HTML page + email
```

1. **Candidates.** One PubMed E-utilities query per run pulls everything added to PubMed in the last 7 days from the configured journals that has an abstract and looks like a trial, meta-analysis, systematic review or cohort/registry study. General journals (NEJM, Lancet, JAMA, BMJ, Annals, Cochrane) are additionally limited to cardiovascular topics. Brand new PubMed records rarely have MeSH terms or curated publication types yet, so these filters use title and abstract words rather than relying on indexing.
2. **Triage.** Claude reads each candidate's abstract and returns its design (primary RCT, secondary RCT analysis, meta-analysis, systematic review, observational, other), the sample size, and a 1 to 5 impact rating.
3. **Selection.** Plain rules in `config.yaml` decide what makes the digest: by default primary RCTs and meta-analyses rated 3 or more, secondary trial analyses rated 4 or more, observational studies with at least 10,000 participants rated 3 or more, capped at 25 papers. Everything screened but not selected is listed at the bottom of the digest with the reason, so you can see what was filtered out.
4. **Editorials.** PubMed links editorials to the article they discuss (`CommentIn` / `CommentOn`). The run collects editorials from the same journals over the last 28 days and matches them. Editorials often get indexed after their article, so ones that arrive late appear in a "Late editorials" section in the following digest.
5. **Summaries.** For each selected paper Claude writes a PICO summary using the abstract, plus the full text and editorial text when they are open access in Europe PMC. Each card says which sources the summary was based on.
6. **Output.** `docs/digests/YYYY-MM-DD.html` plus an archive at `docs/index.html`. `data/history.json` remembers what has been covered so nothing repeats.

## Setup

1. **Anthropic API key.** Create one at https://platform.claude.com and add it as the repository secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions). Expect roughly $1 to $3 per weekly run with the default model; lower it by switching `llm.model` in `config.yaml` to `claude-sonnet-5-5`.
2. **NCBI (optional but recommended).** Add `NCBI_EMAIL` (your email) and `NCBI_API_KEY` (free from your NCBI account settings). This raises PubMed rate limits and is NCBI's requested etiquette.
3. **GitHub Pages.** Settings → Pages → Deploy from a branch → `main` / `docs`. Note that Pages on a *private* repository requires a paid GitHub plan; either make the repository public (it only contains summaries of published papers) or rely on email.
4. **Email (optional).** Add `SMTP_HOST`, `SMTP_PORT` (465 for SSL or 587 for STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` and optionally `EMAIL_FROM`. For Gmail use `smtp.gmail.com`, port 465, and an app password.
5. **Run it once.** Actions → Weekly digest → Run workflow. Try `days: 14` the first time for a fuller page. After that it runs every Sunday at 11:17 UTC; change the cron line in `.github/workflows/weekly-digest.yml` to suit you.

## Running locally

```bash
pip install -r requirements.txt pytest
python -m pytest -q
export ANTHROPIC_API_KEY=...
python -m cardioaggregate            # writes docs/digests/<today>.html
python -m cardioaggregate --no-llm   # no Claude: heuristic selection, abstracts only
```

## Customizing

Everything lives in `config.yaml`: journals (add e.g. `Eur J Heart Fail`, `Circ Heart Fail`, `JACC Heart Fail`, `Heart Rhythm`, `EuroIntervention`, using their MEDLINE abbreviations), lookback windows, impact thresholds, the observational size cutoff, the article cap, and model/effort settings.

## Known limitations

* **Paywalls.** Most NEJM, Lancet and JACC papers and editorials are not open access, so summaries are usually based on the abstract and the editorial is linked rather than summarized. Strengths and limitations are then Claude's own appraisal, and the page says so.
* **Indexing lag.** Papers appear here when PubMed indexes them, usually within a few days of online publication. Editorial links can lag longer, which is what the late editorials section is for.
* **Model judgement.** Impact ratings and sample sizes come from the model reading the abstract. The "Also screened" list is there so you can catch anything it underrated. Always check numbers against the paper before acting on them.
