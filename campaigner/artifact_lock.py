"""Bounded cross-process lock for all proposal writers on this host."""
from contextlib import contextmanager
import os,time

@contextmanager
def artifact_lock(path):
 with open(path,'a+b') as file:
  file.seek(0,2)
  if not file.tell():file.write(b'0');file.flush()
  deadline=time.monotonic()+5
  while True:
   try:
    file.seek(0)
    if os.name=='nt':
     import msvcrt
     msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
    else:
     import fcntl
     fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    break
   except OSError:
    if time.monotonic()>deadline:raise ValueError('Proposal is being updated') from None
    time.sleep(.02)
  try:yield
  finally:
   file.seek(0)
   if os.name=='nt':msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(file.fileno(),fcntl.LOCK_UN)
