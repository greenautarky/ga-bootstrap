#!/bin/sh
# --arg's value 'x' must not be taken as the program
"$JQ_CLI" -e --arg a 'x' 'sub("a"; "b")' < in.json
