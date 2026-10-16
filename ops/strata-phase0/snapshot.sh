#!/bin/zsh
# Strata Phase 0: record the per-user and system state the experiment must not change.
# usage: snapshot.sh <out-dir>     then: diff -r <before> <after>
# Read-only. Listings carry mtimes so a touched file shows up even when its name did not change.
set -u
OUT=$1
mkdir -p $OUT
lsm() { [[ -e $1 ]] && /usr/bin/find "$1" -maxdepth ${2:-2} -exec /usr/bin/stat -f '%m %z %N' {} + 2>/dev/null | /usr/bin/sort -k3; }
lsm ~/.config 3 > $OUT/home-config.txt
lsm ~/.cache 2 > $OUT/home-cache.txt
lsm ~/.local 3 > $OUT/home-local.txt
lsm ~/Library/LaunchAgents 1 > $OUT/launchagents-user.txt
lsm ~/Library/Preferences 1 > $OUT/preferences.txt
lsm ~/"Library/Application Support" 1 > $OUT/app-support.txt
lsm ~/Library/Caches 1 > $OUT/caches-user.txt
lsm /Library/LaunchAgents 1 > $OUT/launchagents-sys.txt
lsm /Library/LaunchDaemons 1 > $OUT/launchdaemons.txt
lsm /usr/local 2 > $OUT/usr-local.txt
for f in ~/.zshrc ~/.zprofile ~/.bashrc ~/.bash_profile ~/.profile; do
  [[ -e $f ]] && /sbin/md5 -r $f
done > $OUT/shell-rc.txt
if [[ -x /opt/homebrew/bin/brew ]]; then /opt/homebrew/bin/brew list --versions > $OUT/brew.txt 2>&1; fi
crontab -l > $OUT/crontab.txt 2>&1
pmset -g > $OUT/pmset.txt 2>&1
sysctl iogpu kern.memorystatus_vm_pressure_level 2>/dev/null | grep -v pressure_level > $OUT/sysctl.txt
launchctl list 2>/dev/null | awk '{print $3}' | sort > $OUT/launchctl.txt
ls /Volumes/Models > $OUT/models-volume.txt
xcode-select -p > $OUT/xcode-select.txt 2>&1
print "snapshot written to $OUT"
