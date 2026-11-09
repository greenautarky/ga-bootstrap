#!/bin/sh
if "$JQ_CLI" -e 'sub("a"; "b")' < in.json; then :; fi
