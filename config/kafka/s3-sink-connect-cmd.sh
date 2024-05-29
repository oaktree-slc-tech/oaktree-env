curl -i -X PUT -H "Accept:application/json" \
    -H "Content-Type:application/json" http://localhost:8083/connectors/sink-s3-voluble/config \
    -d '
 {
	"connector.class": "io.confluent.connect.s3.S3SinkConnector",
	"key.converter": "org.apache.kafka.connect.storage.StringConverter",
	"value.converter": "org.apache.kafka.connect.json.JsonConverter",
	"value.converter.schemas.enable": "false",
	"tasks.max": "1",
	"topics": "audit-event-log",
	"s3.region": "us-east-1",
	"s3.bucket.name": "playground",
	"flush.size": "3",
	"storage.class": "io.confluent.connect.s3.storage.S3Storage",
	"format.class": "io.confluent.connect.s3.format.json.JsonFormat",
	"schema.generator.class": "io.confluent.connect.storage.hive.schema.DefaultSchemaGenerator",
	"schema.compatibility": "NONE",
	"partitioner.class": "io.confluent.connect.storage.partitioner.DefaultPartitioner",
	"transforms": "AddMetadata",
	"transforms.AddMetadata.type": "org.apache.kafka.connect.transforms.InsertField$Value",
	"transforms.AddMetadata.offset.field": "_offset",
	"transforms.AddMetadata.partition.field": "_partition",
	"s3.endpoint": "http://127.0.0.1:9000",
	"aws.access.key.id": "connect",
	"aws.secret.access.key": "BuC2IHO4f8L4jFg3oYUVUuwLd9beTTClOstLbIyt"
}'