#!/usr/bin/env bash
# update_cmip6_site.sh
#
# Regenerates the CMIP6 explorer data files from the live archive and
# pushes the update to GitHub Pages. Meant to be run periodically from cron.
#
# One-time setup (do this once, by hand, before the first cron run):
#   1. git clone git@github.com:lamaliang/<your-repo>.git /home/<you>/cmip6_explorer_repo
#      (use the SSH remote, not https, so push can run non-interactively —
#       see the deploy-key notes at the bottom of this file)
#   2. Copy build_cmip6.py and cmor_lookup_full.json into that repo (or point
#      the paths below at wherever you keep them).
#   3. Edit the four variables below for your paths.
#   4. Test it once by hand:  bash update_cmip6_site.sh
#   5. Add it to cron (see bottom of this file).

set -euo pipefail

# ---- EDIT THESE FOUR PATHS ----
ARCHIVE_ROOT="/lfs/archive/CMIP6"                     # where the .nc files actually live
REPO_DIR="/home/lama/cmip6_explorer_repo"              # local clone of your GitHub repo
BUILD_SCRIPT="$REPO_DIR/build_cmip6.py"                # the parser script (keep a copy in the repo)
CMOR_LOOKUP="$REPO_DIR/cmor_lookup_full.json"          # the CMOR long_name/units lookup (keep a copy in the repo)
# --------------------------------

TS=$(date '+%Y-%m-%d %H:%M:%S')
FILELIST="$REPO_DIR/.filelist_latest.txt"
LOG="$REPO_DIR/.update_log.txt"

echo "[$TS] scanning $ARCHIVE_ROOT ..." | tee -a "$LOG"
find "$ARCHIVE_ROOT" -type f -name "*.nc" > "$FILELIST"
N=$(wc -l < "$FILELIST")
echo "[$TS] found $N files" | tee -a "$LOG"

echo "[$TS] rebuilding data/ ..." | tee -a "$LOG"
python3 "$BUILD_SCRIPT" "$FILELIST" "$REPO_DIR/data" "$CMOR_LOOKUP" | tee -a "$LOG"

cd "$REPO_DIR"
rm -f "$FILELIST"

if git status --porcelain | grep -q .; then
  git add -A
  git commit -m "auto-update CMIP6 index ($TS)" >> "$LOG" 2>&1
  git push origin main >> "$LOG" 2>&1
  echo "[$TS] pushed update." | tee -a "$LOG"
else
  echo "[$TS] no changes, nothing to push." | tee -a "$LOG"
fi

# ---------------------------------------------------------------------------
# CRON SETUP
#   Run once a week (full archive scans of 1M+ files take a while and this
#   dataset doesn't change every day — adjust to taste):
#
#     crontab -e
#     # then add, e.g. every Sunday at 03:00:
#     0 3 * * 0 /bin/bash /home/lama/cmip6_explorer_repo/update_cmip6_site.sh >> /home/lama/cmip6_update_cron.log 2>&1
#
# NON-INTERACTIVE GIT PUSH (do this once)
#   cron has no terminal, so `git push` must work without typing a password.
#   The clean way is an SSH *deploy key* scoped to just this one repo:
#
#     1. ssh-keygen -t ed25519 -f ~/.ssh/cmip6_deploy_key -N ""
#     2. On GitHub: repo -> Settings -> Deploy keys -> Add deploy key
#        - paste the contents of ~/.ssh/cmip6_deploy_key.pub
#        - check "Allow write access"
#     3. Make sure REPO_DIR was cloned with the SSH URL
#        (git@github.com:lamaliang/<repo>.git), then tell SSH to use this
#        key for github.com by adding to ~/.ssh/config:
#
#          Host github.com
#            IdentityFile ~/.ssh/cmip6_deploy_key
#            IdentitiesOnly yes
#
#   That's it — after this, `git push` from the script (or from cron) needs
#   no password prompt. A Personal Access Token in the remote URL also
#   works, but a deploy key is safer since it can only push to this repo.
# ---------------------------------------------------------------------------
