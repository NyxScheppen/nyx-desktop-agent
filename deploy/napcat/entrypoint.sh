#!/bin/bash
set -e

mkdir -p /app/napcat/config
jq --arg token "${ONEBOT_ACCESS_TOKEN:?ONEBOT_ACCESS_TOKEN is required}" \
    '.network.websocketServers[0].token = $token' \
    /app/templates/ws.json > /tmp/onebot11.json
ln -sfn /tmp/onebot11.json /app/napcat/config/onebot11.json
rm -f /app/napcat/config/webui.json

exec /app/entrypoint.sh
