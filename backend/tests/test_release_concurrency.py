import asyncio
from pathlib import Path
import pytest

@pytest.mark.asyncio
async def test_release_concurrency_blocks(db_clean):
    """Test that multiple release commands can run concurrently without failing.
    
    The advisory lock in app.release should force them to run sequentially
    and succeed.
    """
    backend_root = Path(__file__).parent.parent
    
    import sys
    # Run two release processes concurrently
    proc1 = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.release",
        cwd=backend_root
    )
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.release",
        cwd=backend_root
    )
    
    code1 = await proc1.wait()
    code2 = await proc2.wait()
    
    assert code1 == 0
    assert code2 == 0
