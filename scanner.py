"""Scanner for biased LLM-as-judge evaluation setups.

The scanner parses source with the standard-library ``ast`` module and
**never imports or executes the target code**. It flags an evaluation harness
whose *methodology* is biased — the score comes back looking rigorous but is
measuring the wrong thing.

Rules:
  JS001 (blocker) — self-preference bias: the SAME model (or the same model
      family/alias) is used as BOTH the generator/candidate AND the judge in
      the same file/flow, and the judge is scoring that model's own output.
      Models systematically favour text they produced, so a model grading its
      own answers inflates the score. Detected as two LLM calls where the judge
      call's ``model=`` matches (or shares a family with) the generator call's
      ``model=``, and the judge call's prompt references the generated output.
  JS002 (warn)   — a judge LLM call with weak scoring hygiene:
      * no rubric / criteria / reference / scale in its prompt (unanchored
        scoring — the model invents its own scale every run), or
      * a nonzero ``temperature`` on the judge call (nondeterministic scoring —
        the same answer gets a different score on a re-run).

A call is treated as a **judge** when its prompt carries scoring / grading /
rating / evaluation / comparison language, or when it consumes another LLM
call's output. A call is treated as a **generator** when it is an LLM call
that is not a judge and whose result is captured in a variable.

Attribution is intentionally low false-positive:
  * different model families for generator vs judge short-circuits JS001;
  * a rubric / reference in the judge prompt short-circuits the JS002 "no
    rubric" case;
  * ``temperature=0`` short-circuits the JS002 "nondeterministic" case.

Known limitation (documented, not a bug): the analysis reasons about literal
model strings and variable names within a single file. If the model id or the
generated text arrives through machinery it cannot see statically (a dict of
configs, a database row, a helper in another module), it cannot connect the two
calls — a deliberate false-negative tradeoff over a false positive.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from models import BLOCKER, JS001, JS002, WARN, Finding

# Inline suppression: either marker on a flagged line skips it.
INLINE_IGNORE = "judgeskew: ignore"
NOQA = "noqa"

# ---------------------------------------------------------------------------
# LLM call-shape classification (attribute path + anchoring kwarg)
# ---------------------------------------------------------------------------
# A call is treated as an LLM request only when its method path matches a known
# SDK shape AND it carries an anchoring keyword — this keeps false positives low
# (a bare ``.create()`` on some unrelated object is never an LLM call).
_ANCHOR_KWARGS = {"messages", "message", "prompt", "input", "model", "contents"}

# Trailing attribute paths that name an LLM completion/chat call.
_CALL_SUFFIXES = (
    ("chat", "completions", "create"),   # OpenAI
    ("responses", "create"),             # OpenAI Responses API
    ("messages", "create"),              # Anthropic
    ("messages", "stream"),              # Anthropic streaming
    ("completions", "create"),           # OpenAI legacy client
    ("chat", "completions", "parse"),    # OpenAI structured output
)
# Single-segment method names that name an LLM call (distinctive verbs).
_SINGLE_METHODS = {
    "completion", "acompletion",         # litellm
    "generate_content", "generate",      # Google GenAI / Cohere
    "invoke", "ainvoke", "predict",      # LangChain runnables
    "chat",                              # Cohere / Ollama
}

# Prompt language that marks a call as a JUDGE (scoring / grading / comparing).
_JUDGE_TERMS = (
    "score", "rate", "rating", "grade", "grading", "evaluate", "evaluation",
    "judge", "rubric", "criteria", "criterion", "rank", "ranking", "compare",
    "comparison", "assess", "assessment", "verdict", "quality of", "how good",
    "which response", "which answer", "better response", "better answer",
    "on a scale", "out of 10", "out of 5", "1-10", "1-5", "1 to 10", "1 to 5",
    "helpfulness", "correctness", "faithfulness", "preferred", "winner",
)

# Prompt language that ANCHORS a judge's scoring (rubric / reference / scale).
# Presence of any of these short-circuits the JS002 "no rubric" case.
_ANCHOR_TERMS = (
    "rubric", "criteria", "criterion", "reference", "ground truth", "gold",
    "guideline", "on a scale", "scale of", "score from", "out of 10", "out of 5",
    "1-10", "1-5", "0-10", "0-5", "1 to 10", "1 to 5", "expected answer",
    "correct answer", "reference answer", "must include", "points if", "award",
    "deduct", "the following criteria", "grading notes",
)

_FIX_JS001 = (
    "Use a different model (ideally a different model family) as the judge than "
    "the one that produced the answer. A model scoring its own output exhibits "
    "self-preference bias and inflates the result — the eval measures brand "
    "loyalty, not quality. Judge with an independent model, or add human / "
    "reference-based grading."
)
_FIX_JS002_RUBRIC = (
    "Give the judge an explicit rubric or reference: a scoring scale, the "
    "criteria to grade against, and/or the expected/gold answer. Without an "
    "anchor the model invents its own scale each run, so the scores are not "
    "comparable and drift over time."
)
_FIX_JS002_TEMP = (
    "Set temperature=0 on a judge call. A nonzero temperature makes scoring "
    "nondeterministic — the same answer earns a different score on a re-run, so "
    "you cannot reproduce or trust the eval."
)


def _attr_path(func: ast.AST) -> Tuple[Tuple[str, ...], Optional[str]]:
    """Return (trailing attribute names, root Name id) for a call target.

    ``client.chat.completions.create`` -> (("chat","completions","create"),
    "client"). A bare ``func(...)`` yields ((), None)."""
    parts: List[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    parts.reverse()
    root = cur.id if isinstance(cur, ast.Name) else None
    return tuple(parts), root


def _ends_with(path: Tuple[str, ...], suffix: Tuple[str, ...]) -> bool:
    return len(path) >= len(suffix) and path[-len(suffix):] == suffix


def _is_llm_call(node: ast.Call) -> bool:
    """True if this call looks like an LLM completion/chat request."""
    path, _root = _attr_path(node.func)
    if not path:
        return False
    matched = any(_ends_with(path, suf) for suf in _CALL_SUFFIXES)
    if not matched and path[-1] in _SINGLE_METHODS:
        matched = True
    if not matched:
        return False
    return any(kw.arg in _ANCHOR_KWARGS for kw in node.keywords if kw.arg)


# ---------------------------------------------------------------------------
# Model id / family extraction
# ---------------------------------------------------------------------------
def _model_of(call: ast.Call) -> Tuple[str, Optional[str]]:
    """Return ``(kind, value)`` describing the call's ``model=`` argument.

    kind is ``"literal"`` (value = the model string), ``"var"`` (value = the
    variable name), or ``"unknown"`` (value = None)."""
    for kw in call.keywords:
        if kw.arg == "model":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return "literal", kw.value.value
            if isinstance(kw.value, ast.Name):
                return "var", kw.value.id
            if isinstance(kw.value, ast.Attribute):
                return "var", kw.value.attr
            return "unknown", None
    return "unknown", None


def _family(model: str) -> Optional[str]:
    """Coarse model family/alias for a model string.

    ``gpt-4o-mini`` -> ``gpt``; ``claude-3-5-sonnet`` -> ``claude``;
    ``gemini-1.5-pro`` -> ``gemini``. Returns None when no leading letters
    are present."""
    m = re.match(r"[a-zA-Z]+", os.path.basename(model.strip().lower()))
    return m.group(0) if m else None


def _models_match(a: Tuple[str, Optional[str]], b: Tuple[str, Optional[str]]) -> bool:
    """True if two ``_model_of`` results denote the same model (or family).

    Two literals match on an exact string OR a shared family; two variables
    match on the same name. A literal-vs-variable pair is treated as unknown
    (not a match) to stay low false-positive."""
    kind_a, val_a = a
    kind_b, val_b = b
    if val_a is None or val_b is None:
        return False
    if kind_a == "literal" and kind_b == "literal":
        if val_a.strip().lower() == val_b.strip().lower():
            return True
        fam_a, fam_b = _family(val_a), _family(val_b)
        return fam_a is not None and fam_a == fam_b
    if kind_a == "var" and kind_b == "var":
        return val_a == val_b
    return False


# ---------------------------------------------------------------------------
# Prompt-text harvesting & classification
# ---------------------------------------------------------------------------
def _prompt_text(call: ast.Call) -> str:
    """Concatenate every string literal anywhere inside the call, lowercased.

    This captures message contents, a system prompt, and f-string literal
    parts — enough to classify the call's intent without executing anything."""
    chunks: List[str] = []
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            chunks.append(node.value)
    return "\n".join(chunks).lower()


