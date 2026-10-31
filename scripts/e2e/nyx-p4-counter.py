import asyncio, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).parent))
from importlib.util import spec_from_file_location,module_from_spec
s=spec_from_file_location('p4',Path(__file__).with_name('nyx-p4-acceptance.py'));m=module_from_spec(s);s.loader.exec_module(m)
from nyx.config import Config
from nyx.main import build_app_context
from nyx.enums import EventType
from nyx.runtime import root_event
async def main():
 import os
 os.environ['NYX_DB']=':memory:'
 with patch('nyx.app_context.LlmClient.from_config',return_value=m.Fake()),patch('nyx.app_context.build_embed',return_value=None): app=await build_app_context(Config())
 try:
  event=root_event(EventType.USER_MESSAGE,{'message':'反馈','annotation':{'reply_event_id':'s','quote':'q','comment':'c'}})
  await app.bus.publish(event)
  original=app.bus.append_in_transaction
  async def fail(e):
   await original(e)
   raise RuntimeError('append rollback probe')
  before=app.bus.persisted_count
  with patch.object(app.bus,'append_in_transaction',side_effect=fail):
   try: await app.memory.record_annotation_feedback(event,'memory.annotation_recording')
   except RuntimeError: pass
  assert app.bus.persisted_count==before,(before,app.bus.persisted_count)
  print('PASS append rollback counter')
 finally: await app.database.conn.close()
asyncio.run(main())

