#!/bin/bash
# =============================================================================
#  Couchbase demo setup
#
#  This script runs ONCE inside a short-lived "init" container the first time
#  you run `docker compose up`. It turns a brand-new, empty Couchbase Server
#  into a ready-to-demo cluster. Everything here uses Couchbase's public REST
#  API, so you can read it as a worked example of how to automate Couchbase.
#
#  It is safe to run again: every step first checks whether it is already
#  done and skips itself if so.
# =============================================================================
set -euo pipefail

# --- Settings (all come from the .env file; nothing is hard-coded here) ------
CB="${CB_HOST:-couchbase}"
ADMIN="${CB_ADMIN_USER}"
ADMIN_PW="${CB_ADMIN_PASSWORD}"
BUCKET="${CB_BUCKET:-travel-sample}"
APP_USER="${CB_USER}"
APP_PW="${CB_PASSWORD}"

REST="http://${CB}:8091"     # Cluster manager (setup, users, buckets)
QUERY="http://${CB}:8093"    # Query service (SQL++)
SEARCH="http://${CB}:8094"   # Search service (Full Text Search)

SEARCH_INDEX_DIR="/init/search"
GSI_FILE="/init/indexes.n1ql"

# The Couchbase image ships curl and python3 (no jq), so we use python3
# for the small amount of JSON handling we need.
json_get() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

# --- Friendly logging --------------------------------------------------------
step() { echo; echo "[$1] $2"; }
info() { echo "      $*"; }
ok()   { echo "      ✔ $*"; }
fail() { echo "      ✘ $*" >&2; exit 1; }

# Run a command repeatedly until it succeeds, or give up after a timeout.
#   wait_for "<what we are waiting for>" <timeout-seconds> <command...>
wait_for() {
  local what="$1" timeout="$2"; shift 2
  local waited=0
  until "$@" >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout" ]; then
      fail "Timed out after ${timeout}s waiting for: ${what}. Run 'docker compose logs init couchbase' for details."
    fi
    sleep 3; waited=$((waited + 3))
    if [ $((waited % 15)) -eq 0 ]; then info "still waiting for ${what} (${waited}s)..."; fi
  done
}

# Run a SQL++ statement as the admin user and print the JSON response.
n1ql() {
  curl -s -u "${ADMIN}:${ADMIN_PW}" "${QUERY}/query/service" --data-urlencode "statement=$1"
}

echo "============================================================"
echo "  Couchbase demo setup"
echo "  This takes about 2-3 minutes the first time. Sit tight!"
echo "============================================================"

# -----------------------------------------------------------------------------
step "1/7" "Waiting for Couchbase Server to start"
# -----------------------------------------------------------------------------
wait_for "Couchbase REST API" 180 curl -sf "${REST}/ui/index.html"
ok "Couchbase is up at ${REST}"

# -----------------------------------------------------------------------------
step "2/7" "Initializing the cluster (services, memory, admin login)"
# -----------------------------------------------------------------------------
# If the admin credentials already work, a previous run configured this cluster.
if curl -sf -u "${ADMIN}:${ADMIN_PW}" "${REST}/pools/default" >/dev/null; then
  ok "Cluster is already initialized - skipping"
