import sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from work_products import WorkProducts

class WorkProductTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.key=b'x'*32;self.store=WorkProducts(self.temp.name,self.key)
 def tearDown(self):self.temp.cleanup()
 def test_replay_retains_exact_first_artifact_without_authority(self):
  self.assertTrue(self.store.save('experiment','a'*64,{'prepared_at':'first','published':False})['new'])
  self.assertFalse(self.store.save('experiment','a'*64,{'prepared_at':'later','published':False})['new'])
  saved=WorkProducts(self.temp.name,self.key).latest('experiment');self.assertEqual('first',saved['payload']['prepared_at']);self.assertTrue(saved['integrity_verified']);self.assertFalse(saved['live_action_allowed'])
 def test_changed_version_produces_independent_artifact(self):
  self.store.save('experiment','a'*64,{'creative':'old'});self.store.save('experiment','b'*64,{'creative':'new'})
  self.assertEqual('new',self.store.latest('experiment')['payload']['creative'])
 def test_equal_clock_latest_uses_commit_order_not_version_lexical_order(self):
  fixed=datetime(2026,10,18,10,tzinfo=timezone.utc)
  with patch('work_products.datetime') as clock:
   clock.now.return_value=fixed
   self.store.save('experiment','0'*64,{'creative':'old'})
   WorkProducts(self.temp.name,self.key).save('experiment','f'*64,{'creative':'new'})
  saved=WorkProducts(self.temp.name,self.key).latest('experiment')
  self.assertEqual(saved['payload']['creative'],'new')
  self.assertEqual(saved['last_seen'],fixed.isoformat());self.assertTrue(saved['integrity_verified'])
 def test_clock_rollback_does_not_make_previous_artifact_latest(self):
  first=datetime(2026,10,18,10,tzinfo=timezone.utc);later=datetime(2026,10,18,9,tzinfo=timezone.utc)
  with patch('work_products.datetime') as clock:
   clock.now.side_effect=[first,later]
   self.store.save('tracking','a'*64,{'evidence':'old'})
   self.store.save('tracking','b'*64,{'evidence':'new'})
  saved=self.store.latest('tracking');self.assertEqual(saved['payload']['evidence'],'new')
  self.assertEqual(saved['last_seen'],later.isoformat())
 def test_reobserved_version_is_latest_but_retains_first_exact_payload(self):
  fixed=datetime(2026,10,18,10,tzinfo=timezone.utc)
  with patch('work_products.datetime') as clock:
   clock.now.return_value=fixed
   self.store.save('experiment','a'*64,{'creative':'first exact'})
   self.store.save('experiment','b'*64,{'creative':'different version'})
   self.store.save('experiment','a'*64,{'creative':'must not replace first'})
  saved=self.store.latest('experiment');self.assertEqual(saved['version'],'a'*64)
  self.assertEqual(saved['payload']['creative'],'first exact');self.assertTrue(saved['integrity_verified'])
 def test_payload_and_metadata_tamper_fail_closed(self):
  for field,value in [('payload','{}'),('last_seen','fake'),('signature','fake')]:
   with self.subTest(field=field):
    self.store.save('tracking','a'*64,{'evidence':'actual'})
    with self.store.db() as db:db.execute('UPDATE products SET '+field+'=? WHERE kind=?',(value,'tracking'))
    with self.assertRaises(ValueError):self.store.latest('tracking')
    with self.store.db() as db:db.execute('DELETE FROM products')
 def test_other_key_cannot_authenticate_artifact(self):
  self.store.save('tracking','a'*64,{'evidence':'actual'})
  with self.assertRaises(ValueError):WorkProducts(self.temp.name,b'y'*32).latest('tracking')
 def test_invalid_kind_or_version_rejected(self):
  for kind,version in [('execute','a'*64),('tracking','bad')]:
   with self.assertRaises(ValueError):self.store.save(kind,version,{})
