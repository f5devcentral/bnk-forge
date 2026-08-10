#!/bin/sh
# Inject runtime brand into /usr/share/nginx/html/config.js.
# Only the literal value "f5" is accepted; everything else → "forge".
set -e

if [ "${BRAND}" = "f5" ]; then
  brand="f5"
else
  brand="forge"
fi

printf 'window.__BRAND__ = "%s";\n' "${brand}" \
  > /usr/share/nginx/html/config.js