else
  # Couchbase is made of independent services. This single node runs all the
  # ones the demo needs:
  #   kv    = Data service    (fast key-value reads/writes)
  #   index = Index service   (powers secondary indexes for SQL++)
  #   n1ql  = Query service   (runs SQL++)
  #   fts   = Search service  (full-text and vector search)
  info "Enabling services: kv, index, n1ql, fts"
  curl -sf "${REST}/node/controller/setupServices" -d "services=kv%2Cindex%2Cn1ql%2Cfts" >/dev/null

  # Each service gets its own memory budget (in MB). See the README for how
  # these relate to the RAM you give Docker Desktop.
  info "Setting memory quotas: data=${CB_KV_QUOTA_MB}MB index=${CB_INDEX_QUOTA_MB}MB search=${CB_FTS_QUOTA_MB}MB"
  curl -sf "${REST}/pools/default" \
    -d "memoryQuota=${CB_KV_QUOTA_MB}" \
    -d "indexMemoryQuota=${CB_INDEX_QUOTA_MB}" \
    -d "ftsMemoryQuota=${CB_FTS_QUOTA_MB}" \
    -d "clusterName=${CB_CLUSTER_NAME:-travel-demo}" >/dev/null

  # "plasma" is the high-performance index storage engine (Enterprise Edition).
  curl -sf "${REST}/settings/indexes" -d "storageMode=plasma" >/dev/null

  # Finally, create the administrator account. After this call the REST API
  # requires a login.
  info "Creating the administrator account"
  curl -sf "${REST}/settings/web" \
    -d "port=8091" -d "username=${ADMIN}" --data-urlencode "password=${ADMIN_PW}" >/dev/null
  ok "Cluster initialized"
fi

# -----------------------------------------------------------------------------
step "3/7" "Loading the travel-sample data set (~60,000 documents)"
# -----------------------------------------------------------------------------
# Couchbase ships with sample data sets you can install with one REST call.
# travel-sample holds airlines, airports, routes, hotels, and landmarks.
if curl -sf -u "${ADMIN}:${ADMIN_PW}" "${REST}/pools/default/buckets/${BUCKET}" >/dev/null; then
  ok "Bucket '${BUCKET}' already exists - skipping the download"
else
  info "Asking Couchbase to install '${BUCKET}'..."
  resp=$(curl -s -u "${ADMIN}:${ADMIN_PW}" -X POST "${REST}/sampleBuckets/install" -d "[\"${BUCKET}\"]")
  info "Server replied: ${resp}"
fi

# The install runs in the background, so poll until the data is queryable.
# We consider it done when the hotel collection reports its expected size.
hotels_loaded() {
  local n
  n=$(n1ql "SELECT RAW COUNT(*) FROM \`${BUCKET}\`.inventory.hotel" | json_get "d['results'][0]")
  [ "${n:-0}" -ge 900 ]
}
wait_for "travel-sample to finish loading" 600 hotels_loaded
HOTEL_COUNT=$(n1ql "SELECT RAW COUNT(*) FROM \`${BUCKET}\`.inventory.hotel" | json_get "d['results'][0]")
ok "travel-sample is loaded (${HOTEL_COUNT} hotels, plus airlines, airports, routes, landmarks)"

# -----------------------------------------------------------------------------
step "4/7" "Creating a limited-privilege application user"
# -----------------------------------------------------------------------------
# The demos never log in as the administrator. Instead they use this user,
# who can ONLY work with the travel-sample bucket. This is Couchbase's
# role-based access control (RBAC) in action - give apps the least privilege
# they need:
#   data_reader / data_writer = read and write documents by key
#   query_select              = run SELECT statements (SQL++)
#   fts_searcher              = run Search (full-text and vector) queries
ROLES="data_reader[${BUCKET}],data_writer[${BUCKET}],query_select[${BUCKET}],fts_searcher[${BUCKET}]"
curl -sf -u "${ADMIN}:${ADMIN_PW}" -X PUT "${REST}/settings/rbac/users/local/${APP_USER}" \
  --data-urlencode "password=${APP_PW}" \
  --data-urlencode "roles=${ROLES}" \
  -d "name=Demo Application User" >/dev/null
ok "User '${APP_USER}' created with roles: ${ROLES}"

# -----------------------------------------------------------------------------
step "5/7" "Creating secondary (GSI) indexes for the SQL++ demos"
# -----------------------------------------------------------------------------
# Indexes make queries fast by letting Couchbase find matching documents
# without scanning every one. The statements live in init/indexes.n1ql so
# you can read and edit them. "IF NOT EXISTS" makes them safe to re-run.

