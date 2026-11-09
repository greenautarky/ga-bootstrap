#!/bin/sh
"$JQ_CLI" -e '.a' < in.json
xargs "$JQ_CLI" -e '.a' < in.json
