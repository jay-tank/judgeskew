# judgeskew

> A zero-config static gate that flags **biased LLM-as-judge evaluations** —
> the setup where the same model both writes an answer and grades it (self-
> preference bias), or a judge scores with no rubric and a nonzero temperature.
> The number looks rigorous and is quietly measuring the wrong thing. AST-based,
> zero config. Meant to run in CI.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

"LLM-as-judge" has quietly become the default way teams score their AI: instead
of a human rating every output, you ask a strong model to grade it. It is fast,
it is cheap, and — done wrong — it produces a number you can't trust. The most
common mistake is the quietest: you let `gpt-4o` write the candidate answer, and
then you ask `gpt-4o` to score it. Models systematically **prefer text they
produced themselves** (self-preference bias), so the grade comes back optimistic
and your eval is measuring brand loyalty, not quality. Two other habits do
similar damage: a judge with **no rubric** invents its own scale on every run,
and a judge at a **nonzero temperature** gives the same answer a different score
each time.

`judgeskew` reads your source with Python's `ast` module — it never imports or
runs your code — and fails the build when it finds a biased judge setup:

```text
$ judgeskew eval.py

╭─ BLOCKER JS001 ──────────────────────────────────────────────────────────────────╮
│ eval.py:24:13                                                                      │
│ self-preference bias: the judge call uses model 'gpt-4o', the same model as the   │
│ generator on line 18, and grades that model's own output — a model favours text   │
│ it produced, so the score is inflated and measures brand loyalty, not quality.     │
│ Fix: Use a different model (ideally a different model family) as the judge than    │
│ the one that produced the answer. Judge with an independent model, or add human /  │
│ reference-based grading.                                                            │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

## Why static / why CI

A biased judge never raises an exception — the eval runs fine and even reports
*better* scores, which is exactly why nobody questions it. You catch it in a
careful review, or you catch it after you've shipped a model change on the
strength of a self-graded number. A static gate on the pull request catches it
before it lands, with no runtime, no API key, no data, and no execution of your
code.

## What it flags

| Rule | Severity | The mistake | The fix |
| --- | --- | --- | --- |
| `JS001` | blocker | **Self-preference bias.** The same model — or the same model family/alias — is used as both the generator and the judge, and the judge is grading that model's own output. | Judge with a different model (ideally a different family), or add human / reference-based grading. |
| `JS002` | warn | **Weak judge hygiene.** A judge call with no rubric / reference / scale in its prompt (unanchored scoring), or a **nonzero `temperature`** (nondeterministic scoring). | Give the judge an explicit rubric and reference answer; set `temperature=0`. |

A call is treated as a **judge** when its prompt carries scoring / grading /
rating / comparison language (`rate`, `score`, `grade`, `evaluate`, `rubric`,
`on a scale`, `which response`, …) or when it consumes another LLM call's
output. A call is treated as a **generator** when it is an LLM call that is not a
judge and whose result is captured in a variable. Recognised LLM call shapes
include OpenAI (`client.chat.completions.create`), Anthropic
(`client.messages.create`), litellm (`completion`), Google GenAI
(`generate_content`), and LangChain runnables (`invoke`), each gated on an
anchoring keyword (`messages=` / `prompt=` / `model=` / …) so an unrelated
`.create()` is never mistaken for an LLM call.

## Before / after

```python
# BIASED — the model grades its own answer (JS001), no rubric + temp>0 (JS002):
gen = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": q}])
answer = gen.choices[0].message.content
judge = client.chat.completions.create(
    model="gpt-4o", temperature=0.7,
    messages=[{"role": "user", "content": f"Rate the quality of this answer: {answer}"}],
)

# FAIR — an independent judge of a different family, anchored and deterministic:
judge = anthropic.messages.create(
    model="claude-3-5-sonnet-20241022", temperature=0, max_tokens=512,
    messages=[{"role": "user", "content":
        f"Grade on a scale of 1-10 using this rubric (correctness, clarity). "
        f"Reference: {reference}\nAnswer: {answer}"}],
)
```

## Install

```bash
pip install judgeskew
```

## Usage

```bash
judgeskew eval.py                # scan one file
judgeskew evals/                 # recurse a directory (skips vendor/build/caches)
judgeskew evals/ --json          # machine-readable output for CI
judgeskew evals/ --strict        # also fail on JS002 warnings
judgeskew eval.py --no-color     # plain text
```

Exit codes: **0** clean · **1** blocker (or any finding under `--strict`) · **2** usage error.

Scanned extensions: `.py` (AST-based). Files over 2 MiB and vendored/build
directories are skipped. Suppress a line with a trailing `# judgeskew: ignore`
(or `# noqa: JS001`), or exclude paths with `--exclude GLOB` (repeatable) or a
`.judgeskewignore` file (gitignore-style globs).

## In CI

```yaml
- name: LLM-as-judge bias gate
  run: |
    pip install judgeskew
    judgeskew evals/
```

## pre-commit

```yaml
- repo: local
  hooks:
    - id: judgeskew
      name: judgeskew
      entry: judgeskew
      language: system
      types: [python]
```

## Not to be confused with

judgeskew is about **eval-methodology bias** — the judge itself is rigged. It is
deliberately distinct from its siblings: `skewscout` (train/serve feature skew),
and `seepage` / `peekage` (data leakage into training/evaluation). Those catch a
leak in the *data*; judgeskew catches a bias in the *grader*.

## Limitations (honest)

`judgeskew` is a static heuristic. It reasons about **literal model strings and
variable names within a single file**: if the model id or the generated text
arrives through machinery it can't see statically (a dict of configs, a database
row, a helper in another module), it can't connect the generator to the judge
and will under-report. A literal-vs-variable model pair is treated as unknown
(no JS001) to stay low-false-positive. Judge classification is prompt-language
based, so a judge that scores without any evaluative wording — and doesn't
consume a detected generator — may be missed. A deliberate same-model self-eval
(e.g. a reference-anchored self-consistency check you've reasoned about) is a
legitimate case to silence with `# judgeskew: ignore`. It never executes your
code.

> 📖 Read the write-up: [judgeskew](https://jaytank.hashnode.dev/judgeskew-llm-as-judge-bias-gate)

## License

MIT — see [LICENSE](LICENSE).
