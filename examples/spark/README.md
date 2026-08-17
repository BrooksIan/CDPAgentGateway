# Spark examples

`count_to_10.py` is a Livy / CDE Spark 3 batch that prints `n=1` … `n=10`, then writes those rows to an **Iceberg** table in the Hive catalog. Hive MCP can `hive_select` that table afterward (named columns, `limit` ≤ 50). Spark is the write; Hive tools stay read-only.

Operator guide: [docs/spark.md](../../docs/spark.md), then [docs/hive.md](../../docs/hive.md). `spark_submit_batch` is a write as the Knox token subject. Livy cannot read this file from your laptop. Copy it to a URI Ranger allows, then submit through the gateway.

```mermaid
flowchart LR
  laptop["Laptop count_to_10.py"]
  fs["HDFS / Ozone URI"]
  mcp["gateway mcp spark_submit_batch"]
  livy["Livy as Knox sub"]
  ice["Iceberg table in HMS"]
  hive["gateway mcp hive_select"]

  laptop -->|"gateway webhdfs put"| fs
  fs --> mcp
  mcp --> livy
  livy --> ice
  ice --> hive
```

```bash
# example: HDFS through the gateway (adjust user)
gateway webhdfs put examples/spark/count_to_10.py /user/$USER/examples/count_to_10.py

source .venv/bin/activate
gateway mcp --tool spark_submit_batch \
  --arg file=hdfs:///user/$USER/examples/count_to_10.py \
  --arg name=count-to-10 \
  --arg args=$USER,count_to_10
```

Optional args are `<database> <table>`. Defaults: Spark user as database, table `count_to_10`. Ranger must allow that subject to create the database/table. The cluster must already expose Iceberg on `spark_catalog` (CDE Spark 3 does).

Poll:

```bash
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_get_batch --arg batch_id=0
gateway mcp --tool spark_get_log --arg batch_id=0
```

When the log shows `iceberg_table=…`, query Hive (same Knox JWT):

```bash
gateway mcp --adapter hive --tool hive_list_tables --arg database=$USER
gateway mcp --adapter hive --tool hive_describe_table \
  --arg database=$USER --arg table=count_to_10
gateway mcp --adapter hive --tool hive_select \
  --arg database=$USER --arg table=count_to_10 --arg columns=n --arg limit=10
```

Use `s3a://`, `abfs://`, or `o3fs://` if that is where the file lives. `http://` and laptop paths are rejected.
