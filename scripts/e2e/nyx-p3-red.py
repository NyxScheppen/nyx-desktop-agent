import asyncio, os
from unittest.mock import patch
from nyx.config import Config
from nyx.main import build_app_context
from nyx.tools.registry import ToolRegistry
async def main():
    os.environ['NYX_DB']=':memory:'
    captured={}
    original=ToolRegistry.register
    def capture(registry,tool):
        captured[tool.name]=tool
        return original(registry,tool)
    with patch('nyx.app_context.LlmClient.from_config',return_value=type('Fake',(),{})()), patch('nyx.app_context.build_embed',return_value=None), patch.object(ToolRegistry,'register',capture):
        app=await build_app_context(Config())
    try:
        result=await captured['deep_research'].handler(query='P3Nebula')
        assert '联网没开' in str(result), result
    finally:
        await app.database.conn.close()
asyncio.run(main())
