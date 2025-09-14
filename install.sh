#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"


if [ -z $1 ]; then
    echo "No installation path provided"
    exit 1
fi

if [ ! -d "$1" ]; then
    echo "Provided path is not a directory"
    exit 1
fi

if python3 -c "import async_fs_utils; import async_process_utils" > /dev/null; then
    echo "Dependencies are already installed."
else
    read -p "Automatically install https://github.com/etienne-lelouet/my_python_utils in ${HOME}/.local/lib/python/ ? (y/n)" -n 1 -r
    echo    # (optional) move to a new line
    if [[ $REPLY =~ ^[Yy]$ ]]; then
	git clone https://github.com/etienne-lelouet/my_python_utils /tmp/my_python_utils/
	/tmp/my_python_utils/install.sh ${HOME}/.local/lib/python/
    fi
fi

echo "Installing to $1"

cp "${SCRIPT_DIR}/src/launch_vms.py" "$1/launch_vms"

echo "Testing installation..."
if ! [ -x $(which launch_vms) ]; then
	echo "launch_vms is not in your PATH"
	exit 1
fi

echo "Installation succeeded."