# Indexes build in the background. This waits until every index on the bucket
# is ONLINE (ready to serve queries).
all_indexes_online() {
  local pending
  pending=$(n1ql "SELECT RAW COUNT(*) FROM system:indexes WHERE bucket_id = '${BUCKET}' AND state != 'online'" | json_get "d['results'][0]")
  [ "${pending}" = "0" ]
}

# travel-sample installs a handful of its own indexes. Let those finish first
# so our new ones are not created while the server is still busy building.
wait_for "the sample's built-in indexes to come online" 300 all_indexes_online

while IFS= read -r stmt; do
  case "$stmt" in ''|--*) continue ;; esac
  name=$(echo "$stmt" | sed -E 's/.*INDEX (IF NOT EXISTS )?`?([A-Za-z0-9_]+)`?.*/\2/')
  resp=$(n1ql "$stmt")
  status=$(echo "$resp" | json_get "d['status']" || echo "unknown")
  if [ "$status" = "success" ]; then
    info "index ${name}: ready (created now, or already there from a previous run)"
  elif echo "$resp" | grep -q "retry building in the background"; then
    # The server accepted the index and will finish building it on its own.
    info "index ${name}: queued (the server will build it in the background)"
  else
    fail "Could not create index ${name}: ${resp}"
  fi
done < "${GSI_FILE}"

# Now wait until our indexes are online too.
wait_for "all indexes to come online" 300 all_indexes_online
ok "All GSI indexes are online"

# -----------------------------------------------------------------------------
step "6/7" "Creating the Full Text Search index"
# -----------------------------------------------------------------------------
# Search indexes are described by JSON files kept in init/search/. We send
# each file to the Search service's REST API. Open the file to see how the
# hotel fields are analyzed - that is what makes fuzzy, phrase, and
# highlighted searches possible.
for def in "${SEARCH_INDEX_DIR}"/*.json; do
  [ -e "$def" ] || continue
  index_name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$def")
  url="${SEARCH}/api/bucket/${BUCKET}/scope/inventory/index/${index_name}"

  if curl -sf -u "${ADMIN}:${ADMIN_PW}" "${url}" >/dev/null; then
    ok "Search index '${index_name}' already exists - skipping"
  else
    info "Creating Search index '${index_name}' from $(basename "$def")"
    create_index() { curl -sf -u "${ADMIN}:${ADMIN_PW}" -X PUT -H "Content-Type: application/json" "${url}" -d @"$def"; }
    wait_for "the Search service to accept the index" 120 create_index
  fi

  # The index builds in the background. Wait until it has indexed every hotel
  # so a demo never queries a half-built index.
  index_ready() {
    local n
    n=$(curl -sf -u "${ADMIN}:${ADMIN_PW}" "${url}/count" | json_get "d['count']")
    [ "${n:-0}" -ge "${HOTEL_COUNT}" ]
  }
  wait_for "Search index '${index_name}' to finish indexing ${HOTEL_COUNT} hotels" 300 index_ready
  ok "Search index '${index_name}' is ready (${HOTEL_COUNT} hotels indexed)"
done

# -----------------------------------------------------------------------------
step "7/7" "Verifying the application user can connect"
# -----------------------------------------------------------------------------
check=$(curl -s -u "${APP_USER}:${APP_PW}" "${QUERY}/query/service" \
  --data-urlencode "statement=SELECT RAW name FROM \`${BUCKET}\`.inventory.airline USE KEYS 'airline_10'" \
  | json_get "d['results'][0]" || true)
[ -n "${check}" ] || fail "The '${APP_USER}' user could not run a query. Check the roles above."
ok "Logged in as '${APP_USER}' and read airline_10 = ${check}"

echo
echo "============================================================"
echo "  ✔ Couchbase is ready!"
echo
echo "  Web UI:    http://localhost:${CB_PORT_UI:-8091}"
echo "  Login:     ${ADMIN} / ${ADMIN_PW}"
echo "  Demo user: ${APP_USER} (limited to the ${BUCKET} bucket)"
echo
echo "  Explore the UI: Query tab (SQL++), Search tab (full-text/vector)."
echo "============================================================"
