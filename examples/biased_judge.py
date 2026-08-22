"""An LLM-as-judge eval where the same model grades its own output.

Run:  judgeskew examples/biased_judge.py   ->  exits 1 (JS001 blocker).

``gpt-4o`` generates the candidate answer, and then ``gpt-4o`` is asked to score
that same answer. A model systematically prefers text it produced (self-
preference bias), so the score comes back optimistic and measures brand loyalty,
not quality. On top of that, the judge call has no rubric and runs at a nonzero
temperature, so the scoring is also unanchored and nondeterministic (JS002).
"""

from openai import OpenAI

client = OpenAI()


def evaluate(question):
    gen = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}],
    )
    answer = gen.choices[0].message.content

    judge = client.chat.completions.create(
        model="gpt-4o",  # same model that wrote the answer — self-preference
        temperature=0.7,
        messages=[
            {"role": "system", "content": "You are a strict evaluator."},
            {"role": "user", "content": f"Rate the quality of this answer: {answer}"},
        ],
    )
    return judge.choices[0].message.content
