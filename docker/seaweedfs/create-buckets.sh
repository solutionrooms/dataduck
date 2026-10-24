#!/bin/sh
# Creates the platform buckets. Safe to re-run.
set -eu
for bucket in ${DBK_BUCKETS}; do
  if echo "s3.bucket.list" | weed shell -master=seaweedfs:9333 2>/dev/null | grep -qw "${bucket}"; then
    echo "bucket ${bucket} exists"
  else
    echo "s3.bucket.create -name ${bucket}" | weed shell -master=seaweedfs:9333
    echo "bucket ${bucket} created"
  fi
done
