#!/bin/bash
# Excerpt from the private Pink Anchor repository.
# Trimmed for readability.
#
# Monthly disaster-recovery drill (cron, 1st of each month).
# Pulls the most recent daily off-site backup back down from the off-site store
# , then verifies every piece can actually be restored.
# Read-only against production. All pass -> clean up, exit 0.
# Any failure -> keep the work directory for inspection, exit 1, send an alert.
# Not covered: it does not start containers from the restored data.
set -o pipefail

APP_DIR="${APP_DIR:?}"                         # project root
MANIFEST="$APP_DIR/backup/last_manifest.json"  # written by the daily backup job
DRILL="${DRILL_DIR:-/tmp/dr-drill}"
PASS=0; FAIL=0
ok()  { echo "  PASS $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

[ -s "$MANIFEST" ] || { echo "manifest missing"; exit 1; }
rm -rf "$DRILL"; mkdir -p "$DRILL/extract"

# -- 0. the backup job itself reported no failures --
[ "$(jq -r '.failed | length' "$MANIFEST")" -eq 0 ] && ok "manifest has no failed items" \
                                                    || bad "manifest lists failed items"

# -- 1. download every item; size must match the manifest --
while IFS=$'\t' read -r NAME FID FNAME SIZE; do
  fetch_backup_item "$FID" "$DRILL/$FNAME" || { bad "$NAME: download failed"; continue; }   # store client, credentials from env
  GOT=$(stat -c %s "$DRILL/$FNAME" 2>/dev/null || echo 0)
  [ "$GOT" = "$SIZE" ] && ok "$NAME downloaded (${GOT}B)" || bad "$NAME size $GOT != $SIZE"
done < <(jq -r '.items[] | [.name, .file_id, (.file_name // .name), (.size|tostring)] | @tsv' "$MANIFEST")

# -- 2. main database: integrity, row count vs live, schema present --
MAINGZ=$(ls "$DRILL"/main.db.*.gz 2>/dev/null | head -1)
if [ -z "$MAINGZ" ]; then bad "main db missing"; else
  gunzip -kf "$MAINGZ"; DB="${MAINGZ%.gz}"
  IC=$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>&1)
  [ "$IC" = "ok" ] && ok "integrity_check = ok" || bad "integrity_check: $IC"
  Q="SELECT count(*) FROM memories WHERE deleted_at IS NULL LIMIT 1;"
  RESTORED=$(sqlite3 "$DB" "$Q")
  LIVE=$(sqlite3 "file:$APP_DIR/db/main.db?mode=ro" "$Q")   # live db opened read-only
  DIFF=$(( RESTORED > LIVE ? RESTORED - LIVE : LIVE - RESTORED ))
  [ "$DIFF" -le 3 ] && ok "row count restored=$RESTORED live=$LIVE" \
                    || bad "row count drift restored=$RESTORED live=$LIVE"
  [ "$(sqlite3 "$DB" ".tables" | wc -w)" -ge 10 ] && ok "table count" || bad "tables missing"
fi

# -- 3. directory archives are readable --
for n in app_data scripts signals pages; do
  T=$(ls "$DRILL/$n".*.tar.gz 2>/dev/null | head -1)
  [ -n "$T" ] && [ "$(tar tzf "$T" 2>/dev/null | wc -l)" -ge 1 ] && ok "$n archive" || bad "$n archive"
done

echo "=== RESULT: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] && { rm -rf "$DRILL"; exit 0; } || exit 1
