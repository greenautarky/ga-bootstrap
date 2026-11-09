#!/bin/sh
printf '%s' "$x" | jq -r 'if test("^a") then 1 else 0 end'
