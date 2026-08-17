# Spark examples

`count_to_10.py` is a Livy Spark 3 batch that prints `n=1` … `n=10` and `count=10`.

Operator guide: [docs/spark.md](../../docs/spark.md). `spark_submit_batch` is a write as the Knox token subject. Livy cannot read this file from your laptop. Copy it to a URI Ranger allows, then submit through the gateway.

```bash
# example: HDFS (adjust user and FS)
hdfs dfs -mkdir -p /user/$USER/examples
hdfs dfs -put -f examples/spark/count_to_10.py /user/$USER/examples/count_to_10.py

source .venv/bin/activate
gateway mcp --tool spark_submit_batch \
  --arg file=hdfs:///user/$USER/examples/count_to_10.py \
  --arg name=count-to-10
```

Poll:

```bash
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_get_batch --arg batch_id=0
gateway mcp --tool spark_get_log --arg batch_id=0
```

Use `s3a://`, `abfs://`, or `o3fs://` if that is where the file lives. `http://` and laptop paths are rejected.
