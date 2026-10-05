#!/usr/bin/env bash
# Review Radar: push secrets to the two Vercel projects, write local .env files, run migrations.
# Run from Git Bash (not PowerShell: its pipes mangle stdin for `vercel env add`):
#   bash scripts/setup-env.sh
# Reads (never prints):
#   G:\AI Engineering Journey\.env                                      GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
#   G:\Vibe Engineering Apps\.secrets\review-radar-database-url.txt          pooled Neon URL
#   G:\Vibe Engineering Apps\.secrets\review-radar-database-direct-url.txt   direct Neon URL (migrations)
#   G:\Vibe Engineering Apps\.secrets\review-radar-cron-secret.txt
#   G:\Vibe Engineering Apps\.secrets\docpilot-rn-voyage-key.txt          Voyage key (shared with DocPilot)
#   optional, generated when missing: review-radar-app-encryption-key.txt (openssl rand -base64 32),
#                                     review-radar-operator-token.txt (openssl rand -hex 24),
#                                     review-radar-ip-hash-salt.txt
#   optional: review-radar-github-token.txt (fine-grained PAT, Issues: write on
#             saad-official/review-radar-demo-issues), review-radar-qstash-token.txt,
#             review-radar-langfuse-public.txt / -secret.txt
# Vercel projects: review-radar-api (repo root, Python) and review-radar (root directory web/).
set -euo pipefail
export PATH="/c/tools/node24:$PATH"
cd "$(dirname "$0")/.."

SECRETS="/g/Vibe Engineering Apps/.secrets"
JOURNEY_ENV="/g/AI Engineering Journey/.env"
API_URL="https://review-radar-api.vercel.app"
WEB_URL="https://getreviewradar.vercel.app"
# Embeddings: Voyage voyage-4-lite at 1024-d (docs/decisions/0005-voyage-embeddings.md).
# The dimension is also rendered into the migrations by `uv run migrate` at the end.
EMBED_PROVIDER="voyage"
EMBED_DIMENSIONS="1024"

read_env() { grep -E "^\s*$2\s*=" "$1" | head -1 | sed -E "s/^\s*$2\s*=\s*//; s/^[\"']//; s/[\"']\s*$//" | tr -d '\r'; }
read_secret() { tr -d '\r\n' < "$1"; }
opt_secret() { [ -s "$1" ] && read_secret "$1" || true; }
ensure_secret() { # file generator-command...
  local file="$1"; shift
  [ -s "$file" ] || { "$@" | tr -d '\r\n' > "$file"; echo "  generated $(basename "$file")"; }
}
set_env() { # dir name value [--sensitive]
  local dir="$1" name="$2" value="$3" flag="${4:-}"
  [ -n "$value" ] || { echo "  skip $name (empty)"; return 0; }
  for target in production preview development; do
    local ok=0
    for attempt in 1 2 3 4; do
      if printf '%s' "$value" | (cd "$dir" && vercel env add "$name" "$target" --force $flag >/dev/null 2>&1); then ok=1; break; fi
      sleep $((5 * attempt))
    done
    [ "$ok" = 1 ] || { echo "FAILED: vercel env add $name $target in $dir"; exit 1; }
  done
  echo "  set $name ($dir)"
}

echo "Collecting values..."
ensure_secret "$SECRETS/review-radar-app-encryption-key.txt" openssl rand -base64 32
ensure_secret "$SECRETS/review-radar-operator-token.txt" openssl rand -hex 24
ensure_secret "$SECRETS/review-radar-ip-hash-salt.txt" openssl rand -hex 16
GEMINI=$(read_env "$JOURNEY_ENV" GEMINI_API_KEY)
GROQ=$(read_env "$JOURNEY_ENV" GROQ_API_KEY)
OPENROUTER=$(read_env "$JOURNEY_ENV" OPENROUTER_API_KEY)
CRON=$(read_secret "$SECRETS/review-radar-cron-secret.txt")
VOYAGE=$(read_secret "$SECRETS/docpilot-rn-voyage-key.txt")
DB_URL=$(read_secret "$SECRETS/review-radar-database-url.txt")
DIRECT_URL=$(read_secret "$SECRETS/review-radar-database-direct-url.txt")
case "$DB_URL" in postgres*) ;; *) echo "database url must be postgres://"; exit 1;; esac
ENC_KEY=$(read_secret "$SECRETS/review-radar-app-encryption-key.txt")
OPERATOR=$(read_secret "$SECRETS/review-radar-operator-token.txt")
SALT=$(read_secret "$SECRETS/review-radar-ip-hash-salt.txt")
GH_TOKEN_VAL=$(opt_secret "$SECRETS/review-radar-github-token.txt")
QSTASH=$(opt_secret "$SECRETS/review-radar-qstash-token.txt")
LF_PUBLIC=$(opt_secret "$SECRETS/review-radar-langfuse-public.txt")
LF_SECRET=$(opt_secret "$SECRETS/review-radar-langfuse-secret.txt")

