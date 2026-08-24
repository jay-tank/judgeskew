# Contributing

judgeskew is intentionally small: a pure `ast` scanner, two rules, one runtime
dependency (`rich`) for rendering. Contributions that keep it that way are very
welcome.

## Setup

```bash
git clone https://github.com/jay-tank/judgeskew.git
cd judgeskew
pip install -e '.[dev]'
python -m pytest -q
judgeskew examples/biased_judge.py
```

Python 3.9+, `rich` as the only runtime dependency — please keep it that way.

## Design notes

- Detection lives in `scanner.py`. The levers are the call-shape suffixes
  (`_CALL_SUFFIXES` / `_SINGLE_METHODS` — what makes a call an LLM request),
  the judge-language terms (`_JUDGE_TERMS` — what marks a call as a judge), and
  the anchor terms (`_ANCHOR_TERMS` — a rubric/reference/scale that makes a
  judge's scoring anchored).
- The core never imports or executes scanned code — it only parses it with
  `ast.parse`. Keep it that way; that's the whole point of a static gate.
- Precision over recall: JS001 requires a judge call whose `model=` matches (or
  shares a family with) a generator call **and** that references the generator's
  output. Different families short-circuit JS001; a rubric short-circuits the
  JS002 "no rubric" case; `temperature=0` short-circuits the JS002 determinism
  case. If you add a new term or shape, add a near-miss test proving it stays
  quiet.

## Adding coverage

If you're adding support for another SDK/client shape:

1. Add the trailing attribute path to `_CALL_SUFFIXES` (or the method name to
   `_SINGLE_METHODS`) in `scanner.py`.
2. Add a positive test in `tests/test_scanner.py` (a same-model gen+judge is
   flagged) and a negative test (a different-family judge stays clean).
3. Update the "Recognized call shapes" note in `scanner.py`'s module docstring
   and `docs/USAGE.md`.

## Reporting false positives / false negatives

Open an issue with the exact lines. judgeskew's value is a low false-positive
rate — a fair, independent-judge eval getting flagged is a bug worth fixing. A
missed same-model self-judge with a real, detectable shape is worth reporting; a
case we can't connect statically (the model id or generated text arriving
through machinery we can't see) is a documented tradeoff, not a bug.
