#!/bin/sh
"$JQ_CLI" -e --arg u "$U" '
  def n: ascii_downcase
         | gsub("\\.git$"; "");
  (.data | map(.source | capture("(?<h>[a-z]+)://")))
' < in.json
