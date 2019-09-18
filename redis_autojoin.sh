#!/bin/bash
set -e

# Get autojoin configuration for Hipchat users from Redis. Script is best run on a Hipchat App node.

SITECONFIG=/hipchat/config/site.json
OUTPUT_FILE=./autojoin.json

# check if the redis configuration is stored directly in the site config
export RHOST=$(jq -r .redis[0].host $SITECONFIG)
if [ "$RHOST" != "null" ]; then
  export RPORT=$(jq -r .redis[0].port $SITECONFIG)
  export RPWD=$(jq -r .redis[0].auth $SITECONFIG)
else
  export PGHOST=$(jq -r '.databases.hipchat_postgres.servers[0]' $SITECONFIG | cut -d: -f1)
  export PGUSER=$(jq -r '.databases.hipchat_postgres.user' $SITECONFIG)
  export PGSCHEMA=$(jq -r '.databases.hipchat_postgres.schema' $SITECONFIG)
  export PGPASSWORD=$(jq -r '.databases.hipchat_postgres.pass' $SITECONFIG)

  export RHOST=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redishostname';")
  export RPORT=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redisport';")
  export RPWD=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redispass';")
fi

if [ "$RPWD" != "null" ]; then
  RPWD="-a $RPWD"
else
  RPWD=""
fi

echo "{\"autojoins\": [" > $OUTPUT_FILE
for key in $(redis-cli -h $RHOST -p $RPORT $RPWD KEYS '*' | grep  "pref:autoJoin:" | cut -d" " -f2); do
  echo "Found key: $key"
  VALUE=$(redis-cli -h $RHOST -p $RPORT $RPWD GET "$key")
  [[ ${#VALUE} -eq 4 && "$VALUE" -eq "None" ]] && VALUE="[]"
  USER_ID=$(echo $key | cut -d":" -f3)
  echo "{\"user_id\": $USER_ID, \"rooms\": $VALUE }," >> $OUTPUT_FILE
done
truncate -s-2 $OUTPUT_FILE # remove superfluous comma
echo "]}" >> $OUTPUT_FILE
