# CardioAggregate

A weekly digest of the cardiology papers worth reading: primary results of randomized trials, meta-analyses and systematic reviews (including Cochrane), and very large observational studies from 31 journals:

* **General medical:** NEJM, NEJM Evidence, The Lancet, JAMA, JAMA Internal Medicine, BMJ, Annals of Internal Medicine, Cochrane (cardiovascular topics only)
* **General cardiology:** JACC, European Heart Journal, Circulation, JAMA Cardiology
* **Heart failure and transplant:** European Journal of Heart Failure, JACC: Heart Failure, Circulation: Heart Failure, J Heart Lung Transplant
* **Electrophysiology:** Heart Rhythm, Europace, JACC: Clinical Electrophysiology, Circulation: Arrhythmia and Electrophysiology
* **Interventional and structural:** JACC: Cardiovascular Interventions, EuroIntervention, Circulation: Cardiovascular Interventions
* **Imaging:** JACC: Cardiovascular Imaging, EHJ Cardiovascular Imaging, Circulation: Cardiovascular Imaging
* **Prevention, hypertension, outcomes, acute care, surgery:** European Journal of Preventive Cardiology, Hypertension, Circulation: Cardiovascular Quality and Outcomes, EHJ Acute Cardiovascular Care, J Thorac Cardiovasc Surg

Each selected paper is tagged with its subspecialty and gets a PICO summary, an appraisal of strengths and limitations, and links to its accompanying editorials.

It costs nothing to run beyond the Claude plan you already have. GitHub Actions does the free parts (fetching from PubMed, building the page, email), and a scheduled Claude Code routine on your Claude plan does the reading and writing. GitHub Pages hosts the result.

## How it works

```
Saturday, GitHub Actions (free)          Sunday to Tuesday, Claude Code routine (your plan)       Free
PubMed -> prescore -> preliminary page   triage top 80 -> select ~10 -> PICO summaries           final page + email
```

1. **Candidates (free).** Every Saturday a GitHub Action runs one PubMed query that pulls everything added in the last 7 days from the configured journals that has an abstract and looks like a trial, meta-analysis, systematic review or cohort/registry study. General journals are limited to cardiovascular topics. Brand new PubMed records rarely have MeSH terms or curated publication types yet, so these filters use title and abstract words.
2. **Prescore (free).** A simple score ranks the candidates: study design, flagship journal, sample size, and above all whether the paper has an accompanying editorial (journals rarely commission one for a minor paper). Only the top 80 go to Claude, which keeps the reading cheap. A preliminary digest built from this score is published straight away, so you always get something even if the routine misses a week.
3. **Triage (Claude).** The routine reads the queued abstracts 20 at a time and rates each one: design (primary RCT, secondary analysis, meta-analysis, systematic review, observational), subspecialty, sample size, and impact from 1 to 5.
4. **Selection (free).** Rules in `config.yaml` pick the digest: a normal week is about 10 papers; papers rated 4 or 5 always get in up to a ceiling of 20, so a week with a major meeting (ESC, AHA, ACC) grows on its own and is flagged as a busy week. The best 60 near misses are listed at the bottom with the reason.
5. **Summaries (Claude).** Only the selected papers get the full PICO summary, appraisal and editorial view, one at a time. The full text and editorial text are used when they are open access in Europe PMC; otherwise the abstract.
6. **Finalize (free).** The final page replaces the preliminary one, `data/history.json` records what was covered so nothing repeats, and an Action emails the digest.

**Progress is saved after every step.** Each batch of ratings and each summary is committed to the repo, so the routine can stop at any point (usage limit, closed session) and the next day's run carries on. The routine runs on Sunday, Monday and Tuesday, and does nothing once the week is done. If it still hasn't finished by the following Saturday, that run finalizes the old week with whatever was done, using the free score for anything left.

**Editorials.** PubMed links editorials to the article they discuss (`CommentIn` / `CommentOn`). Editorials from the same journals over the last 28 days are matched; ones indexed after their article appear in a "Late editorials" section the following week.

## Setup

1. **GitHub Pages.** Settings → Pages → Deploy from a branch → `main` / `docs`. Pages on a *private* repository requires a paid GitHub plan; either make the repository public (it only contains summaries of published papers) or rely on email.
2. **NCBI (optional but recommended).** Add the repository secrets `NCBI_EMAIL` (your email) and `NCBI_API_KEY` (free from your NCBI account). This raises PubMed rate limits and is NCBI's requested etiquette.
3. **Email (optional).** Add `SMTP_HOST`, `SMTP_PORT` (465 for SSL or 587 for STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` and optionally `EMAIL_FROM`. For Gmail use `smtp.gmail.com`, port 465, and an app password.
4. **First run.** Actions → Weekly digest → Run workflow, with `days: 14` for a fuller first page. Each run first checks that every journal abbreviation in `config.yaml` matches PubMed and warns about any that don't.
5. **The routine.** A Claude Code routine fires on Sunday, Monday and Tuesday and follows [`ROUTINE.md`](ROUTINE.md). It needs only GitHub access; PubMed is fetched by Actions. Optionally allow `www.ebi.ac.uk` in the routine's cloud environment network settings so it can read open access full texts.

**Prefer pay per use?** Add an `ANTHROPIC_API_KEY` repository secret and the Saturday Action does the triage and summaries itself with the API (`python -m cardioaggregate api`), roughly $1 to $3 a week. Then the routine is not needed.

## Running locally

```bash
pip install -r requirements.txt pytest
python -m pytest -q
python -m cardioaggregate prepare          # fetch PubMed, preliminary digest
python -m cardioaggregate status           # what to do next
python -m cardioaggregate triage-batch     # then save-triage, summary-next, save-summary, finalize
```

## Customizing

Everything lives in `config.yaml`: journals (by MEDLINE abbreviation; JAHA and JAMA Network Open are included but commented out because they are high volume and mostly observational), lookback windows, impact thresholds, the observational size cutoff, the digest size, how many candidates Claude reads (`max_triage`), and the API model settings.

## Known limitations

* **Paywalls.** Most NEJM, Lancet and JACC papers and editorials are not open access, so summaries are usually based on the abstract and the editorial is linked rather than summarized. Strengths and limitations are then Claude's own appraisal, and the page says so.
* **Indexing lag.** Papers appear here when PubMed indexes them, usually within a few days of online publication. Editorial links can lag longer, which is what the late editorials section is for.
* **Model judgement.** Impact ratings and sample sizes come from the model reading the abstract. The "Also screened" list is there so you can catch anything it underrated. Always check numbers against the paper before acting on them.
* **The free prescore decides what Claude reads.** Candidates beyond the top 80 are never rated by Claude. That should mostly drop small observational studies, but it is unverified until real weeks run; raise `max_triage` if you see a gap.
* **Plan usage.** The routine uses your Claude plan's limits, more in a conference week. That is why the work is spread over up to three days.
