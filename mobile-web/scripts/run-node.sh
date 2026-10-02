#!/bin/sh

node_bin="$(command -v node 2>/dev/null || true)"
node_major=""

if [ -n "$node_bin" ]; then
  node_major="$($node_bin -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
fi

if [ -z "$node_major" ] || [ "$node_major" -lt 20 ]; then
  newest_nvm_node="$(find "$HOME/.nvm/versions/node" -path '*/bin/node' -type f 2>/dev/null | sort -V | tail -n 1)"
  if [ -z "$newest_nvm_node" ]; then
    echo "需要 Node.js 20.19 或更高版本。请先安装新版 Node.js。" >&2
    exit 1
  fi
  PATH="$(dirname "$newest_nvm_node"):$PATH"
  export PATH
fi

exec "$@"
