#!/bin/sh
while ! "$JQ_CLI" -e 'test("a")' < in.json; do :; done
