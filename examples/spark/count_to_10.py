"""Livy / CDE Spark 3 example: write 1..10 to Iceberg for Hive to SELECT.

The table is registered in the Hive catalog (Iceberg), not a Spark-only catalog.
Hive MCP is read-only: this job is the write; `hive_select` is the next step.

Submit from HDFS (or a CDE job resource), not a laptop path:

    gateway webhdfs put examples/spark/count_to_10.py /user/$USER/examples/count_to_10.py
    gateway mcp --tool spark_submit_batch \\
      --arg file=hdfs:///user/$USER/examples/count_to_10.py \\
      --arg name=count-to-10

Optional args: <database> <table>. Defaults: Spark user database, table count_to_10.

Then:

    gateway mcp --adapter hive --tool hive_select \\
      --arg database=$USER --arg table=count_to_10 --arg columns=n --arg limit=10
"""

from __future__ import annotations

import re
import sys

from pyspark.sql import SparkSession

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
DEFAULT_TABLE = "count_to_10"


def hive_ident(raw: str, *, field: str) -> str:
    name = (raw or "").strip().strip("`")
    if not _IDENT.match(name):
        raise SystemExit(f"{field} must be a Hive identifier, got {raw!r}")
    return name


def spark_user_database(spark: SparkSession) -> str:
    user = (spark.sparkContext.sparkUser() or "default").split("@", 1)[0]
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", user) or "default"
    if cleaned[0].isdigit():
        cleaned = f"u_{cleaned}"
    return hive_ident(cleaned, field="database")


def resolve_target(spark: SparkSession, argv: list[str]) -> tuple[str, str]:
    database = argv[1] if len(argv) > 1 else ""
    table = argv[2] if len(argv) > 2 else ""
    if not database:
        database = spark.conf.get("agentgateway.example.database", "")
    if not table:
        table = spark.conf.get("agentgateway.example.table", "")
    if not database:
        database = spark_user_database(spark)
    if not table:
        table = DEFAULT_TABLE
    return hive_ident(database, field="database"), hive_ident(table, field="table")


def main(argv: list[str] | None = None) -> None:
    spark = SparkSession.builder.appName("count-to-10").enableHiveSupport().getOrCreate()
    database, table = resolve_target(spark, argv if argv is not None else sys.argv)
    qualified = f"`{database}`.`{table}`"

    numbers = spark.range(1, 11).selectExpr("id AS n")
    values = [int(row.n) for row in numbers.collect()]
    total = numbers.count()
    for value in values:
        print(f"n={value}")
    print(f"count={total}")
    if values != list(range(1, 11)) or total != 10:
        raise SystemExit(f"expected 1..10, got {values} count={total}")

    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    (
        numbers.writeTo(f"{database}.{table}")
        .using("iceberg")
        .tableProperty("format-version", "2")
        .createOrReplace()
    )

    written = [int(row.n) for row in spark.table(qualified).orderBy("n").collect()]
    if written != list(range(1, 11)):
        raise SystemExit(f"Iceberg {qualified} expected 1..10, got {written}")

    print(f"iceberg_table={database}.{table}")
    print("hive_columns=n")
    print(
        "hive_select: "
        f"gateway mcp --adapter hive --tool hive_select "
        f"--arg database={database} --arg table={table} --arg columns=n --arg limit=10"
    )
    spark.stop()


if __name__ == "__main__":
    main()
