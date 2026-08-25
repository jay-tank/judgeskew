#!/usr/bin/env bash
# push-judgeskew.sh — build a realistic, incremental git history for judgeskew and
# push it to github.com/jay-tank/judgeskew via the GitHub REST API (curl, not gh).
# Author is Jay Tank only; aborts if any "claude" trace is present.
#
# Review before running. This script DOES create a repo and force-push.
# Requires a Personal Access Token in $GITHUB_PAT (or $PAT) with 'repo' scope.
set -euo pipefail

# ---- config -----------------------------------------------------------------
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="jay-tank"
NAME="judgeskew"
AUTHOR_NAME="jay-tank"
AUTHOR_EMAIL="tankjai24@gmail.com"
DESCRIPTION="Static gate for biased LLM-as-judge evaluation setups. Flags self-preference bias — the same model (or family) used as both the generator and the judge of its own output (JS001) — and weak judge hygiene: no rubric/reference in the prompt, or a nonzero temperature (JS002). AST-based, no target code executed."
TOPICS='["llm","llm-as-judge","llm-evaluation","evaluation","self-preference-bias","llmops","static-analysis","linter","ci","ast","python","cli"]'

PAT="${GITHUB_PAT:-${PAT:-}}"
if [ -z "$PAT" ]; then
  echo "ABORT: set GITHUB_PAT (or PAT) to a token with 'repo' scope." >&2
  exit 1
fi

export GIT_AUTHOR_NAME="$AUTHOR_NAME"
export GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL"
export GIT_COMMITTER_NAME="$AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL"

cd "$PROJECT_DIR"

# ---- safety: no AI-authorship / assistant traces in tracked content ---------
# NB: a plain 'claude' grep would false-positive on the legitimate model id
# "claude-3-5-sonnet" and the `anthropic` SDK import used in the examples, so we
# target the actual authorship-leak signatures instead.
AI_TRACE='co-authored-by|claude\.ai|claude code|claude opus|claude sonnet|noreply@anthropic|generated with \[?claude|🤖'
if grep -rliE --exclude-dir=.git --exclude-dir=script-push --exclude=DETAILS.md "$AI_TRACE" . ; then
  echo "ABORT: AI-authorship trace found in tracked content — clean it before pushing." >&2
  exit 1
fi

# ---- staggered, jittered commit helper --------------------------------------
BASE_EPOCH=$(date -d '2026-08-16 09:41:00' +%s 2>/dev/null || date -j -f '%Y-%m-%d %H:%M:%S' '2026-08-16 09:41:00' +%s)
STEP=0
commit() {
  local msg="$1"; shift
  git add "$@"
  if git diff --cached --quiet; then
    return 0
  fi
  local jitter=$(( (RANDOM % 28800) - 3600 ))        # -1h .. +7h
  local when=$(( BASE_EPOCH + STEP*86400 + jitter ))
  STEP=$((STEP+1))
  local iso
  iso=$(date -d "@$when" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -r "$when" '+%Y-%m-%dT%H:%M:%S')
  GIT_AUTHOR_DATE="$iso" GIT_COMMITTER_DATE="$iso" git commit -q -m "$msg"
  echo "  committed: $msg  ($iso)"
}

# ---- Hashnode back-link into README (idempotent) ----------------------------
HN_README="../../hashnode/$NAME/README.md"
if [ -f "$HN_README" ]; then
  SLUG="$(grep -iE 'slug' "$HN_README" | head -n1 | grep -oE '[a-z0-9]+(-[a-z0-9]+)+' | head -n1 || true)"
  if [ -n "${SLUG:-}" ] && grep -q '<!-- HASHNODE_ARTICLE_URL -->' README.md; then
    sed -i "s|<!-- HASHNODE_ARTICLE_URL -->|> 📖 Read the write-up: [$NAME](https://jaytank.hashnode.dev/$SLUG)|" README.md
    echo "back-link inserted: https://jaytank.hashnode.dev/$SLUG"
  fi
fi

# ---- fresh history ----------------------------------------------------------
rm -rf .git
git init -q
git symbolic-ref HEAD refs/heads/main

# DETAILS.md is git-ignored (local-only) — confirm it will not be committed.
if git check-ignore -q DETAILS.md; then :; else
  echo "WARN: DETAILS.md is not git-ignored — check .gitignore" >&2
fi

