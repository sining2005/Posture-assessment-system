from __future__ import annotations

import hashlib
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from posture_assessment.models import Base, Operator


def hash_password(password: str, salt_hex: str) -> str:
    iterations = max(1_000, int(os.environ.get("POSTURE_PASSWORD_ITERATIONS", "200000")))
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    )
    return digest.hex()


class DatabaseManager:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            existing = session.scalar(select(Operator).where(Operator.username == "admin"))
            if existing is None:
                salt = secrets.token_hex(16)
                session.add(
                    Operator(
                        username="admin",
                        salt=salt,
                        password_hash=hash_password("admin123", salt),
                    )
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
