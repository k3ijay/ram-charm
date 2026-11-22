#!/bin/bash
cd "$(dirname "$0")"

# RAM Charm (ram-charm) macOS 起動スクリプト
SCRIPT="ram_charm.py"

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    osascript -e 'display alert "RAM Charm" message "Python 3 が見つかりませんでした。Python 3 をインストールしてください。" as critical'
    exit 1
fi

exec "$PYTHON" "$SCRIPT" "$@"
