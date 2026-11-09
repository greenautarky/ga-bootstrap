#!/bin/sh
x && "$JQ_CLI" -e 'scan("a")' < in.json
