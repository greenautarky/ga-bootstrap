#!/bin/sh
# jq 'sub(x)' in a shell comment is not a program
"$JQ_CLI" -e --arg a 'x' --argjson b '1' '.data | any(. == $a)' < in.json
