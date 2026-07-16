#!/usr/bin/env python3
"""Copy an existing model-router SQLite database into PostgreSQL.

Run this once during the cutover, after ``alembic upgrade head`` has created
the target schema. It intentionally refuses to truncate data and never prints
database URLs or row contents.
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, inspect, select

from app import models  # noqa: F401
from app.database import Base


def migrate(source_url: str, target_url: str, *, batch_size: int = 500) -> int:
    source = create_engine(source_url)
    target = create_engine(target_url, pool_pre_ping=True)
    total = 0
    with source.connect() as source_connection, target.begin() as target_connection:
        for table in Base.metadata.sorted_tables:
            source_columns = {column["name"] for column in inspect(source).get_columns(table.name)}
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
                normalized_rows.append(row)
            for start in range(0, len(rows), batch_size):
                target_connection.execute(table.insert(), normalized_rows[start : start + batch_size])
            total += len(rows)
            print(f"migrated {table.name}: {len(rows)} rows")
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
