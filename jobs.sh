#!/bin/bash
# vim: set et ts=3 sw=3 :

set -o nounset
set -o pipefail

# each line in jobs.conf is a space-separated list where the first
# item is the content view to be published, and the other items are
# lifecycle environments to be promoted (each of which should be
# using said content view)
jobsconf='jobs.conf'

# number of tries, and time to wait between (seconds)
ntry=3
delay=300

# logging level for the Python script
loglevel='WARNING'


function error () {
   local msg="$1"
   echo "ERROR: ${msg}" >&2
   exit 1
}

function warn () {
   local msg="$1"
   echo "WARNING: ${msg}" >&2
}

function retry () {
   local rc
   local try=1
   while true; do
      "$@"
      rc=$?
      [[ $rc -eq 0 ]] && return 0
      (( try += 1 ))
      [[ $try -gt $ntry ]] && break
      warn "running '${1}' failed (rc=${rc}), will retry in ${delay} seconds..."
      sleep "${delay}"
   done
   warn "running '${1}' failed (rc=${rc}), giving up"
   return 1
}

function run_tasks () {
   # first task is the content view
   retry ./rhelsat-automate.py publish --log-level "${loglevel}" --wait "$1"
   if [[ $? -ne 0 ]]; then
      warn "failed to publish content view '$1', skipping dependent tasks"
      return 1
   fi
   shift
   # remaining tasks are lifecycle environments
   for le in "$@"; do
      retry ./rhelsat-automate.py promote --log-level "${loglevel}" --wait "$le"
      if [[ $? -ne 0 ]]; then
         warn "failed to promote lifecycle environment '$le', skipping dependent tasks"
         return 1
      fi
   done
   return 0
}


scriptdir="$(dirname "$(realpath "$0")")"
[[ -d "${scriptdir}" ]] \
|| error "no such directory '${scriptdir}'"
cd "${scriptdir}"

[[ -r "${jobsconf}" ]] \
|| error "configuration file '${jobsconf}' not present"

. venv/bin/activate \
|| error "failed to activate Python venv"

maxrc=0
while read -a tasks; do
   run_tasks "${tasks[@]}" </dev/null
   rc=$?
   [[ $rc -gt $maxrc ]] && maxrc=$rc
done < "${jobsconf}"
exit $maxrc
