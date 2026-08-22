# Examples

Two versions of the same LLM-as-judge evaluation harness.

## `biased_judge.py` — findings, exits 1

```bash
judgeskew examples/biased_judge.py
```

Flags one blocker (`JS001`): `gpt-4o` writes the answer and then `gpt-4o` is
asked to grade that same answer — self-preference bias, so the score is
inflated. It also raises two `JS002` warnings on the judge call: no rubric or
reference in the prompt (unanchored scoring), and `temperature=0.7`
(nondeterministic scoring).

## `fair_judge.py` — clean, exits 0

```bash
judgeskew examples/fair_judge.py
```

The same job, done right. The candidate is produced by `gpt-4o`, but the judge
is an independent model of a different family (`claude-3-5-sonnet`), so there is
no self-preference. The judge prompt carries an explicit `1-10` rubric and a
reference answer, and it runs at `temperature=0`, so the scoring is anchored and
reproducible. judgeskew reports nothing.
