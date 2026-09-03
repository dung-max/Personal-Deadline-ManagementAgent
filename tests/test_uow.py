from personal_deadline_management_agent.uow import UnitOfWork


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_commit_calls_session_commit():
    session = FakeSession()
    uow = UnitOfWork(session)
    uow.commit()
    assert session.committed


def test_rollback_calls_session_rollback():
    session = FakeSession()
    uow = UnitOfWork(session)
    uow.rollback()
    assert session.rolled_back


def test_close_does_not_commit():
    session = FakeSession()
    uow = UnitOfWork(session)
    uow.close()
    assert session.closed
    assert not session.committed
