# judgeskew — Usage

`judgeskew` is a zero-config static gate for **biased LLM-as-judge evaluation
setups**. It parses source with the standard-library `ast` module and **never
imports or executes the target code**, so it is safe to run over untrusted
source in CI.

## Command

```
judgeskew [PATHS ...] [--strict] [--json] [--no-color] [--exclude GLOB] [--version] [-h]
```

- `PATHS` — one or more files or directories. Directories are walked
  recursively; vendored/build/cache directories (`node_modules`, `vendor`,
  `build`, `dist`, `.venv`, `target`, …) are skipped.
- `--strict` — also fail (exit 1) on `JS002` warnings, not just blockers.
- `--json` — emit machine-readable JSON instead of the terminal report.
- `--no-color` — disable ANSI colour (useful for logs / CI).
- `--exclude GLOB` — exclude matching paths; repeatable.
- `--version` — print the version and exit.
- `-h`, `--help` — show help.

Only `.py` files are read and analysed (precise, AST-based). Files larger than
2 MiB are skipped.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | No blocking findings (JS002 warnings alone pass unless `--strict`). |
| `1` | One or more blockers (JS001), or any finding under `--strict`. |
| `2` | Usage error — no paths given, or no scannable files found. |

Files that fail to parse (Python syntax errors) or can't be read are reported
on stderr and skipped; they don't crash the run.

## Rules

### JS001 — self-preference bias (blocker)

The same model — or the same model **family/alias** — is used as both the
generator/candidate and the judge, and the judge is grading that model's own
output. Models systematically prefer text they produced, so the resulting score
is inflated: the eval measures brand loyalty, not quality.

judgeskew fires JS001 when a **judge** call's `model=` matches a **generator**
call's `model=` — an exact string, a shared family (`gpt-4o` vs `gpt-4o-mini`
both map to `gpt`), or the same model variable — **and** the judge call
references the generator's output (the variable that holds it, directly or
through a simple `.choices[0].message.content`-style derivation).

**Fix:** use a different model as the judge — ideally a different family — or add
human / reference-based grading.

### JS002 — weak judge hygiene (warning)

Two independent sub-cases, both reported on the judge call:

- **Unanchored scoring** — the judge prompt has no rubric, reference, gold
  answer, or scale. With nothing to anchor it, the model invents its own scale
  every run, so the scores are not comparable and drift over time.
- **Nondeterministic scoring** — the judge call sets a **nonzero
  `temperature`**. The same answer earns a different score on a re-run, so the
  eval is not reproducible.

**Fix:** give the judge an explicit rubric and a reference/gold answer; set
`temperature=0`.

## What counts as a judge, and as a generator

- A call is a **judge** when its prompt carries evaluative language — `rate`,
  `score`, `grade`, `evaluate`, `rubric`, `criteria`, `rank`, `compare`,
  `assess`, `verdict`, `on a scale`, `which response`, … — or when it consumes
  another LLM call's output.
- A call is a **generator** when it is a recognised LLM call that is *not* a
  judge and whose result is assigned to a variable (so its output can be traced
  into a later judge call).

## Recognised LLM call shapes

A call is treated as an LLM request only when its method path matches a known
shape **and** it carries an anchoring keyword (`messages=`, `message=`,
`prompt=`, `input=`, `model=`, or `contents=`):

| Shape | Example |
| --- | --- |
| OpenAI chat | `client.chat.completions.create(...)` |
| OpenAI Responses / legacy | `client.responses.create(...)`, `openai.Completion.create(...)` |
| Anthropic | `client.messages.create(...)`, `client.messages.stream(...)` |
| litellm | `litellm.completion(...)`, `litellm.acompletion(...)` |
| Google GenAI | `model.generate_content(...)` |
| Cohere / Ollama | `co.chat(...)`, `client.generate(...)` |
| LangChain runnables | `llm.invoke(...)`, `chain.predict(...)` |

A bare `.create()` (e.g. Django's `Model.objects.create(...)`) has no anchoring
keyword and is never flagged.

## Low-false-positive short-circuits

- **Different model families** for generator vs judge → no JS001.
- **A rubric / reference / scale** in the judge prompt → no JS002 "no rubric".
- **`temperature=0`** on the judge call → no JS002 "nondeterministic".

## Suppression & tuning false positives

- **Inline:** a trailing `# judgeskew: ignore` on a flagged line suppresses it.
  A `# noqa: JS001` (or bare `# noqa`) works too. For a multi-line call, put the
  comment on the line where the call *starts* (that is the line judgeskew
  reports).
- **`--exclude GLOB`:** repeatable, gitignore-style globs.
- **`.judgeskewignore`:** gitignore-style glob patterns (one per line, `#`
  comments allowed), read from the current directory; matching paths are
  excluded from the scan.

A deliberate same-model self-evaluation — a reference-anchored self-consistency
check you've reasoned about — is a legitimate case to silence with
`# judgeskew: ignore`.

## Integration

### pre-commit

```yaml
- repo: local
  hooks:
    - id: judgeskew
      name: judgeskew
      entry: judgeskew
      language: system
      types: [python]
```

### CI

```yaml
- name: LLM-as-judge bias gate
  run: |
    pip install judgeskew
    judgeskew evals/
```

## JSON shape

```json
{
  "files_scanned": 1,
  "findings": [
    {
      "file": "eval.py",
      "line": 24,
      "col": 13,
      "rule": "JS001",
      "severity": "blocker",
      "message": "...",
      "fix": "..."
    }
  ],
  "summary": { "total": 1, "blockers": 1, "warnings": 0 }
}
```
