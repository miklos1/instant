#!/bin/sh 

sh clean.sh 

for file in *.py; do
   echo "running test $file ";
   if [ $file != "test_omp.py" ]; then
     python $file;
   fi
   if [ $? != 0 ]; then
     exit 1
   fi
done
