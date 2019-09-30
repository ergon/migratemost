#!/bin/bash

# path to all the source directories (contents of the extracted hipchat tarballs)
# newest directory should be at the end
SOURCEDIR="/persistent/hipchat/2016 /persistent/hipchat/2017 /persistent/hipchat/2018 /persistent/hipchat/2019 /persistent/hipchat/2019-09"

# path to the destination directory that will hold all the merged data
TARGETDIR="/persistent/hipchat/overall"

LOG="$TARGETDIR/merge.log"

# create target directory if it does not exist
if ! test -d "$TARGETDIR"; then
       echo -n "Creating target directory $TARGETDIR..."
       if ! mkdir -p "$TARGETDIR"; then
               echo "failed."
               exit 1
       fi
       echo "done."
fi

# empty the logfile
echo "Merge started at $(date)" > $LOG

# merge room and user structure as well as attachments (but not the posts itself)
for sdir in $SOURCEDIR; do
  if ! test -d $sdir; then
    echo "sdir does not exist, exiting"
    exit 1
  fi
  echo -n "Syncing structure and attachments from $sdir to $TARGETDIR..."
  rsync --exclude /history.json -av $sdir/ $TARGETDIR/ >> $LOG 2>&1
  echo "done."
done

# copy user and room metadata from the most recent part (only the most recent information is relevant)
echo -n "Copying $sdir/users.json to $TARGETDIR..."
if ! cp -f $sdir/users.json $TARGETDIR; then
        echo "failed."
        exit 1
fi
echo "done."
echo -n "Copying $sdir/rooms.json to $TARGETDIR..."
if ! cp -f $sdir/rooms.json $TARGETDIR; then
        echo "failed."
        exit 1
fi
echo "done."

# merge rooms and user history
for type in rooms users; do
  for dir in $TARGETDIR/$type/*; do
    ID=$(basename $dir)
    SOURCEFILES=""
    for sdir in $SOURCEDIR; do
      needle=$sdir/$type/$ID/history.json
      test -f $needle && SOURCEFILES="$SOURCEFILES $needle"
    done
    if [ "$SOURCEFILES" != "" ]; then
      echo -n "Merge history ($type/$ID)..."
      if ! jq -s add $SOURCEFILES > $TARGETDIR/$type/$ID/history.json; then
            echo "failed."
      else
            echo "done."
      fi
    fi
  done
done

echo "Merge finished at $(date)" >> $LOG