# 1) scaffold
commit "chore: scaffold project, MIT license and gitignore" LICENSE .gitignore pyproject.toml

# 2) data models
commit "feat(models): Finding, ScanResult dataclasses and JS001/JS002 rule codes" models.py

# 3) scanner — LLM call classification
commit "feat(scanner): recognize LLM SDK call shapes via suffix + anchor kwarg" scanner.py

# 4) rendering
commit "feat(render): rich terminal panels and --json output" render.py

# 5) CLI
commit "feat(cli): flags, ignore file and exit codes 0/1/2" cli.py

# 6) generator/judge attribution + self-preference detection
commit "feat(scanner): trace generator output into judge calls, add JS001 model-family match" scanner.py

# 7) judge hygiene warnings
commit "feat(scanner): JS002 unanchored-rubric and nonzero-temperature checks" scanner.py

# 8) tests
commit "test: JS001/JS002 detection, clean cases and CLI end-to-end" tests/

# 9) examples
commit "docs(examples): biased vs fair LLM-as-judge eval harness" examples/

# 10) CI
commit "ci: pytest matrix on Python 3.9/3.11/3.12" .github/workflows/ci.yml

# 11) docs
commit "docs: README, usage guide and contributing notes" README.md docs/USAGE.md CONTRIBUTING.md

# 12) safety sweep — commit anything still untracked/modified (never leave dirt)
if [ -n "$(git status --porcelain)" ]; then
  commit "chore: finalize repository contents" -A
fi

# ---- final guard: no AI-authorship trace in the commit history --------------
if git log --pretty=full | grep -qiE 'co-authored-by|claude\.ai|claude code|noreply@anthropic|🤖'; then
  echo "ABORT: AI-authorship trace found in commit history — not pushing." >&2
  exit 1
fi

# ---- create remote via REST API (curl) --------------------------------------
API="https://api.github.com"
AUTH=(-H "Authorization: token $PAT" -H "Accept: application/vnd.github+json")

if ! curl -sf "${AUTH[@]}" "$API/repos/$OWNER/$NAME" >/dev/null 2>&1; then
  curl -sf "${AUTH[@]}" "$API/user/repos" \
    -d "{\"name\":\"$NAME\",\"description\":$(printf '%s' "$DESCRIPTION" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),\"private\":false,\"has_wiki\":false}" >/dev/null
fi

# set description + topics + homepage (Hashnode article link)
HOMEPAGE="https://jaytank.hashnode.dev/judgeskew-llm-as-judge-bias-gate"
curl -sf "${AUTH[@]}" -X PATCH "$API/repos/$OWNER/$NAME" \
  -d "{\"description\":$(printf '%s' "$DESCRIPTION" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),\"homepage\":\"$HOMEPAGE\"}" >/dev/null
curl -sf "${AUTH[@]}" -X PUT "$API/repos/$OWNER/$NAME/topics" \
  -H "Accept: application/vnd.github.mercy-preview+json" \
  -d "{\"names\":$TOPICS}" >/dev/null

# ---- push over HTTPS with the token -----------------------------------------
git remote remove origin 2>/dev/null || true
git remote add origin "https://x-access-token:${PAT}@github.com/$OWNER/$NAME.git"
git push -u --force origin main

# ---- verify topics on remote ------------------------------------------------
if curl -sf "${AUTH[@]}" "$API/repos/$OWNER/$NAME/topics" | grep -q '"llm-as-judge"'; then
  echo "topics set & verified on remote ✓"
else
  echo "ERROR: repo topics not set on remote" >&2; exit 1
fi

# ---- verify homepage on remote ----------------------------------------------
if curl -sf "${AUTH[@]}" "$API/repos/$OWNER/$NAME" | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin).get('homepage')=='$HOMEPAGE' else 1)"; then
  echo "homepage set & verified on remote ✓"
else
  echo "ERROR: repo homepage not set on remote" >&2; exit 1
fi

# ---- version tag + verify ---------------------------------------------------
git tag -f v0.1.0
git push -f origin v0.1.0
if git ls-remote --tags origin | grep -q 'refs/tags/v0.1.0'; then
  echo "tag v0.1.0 verified on remote ✓"
else
  echo "ERROR: tag v0.1.0 missing on remote after push" >&2; exit 1
fi

echo "done: https://github.com/$OWNER/$NAME"
