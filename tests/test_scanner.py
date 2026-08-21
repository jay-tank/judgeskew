from models import JS001, JS002
from scanner import scan_source


def _rules(findings):
    return {f.rule for f in findings}


# --- JS001: self-preference (same model generates AND judges) ---------------
def test_same_model_gen_and_judge_is_js001():
    src = (
        "def evaluate(q):\n"
        "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model='gpt-4o', temperature=0,\n"
        "        messages=[{'role':'user','content':'Rate this answer on a scale of 1-5 using the rubric: ' + answer}])\n"
        "    return judge\n"
    )
    findings = scan_source(src, "u.py")
    js001 = [f for f in findings if f.rule == JS001]
    assert len(js001) == 1
    assert js001[0].severity == "blocker"
    assert js001[0].line == 4


def test_same_family_gen_and_judge_is_js001():
    # gpt-4o generates, gpt-4o-mini judges — same family, still self-preference.
    src = (
        "def evaluate(q):\n"
        "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model='gpt-4o-mini', temperature=0,\n"
        "        messages=[{'role':'user','content':'Grade this answer with the rubric 1-5: ' + answer}])\n"
        "    return judge\n"
    )
    assert JS001 in _rules(scan_source(src, "u.py"))


def test_same_model_variable_gen_and_judge_is_js001():
    src = (
        "def evaluate(q, MODEL):\n"
        "    gen = client.chat.completions.create(model=MODEL, messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model=MODEL, temperature=0,\n"
        "        messages=[{'role':'user','content':'Score this answer, rubric 1-5: ' + answer}])\n"
        "    return judge\n"
    )
    assert JS001 in _rules(scan_source(src, "u.py"))


# --- JS002: unanchored scoring ---------------------------------------------
def test_judge_without_rubric_is_js002():
    src = (
        "def evaluate(answer):\n"
        "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0,\n"
        "        messages=[{'role':'user','content':'Rate the quality of this answer: ' + answer}])\n"
        "    return judge\n"
    )
    findings = scan_source(src, "u.py")
    js002 = [f for f in findings if f.rule == JS002]
    assert len(js002) == 1
    assert js002[0].severity == "warn"
    assert JS001 not in _rules(findings)


# --- JS002: nondeterministic scoring ---------------------------------------
def test_judge_with_nonzero_temperature_is_js002():
    src = (
        "def evaluate(answer):\n"
        "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0.7,\n"
        "        messages=[{'role':'user','content':'Grade on a scale of 1-10 using this rubric: ' + answer}])\n"
        "    return judge\n"
    )
    findings = scan_source(src, "u.py")
    js002 = [f for f in findings if f.rule == JS002]
    assert len(js002) == 1
    assert "temperature" in js002[0].message
    assert JS001 not in _rules(findings)


def test_biased_judge_reports_all_three():
    # same model + no rubric + temp>0 -> JS001 + two JS002.
    src = (
        "def evaluate(q):\n"
        "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model='gpt-4o', temperature=0.9,\n"
        "        messages=[{'role':'user','content':'Rate this answer: ' + answer}])\n"
        "    return judge\n"
    )
    findings = scan_source(src, "u.py")
    assert JS001 in _rules(findings)
    assert len([f for f in findings if f.rule == JS002]) == 2


# --- negatives -------------------------------------------------------------
def test_different_family_judge_is_clean():
    src = (
        "def evaluate(q, ref):\n"
        "    gen = gen_client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0, max_tokens=256,\n"
        "        messages=[{'role':'user','content':'Grade on a scale of 1-10 using this rubric and reference: ' + ref + answer}])\n"
        "    return judge\n"
    )
    assert scan_source(src, "u.py") == []


def test_anchored_deterministic_same_family_still_js001_but_no_js002():
    # Even with a rubric and temp=0, a same-model self-judge is JS001.
    src = (
        "def evaluate(q):\n"
        "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model='gpt-4o', temperature=0,\n"
        "        messages=[{'role':'user','content':'Grade on a scale of 1-10 using this rubric: ' + answer}])\n"
        "    return judge\n"
    )
    findings = scan_source(src, "u.py")
    assert JS001 in _rules(findings)
    assert JS002 not in _rules(findings)


def test_generator_only_is_clean():
    # A plain generation call (no judge language, nothing consumes it) is fine.
    src = (
        "def run(q):\n"
        "    out = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    return out.choices[0].message.content\n"
    )
    assert scan_source(src, "u.py") == []


def test_non_llm_create_is_ignored():
    # Django-style .objects.create() — no anchor kwarg, never treated as a judge.
    src = (
        "def save(q):\n"
        "    Review.objects.create(text='please rate and score this', stars=5)\n"
    )
    assert scan_source(src, "u.py") == []


def test_judge_of_external_answer_is_not_js001():
    # The answer comes from outside (a fixture), not from an in-file generator.
    src = (
        "def evaluate(answer):\n"
        "    judge = client.chat.completions.create(model='gpt-4o', temperature=0,\n"
        "        messages=[{'role':'user','content':'Grade on a scale of 1-10 with this rubric: ' + answer}])\n"
        "    return judge\n"
    )
    assert JS001 not in _rules(scan_source(src, "u.py"))


def test_inline_ignore_suppresses_line():
    src = (
        "def evaluate(q):\n"
        "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
        "    answer = gen.choices[0].message.content\n"
        "    judge = client.chat.completions.create(model='gpt-4o', temperature=0, messages=[{'role':'user','content':'Rate: ' + answer}])  # judgeskew: ignore\n"
        "    return judge\n"
    )
    assert scan_source(src, "u.py") == []


def test_noqa_with_rule_suppresses_only_that_rule():
    src = (
        "def evaluate(answer):\n"
        "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0.7, messages=[{'role':'user','content':'Rate the quality: ' + answer}])  # noqa: JS002\n"
        "    return judge\n"
    )
    # Both JS002 findings are on that judge line and are suppressed.
    assert JS002 not in _rules(scan_source(src, "u.py"))


def test_unknown_extension_is_ignored():
    src = (
        "judge = client.chat.completions.create(model='gpt-4o',\n"
        "    messages=[{'role':'user','content':'rate this'}])\n"
    )
    assert scan_source(src, "notes.txt") == []
