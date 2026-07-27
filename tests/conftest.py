from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.entities import Base


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    Base.metadata.drop_all(engine)


@pytest.fixture
def sample_html(tmp_path: Path) -> Path:
    path = tmp_path / "sample.html"
    path.write_text(
        """
        <html><head><title>演示规则</title><style>.x{display:none}</style></head>
        <body><nav>无关导航</nav><main><h1>演示规则</h1><p>仅用于测试。</p></main>
        <script>danger()</script></body></html>
        """,
        encoding="utf-8",
    )
    return path
