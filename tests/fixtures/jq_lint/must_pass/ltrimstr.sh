#!/bin/sh
"$JQ_CLI" -e --arg url "$U" '
  # a comment mentioning sub(...) and test(...) must not trip the lint
  def n: ascii_downcase | ltrimstr("https://") | rtrimstr("/") | split("://") | join("/");
  .data | map(.source | n) | any(. == $url)
' < in.json
printf '%s' "$j" | jq -r '.a | tostring'
echo "sub(x) in shell text outside any jq program is fine"
