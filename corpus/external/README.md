# The out-of-distribution check

Every figure on this repository's scoreboard is a statement about a corpus **this repository
generated**. That is said everywhere it appears, and saying it is not the same as testing it.

This directory is where a real, publicly licensed document set goes, so that claim 1 can report
two columns — the threshold derived on the generated corpus, and the threshold derived on paper
nobody here designed. If they agree, the generator has earned its credibility. If they diverge,
that divergence is the most interesting result this project can produce.

**Nothing is committed here, and the reason is decision 13's own rule: *verify the licence before
committing anything*.** A dataset of scanned commercial documents is somebody's data before it is
anybody's benchmark, and a repository that vendored one without reading its terms would have
failed the standard it spends nine hundred lines arguing for.

## What a set has to satisfy before it lands here

1. **A licence that permits redistribution and derived works**, quoted in `LICENCE.md` beside the
   files, with the URL it was read from and the date. Not "it is on the internet". Not "it is for
   research". The words.
2. **Real scans, not renders.** The point is paper that was photographed by somebody else's
   scanner at somebody else's settings. A synthetic set from a different generator is a second
   invented distribution, and averaging two inventions does not approach reality.
3. **Field-level ground truth, or an honest statement that there is none.** Where a set has no
   labels, the only measurement available is the ISO 6346 check digit on container numbers, which
   gives a **lower bound** on the error rate and nothing else — and the report says so rather than
   presenting a partial measurement as a whole one.
4. **No personal data that this project has no basis to hold.** A commercial invoice names people.
   `docs/REGULATORY.md` governs what may be kept and for how long, and a public dataset does not
   suspend it.

## What happens once one lands

`scripts/check_external_corpus.py` refuses to run without `LICENCE.md`, and `evals/calibration/`
gains its second column. Neither needs code changes: the harness is written and waiting, which is
the difference between "not done" and "not possible".

## What this directory must never become

A set dropped in without `LICENCE.md`, scored, and quoted. The check refuses that, and the refusal
is the point — this is the one place in the repository where the temptation is to take the number
first and read the terms afterwards.
