#!/bin/sh
jq -r '.a | split("a+"; "g")' f.json
jq -r '.a | scan("x")' f.json
jq -r '.a | match("x")' f.json
jq -r '.a | [splits("x")]' f.json
