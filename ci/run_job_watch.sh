#!/bin/sh
# Run one job_watch list. Used by the Jenkinsfile; also runnable by hand:
#   sh ci/run_job_watch.sh <banks|tech> <data dir> <report dir>
# Writes <report dir>/<list>.txt (the printed report) and <list>-latest.json (the newest
# snapshot), and exits with job_watch's status: 0 ok, 2 some source failed (snapshot
# still saved, previous results kept), anything else = the run itself failed.
set -u
if [ $# -ne 3 ]; then
    echo "usage: $0 <banks|tech> <data dir> <report dir>" >&2
    exit 64
fi
watch=$1 data_dir=$2 report_dir=$3
python=${PYTHON:-.venv/bin/python}

mkdir -p "$data_dir" "$report_dir"
report="$report_dir/$watch.txt"
"$python" -m week01_http.job_watch --watch "$watch" --data-dir "$data_dir" > "$report"
status=$?
cat "$report"

latest=$(ls -1 "$data_dir"/jobs-*.json 2>/dev/null | tail -n 1)
if [ -n "$latest" ]; then
    cp "$latest" "$report_dir/$watch-latest.json"
fi
exit $status