def _looks_like_judge(prompt: str) -> bool:
    return any(term in prompt for term in _JUDGE_TERMS)


def _is_anchored(prompt: str) -> bool:
    return any(term in prompt for term in _ANCHOR_TERMS)


def _temperature_of(call: ast.Call) -> Tuple[str, Optional[float]]:
    """Return ``(kind, value)`` for the call's ``temperature=`` argument.

    kind is ``"literal"`` (value = the number), ``"absent"``, or ``"dynamic"``
    (present but not a constant number)."""
    for kw in call.keywords:
        if kw.arg == "temperature":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float)):
                return "literal", float(kw.value.value)
            return "dynamic", None
    return "absent", None


def _names_in(call: ast.Call) -> Set[str]:
    """All variable names referenced anywhere in a call's args/keywords."""
    out: Set[str] = set()
    for node in ast.walk(call):
        if isinstance(node, ast.Name):
            out.add(node.id)
    return out


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------
def _assign_targets(node: ast.Assign) -> List[str]:
    names: List[str] = []
    for tgt in node.targets:
        if isinstance(tgt, ast.Name):
            names.append(tgt.id)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            for elt in tgt.elts:
                if isinstance(elt, ast.Name):
                    names.append(elt.id)
    return names


class _Call:
    """Metadata for one recognised LLM call."""

    __slots__ = ("node", "model", "prompt", "temp", "is_judge", "assigned")

    def __init__(self, node: ast.Call) -> None:
        self.node = node
        self.model = _model_of(node)
        self.prompt = _prompt_text(node)
        self.temp = _temperature_of(node)
        self.is_judge = _looks_like_judge(self.prompt)
        self.assigned: List[str] = []


