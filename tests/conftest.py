from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
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
