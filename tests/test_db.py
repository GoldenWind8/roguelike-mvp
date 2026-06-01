"""Foundation tests for the data-access layer (issue #19).

These don't test any model yet — they prove the plumbing works: a session
can be opened, run a query, and the declarative Base is wired up.
"""
from sqlalchemy import text

from backend.db import Base, get_session


async def test_session_executes_query(session):
    """A session from the fixture can talk to the database."""
    result = await session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_get_session_dependency_yields_and_closes():
    """The FastAPI dependency yields exactly one usable session, then closes."""
    gen = get_session()
    s = await gen.__anext__()
    assert (await s.execute(text("SELECT 1"))).scalar_one() == 1

    # Driving the generator past its single yield must end it (session closed).
    try:
        await gen.__anext__()
        assert False, "get_session should yield only once"
    except StopAsyncIteration:
        pass


def test_base_has_metadata():
    """Base is a proper declarative base; models will register on its metadata."""
    assert hasattr(Base, "metadata")