def _scan_python(source: str, filename: str) -> List[Finding]:
    tree = ast.parse(source, filename=filename)

    # 1) Collect every LLM call and (if directly assigned) its target vars.
    calls: List[_Call] = []
    call_by_node: Dict[ast.Call, _Call] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_llm_call(node):
            c = _Call(node)
            calls.append(c)
            call_by_node[node] = c
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            c = call_by_node.get(node.value)
            if c is not None:
                c.assigned = _assign_targets(node)

    # 2) Map each generator's output to the variables that carry it.
    #    A generator = an LLM call that is not a judge (by prompt language).
    #    var_origin[var] -> the generator _Call whose output var ultimately is.
    var_origin: Dict[str, _Call] = {}
    for c in calls:
        if not c.is_judge:
            for name in c.assigned:
                var_origin[name] = c

    #    Propagate through simple derivations: ``answer = gen.choices[0]...`` or
    #    ``answer = resp.content`` — the target inherits the generator origin.
    for _ in range(6):  # small fixpoint; deep chains are rare
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if isinstance(node.value, ast.Call) and node.value in call_by_node:
                continue  # already handled as a direct LLM-call assignment
            referenced = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            hit = next((var_origin[r] for r in referenced if r in var_origin), None)
            if hit is None:
                continue
            for tgt in _assign_targets(node):
                if tgt not in var_origin:
                    var_origin[tgt] = hit
                    changed = True
        if not changed:
            break

    # 3) Judge classification is finalised: language OR consuming a gen output.
    for c in calls:
        if not c.is_judge:
            names = _names_in(c.node)
            if any(n in var_origin for n in names):
                # A call consuming another LLM call's output is a judge/critic —
                # but don't treat a generator's own output vars as self-consumed.
                consumed = {n for n in names if n in var_origin}
                if any(var_origin[n] is not c for n in consumed):
                    c.is_judge = True

    findings: List[Finding] = []

    for c in calls:
        if not c.is_judge:
            continue

        # --- JS001: self-preference (same model generates AND judges) --------
        names = _names_in(c.node)
        matched_gen: Optional[_Call] = None
        for n in names:
            origin = var_origin.get(n)
            if origin is None or origin is c:
                continue
            if _models_match(origin.model, c.model):
                matched_gen = origin
                break
        if matched_gen is not None:
            findings.append(_make_js001(c, matched_gen, filename))
            # A JS001 blocker already indicates a broken methodology; still
            # surface the independent JS002 hygiene warnings below.

        # --- JS002: unanchored scoring --------------------------------------
        if not _is_anchored(c.prompt):
            findings.append(
                _finding(c.node, filename, JS002, WARN,
                         "a judge LLM call scores with no rubric, reference, or "
                         "scale in its prompt — unanchored grading; the model "
                         "invents its own scale each run, so scores drift and are "
                         "not comparable.",
                         _FIX_JS002_RUBRIC)
            )

        # --- JS002: nondeterministic scoring --------------------------------
        kind, val = c.temp
        if kind == "literal" and val != 0:
            findings.append(
                _finding(c.node, filename, JS002, WARN,
                         f"a judge LLM call sets temperature={_fmt(val)} (not 0) — "
                         "nondeterministic scoring; the same answer earns a "
                         "different score on a re-run, so the eval is not "
                         "reproducible.",
                         _FIX_JS002_TEMP)
            )

    findings = _dedupe(findings)
    return _apply_inline_ignore(findings, source)


