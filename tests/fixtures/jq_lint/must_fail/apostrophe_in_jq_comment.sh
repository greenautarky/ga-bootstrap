#!/bin/sh
# the apostrophe in the jq comment closes the shell quote early
"$JQ_CLI" -e '
  # don't do this
  sub("a"; "b")
' < in.json
