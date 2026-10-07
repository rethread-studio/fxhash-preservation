#!/usr/bin/env bash
# Keep the tiramisu -> rootmab tunnel to rootmab's kubo gateway (127.0.0.1:8086) up on
# local port 28086. The key ~/.ssh/fxhash-ipfs-tunnel is authorized on rootmab with
# restrict,port-forwarding,permitopen="127.0.0.1:8086",command="/bin/false": it can
# open this one forward and nothing else. Run in tmux.
# autossh restarts ssh after a drop; -M 0: liveness via ServerAlive* instead of a
# monitor port; AUTOSSH_GATETIME=0: also retry when the very first connection fails.
# The outer loop covers what autossh deliberately gives up on (ssh killed by a signal).
set -u
export AUTOSSH_GATETIME=0
while true; do
    autossh -M 0 -N -i "$HOME/.ssh/fxhash-ipfs-tunnel" -o IdentitiesOnly=yes \
        -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 -L 28086:127.0.0.1:8086 martin@ns334032.ip-37-187-127.eu
    echo "$(date '+%F %T') autossh exited ($?), restarting in 10 s" >&2
    sleep 10
done
