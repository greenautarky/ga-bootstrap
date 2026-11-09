#!/bin/sh
if ! "$JQ_CLI" -e '.a | ltrimstr("x")' < in.json; then :; fi
x="$("$JQ_CLI" -r "type" < in.json)"
