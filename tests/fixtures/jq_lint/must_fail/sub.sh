#!/bin/sh
echo '"A/"' | "$JQ_CLI" -e 'sub("/+$"; "")'
