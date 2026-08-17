"""Livy Spark 3 example: count from 1 to 10.

Submit this file from HDFS/Ozone (not from the laptop path):

    gateway mcp --tool spark_submit_batch \\
      --arg file=hdfs:///user/$USER/examples/count_to_10.py \\
      --arg name=count-to-10
"""

from __future__ import annotations

from pyspark.sql import SparkSession


def main() -> None:
    spark = SparkSession.builder.appName("count-to-10").getOrCreate()
    numbers = spark.range(1, 11)
    values = [int(row.id) for row in numbers.collect()]
    total = numbers.count()
    for value in values:
        print(f"n={value}")
    print(f"count={total}")
    if values != list(range(1, 11)) or total != 10:
        raise SystemExit(f"expected 1..10, got {values} count={total}")
    spark.stop()


if __name__ == "__main__":
    main()
