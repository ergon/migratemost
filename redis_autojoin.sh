#!/bin/bash
set -e

# Get autojoin configuration for Hipchat users from Redis. Script is best run on a Hipchat App node.

export PGHOST=$(cat /hipchat/config/site.json | jq -r '.databases.hipchat_postgres.servers[0]' | cut -d: -f1)
export PGUSER=$(cat /hipchat/config/site.json | jq -r '.databases.hipchat_postgres.user')
export PGSCHEMA=$(cat /hipchat/config/site.json | jq -r '.databases.hipchat_postgres.schema')
export PGPASSWORD=$(cat /hipchat/config/site.json | jq -r '.databases.hipchat_postgres.pass')

RHOST=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redishostname';")
RPORT=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redisport';")
RPWD=$(psql -h $PGHOST -U $PGUSER -d $PGSCHEMA -t -c "SELECT value FROM configurations WHERE key='redispass';")

OUTPUT_FILE=./autojoin.json
echo "{\"autojoins\": [" > $OUTPUT_FILE
for key in $(redis-cli -h $RHOST -p $RPORT -a $RPWD KEYS '*' | grep  "pref:autoJoin:" | cut -d" " -f2); do
  echo "Found key: $key"
  VALUE=$(redis-cli -h $RHOST -p $RPORT -a $RPWD GET "$key")
  [[ ${#VALUE} -eq 4 && "$VALUE" -eq "None" ]] && VALUE="[]"
  USER_ID=$(echo $key | cut -d":" -f3)
  echo "{\"user_id\": $USER_ID, \"rooms\": $VALUE }," >> $OUTPUT_FILE
done
truncate -s-2 $OUTPUT_FILE # remove superfluous comma
echo "]}" >> $OUTPUT_FILE
