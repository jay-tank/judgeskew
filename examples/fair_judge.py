"""The same eval, done fairly: an independent judge, anchored and deterministic.

Run:  judgeskew examples/fair_judge.py   ->  exits 0 (clean).

The candidate answer is produced by ``gpt-4o``, but the judge is a DIFFERENT
model family (``claude-3-5-sonnet``), so there is no self-preference bias. The
judge prompt carries an explicit rubric and a reference answer, and it runs at
temperature=0, so the scoring is anchored and reproducible.
"""

from anthropic import Anthropic
from openai import OpenAI

gen_client = OpenAI()
judge_client = Anthropic()


def evaluate(question, reference):
    gen = gen_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}],
    )
    answer = gen.choices[0].message.content

    judge = judge_client.messages.create(
        model="claude-3-5-sonnet-20241022",  # independent judge, different family
        max_tokens=512,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": (
                    "Grade the answer on a scale of 1-10 using this rubric: "
                    "correctness, completeness, and clarity. "
                    f"Reference answer: {reference}\n"
                    f"Answer to grade: {answer}"
                ),
            }
        ],
    )
    return judge.content[0].text
