#!/bin/sh
"$JQ_CLI" -e "sub(\"a\"; \"b\")" < in.json