echo "Pushing API env (project review-radar-api, repo root)..."
set_env . DATABASE_URL "$DB_URL" --sensitive
set_env . GROQ_API_KEY "$GROQ" --sensitive
set_env . GEMINI_API_KEY "$GEMINI" --sensitive
set_env . OPENROUTER_API_KEY "$OPENROUTER" --sensitive
set_env . VOYAGE_API_KEY "$VOYAGE" --sensitive
set_env . EMBEDDING_PROVIDER "$EMBED_PROVIDER"
set_env . EMBEDDING_DIMENSIONS "$EMBED_DIMENSIONS"
set_env . CRON_SECRET "$CRON" --sensitive
set_env . OPERATOR_TOKEN "$OPERATOR" --sensitive
set_env . APP_ENCRYPTION_KEY "$ENC_KEY" --sensitive
set_env . IP_HASH_SALT "$SALT" --sensitive
set_env . GITHUB_TOKEN "$GH_TOKEN_VAL" --sensitive
set_env . QSTASH_TOKEN "$QSTASH" --sensitive
set_env . LANGFUSE_PUBLIC_KEY "$LF_PUBLIC"
set_env . LANGFUSE_SECRET_KEY "$LF_SECRET" --sensitive
set_env . WEB_ORIGIN "$WEB_URL,http://localhost:3800,http://localhost:3600,http://localhost:3000"
set_env . PUBLIC_API_URL "$API_URL"

echo "Pushing web env (project review-radar, root dir web/)..."
set_env web NEXT_PUBLIC_API_URL "$API_URL"
set_env web NEXT_PUBLIC_APP_URL "$WEB_URL"

echo "Writing .env (API) and web/.env.local..."
cat > .env <<EOF
DATABASE_URL=$DB_URL
DATABASE_DIRECT_URL=$DIRECT_URL
GROQ_API_KEY=$GROQ
GEMINI_API_KEY=$GEMINI
OPENROUTER_API_KEY=$OPENROUTER
VOYAGE_API_KEY=$VOYAGE
EMBEDDING_PROVIDER=$EMBED_PROVIDER
EMBEDDING_DIMENSIONS=$EMBED_DIMENSIONS
CRON_SECRET=$CRON
OPERATOR_TOKEN=$OPERATOR
APP_ENCRYPTION_KEY=$ENC_KEY
IP_HASH_SALT=$SALT
GITHUB_TOKEN=$GH_TOKEN_VAL
QSTASH_TOKEN=$QSTASH
LANGFUSE_PUBLIC_KEY=$LF_PUBLIC
LANGFUSE_SECRET_KEY=$LF_SECRET
WEB_ORIGIN=http://localhost:3800,http://localhost:3600,http://localhost:3000
PUBLIC_API_URL=http://localhost:7860
PORT=7860
EOF
cat > web/.env.local <<EOF
NEXT_PUBLIC_API_URL=http://localhost:7860
NEXT_PUBLIC_APP_URL=http://localhost:3800
EOF

echo "Running database migrations (direct URL)..."
uv run migrate 2>&1 | tail -3
echo "Done. The operator token is in $SECRETS/review-radar-operator-token.txt (paste it into the web UI)."
echo "Deploy with: vercel deploy --prod --yes   (repo root = API)   and   (cd web && vercel deploy --prod --yes)"
