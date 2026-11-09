#!/bin/sh
env "$JQ_CLI" -e 'gsub("a"; "b")' < in.json
