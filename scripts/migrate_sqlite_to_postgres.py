#!/usr/bin/env python3
"""Copy an existing model-router SQLite database into PostgreSQL.

Run this once during the cutover, after ``alembic upgrade head`` has created
the target schema. It intentionally refuses to truncate data and never prints
database URLs or row contents. Missing source tables are skipped for older
SQLite installs; stale references in nullable historical foreign keys are
cleared and reported rather than inventing deleted parent rows.
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, inspect, select

from app import models  # noqa: F401
from app.database import Base


def normalize_foreign_keys(row: dict, table, source_connection) -> tuple[dict, list[str]]:
    """Preserve rows whose deleted nullable parents are no longer available."""
    normalized = dict(row)
    adjustments: list[str] = []
    for foreign_key in table.foreign_keys:
        column = foreign_key.parent
        value = normalized.get(column.name)
        if value is None:
            continue
        target_column = foreign_key.column
        exists = source_connection.execute(
            select(target_column).where(target_column == value)
        ).first()
        if exists is not None:
            continue
        if not column.nullable:
            raise ValueError(
                f"{table.name}.{column.name} references missing required row {value}"
            )
        normalized[column.name] = None
        adjustments.append(f"{table.name}.{column.name}")
    return normalized, adjustments


def migrate(source_url: str, target_url: str, *, batch_size: int = 500) -> int:
    source = create_engine(source_url)
    target = create_engine(target_url, pool_pre_ping=True)
    total = 0
    adjustments: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        source_inspector = inspect(source)
        for table in Base.metadata.sorted_tables:
            if not source_inspector.has_table(table.name):
                print(f"skipped missing source table: {table.name}")
                continue
            source_columns = {column["name"] for column in source_inspector.get_columns(table.name)}
            columns = [column for column in table.columns if column.name in source_columns]
            if not columns:
                continue
            rows = source_connection.execute(select(*columns).select_from(table)).mappings().all()
            if not rows:
                continue
            normalized_rows = []
            for source_row in rows:
                row = dict(source_row)
                for column in table.columns:
                    if column.name in row or column.default is None:
                        continue
                    default = column.default.arg
                    row[column.name] = default() if callable(default) else default
                row, changed = normalize_foreign_keys(row, table, source_connection)
                for column_name in changed:
                    adjustments[column_name] = adjustments.get(column_name, 0) + 1
                normalized_rows.append(row)
            for start in range(0, len(rows), batch_size):
                target_connection.execute(table.insert(), normalized_rows[start : start + batch_size])
            total += len(rows)
            print(f"migrated {table.name}: {len(rows)} rows")
    for column_name, count in sorted(adjustments.items()):
        print(f"cleared missing nullable references {column_name}: {count} rows")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("MODEL_ROUTER_SOURCE_DATABASE_URL", "sqlite:///./model_router.db"))
    parser.add_argument("--target", default=os.getenv("MODEL_ROUTER_DATABASE_URL"))
    args = parser.parse_args()
    if not args.target:
        raise SystemExit("Set MODEL_ROUTER_DATABASE_URL or pass --target")
    try:
        total = migrate(args.source, args.target)
    except Exception as exc:  # noqa: BLE001
        print(f"migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"migrated {total} rows")


if __name__ == "__main__":
    main()
