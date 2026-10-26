import asyncio

import pytest
from test_resources import alive
from test_session_manager import REQUEST, build_manager, wait_status


def test_configured_application_lifespan_cleans_sessions_after_handler_failure(tmp_path):
    from app.main import create_app
    async def run():
        manager = build_manager(tmp_path)
        application = create_app(session_manager=manager)
        pid = None
        with pytest.raises(RuntimeError, match='test interruption'):
            async with application.router.lifespan_context(application):
                created = await manager.create(REQUEST)
                status = await wait_status(manager, created.session_id, 'ready')
                pid = status.worker.pid
                assert application.state.session_manager is manager
                raise RuntimeError('test interruption')
        assert pid is not None and not alive(pid)
        assert manager.gateway.process.returncode is not None
    asyncio.run(run())
