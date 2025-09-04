import datetime
from typing import List

from sqlalchemy import (
    MetaData,
    BigInteger,
    String,
    Table,
    Column,
    ForeignKey,
    DateTime,
    Boolean,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

convention = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    metadata = MetaData(naming_convention=convention)
    type_annotation_map = {
        datetime.datetime: DateTime(timezone=True),
    }


# Intermediate table для связи many-to-many – столбцы определяются через core.Column
position_pool = Table(
    'position_pool',
    Base.metadata,
    Column(
        'position_id',
        BigInteger,
        ForeignKey('positions.id', ondelete='CASCADE'),
        primary_key=True
    ),
    Column(
        'pool_id',
        String,
        ForeignKey('pools.id', ondelete='CASCADE'),
        primary_key=True
    ),
)


class Position(Base):
    __tablename__ = 'positions'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_liquidated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=True,
        default=False,
        index=True,
        comment="Indicates if the position has been liquidated"
    )
    predict_check_timestamp: Mapped[BigInteger | None] = mapped_column(
        BigInteger,
        index=True,
        default=None,
        nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    pools: Mapped[List["Pool"]] = relationship(
        secondary=position_pool,
        back_populates="positions",
        cascade="all, delete"
    )


class Pool(Base):
    __tablename__ = 'pools'
    id: Mapped[str] = mapped_column(
        String(42),
        primary_key=True,
        comment="Unique identifier for the pool (address)"
    )

    positions: Mapped[List[Position]] = relationship(
        secondary=position_pool,
        back_populates="pools",
        cascade="all, delete"
    )
