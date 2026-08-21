import json

from cli import main

_BIASED = (
    "def evaluate(q):\n"
    "    gen = client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
    "    answer = gen.choices[0].message.content\n"
    "    judge = client.chat.completions.create(model='gpt-4o', temperature=0,\n"
    "        messages=[{'role':'user','content':'Rate on a scale of 1-5 using this rubric: ' + answer}])\n"
    "    return judge\n"
)
_CLEAN = (
    "def evaluate(q, ref):\n"
    "    gen = gen_client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':q}])\n"
    "    answer = gen.choices[0].message.content\n"
    "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0, max_tokens=256,\n"
    "        messages=[{'role':'user','content':'Grade 1-10 with this rubric and reference: ' + ref + answer}])\n"
    "    return judge\n"
)
_WARN_ONLY = (
    "def evaluate(answer):\n"
    "    judge = judge_client.messages.create(model='claude-3-5-sonnet', temperature=0,\n"
    "        messages=[{'role':'user','content':'Rate the quality of this answer: ' + answer}])\n"
    "    return judge\n"
)


def run(args, capsys):
    code = main(args)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_exit_1_on_blocker(capsys, tmp_path):
    f = tmp_path / "bad.py"
    f.write_text(_BIASED)
    code, out, err = run([str(f), "--no-color"], capsys)
    assert code == 1
    assert "BLOCKER" in out
    assert "JS001" in out


def test_exit_0_on_clean_file(capsys, tmp_path):
    f = tmp_path / "good.py"
    f.write_text(_CLEAN)
    code, out, err = run([str(f), "--no-color"], capsys)
    assert code == 0
    assert "no biased LLM-as-judge setup" in out


def test_exit_2_when_no_paths(capsys):
    code, out, err = run([], capsys)
    assert code == 2
    assert "no paths given" in err


def test_exit_2_when_no_source_files(capsys, tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    code, out, err = run([str(tmp_path)], capsys)
    assert code == 2
    assert "no scannable source" in err


def test_json_output_shape(capsys, tmp_path):
    f = tmp_path / "bad.py"
    f.write_text(_BIASED)
    code, out, err = run([str(f), "--json"], capsys)
    assert code == 1
    data = json.loads(out)
    assert data["files_scanned"] == 1
    assert data["summary"]["total"] == len(data["findings"])
    assert data["summary"]["blockers"] >= 1
    first = data["findings"][0]
    assert {"file", "line", "col", "rule", "severity", "message", "fix"} <= set(first)


def test_warning_only_exits_0_without_strict_and_1_with_strict(capsys, tmp_path):
    f = tmp_path / "warn.py"
    f.write_text(_WARN_ONLY)
    code, out, err = run([str(f), "--no-color"], capsys)
    assert code == 0  # a JS002 warning alone does not fail by default
    code, out, err = run([str(f), "--no-color", "--strict"], capsys)
    assert code == 1  # --strict promotes warnings to a failure


def test_exclude_glob_skips_paths(capsys, tmp_path):
    (tmp_path / "bad.py").write_text(_BIASED)
    code, out, err = run([str(tmp_path), "--exclude", "*bad.py"], capsys)
    assert code == 2
    assert "no scannable source" in err


def test_directory_scanned_recursively_skips_node_modules(capsys, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(_BIASED)
    vend = tmp_path / "node_modules"
    vend.mkdir()
    (vend / "b.py").write_text(_BIASED)
    code, out, err = run([str(tmp_path), "--json"], capsys)
    assert code == 1
    data = json.loads(out)
    assert data["files_scanned"] == 1  # node_modules skipped


def test_syntax_error_file_is_skipped_not_crashed(capsys, tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def evaluate(q)\n    return 1\n")  # missing colon
    code, out, err = run([str(f)], capsys)
    assert code == 2
    assert "could not parse" in err


def test_bundled_examples_biased_and_fair(capsys):
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    biased = os.path.join(root, "examples", "biased_judge.py")
    fair = os.path.join(root, "examples", "fair_judge.py")
    assert run([biased, "--json"], capsys)[0] == 1
    assert run([fair, "--no-color"], capsys)[0] == 0
