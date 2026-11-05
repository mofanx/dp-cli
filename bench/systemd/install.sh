#!/bin/bash
# dpx-taskpool systemd unit 安装——service 入库防手工改丢
set -e
DST="$HOME/.config/systemd/user/dpx-taskpool.service"
cp "$(dirname "$0")/dpx-taskpool.service" "$DST"
systemctl --user daemon-reload
echo "installed → $DST"