def _fmt(val: float) -> str:
    return str(int(val)) if float(val).is_integer() else str(val)


def _model_label(model: Tuple[str, Optional[str]]) -> str:
    kind, val = model
    if kind == "literal":
        return f"model '{val}'"
    if kind == "var":
        return f"model variable '{val}'"
    return "the same model"


def _make_js001(judge: _Call, gen: _Call, filename: str) -> Finding:
    same = "the same model" if _same_exact(judge.model, gen.model) else "a same-family model"
    msg = (
        f"self-preference bias: the judge call uses {_model_label(judge.model)}, "
        f"{same} as the generator on line {gen.node.lineno}, and grades that "
        "model's own output — a model favours text it produced, so the score is "
        "inflated and measures brand loyalty, not quality."
    )
    return _finding(judge.node, filename, JS001, BLOCKER, msg, _FIX_JS001)


def _same_exact(a: Tuple[str, Optional[str]], b: Tuple[str, Optional[str]]) -> bool:
    (ka, va), (kb, vb) = a, b
    if va is None or vb is None:
        return False
    if ka == "literal" and kb == "literal":
        return va.strip().lower() == vb.strip().lower()
    if ka == "var" and kb == "var":
        return va == vb
    return False


def _finding(call: ast.Call, filename: str, rule: str, severity: str,
             message: str, fix: str) -> Finding:
    return Finding(
        file=filename, line=call.lineno, col=call.col_offset + 1,
        rule=rule, severity=severity, message=message, fix=fix,
    )


def _dedupe(findings: List[Finding]) -> List[Finding]:
    # Keyed on the message too, so the two distinct JS002 sub-cases (no rubric /
    # nonzero temperature) on one judge call are both kept — only true repeats
    # from a re-walk are dropped.
    seen: Set[Tuple[int, int, str, str]] = set()
    out: List[Finding] = []
    for f in findings:
        key = (f.line, f.col, f.rule, f.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Inline suppression
# ---------------------------------------------------------------------------
def _line_suppresses(line: str, rule: str) -> bool:
    """True if a trailing comment on ``line`` silences ``rule``.

    Honours ``# judgeskew: ignore`` (silences any rule), a bare ``# noqa``
    (silences everything), and a targeted ``# noqa: JS001`` (this rule only)."""
    if INLINE_IGNORE in line:
        return True
    idx = line.find(NOQA)
    if idx == -1:
        return False
    rest = line[idx + len(NOQA):].lstrip()
    if not rest.startswith(":"):
        return True  # bare noqa suppresses everything on the line
    return rule in rest


def _apply_inline_ignore(findings: List[Finding], source: str) -> List[Finding]:
    """Drop findings whose line carries ``# judgeskew: ignore`` or a noqa."""
    lines = source.splitlines()
    kept: List[Finding] = []
    for f in findings:
        idx = f.line - 1
        if 0 <= idx < len(lines) and _line_suppresses(lines[idx], f.rule):
            continue
        kept.append(f)
    return kept


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
PY_EXT = ".py"
CODE_EXTS = (PY_EXT,)


def scan_source(source: str, filename: str) -> List[Finding]:
    """Scan one source string. Only ``.py`` is analysed (AST-based).

    Raises SyntaxError only for unparseable Python; the CLI catches it."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == PY_EXT:
        findings = _scan_python(source, filename)
    else:
        findings = []
    findings.sort(key=lambda f: (f.line, f.col, f.rule))
    return findings
