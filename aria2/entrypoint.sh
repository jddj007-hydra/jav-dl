#!/bin/sh
set -e
mkdir -p /data /downloads
touch /data/aria2.session
exec aria2c --conf-path=/etc/aria2.conf --rpc-secret="${ARIA2_SECRET:-jav-dl-rpc}"
