#!/bin/sh
# Writes the S3 identity file from env, then starts an all-in-one SeaweedFS server.
set -eu
cat > /tmp/s3.json <<JSON
{
  "identities": [
    {
      "name": "duckbricks",
      "credentials": [{"accessKey": "${DBK_S3_ACCESS_KEY}", "secretKey": "${DBK_S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "List", "Tagging", "Write"]
    }
  ]
}
JSON
exec weed server -dir=/data -ip=seaweedfs -ip.bind=0.0.0.0 -volume.max=0 \
  -master.volumeSizeLimitMB=1024 -s3 -s3.port=8333 -s3.config=/tmp/s3.json
